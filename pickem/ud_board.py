"""Underdog pick'em board -> normalized NFL legs.

Same public endpoint pirate-bets' underdog_fetcher and mlb-fantasy's market_lines use:
    GET https://api.underdogfantasy.com/v1/over_under_lines
(flat lists over_under_lines / appearances / players / games joined by ids).

Each option carries Underdog's own per-pick price (`american_price`) and `payout_multiplier`
(the legacy per-pick payout adjustment, 1.0 on a standard pick).
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

UD_URL = os.environ.get("UNDERDOG_BOARD_URL", "https://api.underdogfantasy.com/v1/over_under_lines")

# Underdog display_stat -> our stat key. Anything else on the board is skipped (we can't price it).
UD_STATS = {
    "Fantasy Points": "fantasy",
    "Receiving Yards": "rec_yds",
    "Receptions": "receptions",
    "Rush Yards": "rush_yds",
    "Rush Attempts": "rush_att",
    "Pass Yards": "pass_yds",
    "Pass Attempts": "pass_att",
    "Completions": "pass_cmp",
    "Pass TDs": "pass_tds",
    "INTs Thrown": "ints",
    "Rush + Rec TDs": "anytime_td",
}


def fetch(url=UD_URL):
    if os.environ.get("UD_MOCK"):
        return json.load(open(os.environ["UD_MOCK"]))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (endzone-lab pickem scan)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def _float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def nfl_legs(raw: dict) -> list[dict]:
    """One dict per (line, side) that we know how to price."""
    games = {g["id"]: g for g in raw.get("games", []) if g.get("sport_id") == "NFL"}
    team_abbr = {}
    for g in games.values():
        t = g.get("abbreviated_title") or g.get("title") or ""
        m = re.match(r"\s*([A-Z]{2,3})\s*@\s*([A-Z]{2,3})", t)
        if m:
            team_abbr[g["away_team_id"]], team_abbr[g["home_team_id"]] = m.group(1), m.group(2)
    apps = {a["id"]: a for a in raw.get("appearances", []) if a.get("match_id") in games}
    players = {p["id"]: p for p in raw.get("players", [])}

    out = []
    for line in raw.get("over_under_lines", []):
        if line.get("status") not in (None, "active"):
            continue
        ou = line.get("over_under") or {}
        st = (ou.get("appearance_stat") or {})
        stat = UD_STATS.get(st.get("display_stat"))
        app = apps.get(st.get("appearance_id"))
        if not stat or not app:
            continue
        pl = players.get(app.get("player_id")) or {}
        g = games[app["match_id"]]
        value = _float(line.get("stat_value"))
        if value is None:
            continue
        name = f"{pl.get('first_name', '')} {pl.get('last_name', '')}".strip()
        for opt in line.get("options") or []:
            if opt.get("status") not in (None, "active"):
                continue
            side = {"higher": "over", "lower": "under"}.get(opt.get("choice"))
            if not side:
                continue
            out.append({
                "line_id": line.get("id"),
                "player": name,
                "pos": pl.get("position_name") or "WR",
                "team": team_abbr.get(app.get("team_id"), "?"),
                "game": g.get("abbreviated_title") or g.get("title"),
                "game_id": g["id"],
                "kickoff": g.get("scheduled_at"),
                "game_status": g.get("status"),
                "stat": stat,
                "ud_stat": st.get("display_stat"),
                "line": value,
                "side": side,
                "ud_price": _float(str(opt.get("american_price") or "").replace("+", "")),
                "ud_mult": _float(opt.get("payout_multiplier")) or 1.0,
            })
    return out
