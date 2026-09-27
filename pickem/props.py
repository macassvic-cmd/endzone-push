"""Sportsbook player props from The Odds API -> per-player consensus fair lines.

Reuses the lab's odds.py client (same ODDS_API_KEY / ODDS_MOCK / ODDS_SAVE conventions).
Per-book two-way markets are devigged per book and the median fair P(over) across books is used;
one-way markets (anytime TD) are divided by the book's measured overround (odds.measure_hold).

CREDIT COST: 1 credit per market per event (one region). The default 10 markets over a
15-game slate is ~150 credits per run, +40% with PICKEM_ALTS=1.
"""
from __future__ import annotations

import os
from collections import defaultdict

import odds as lab_odds  # endzone-push/odds.py

# Odds API market -> our stat key
MARKETS = {
    "player_reception_yds": "rec_yds",
    "player_receptions": "receptions",
    "player_rush_yds": "rush_yds",
    "player_rush_attempts": "rush_att",
    "player_pass_yds": "pass_yds",
    "player_pass_attempts": "pass_att",
    "player_pass_completions": "pass_cmp",
    "player_pass_tds": "pass_tds",
    "player_pass_interceptions": "ints",
    "player_anytime_td": "anytime_td",
}
ALT_MARKETS = {
    "player_reception_yds_alternate": "rec_yds",
    "player_receptions_alternate": "receptions",
    "player_rush_yds_alternate": "rush_yds",
    "player_pass_yds_alternate": "pass_yds",
}


def market_map():
    m = dict(MARKETS)
    if os.environ.get("PICKEM_ALTS") == "1":
        m.update(ALT_MARKETS)
    return m


def fetch_events(api_key=None, fetch=None):
    return lab_odds.fetch_all(api_key=api_key, fetch=fetch, markets=list(market_map()))


def anytime_td_power(events, anchor=None):
    """Fair anytime-TD probabilities with the power method, per game and book.

    Dividing every Yes price by one overround (multiplicative) over-shades favourites: books load
    most of the margin onto longshots. Instead solve sum_i implied_i ** k = expected distinct TD
    scorers per game (the lab's ANCHOR, 4.10) for each book's full game market, then p_i = implied_i ** k.
    Returns {norm_player: median fair P(>=1 TD) across books}."""
    from scipy import optimize
    anchor = anchor or lab_odds.ANCHOR["player_anytime_td"]
    per_player = defaultdict(list)
    ks = []
    for ev in events or []:
        for b in ev.get("props", {}).get("bookmakers", []):
            for m in b["markets"]:
                if m["key"] != "player_anytime_td":
                    continue
                imp = {}
                for o in m["outcomes"]:
                    if o["name"].lower() in ("yes", "over"):
                        imp[lab_odds.norm_name(o.get("description") or o["name"])] = lab_odds.implied(o["price"])
                if len(imp) < 12 or sum(imp.values()) <= anchor:      # partial market: can't anchor it
                    continue
                vals = list(imp.values())
                k = optimize.brentq(lambda k: sum(v ** k for v in vals) - anchor, 1.0, 5.0)
                ks.append(k)
                for pl, v in imp.items():
                    per_player[pl].append(v ** k)
    k_default = float(sorted(ks)[len(ks) // 2]) if ks else 1.25
    # players only priced in partial markets: use the typical exponent
    for ev in events or []:
        for b in ev.get("props", {}).get("bookmakers", []):
            for m in b["markets"]:
                if m["key"] != "player_anytime_td":
                    continue
                for o in m["outcomes"]:
                    pl = lab_odds.norm_name(o.get("description") or o["name"])
                    if o["name"].lower() in ("yes", "over") and pl not in per_player:
                        per_player.setdefault(pl + "#fallback", []).append(lab_odds.implied(o["price"]) ** k_default)
    out = {}
    for pl, v in per_player.items():
        name = pl.replace("#fallback", "")
        if name not in out or not pl.endswith("#fallback"):
            out[name] = float(sorted(v)[len(v) // 2])
    return out


def consensus(events) -> dict:
    """{norm_player: {stat: {"line": main_line, "fair_points": [(line, p_over)], "n_books": n}}}"""
    mm = market_map()
    board = lab_odds.prop_board(events)
    hold = lab_odds.measure_hold(events)
    # a partial board (few players posted) makes the anchored overround < 1, which would inflate
    # probabilities past 100% - fall back to the lab's measured default for that market
    for k, v in list(hold.items()):
        mk = k[0] if isinstance(k, tuple) else k
        if v < 1.0:
            hold[k] = lab_odds.DEFAULT_OVERROUND.get(mk, 1.05)
    per = defaultdict(lambda: defaultdict(dict))   # player -> stat -> point -> (p, n_books)
    for (market, player, point), books in board.items():
        stat = mm.get(market)
        if not stat:
            continue
        s = lab_odds.price_summary(board, market, player, point, hold)
        if not s:
            continue
        pt = 0.5 if stat == "anytime_td" else point
        if pt is None:
            continue
        prev = per[player][stat].get(pt)
        n = s["n_books"]
        if prev is None or n > prev[1]:
            per[player][stat][pt] = (s["market_p"], n)
    td = anytime_td_power(events)
    for player, p in td.items():
        per[player]["anytime_td"] = {0.5: (p, 2)}
    out = {}
    for player, stats in per.items():
        out[player] = {}
        for stat, pts in stats.items():
            main = max(pts.items(), key=lambda kv: kv[1][1])[0]
            out[player][stat] = {"line": float(main),
                                 "fair_points": [(float(l), p) for l, (p, _) in pts.items()],
                                 "n_books": pts[main][1]}
    return out


def game_lines(events):
    return lab_odds.game_lines(events)
