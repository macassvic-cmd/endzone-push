"""Offline end-to-end test on the 2026 wk3 LAC@BUF prices (python -m pytest pickem)."""
import json, os
import pytest

BOOKS = ["FanDuel", "DraftKings"]
PROPS = {  # player: {odds-api market: [(point, over, under)]}
    "Ladd McConkey": {"player_reception_yds": [(58.5, -113, -113)], "player_receptions": [(4.5, -140, 106)],
                      "player_anytime_td": [(None, 210, None)]},
    "Dalton Kincaid": {"player_reception_yds": [(56.5, -113, -113)], "player_receptions": [(4.5, 106, -140)],
                       "player_anytime_td": [(None, 185, None)]},
    "Oronde Gadsden": {"player_reception_yds": [(35.5, -113, -113)], "player_receptions": [(3.5, 112, -148)],
                       "player_anytime_td": [(None, 250, None)]},
    "Khalil Shakir": {"player_reception_yds": [(43.5, -113, -113), (39.5, -146, 115), (49.5, 122, -155)],
                      "player_receptions": [(3.5, -172, 128)], "player_anytime_td": [(None, 200, None)]},
    "Justin Herbert": {"player_pass_attempts": [(31.5, -110, -110)]},
    "Josh Allen": {"player_pass_attempts": [(30.5, -107, -113)], "player_pass_yds": [(238.5, -115, -115)]},
}
UD = [  # player, pos, team, display_stat, line, (higher mult, price), (lower mult, price)
    ("Ladd McConkey", "WR", "LAC", "Fantasy Points", 9.05, (1.0, "-112"), (1.0, "-112")),
    ("Ladd McConkey", "WR", "LAC", "Receiving Yards", 59.5, (1.0, "-112"), (1.0, "-112")),
    ("Dalton Kincaid", "TE", "BUF", "Fantasy Points", 8.95, (1.0, "-112"), (1.0, "-112")),
    ("Oronde Gadsden", "TE", "LAC", "Fantasy Points", 5.95, (1.0, "-112"), (1.0, "-112")),
    ("Khalil Shakir", "WR", "BUF", "Fantasy Points", 7.35, (1.0, "-112"), (1.0, "-112")),
    ("Justin Herbert", "QB", "LAC", "Pass Attempts", 31.5, (1.0, "-106"), (1.0, "-117")),
    ("Josh Allen", "QB", "BUF", "Pass Attempts", 30.5, (1.07, "+108"), (0.89, "-132")),
]


def ud_raw():
    teams = {"LAC": "t-lac", "BUF": "t-buf"}
    raw = {"games": [{"id": 1, "sport_id": "NFL", "abbreviated_title": "LAC @ BUF", "away_team_id": "t-lac",
                      "home_team_id": "t-buf", "scheduled_at": "2099-09-27T17:00:00Z", "status": "scheduled"}],
           "appearances": [], "players": [], "over_under_lines": []}
    for i, (name, pos, team, stat, line, hi, lo) in enumerate(UD):
        pid, aid = f"p{name}", f"a{name}"
        if not any(p["id"] == pid for p in raw["players"]):
            fn, ln = name.split(" ", 1)
            raw["players"].append({"id": pid, "first_name": fn, "last_name": ln, "position_name": pos})
            raw["appearances"].append({"id": aid, "player_id": pid, "match_id": 1, "team_id": teams[team]})
        raw["over_under_lines"].append({"id": f"l{i}", "status": "active", "stat_value": str(line),
            "over_under": {"appearance_stat": {"appearance_id": aid, "display_stat": stat}},
            "options": [{"choice": "higher", "status": "active", "payout_multiplier": str(hi[0]), "american_price": hi[1]},
                        {"choice": "lower", "status": "active", "payout_multiplier": str(lo[0]), "american_price": lo[1]}]})
    return raw


def odds_events():
    bms = []
    for b in BOOKS:
        mk = {}
        for player, markets in PROPS.items():
            for m, rows in markets.items():
                for point, o, u in rows:
                    outs = mk.setdefault(m, [])
                    if m == "player_anytime_td":
                        outs.append({"name": "Yes", "description": player, "price": o})
                    else:
                        outs.append({"name": "Over", "description": player, "price": o, "point": point})
                        outs.append({"name": "Under", "description": player, "price": u, "point": point})
        bms.append({"title": b, "markets": [{"key": k, "outcomes": v} for k, v in mk.items()]})
    return [{"id": "e1", "home_team": "Buffalo Bills", "away_team": "Los Angeles Chargers",
             "commence_time": "2099-09-27T17:00:00Z", "bookmakers": [], "props": {"bookmakers": bms}}]


@pytest.fixture
def mocks(tmp_path, monkeypatch):
    (tmp_path / "ud.json").write_text(json.dumps(ud_raw()))
    (tmp_path / "odds.json").write_text(json.dumps(odds_events()))
    monkeypatch.setenv("UD_MOCK", str(tmp_path / "ud.json"))
    monkeypatch.setenv("ODDS_MOCK", str(tmp_path / "odds.json"))
    monkeypatch.setenv("PICKEM_OUT", str(tmp_path / "out.json"))
    return tmp_path


def test_scan_end_to_end(mocks):
    from pickem import scan
    res = scan.run()
    legs = {(r["player"], r["ud_stat"], r["side"]): r for r in res["legs"]}
    mc = legs[("Ladd McConkey", "Fantasy Points", "over")]
    assert 0.53 < mc["p_hit"] < 0.57                     # matches the hand-built model (55.1%)
    assert mc["edge_vs_ud"] > 0
    # Allen 30.5 attempts over at +108 vs a ~50% fair price is a real edge
    assert legs[("Josh Allen", "Pass Attempts", "over")]["edge_vs_ud"] > 0.02
    assert res["entries"]["6"]  and res["entries"]["2"]
    six = res["entries"]["6"][0]
    assert abs(six["payout"] - 31.45) < 0.05             # 29.39 x 1.07 reproduces the app's slip
    assert six["p_all"] > six["p_all_independent"]
    assert json.loads((mocks / "out.json").read_text())["counts"]["legs_priced"] >= 12


def test_qb_with_imputed_ints_and_ud_int_line(mocks, monkeypatch):
    """A QB with pass props but no INT market still prices fantasy; an Underdog INT line is skipped, not a crash."""
    import json
    raw = json.loads((mocks / "ud.json").read_text())
    raw["over_under_lines"].append({"id": "lint", "status": "active", "stat_value": "0.5",
        "over_under": {"appearance_stat": {"appearance_id": "aJosh Allen", "display_stat": "INTs Thrown"}},
        "options": [{"choice": "higher", "status": "active", "payout_multiplier": "1", "american_price": "-112"}]})
    (mocks / "ud.json").write_text(json.dumps(raw))
    from pickem import scan
    res = scan.run()
    assert not any(r["ud_stat"] == "INTs Thrown" for r in res["legs"])
