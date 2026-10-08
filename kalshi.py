"""Kalshi as a price source: player anytime-TD (series KXNFLTD, markets "Player: 1+") and first-TD (KXNFLFIRSTTD).

Public market-data API, no auth: GET https://api.elections.kalshi.com/trade-api/v2/events?series_ticker=...&status=open
&with_nested_markets=true. Prices are yes/no bid/ask in dollars (0.56 = 56% implied, no vig on a single price). Kalshi's
fee is charged on the trade, not on winnings: taker fee = 0.07 * C * P * (1 - P) per contract, rounded up to the cent
(fee schedule, July 2026), maker fee = 0.0175 * C * P * (1 - P). We price as a taker at the yes ask and put the
fee-adjusted price on the board so EV is net of fees; the raw quotes are kept in odds_history.
"""
import json, math, re, time, urllib.request, urllib.parse
from odds import TEAM_ABBR, norm_name

BASE = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = {"player_anytime_td": "KXNFLTD", "player_1st_td": "KXNFLFIRSTTD", "player_tds_over": "KXNFLTD"}   # "Player: 1+" and "Player: 2+" share a series
TAKER_FEE, MAKER_FEE = 0.07, 0.0175
BOOK = "Kalshi"
MIN_DOLLARS_AT_ASK = 100.0      # Kalshi counts as a real price (best/median/EV) only with >= $100 available at the ask; else reference only
MIN_ASK, MAX_SPREAD, MIN_VOLUME = 0.04, 0.02, 100.0   # and only when the ask is at least 4c (a 1c tick is half the price at 2c), the bid sits within 2c of it,
                                                      # and the market has traded (24h volume, or 100+ contracts all-time): a resting 2c/1c quote with $489 at
                                                      # the ask and no trades for a day (week 4, Hutchinson 2+ TD at +4893) is a market-maker placeholder
# Kalshi event tickers end in <AWAY><HOME> with its own 3-letter codes; map the ones that differ from nflverse
KALSHI_ABBR = {"LAR": "LA", "JAC": "JAX", "WSH": "WAS", "ARZ": "ARI", "BLT": "BAL", "CLV": "CLE", "HST": "HOU", "SL": "LA"}
CODES = sorted({v for v in TEAM_ABBR.values()} | set(KALSHI_ABBR), key=len, reverse=True)


def _get(path, params):
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{BASE}{path}?{q}", headers={"User-Agent": "endzone-lab/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def fetch_events(series, status="open"):
    """All events of a series with nested markets (paginated)."""
    out, cursor = [], None
    for _ in range(20):
        params = dict(series_ticker=series, status=status, with_nested_markets="true", limit=200)
        if cursor: params["cursor"] = cursor
        d = _get("/events", params)
        out += d.get("events", [])
        cursor = d.get("cursor")
        if not cursor: break
        time.sleep(0.2)
    return out


def taker_fee(p, c=1):
    """Fee in dollars for c contracts at price p (dollars), rounded up to the cent."""
    return math.ceil(TAKER_FEE * c * p * (1 - p) * 100) / 100


def fee_adjusted_american(p):
    """American price whose payout equals Kalshi's net payout after the taker fee: stake p, win 1 - p - fee."""
    fee = TAKER_FEE * p * (1 - p)                       # per contract, before the per-order cent rounding
    net = (1 - p - fee) / p                             # profit per dollar staked
    dec = 1 + net
    return int(round((dec - 1) * 100)) if dec >= 2 else int(round(-100 / (dec - 1)))


def split_ticker(event_ticker):
    """KXNFLTD-26SEP27CARCLE -> ('CAR', 'CLE') in nflverse codes, or None."""
    m = re.search(r"-\d{2}[A-Z]{3}\d{2}([A-Z]+)$", event_ticker)
    if not m: return None
    tail = m.group(1)
    for a in CODES:
        if tail.startswith(a) and tail[len(a):] in CODES:
            f = lambda x: KALSHI_ABBR.get(x, x)
            return f(a), f(tail[len(a):])
    return None


def player_from(market, series_key):
    sub = market.get("yes_sub_title") or market.get("title") or ""
    if series_key in ("player_anytime_td", "player_tds_over"):
        m = re.match(r"^(.*?):\s*([12])\+$", sub)
        if not m or m.group(2) != ("1" if series_key == "player_anytime_td" else "2"): return None
        return m.group(1).strip()
    if "D/ST" in sub or sub.lower().startswith("no touchdown"): return None
    return sub.strip() or None


def fetch_board():
    """{(away, home): {market_key: [dict(player, yes_ask, yes_bid, last, volume, ticker, close)]}} plus raw events."""
    board, raw = {}, {}
    fetched = {}
    for key, series in SERIES.items():
        evs = fetched.get(series) or fetch_events(series)
        fetched[series] = evs; raw[series] = evs
        for e in evs:
            g = split_ticker(e["event_ticker"])
            if not g: continue
            rows = []
            for m in e.get("markets", []):
                player = player_from(m, key)
                ask = m.get("yes_ask_dollars"); bid = m.get("yes_bid_dollars"); last = m.get("last_price_dollars")
                if not player or ask is None: continue
                ask, bid = float(ask), float(bid) if bid is not None else None
                if ask <= 0 or ask >= 1: continue
                size = float(m.get("yes_ask_size_fp") or 0)          # contracts available at the ask
                rows.append(dict(player=player, yes_ask=ask, yes_bid=bid, last=float(last) if last is not None else None,
                                 volume=float(m.get("volume_fp") or 0), volume_24h=float(m.get("volume_24h_fp") or 0),
                                 ask_size=size, dollars_at_ask=round(size * ask, 2), ticker=m["ticker"], close=m.get("close_time")))
            board.setdefault(g, {})[key] = rows
    return board, raw


def is_liquid(r):
    """A Kalshi quote that can stand in for a book price: enough dollars at the ask, a real (not placeholder) price level,
       a tight bid-ask, and some trading."""
    ask, bid = r.get("yes_ask"), r.get("yes_bid")
    if ask is None or r["dollars_at_ask"] < MIN_DOLLARS_AT_ASK or ask < MIN_ASK: return False
    if bid is None or bid <= 0 or ask - bid > MAX_SPREAD + 1e-9: return False
    return (r.get("volume_24h") or 0) > 0 or (r.get("volume") or 0) >= MIN_VOLUME


def attach(events, board=None, min_volume=0.0):
    """Add Kalshi as a bookmaker on each Odds API event's props (same shape as the Odds API books).
       Price = fee-adjusted American price of the yes ask; description = player name. Outcomes with less than
       MIN_DOLLARS_AT_ASK available at the ask carry reference=True: shown on the board, excluded from best/median/EV.
       Returns (attached, liquid) counts."""
    if board is None:
        board, _ = fetch_board()
    n = liquid = 0
    for ev in events or []:
        g = (TEAM_ABBR.get(ev["away_team"]), TEAM_ABBR.get(ev["home_team"]))
        k = board.get(g)
        if not k: continue
        markets = []
        for key, rows in k.items():
            oc = [dict(name="Over" if key == "player_tds_over" else "Yes", description=r["player"], price=fee_adjusted_american(r["yes_ask"]),
                       **({"point": 1.5} if key == "player_tds_over" else {}),
                       reference=not is_liquid(r),
                       kalshi_yes_ask=r["yes_ask"], kalshi_yes_bid=r["yes_bid"], kalshi_ask_size=r["ask_size"],
                       kalshi_dollars_at_ask=r["dollars_at_ask"], kalshi_volume=r["volume"], kalshi_volume_24h=r["volume_24h"],
                       kalshi_ticker=r["ticker"])
                  for r in rows if r["volume"] >= min_volume]
            if oc: markets.append(dict(key=key, outcomes=oc))
        if markets:
            ev.setdefault("props", {}).setdefault("bookmakers", []).append(dict(key="kalshi", title=BOOK, markets=markets))
            n += sum(len(m["outcomes"]) for m in markets); liquid += sum(1 for m in markets for o in m["outcomes"] if not o["reference"])
    return n, liquid


if __name__ == "__main__":
    board, raw = fetch_board()
    games = sorted(board)
    print("games:", len(games), games[:6])
    for g in games[:2]:
        for key, rows in board[g].items():
            print(g, key, len(rows), [(r["player"], r["yes_ask"], fee_adjusted_american(r["yes_ask"])) for r in rows[:4]])
