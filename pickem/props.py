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
