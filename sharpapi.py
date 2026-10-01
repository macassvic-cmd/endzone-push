"""SharpAPI (free tier: DraftKings + FanDuel, 12 req/min, 60 s delay) as the primary source for DK/FD anytime TD,
first TD (FanDuel) and every yardage market (main + alternate lines).

fetch_week() -> list of events in the same shape as The Odds API's (home_team / away_team / commence_time /
bookmakers[] for spreads+totals / props.bookmakers[].markets[].outcomes[]), so odds.game_lines, odds.prop_board and
odds.measure_hold work unchanged. Every outcome carries source="sharpapi" and ts (SharpAPI's timestamp) so the
merge in run_week.py can keep the fresher quote when a book appears from two sources. Yardage rows are kept under
The Odds API's keys (player_rush_yds, player_reception_yds, player_pass_yds, plus *_alternate) with point and main
flags for the yard ladders. Team-defense and header rows are skipped. Key: SHARPAPI_KEY in the environment.
"""
import os, json, time, datetime as dt, urllib.request, urllib.parse

BASE = "https://api.sharpapi.io/api/v1"
BOOK_TITLE = {"draftkings": "DraftKings", "fanduel": "FanDuel", "betmgm": "BetMGM", "caesars": "Caesars", "pinnacle": "Pinnacle", "kalshi": "Kalshi"}
MARKET_MAP = {"anytime_touchdown_scorer": "player_anytime_td", "first_touchdown_scorer": "player_1st_td",
              "player_rushing_yards": "player_rush_yds", "player_receiving_yards": "player_reception_yds", "player_passing_yards": "player_pass_yds",
              "player_receptions": "player_receptions", "player_rushing_attempts": "player_rush_attempts",
              "player_rushing_+_receiving_yards": "player_rush_reception_yds", "player_passing_+_rushing_yards": "player_pass_rush_yds"}
SKIP_NAMES = ("anytime td", "defense", "d/st", "special teams")
REQS = 0


def get(path, **params):
    global REQS
    key = (os.environ.get("SHARPAPI_KEY") or "").strip()      # secrets pasted with a trailing newline break the header
    if not key:
        raise RuntimeError("SHARPAPI_KEY not set")
    REQS += 1
    q = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(f"{BASE}/{path}" + (f"?{q}" if q else ""), headers={"X-API-Key": key, "User-Agent": "endzone-lab/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.load(r)
    time.sleep(5.2)                                     # 12 requests/min on the free tier
    return d


def fixtures(league="nfl", horizon_days=6):
    """Upcoming fixtures (not started, within the horizon), paging through the outright-heavy event list."""
    now = dt.datetime.now(dt.timezone.utc); out, cursor = [], None
    hi = (now + dt.timedelta(days=horizon_days)).isoformat()
    for _ in range(20):                                 # the list is start-time ordered and outright-heavy: page until past the horizon
        d = get("events", league=league, limit=100, cursor=cursor)
        rows = d.get("data", d) if isinstance(d, dict) else d
        out += [e for e in rows if e.get("event_type") == "fixture" and e.get("home_team") and e.get("away_team")
                and (e.get("start_time") or "") > now.isoformat() and (e.get("start_time") or "") <= hi]
        pg = d.get("pagination", {}) if isinstance(d, dict) else {}
        cursor = pg.get("next_cursor")
        starts = [e.get("start_time") or "" for e in rows if e.get("event_type") == "fixture"]
        if not cursor or not pg.get("has_more") or not rows or (starts and min(starts) > hi): break
    seen, uniq = set(), []
    for e in out:
        if e["id"] not in seen: seen.add(e["id"]); uniq.append(e)
    return uniq


def is_player(name):
    n = (name or "").strip().lower()
    return bool(n) and not any(w in n for w in SKIP_NAMES)


def convert(event, rows):
    """SharpAPI odds rows for one event -> our event dict."""
    ev = dict(id=f"sharp_{event['id']}", commence_time=event["start_time"], home_team=event["home_team"], away_team=event["away_team"],
              bookmakers=[], props={"bookmakers": []}, source="sharpapi")
    lines, props = {}, {}
    for r in rows:
        bk = str(r.get("sportsbook", "")).lower(); mt = r.get("market_type"); ts = r.get("timestamp")
        if r.get("is_live"): continue
        if mt in ("point_spread", "total_points") and r.get("is_main_line"):
            m = lines.setdefault(bk, {}).setdefault("spreads" if mt == "point_spread" else "totals", [])
            side = r.get("team_side") or r.get("selection")
            name = event["home_team"] if side in ("home", event["home_team"]) else event["away_team"] if side in ("away", event["away_team"]) else r.get("selection")
            m.append(dict(name=name, price=r.get("odds_american"), point=r.get("line"), source="sharpapi", ts=ts))
        key = MARKET_MAP.get(mt)
        if not key or not is_player(r.get("player_name")): continue
        if key in ("player_anytime_td", "player_1st_td"):
            oc = dict(name="Yes", description=r["player_name"].strip(), price=int(r["odds_american"]), source="sharpapi", ts=ts)
        else:
            if r.get("selection_type") not in ("over", "under") or r.get("line") is None: continue
            k2 = key if r.get("is_main_line") else key + "_alternate"
            oc = dict(name="Over" if r["selection_type"] == "over" else "Under", description=r["player_name"].strip(), price=int(r["odds_american"]),
                      point=float(r["line"]), main=bool(r.get("is_main_line")), source="sharpapi", ts=ts)
            key = k2
        props.setdefault(bk, {}).setdefault(key, []).append(oc)
    for bk, mk in lines.items():
        ev["bookmakers"].append(dict(key=bk, title=BOOK_TITLE.get(bk, bk), markets=[dict(key=k, outcomes=v) for k, v in mk.items()]))
    for bk, mk in props.items():
        ev["props"]["bookmakers"].append(dict(key=bk, title=BOOK_TITLE.get(bk, bk), markets=[dict(key=k, outcomes=v) for k, v in mk.items()]))
    return ev


def fetch_week():
    """All upcoming NFL fixtures with their DK/FD odds. Raises on failure (the caller falls back to The Odds API)."""
    global REQS
    REQS = 0
    fx = fixtures()
    events = {}
    for e in sorted(fx, key=lambda e: -(e.get("book_count") or 0)):     # duplicates of a game: keep the entry with the most books
        k = (e["away_team"], e["home_team"], (e.get("start_time") or "")[:10])
        if k in events: continue
        d = get(f"events/{e['id']}/odds")
        rows = d.get("data", d) if isinstance(d, dict) else d
        ev = convert(e, rows)
        if ev["props"]["bookmakers"] or k not in events: events[k] = ev
    return list(events.values())


if __name__ == "__main__":
    evs = fetch_week()
    print("fixtures:", len(evs), "| requests:", REQS)
    for ev in evs[:3]:
        print(ev["away_team"], "@", ev["home_team"], ev["commence_time"], "| line books:", [b["title"] for b in ev["bookmakers"]],
              "| prop books/markets:", {b["title"]: {m["key"]: len(m["outcomes"]) for m in b["markets"]} for b in ev["props"]["bookmakers"]})
