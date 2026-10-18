"""The Odds API client: live game lines + TD props for every book, with best price and no-vig consensus.

Cost per run (us region): 2 credits for lines + 1 per market per game for props (markets actually returned): 2 TD markets with SharpAPI
covering anytime/first, plus 4 yardage markets when YARDS_SOURCE=oddsapi (~86 for a 14-game slate); with PROPS_SOURCE=oddsapi (The Odds
API primary for every prop market, fixed book set PRIMARY_BOOKS) 4 TD + 4 yardage markets, ~114 for a 14-game slate. Every paid call is
logged to odds_api_usage.jsonl (tag from ODDS_API_TAG, default nfl); usage_summary() feeds the budget guard and the site notice.
Set ODDS_API_KEY in the environment. Without it everything falls back to nflverse lines and no book prices.
"""
import json, os, re, time, unicodedata, urllib.request, urllib.parse
from collections import defaultdict
import numpy as np

BASE = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl"
PROP_MARKETS = ["player_anytime_td", "player_1st_td", "player_pass_tds", "player_tds_over"]   # full set (fallback when SharpAPI is unavailable)
PROP_MARKETS_TRIMMED = ["player_tds_over", "player_pass_tds"]   # with SharpAPI covering DK/FD anytime, first TD and yardage: 2 markets per event instead of 4
YARD_MARKETS = ["player_rush_yds", "player_reception_yds", "player_rush_yds_alternate", "player_reception_yds_alternate"]   # YARDS_SOURCE=oddsapi: 4 credits per event (us)
USAGE_FILE, USAGE_TAG = "odds_api_usage.jsonl", os.environ.get("ODDS_API_TAG", "nfl")   # one line per paid call: ts, tag, path, cost, used, remaining
NFL_MTD_LIMIT, REMAINING_FLOOR = 60_000, 20_000                                        # budget guard (pull loops only; scheduled runs keep the Odds API); 60K since 2026-10-18 (early-week pulls, NBA expansion off)


def yards_source(week=None):
    """'sharpapi' (default) or 'oddsapi' (YARDS_SOURCE): which feed's rush/rec yardage ladders price the board. With a week, the
       go-live gate applies: YARDS_FROM_WEEK (repository variable, e.g. 7) keeps earlier weeks on SharpAPI so a flip made mid-week
       cannot touch the slate in progress."""
    src = "oddsapi" if os.environ.get("YARDS_SOURCE", "sharpapi").strip().lower() == "oddsapi" else "sharpapi"
    if src == "oddsapi" and week is not None and int(week) < yards_from_week(): return "sharpapi"
    return src


def yards_from_week():
    """First NFL week the Odds API yardage feed may price the board (YARDS_FROM_WEEK, default 0 = no gate)."""
    try: return int(os.environ.get("YARDS_FROM_WEEK") or 0)
    except ValueError: return 0


PRIMARY_BOOKS = ("DraftKings", "FanDuel", "BetMGM", "Caesars", "Hard Rock Bet", "BetRivers", "Fanatics", "ESPN BET")   # fixed book set when The Odds API is primary (= yard_prices.YARD_BOOKS)
FETCH = {}   # accounting for the last fetch_all(): events, with_props, props_errors, lines_error -> fallback_reason()


def props_source(week=None):
    """'sharpapi' (default): SharpAPI is primary for DK/FD anytime, first TD and yardage and The Odds API fills the rest (2+ TD, pass TDs).
       'oddsapi' (PROPS_SOURCE): The Odds API is primary for EVERY prop market (TD + yardage ladders, region us, fixed book set
       PRIMARY_BOOKS) and SharpAPI is a fallback only (an Odds API call errors or a credit guard trips). With a week, PROPS_FROM_WEEK
       gates the switch the way YARDS_FROM_WEEK gates the yardage-only flag."""
    src = "oddsapi" if os.environ.get("PROPS_SOURCE", "sharpapi").strip().lower() == "oddsapi" else "sharpapi"
    if src == "oddsapi" and week is not None and int(week) < props_from_week(): return "sharpapi"
    return src


def props_from_week():
    """First NFL week The Odds API is primary for every prop market (PROPS_FROM_WEEK, default 0 = no gate)."""
    try: return int(os.environ.get("PROPS_FROM_WEEK") or 0)
    except ValueError: return 0


def fallback_reason(events):
    """PROPS_SOURCE=oddsapi: why this pull falls back to SharpAPI, or None when The Odds API answered cleanly. Triggers: the credit guard
       tripped (pull_loop blanks the key and sets ODDS_GUARD_TRIPPED), no key, the lines call failed, any per-event props call errored,
       or games were returned but none with prop prices. An empty slate (no games inside the horizon) is not a failure."""
    if os.environ.get("ODDS_GUARD_TRIPPED"): return "credit guard tripped"
    if events is None: return f"Odds API lines call failed: {FETCH['lines_error']}" if FETCH.get("lines_error") else "no Odds API key"
    if FETCH.get("props_errors"): return f"{FETCH['props_errors']} of {FETCH.get('events', 0)} Odds API props calls errored"
    if events and not any(e.get("props", {}).get("bookmakers") for e in events): return "Odds API returned no prop prices"
    return None


def tag_sources(events):
    """Every Odds API prop outcome carries source="oddsapi" and ts = its market's last_update (the capture time the price-age rule
       reads). Must run on every pull: without ts the timing rule holds the edge as "price age unknown" (the 2026-10-18 02:39Z go-live
       pull held 88 edges because the tagging lived inside the SharpAPI merge, which the primary mode no longer runs)."""
    for e in events or []:
        for b in e.get("props", {}).get("bookmakers", []):
            for m in b["markets"]:
                for o in m["outcomes"]:
                    o.setdefault("source", "oddsapi"); o.setdefault("ts", m.get("last_update") or b.get("last_update"))
    return events


def only_books(events, titles, keep=("Kalshi",)):
    """Copy of `events` keeping only the prop bookmakers in `titles` (plus `keep`, Kalshi); game lines (spreads/totals) untouched."""
    want = {t.lower() for t in titles} | {t.lower() for t in keep}
    out = []
    for e in events or []:
        e2 = dict(e)
        if "props" in e2:
            e2["props"] = dict(e2["props"], bookmakers=[b for b in e2["props"].get("bookmakers", []) if (b.get("title") or b.get("key") or "").lower() in want])
        out.append(e2)
    return out


def log_usage(path, headers, tag=None, region=None):
    """Append one record per paid call so NFL credits can be summed month to date and per pull type (committed with the odds history).
       pull_type = PULL_TYPE env (scheduled | early | loop | manual | pickem | local), region = the regions parameter (us | us_ex)."""
    import datetime as dt
    rec = dict(ts=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), tag=tag or USAGE_TAG, path=path,
               cost=int(headers.get("x-requests-last") or 0), used=int(headers.get("x-requests-used") or 0), remaining=int(headers.get("x-requests-remaining") or 0),
               pull_type=os.environ.get("PULL_TYPE") or ("manual" if os.environ.get("GITHUB_ACTIONS") else "local"), region=region or "us")
    try:
        with open(USAGE_FILE, "a") as f: f.write(json.dumps(rec) + "\n")
    except OSError as e:
        print("usage log failed:", e)
    return rec


def usage_summary(fn=USAGE_FILE, tag="nfl", now=None):
    """{nfl_mtd, remaining, last_call, month, guard: {tripped, reasons, mtd_limit, remaining_floor}} from the usage log (UTC calendar month)."""
    import datetime as dt
    now = now or dt.datetime.now(dt.timezone.utc); month = now.strftime("%Y-%m")
    rows = []
    if os.path.exists(fn):
        for ln in open(fn):
            ln = ln.strip()
            if ln:
                try: rows.append(json.loads(ln))
                except ValueError: pass
    mtd = sum(r.get("cost", 0) for r in rows if r.get("tag") == tag and str(r.get("ts", "")).startswith(month))
    last = rows[-1] if rows else None
    remaining = last.get("remaining") if last else None
    reasons = []
    if mtd > NFL_MTD_LIMIT: reasons.append(f"NFL credits this month {mtd:,} > {NFL_MTD_LIMIT:,}")
    if remaining is not None and remaining < REMAINING_FLOOR: reasons.append(f"account remaining {remaining:,} < {REMAINING_FLOOR:,}")
    return dict(nfl_mtd=mtd, remaining=remaining, last_call=last.get("ts") if last else None, month=month,
                guard=dict(tripped=bool(reasons), reasons=reasons, mtd_limit=NFL_MTD_LIMIT, remaining_floor=REMAINING_FLOOR))


# ---------------- cross-venue line shop: The Odds API us_ex region (Novig, ProphetX, Polymarket, Kalshi) ----------------
EXCHANGE_REGION = "us_ex"
EXCHANGE_MARKETS = ["player_anytime_td", "player_1st_td", "player_rush_yds", "player_reception_yds", "player_rush_yds_alternate", "player_reception_yds_alternate"]
EXCHANGE_CREDITS_PER_GAME = len(EXCHANGE_MARKETS)        # The Odds API charges 1 credit per market RETURNED per region per event (probe 2026-10-18: 6 when all six came back, 1 for a game with Kalshi only)


def exchange_mode():
    """EXCHANGE_PULL env: 'all' (scheduled runs: every game with an edge), 'final' (kickoff loops: only a game's final pre-kickoff pull),
       'off' (default: early-week and manual pulls spend nothing on us_ex). The budget guard (budget_projection stage >= 1) forces off."""
    m = (os.environ.get("EXCHANGE_PULL") or "off").strip().lower()
    return m if m in ("all", "final", "off") else "off"


def exchange_games(edge_teams, games, now, mode, step_min=10, cutoff_min=30):
    """Which games get a us_ex call this pull: games with >= 1 edge on the board; in 'final' mode only those whose kickoff is between the
       cutoff and cutoff + step (the last pull at which the game is still actionable). `games` = run_week's game dicts (home, away, kick_utc)."""
    import pandas as pd
    if mode == "off": return []
    out = []
    for g in games:
        if g["home"] not in edge_teams and g["away"] not in edge_teams: continue
        if mode == "final":
            try: km = (pd.to_datetime(g["kick_utc"], utc=True) - now) / pd.Timedelta(minutes=1)
            except (TypeError, ValueError): continue
            if not (cutoff_min <= km < cutoff_min + step_min): continue
        out.append(g)
    return out


def fetch_exchange(events, event_ids, api_key=None, fetch=None):
    """One us_ex call per listed event (cost: markets returned, <= EXCHANGE_CREDITS_PER_GAME). Returns {event_id: response}; errors are
       skipped and counted in FETCH['exchange_errors']. Nothing here touches events' props (the edge rule never sees these quotes)."""
    api_key = api_key or os.environ.get("ODDS_API_KEY")
    FETCH["exchange_events"], FETCH["exchange_errors"] = 0, 0
    if not (api_key or fetch) or not event_ids: return {}
    base = dict(apiKey=api_key or "x", regions=EXCHANGE_REGION, oddsFormat="american", markets=",".join(EXCHANGE_MARKETS))
    out = {}
    for ev in events or []:
        if ev["id"] not in event_ids: continue
        try:
            out[ev["id"]] = _get(f"{BASE}/events/{ev['id']}/odds", base, fetch); FETCH["exchange_events"] += 1
        except Exception as e:
            print("exchange fail", ev.get("id"), e); FETCH["exchange_errors"] += 1
        time.sleep(0.2)
    return out


# ---------------- credit budget: projected balance at the billing reset (the 1st of the month), staged guard ----------------
RESET_DAY, PROJECTED_FLOOR = 1, 10_000             # the plan resets on the 1st of each month (user, 2026-10-18); below 10,000 projected at the reset: stage 1 skips us_ex, stage 2 halves the kickoff-loop cadence
SCHEDULED_PULLS_PER_WEEK, PICKEM_CREDITS_PER_GAME = 4, 7
MARKETS_PER_GAME = 8                               # PROPS_SOURCE=oddsapi: 4 TD + 4 yardage markets per not-yet-kicked-off game, region us


def next_reset(now):
    import datetime as dt
    y, m = now.year, now.month
    if now.day >= RESET_DAY: m += 1
    if m > 12: y, m = y + 1, 1
    return dt.datetime(y, m, RESET_DAY, tzinfo=dt.timezone.utc)


def planned_weekly(windows=None, fn="pull_windows.json", exchange=True):
    """Planned Odds API credits per week from pull_windows.json (kickoff loops + early-week pulls) plus the scheduled runs, the pick'em
       scanner and the us_ex line shop (scheduled pulls + one final pre-kickoff pull per game). {total, loops, early, scheduled, pickem, exchange, games}."""
    w = windows
    if w is None and os.path.exists(fn):
        try: w = json.load(open(fn))
        except ValueError: w = None
    games = sum(len(c.get("games", [])) for c in (w or {}).get("clusters", [])) or 14
    loops = int((w or {}).get("odds_api_credits") or 0) or 7300
    early = int(((w or {}).get("early") or {}).get("credits") or 0)
    sched = SCHEDULED_PULLS_PER_WEEK * (2 + MARKETS_PER_GAME * games)
    pick = PICKEM_CREDITS_PER_GAME * games
    ex = EXCHANGE_CREDITS_PER_GAME * games * (SCHEDULED_PULLS_PER_WEEK + 1) if exchange else 0
    return dict(total=loops + early + sched + pick + ex, loops=loops, early=early, scheduled=sched, pickem=pick, exchange=ex, games=games)


def observed_daily(rows, now, days=3):
    """Account burn per day over the trailing window (3 days: a one-off manual spike clears in three days) from the `remaining` series in the
       usage log (every call records the account's remaining credits, so the drop between records covers everything spent in between,
       pipeline or not). None without a 1-day span or across a reset (remaining rises)."""
    import datetime as dt
    lo = (now - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    pts = [(r["ts"], r["remaining"]) for r in rows if r.get("remaining") is not None and str(r.get("ts", "")) >= lo]
    if len(pts) < 2: return None
    pts.sort()
    (t0, r0), (t1, r1) = pts[0], pts[-1]
    span = (dt.datetime.fromisoformat(t1.replace("Z", "+00:00")) - dt.datetime.fromisoformat(t0.replace("Z", "+00:00"))).total_seconds() / 86400
    if span < 1 or r1 > r0: return None
    return (r0 - r1) / span


def budget_projection(now=None, remaining=None, fn=USAGE_FILE, windows=None, windows_fn="pull_windows.json"):
    """Projected account balance at the next billing reset and the guard stage. burn/day = max(planned pipeline burn, observed trailing
       3-day burn); stage 1 (projected < PROJECTED_FLOOR): skip us_ex; stage 2 (still under the floor without us_ex): halve the kickoff-loop
       cadence as well. `remaining` defaults to the newest usage record."""
    import datetime as dt
    now = now or dt.datetime.now(dt.timezone.utc)
    rows = []
    if os.path.exists(fn):
        for ln in open(fn):
            ln = ln.strip()
            if ln:
                try: rows.append(json.loads(ln))
                except ValueError: pass
    if remaining is None:
        rem_rows = [r for r in rows if r.get("remaining") is not None]
        remaining = rem_rows[-1]["remaining"] if rem_rows else None
    reset = next_reset(now); days = max((reset - now).total_seconds() / 86400, 0)
    plan = planned_weekly(windows, windows_fn); planned_day = plan["total"] / 7; ex_day = plan["exchange"] / 7
    obs = observed_daily(rows, now)
    burn = max(planned_day, obs or 0)
    out = dict(remaining=remaining, reset_at=reset.strftime("%Y-%m-%dT%H:%M:%SZ"), days_to_reset=round(days, 2), planned_per_day=round(planned_day),
               observed_per_day=None if obs is None else round(obs), burn_per_day=round(burn), planned=plan, floor=PROJECTED_FLOOR,
               projected_at_reset=None, stage=0, actions=[])
    if remaining is None: return out
    proj = remaining - burn * days
    out["projected_at_reset"] = round(proj)
    if proj < PROJECTED_FLOOR:
        out["stage"] = 1; out["actions"].append("skip us_ex (exchange) pulls")
        if proj + ex_day * days < PROJECTED_FLOOR:
            out["stage"] = 2; out["actions"].append("kickoff loops pull every 20 min instead of 10")
    return out


TWO_PLUS = ("player_tds_over", 1.5)
# The Odds API calls first-TD "player_1st_td"; the rest of the code uses "player_first_td".
MARKET_ALIAS = {"player_1st_td": "player_first_td"}
# What a book's Yes prices for one game should sum to with no margin (2024-25 regular season play-by-play):
# anytime TD -> mean distinct offensive (run/pass) TD scorers per game; first TD -> share of games whose first
# TD is scored by an offensive player. Dividing the observed sum by this gives the book's real overround.
ANCHOR = {"player_anytime_td": 4.10, "player_first_td": 0.945, "player_tds_over": 0.68}   # tds_over: players with 2+ TDs per game (2024-25), measured at point 1.5 only
ANCHOR_POINT = {"player_tds_over": 1.5}
DEFAULT_OVERROUND = {"player_anytime_td": 1.22, "player_first_td": 1.43, "player_tds_over": 1.25}   # measured 2026 wk3, used if a book has too few games
TEAM_ABBR = {
    "Arizona Cardinals": "ARI", "Atlanta Falcons": "ATL", "Baltimore Ravens": "BAL", "Buffalo Bills": "BUF",
    "Carolina Panthers": "CAR", "Chicago Bears": "CHI", "Cincinnati Bengals": "CIN", "Cleveland Browns": "CLE",
    "Dallas Cowboys": "DAL", "Denver Broncos": "DEN", "Detroit Lions": "DET", "Green Bay Packers": "GB",
    "Houston Texans": "HOU", "Indianapolis Colts": "IND", "Jacksonville Jaguars": "JAX", "Kansas City Chiefs": "KC",
    "Las Vegas Raiders": "LV", "Los Angeles Chargers": "LAC", "Los Angeles Rams": "LA", "Miami Dolphins": "MIA",
    "Minnesota Vikings": "MIN", "New England Patriots": "NE", "New Orleans Saints": "NO", "New York Giants": "NYG",
    "New York Jets": "NYJ", "Philadelphia Eagles": "PHI", "Pittsburgh Steelers": "PIT", "San Francisco 49ers": "SF",
    "Seattle Seahawks": "SEA", "Tampa Bay Buccaneers": "TB", "Tennessee Titans": "TEN", "Washington Commanders": "WAS",
}


def norm_name(n: str) -> str:
    n = unicodedata.normalize("NFKD", n).encode("ascii", "ignore").decode().lower()
    n = re.sub(r"[.'\-]", "", n)
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n)
    return re.sub(r"\s+", " ", n).strip()


def implied(price):
    return 100 / (price + 100) if price > 0 else -price / (-price + 100)


def decimal(price):
    return 1 + (price / 100 if price > 0 else 100 / -price)


class LiveCallsOff(RuntimeError):
    """Raised for a live Odds API call outside CI without ODDS_LIVE=1 (credits rule, 2026-10-18): local runs replay a saved pull."""


def live_allowed():
    return bool(os.environ.get("GITHUB_ACTIONS") or os.environ.get("ODDS_LIVE"))


def newest_pull(season=None, week=None, d="odds_history"):
    """Newest saved pull (for the week when given) to replay locally instead of spending credits."""
    import glob
    pat = f"{d}/{season}_w{week}_*.json" if season and week else f"{d}/*_w*_*.json"
    fs = sorted(f for f in glob.glob(pat) if re.search(r"_\d{8}T\d{4}Z\.json$", f))
    return fs[-1] if fs else None


def _get(url, params, fetch=None):
    if fetch:                       # injectable for tests
        return fetch(url, params)
    if not live_allowed():
        raise LiveCallsOff("live Odds API calls are off outside CI: run with --live (ODDS_LIVE=1) or set ODDS_MOCK to a saved pull")
    q = urllib.parse.urlencode(params)
    with urllib.request.urlopen(f"{url}?{q}", timeout=30) as r:
        left = r.headers.get("x-requests-remaining")
        if left:
            print("odds api credits remaining:", left, "| this call:", r.headers.get("x-requests-last"), "| region:", params.get("regions", "us"))
            log_usage(url.replace(BASE, ""), r.headers, region=params.get("regions"))
        return json.load(r)


def fetch_all(api_key=None, fetch=None, markets=None):
    markets = markets or (PROP_MARKETS_TRIMMED if os.environ.get("ODDS_API_PROPS", "trimmed") == "trimmed" and os.environ.get("SHARPAPI_KEY") else PROP_MARKETS)
    if props_source() == "oddsapi": markets = list(PROP_MARKETS)                                           # primary: every TD market from The Odds API
    if yards_source() == "oddsapi" or props_source() == "oddsapi": markets = list(markets) + [m for m in YARD_MARKETS if m not in markets]   # rush/rec main + alternate ladders, region us
    FETCH.clear(); FETCH.update(events=0, with_props=0, props_errors=0, markets=list(markets))
    if os.environ.get("ODDS_MOCK"):                     # offline test fixture
        return json.load(open(os.environ["ODDS_MOCK"]))
    api_key = api_key or os.environ.get("ODDS_API_KEY")
    if not api_key and not fetch:
        return None
    base = dict(apiKey=api_key or "x", regions="us", oddsFormat="american")
    try:
        events = _get(f"{BASE}/odds", {**base, "markets": "spreads,totals"}, fetch)
    except Exception as e:
        FETCH["lines_error"] = f"{type(e).__name__}: {e}"[:200]; raise
    # only this week's games that have not kicked off: props cost credits per event, and in-game boards are partial
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = (now + dt.timedelta(days=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
    started = (now - dt.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    events = [e for e in events if started <= e["commence_time"] <= cutoff]
    for ev in events:
        try:
            ev["props"] = _get(f"{BASE}/events/{ev['id']}/odds", {**base, "markets": ",".join(markets)}, fetch)
        except Exception as e:
            print("props fail", ev.get("id"), e); ev["props"] = {"bookmakers": []}; FETCH["props_errors"] += 1
        time.sleep(0.2)
    FETCH.update(events=len(events), with_props=sum(1 for ev in events if ev["props"].get("bookmakers")))
    if os.environ.get("ODDS_SAVE"):                    # keep the raw response so reruns can use ODDS_MOCK
        json.dump(events, open(os.environ["ODDS_SAVE"], "w"))
    return events


def game_lines(events):
    """(away, home) abbr -> dict(spread_line [nflverse sign: + = home favored], total_line)."""
    out = {}
    for ev in events or []:
        h, a = TEAM_ABBR.get(ev["home_team"]), TEAM_ABBR.get(ev["away_team"])
        sp, tot = [], []
        for b in ev.get("bookmakers", []):
            for m in b["markets"]:
                if m["key"] == "spreads":
                    for o in m["outcomes"]:
                        if o["name"] == ev["home_team"]: sp.append(-o["point"])
                if m["key"] == "totals":
                    tot += [o["point"] for o in m["outcomes"] if o["name"] == "Over"]
        if sp and tot:
            out[(a, h)] = dict(spread_line=float(np.median(sp)), total_line=float(np.median(tot)),
                               commence=ev["commence_time"], books=len(ev.get("bookmakers", [])))
    return out


REFERENCE = {}     # (market, norm_player, point) -> {book: price} for quotes shown but not priced (e.g. illiquid Kalshi)


def prop_board(events):
    """Returns {(market, norm_player, point): {book: {"yes": price, "no": price}}}.
       Outcomes flagged reference=True go to REFERENCE instead (displayed, never used for best/median/EV)."""
    board = defaultdict(lambda: defaultdict(dict))
    REFERENCE.clear()
    for ev in events or []:
        for b in ev.get("props", {}).get("bookmakers", []):
            for m in b["markets"]:
                for o in m["outcomes"]:
                    player = o.get("description") or o.get("name")
                    side = o["name"].lower()
                    side = "yes" if side in ("yes", "over") else "no" if side in ("no", "under") else "yes"
                    key = (MARKET_ALIAS.get(m["key"], m["key"]), norm_name(player), o.get("point"))
                    if o.get("name") in ("Over", "Under") and m["key"].startswith("player_") and "yds" in m["key"] or m["key"].endswith("_alternate"):
                        continue                                           # yardage ladders are not TD props (yard ladders read them separately)
                    if o.get("reference"):
                        if side == "yes": REFERENCE.setdefault(key, {})[b["title"]] = o["price"]
                        continue
                    board[key][b["title"]][side] = o["price"]
                    if o.get("ts"): board[key][b["title"]]["ts"] = max(board[key][b["title"]].get("ts") or "", str(o["ts"]))   # capture time, for price age
    return board


HOLD_MIN_OUTCOMES, HOLD_RANGE = 8, (1.0, 2.0)   # a board with fewer listed players is partial (in-game): skip it; a book's overround is never below 1


def measure_hold(events, min_games=3):
    """Measured overround per (market, book): median over games of sum(implied Yes) / ANCHOR[market].
    Partial boards (fewer than HOLD_MIN_OUTCOMES priced players, typical once a game has started) are skipped and
    the result is clamped to HOLD_RANGE, so an in-game pull cannot inflate every market probability.
    Falls back to the pooled market figure, then DEFAULT_OVERROUND, when a book prices too few games."""
    sums = defaultdict(list)
    for ev in events or []:
        for b in ev.get("props", {}).get("bookmakers", []):
            for m in b["markets"]:
                mk = MARKET_ALIAS.get(m["key"], m["key"])
                if mk not in ANCHOR:
                    continue
                yes = [o for o in m["outcomes"] if o["name"].lower() in ("yes", "over") and not o.get("reference")
                       and (mk not in ANCHOR_POINT or o.get("point") == ANCHOR_POINT[mk])]
                if len(yes) < HOLD_MIN_OUTCOMES:
                    continue
                s = sum(implied(o["price"]) for o in yes)
                if s > 0:
                    sums[(mk, b["title"])].append(s / ANCHOR[mk])
    clamp = lambda x: float(min(max(x, HOLD_RANGE[0]), HOLD_RANGE[1]))
    out = dict(DEFAULT_OVERROUND)
    for mk in ANCHOR:
        pooled = [x for (m, _), v in sums.items() if m == mk for x in v]
        if pooled:
            out[mk] = clamp(np.median(pooled))
    for (mk, bk), v in sums.items():
        out[(mk, bk)] = clamp(np.median(v)) if len(v) >= min_games else out[mk]
    return out


def american(dec):
    return int(round((dec - 1) * 100)) if dec >= 2 else int(round(-100 / (dec - 1)))


def price_summary(board, market, player, point=None, hold=None):
    """Best and median Yes/Over price across books, and no-vig consensus probability.
    `hold` is the dict from measure_hold(); without it a flat DEFAULT_OVERROUND is used."""
    books = board.get((market, norm_name(player), point))
    if not books:
        return None
    yes = [(bk, v["yes"]) for bk, v in books.items() if "yes" in v]
    if not yes:
        return None
    best_book, best = max(yes, key=lambda x: decimal(x[1]))
    med = american(float(np.median([decimal(pr) for _, pr in yes])))
    hold = hold or {}
    fair = []
    for bk, v in books.items():
        if "yes" in v and "no" in v:                       # two-sided: strip the vig directly
            py, pn = implied(v["yes"]), implied(v["no"])
            fair.append(py / (py + pn))
        elif "yes" in v:                                   # one-sided: divide by that book's measured overround
            over = hold.get((market, bk)) or hold.get(market) or DEFAULT_OVERROUND.get(market, 1.05)
            fair.append(implied(v["yes"]) / over)
    return dict(best=int(best), book=best_book, median=med, n_books=len(yes), market_p=float(np.median(fair)), captured=books[best_book].get("ts"))
