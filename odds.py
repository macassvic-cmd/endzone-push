"""The Odds API client: live game lines + TD props for every book, with best price and no-vig consensus.

Cost per run (us region): 2 credits for lines + 3 per game for props (~47 for a 15-game slate).
Set ODDS_API_KEY in the environment. Without it everything falls back to nflverse lines and no book prices.
"""
import json, os, re, time, unicodedata, urllib.request, urllib.parse
from collections import defaultdict
import numpy as np

BASE = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl"
PROP_MARKETS = ["player_anytime_td", "player_1st_td", "player_pass_tds"]
# The Odds API calls first-TD "player_1st_td"; the rest of the code uses "player_first_td".
MARKET_ALIAS = {"player_1st_td": "player_first_td"}
# What a book's Yes prices for one game should sum to with no margin (2024-25 regular season play-by-play):
# anytime TD -> mean distinct offensive (run/pass) TD scorers per game; first TD -> share of games whose first
# TD is scored by an offensive player. Dividing the observed sum by this gives the book's real overround.
ANCHOR = {"player_anytime_td": 4.10, "player_first_td": 0.945}
DEFAULT_OVERROUND = {"player_anytime_td": 1.22, "player_first_td": 1.43}   # measured 2026 wk3, used if a book has too few games
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


def _get(url, params, fetch=None):
    if fetch:                       # injectable for tests
        return fetch(url, params)
    q = urllib.parse.urlencode(params)
    with urllib.request.urlopen(f"{url}?{q}", timeout=30) as r:
        left = r.headers.get("x-requests-remaining")
        if left: print("odds api credits remaining:", left)
        return json.load(r)


def fetch_all(api_key=None, fetch=None, markets=PROP_MARKETS):
    if os.environ.get("ODDS_MOCK"):                     # offline test fixture
        return json.load(open(os.environ["ODDS_MOCK"]))
    api_key = api_key or os.environ.get("ODDS_API_KEY")
    if not api_key and not fetch:
        return None
    base = dict(apiKey=api_key or "x", regions="us", oddsFormat="american")
    events = _get(f"{BASE}/odds", {**base, "markets": "spreads,totals"}, fetch)
    # only this week's games: props cost credits per event
    import datetime as dt
    cutoff = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
    events = [e for e in events if e["commence_time"] <= cutoff]
    for ev in events:
        try:
            ev["props"] = _get(f"{BASE}/events/{ev['id']}/odds", {**base, "markets": ",".join(markets)}, fetch)
        except Exception as e:
            print("props fail", ev.get("id"), e); ev["props"] = {"bookmakers": []}
        time.sleep(0.2)
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


def prop_board(events):
    """Returns {(market, norm_player, point): {book: {"yes": price, "no": price}}}"""
    board = defaultdict(lambda: defaultdict(dict))
    for ev in events or []:
        for b in ev.get("props", {}).get("bookmakers", []):
            for m in b["markets"]:
                for o in m["outcomes"]:
                    player = o.get("description") or o.get("name")
                    side = o["name"].lower()
                    side = "yes" if side in ("yes", "over") else "no" if side in ("no", "under") else "yes"
                    key = (MARKET_ALIAS.get(m["key"], m["key"]), norm_name(player), o.get("point"))
                    board[key][b["title"]][side] = o["price"]
    return board


def measure_hold(events, min_games=3):
    """Measured overround per (market, book): median over games of sum(implied Yes) / ANCHOR[market].
    Falls back to the pooled market figure, then DEFAULT_OVERROUND, when a book prices too few games."""
    sums = defaultdict(list)
    for ev in events or []:
        for b in ev.get("props", {}).get("bookmakers", []):
            for m in b["markets"]:
                mk = MARKET_ALIAS.get(m["key"], m["key"])
                if mk not in ANCHOR:
                    continue
                s = sum(implied(o["price"]) for o in m["outcomes"] if o["name"].lower() in ("yes", "over"))
                if s > 0:
                    sums[(mk, b["title"])].append(s / ANCHOR[mk])
    out = dict(DEFAULT_OVERROUND)
    for mk in ANCHOR:
        pooled = [x for (m, _), v in sums.items() if m == mk for x in v]
        if pooled:
            out[mk] = float(np.median(pooled))
    for (mk, bk), v in sums.items():
        out[(mk, bk)] = float(np.median(v)) if len(v) >= min_games else out[mk]
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
    return dict(best=int(best), book=best_book, median=med, n_books=len(yes), market_p=float(np.median(fair)))
