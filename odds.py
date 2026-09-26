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


def price_summary(board, market, player, point=None):
    """Best available Yes/Over price across books, and no-vig consensus probability."""
    books = board.get((market, norm_name(player), point))
    if not books:
        return None
    best_book, best = max(((bk, v["yes"]) for bk, v in books.items() if "yes" in v), key=lambda x: decimal(x[1]),
                          default=(None, None))
    if best is None:
        return None
    fair = []
    for v in books.values():
        if "yes" in v and "no" in v:
            py, pn = implied(v["yes"]), implied(v["no"])
            fair.append(py / (py + pn))
    # one-sided markets (most anytime/first TD boards): strip a typical margin
    if not fair:
        hold = 0.07 if market == "player_anytime_td" else 0.20 if market == "player_first_td" else 0.05
        fair = [implied(v["yes"]) / (1 + hold) for v in books.values() if "yes" in v]
    return dict(best=int(best), book=best_book, n_books=len(books), market_p=float(np.median(fair)))
