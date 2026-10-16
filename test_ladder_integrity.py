"""Yard-ladder integrity: listed-only alt rungs, ladder verification, monotonicity, kickoff and staleness rules.

    python test_ladder_integrity.py        # CI runs it in the "Tests and data" step

Background (2026-10-15, week 6): the Yards tab showed "Emanuel Wilson Over 19.5 rush yds, BEST -135, BOOK DraftKings" while
DraftKings listed nothing below 30+. The feed (SharpAPI) had delivered a DraftKings "alternate rushing yards" ladder for
Wilson (4.5 -1800 ... 54.5 +2000) that contradicted DraftKings' own two-sided 66.5 at -110: a different market under the
yards key. The old per-row sanity filter dropped only the rows that violated the main line and kept 4.5-19.5, the pooled
curve then averaged the phantom 19.5 with FanDuel's 39.5 and 49.5 (67.7%), and the rung went on the board.
Rules under test (yard_prices.verify_ladders, rules.not_actionable, yard_prices.monotone_violations):
  1. a book's one-sided ladder counts only when every rung is consistent with that book's own two-sided line and never
     rises with the line; one violation rejects the whole ladder (research-only), two-sided lines stay;
  2. a ladder with no two-sided line at the book cannot be verified and is rejected;
  3. P(over) never rises with the line on any curve (helper used by run_week's build gate);
  4. no actionable edge inside the kickoff cutoff or once underway; staleness enforced once MAX_PRICE_AGE_MIN is set;
  5. regression on the saved week-6 pull: no DraftKings one-sided rung for Wilson survives, FanDuel's 29.5 -1450 does,
     every verified ladder and every pooled curve is monotone.
"""
import json, os, sys
import pandas as pd
import yard_prices as YP, rules as RU
from odds import norm_name

W6 = "odds_history/2026_w6_20261015T2209Z.json"


def ev(home, book, main_key, rows):
    """One event with one book; rows = [(key, player, name, point, price)]."""
    mk = {}
    for key, player, name, point, price in rows:
        mk.setdefault(key, []).append(dict(name=name, description=player, point=point, price=price, main=key == main_key, ts="2026-10-15T22:07:41Z"))
    return dict(home_team=home, away_team="X", commence_time="2026-10-16T00:15:00Z", bookmakers=[],
                props=dict(bookmakers=[dict(key=book.lower(), title=book, markets=[dict(key=k, outcomes=v) for k, v in mk.items()])]))


def wilson_events():
    dk = [("player_rush_yds", "Emanuel Wilson", "Over", 66.5, -110), ("player_rush_yds", "Emanuel Wilson", "Under", 66.5, -114)]
    dk += [("player_rush_yds_alternate", "Emanuel Wilson", "Over", pt, pr) for pt, pr in
           [(4.5, -1800), (9.5, -575), (14.5, -260), (19.5, -135), (24.5, 125), (29.5, 210), (34.5, 340), (39.5, 550), (44.5, 850), (49.5, 1300), (54.5, 2000)]]
    fd = [("player_rush_yds", "Emanuel Wilson", "Over", 66.5, -113), ("player_rush_yds", "Emanuel Wilson", "Under", 66.5, -113)]
    fd += [("player_rush_yds_alternate", "Emanuel Wilson", "Over", pt, pr) for pt, pr in
           [(29.5, -1450), (39.5, -550), (49.5, -280), (59.5, -162), (69.5, 100), (79.5, 154), (89.5, 225), (99.5, 320), (109.5, 450), (124.5, 750), (149.5, 1400)]]
    return [ev("Denver Broncos", "DraftKings", "player_rush_yds", dk), ev("Denver Broncos", "FanDuel", "player_rush_yds", fd)]


def test_phantom_ladder_rejected():
    rej = {}
    board, hold = YP.ladder_board(wilson_events(), rejected=rej)
    assert YP.offered_at(board, "rush", "Emanuel Wilson", 19.5) == [], "the phantom DraftKings 19.5 must not be listed"
    for pt in (4.5, 9.5, 14.5, 24.5, 54.5):
        assert YP.offered_at(board, "rush", "Emanuel Wilson", pt) == [], f"DraftKings {pt} must go with the rest of its ladder"
    assert YP.offered_at(board, "rush", "Emanuel Wilson", 29.5) == [("FanDuel", -1450)], "FanDuel's consistent ladder stays"
    assert sorted(bk for bk, _ in YP.offered_at(board, "rush", "Emanuel Wilson", 66.5)) == ["DraftKings", "FanDuel"], "two-sided lines always stay"
    key = ("rush", norm_name("Emanuel Wilson"), "DraftKings")
    assert key in rej and "contradicts" in rej[key]["reason"] and len(rej[key]["rungs"]) == 11, rej.get(key)
    assert ("rush", norm_name("Emanuel Wilson"), "FanDuel") not in rej
    curve = YP.market_curve(board, hold, "rush", "Emanuel Wilson")
    assert curve["lo"] == 29.5 and curve["books"] == ["DraftKings", "FanDuel"], curve
    assert YP.curve_at(curve, 19.5) is None, "no extrapolation below the lowest verified rung"
    assert YP.curve_at(curve, 29.5) > 0.85, "the market at 29.5 is FanDuel's -1450, not a pooled average with a phantom"
    assert not YP.monotone_violations(curve["lines"], curve["probs"])


def test_consistent_ladder_kept_and_no_anchor_rejected():
    fd = [("player_rush_yds", "RJ Harvey", "Over", 14.5, -113), ("player_rush_yds", "RJ Harvey", "Under", 14.5, -113)]
    fd += [("player_rush_yds_alternate", "RJ Harvey", "Over", pt, pr) for pt, pr in [(4.5, -650), (9.5, -235), (19.5, 152), (24.5, 235), (29.5, 350), (39.5, 640), (49.5, 1100), (59.5, 1500)]]
    rej = {}
    board, hold = YP.ladder_board([ev("Denver Broncos", "FanDuel", "player_rush_yds", fd)], rejected=rej)
    assert YP.offered_at(board, "rush", "RJ Harvey", 24.5) == [("FanDuel", 235)] and not rej
    assert YP.book_ranges(board, "rush", "RJ Harvey") == {"FanDuel": (4.5, 59.5)}
    assert YP.captured_at(board, "rush", "RJ Harvey", 24.5, "FanDuel") == "2026-10-15T22:07:41Z"
    # the same ladder without a two-sided line cannot be verified
    rej = {}
    board, _ = YP.ladder_board([ev("Denver Broncos", "FanDuel", "player_rush_yds", fd[2:])], rejected=rej)
    assert YP.offered_at(board, "rush", "RJ Harvey", 24.5) == [] and "no two-sided line" in rej[("rush", norm_name("RJ Harvey"), "FanDuel")]["reason"]
    # a ladder that rises with the line is rejected even when every rung sits on the right side of the main
    bad = fd[:2] + [("player_rush_yds_alternate", "RJ Harvey", "Over", 19.5, 300), ("player_rush_yds_alternate", "RJ Harvey", "Over", 24.5, 110)]
    rej = {}
    board, _ = YP.ladder_board([ev("Denver Broncos", "FanDuel", "player_rush_yds", bad)], rejected=rej)
    assert YP.offered_at(board, "rush", "RJ Harvey", 24.5) == [] and "rises" in rej[("rush", norm_name("RJ Harvey"), "FanDuel")]["reason"]


def test_monotone_helper():
    assert YP.monotone_violations([10, 20, 30], [0.9, 0.6, 0.6]) == []
    v = YP.monotone_violations([10, 20, 30], [0.9, 0.6, 0.65])
    assert v == [(30, 0.65, 20, 0.6)], v
    assert YP.monotone_violations([10, 20], [0.60, 0.61], tol=0.02) == []


def test_timing_rules():
    now = pd.Timestamp("2026-10-15T22:09:00Z")
    e = dict(kick_utc="2026-10-16T00:15Z", captured="2026-10-15T22:07:41Z")
    assert RU.not_actionable(dict(e), now) == [] and round(RU.minutes_to_kick(e["kick_utc"], now)) == 126
    NOAGE = 10**9                                                              # kickoff rule alone (the age limit is tested below)
    assert RU.not_actionable(dict(e), pd.Timestamp("2026-10-15T23:50:00Z"), max_age=NOAGE) == ["kickoff in 25 min (under 30)"]
    assert RU.not_actionable(dict(e), pd.Timestamp("2026-10-16T00:15:00Z"), max_age=NOAGE) == ["game underway"]
    assert RU.not_actionable(dict(e), pd.Timestamp("2026-10-16T01:00:00Z"), max_age=NOAGE) == ["game underway"]
    assert RU.MAX_PRICE_AGE_MIN == 15                                          # set 2026-10-16; the module default applies when max_age is None
    assert RU.not_actionable(dict(e), pd.Timestamp("2026-10-15T23:50:00Z")) == ["kickoff in 25 min (under 30)", "price captured 102 min ago (over 15)"]
    assert RU.not_actionable(dict(e, kick_utc=None), now) == ["kickoff unknown"]
    assert RU.not_actionable(dict(e), now, max_age=1) == ["price captured 1 min ago (over 1)"]
    assert RU.not_actionable(dict(e), now, max_age=5) == []
    assert RU.not_actionable(dict(e, captured=None), now, max_age=5) == ["price age unknown"]
    x = dict(e); RU.not_actionable(x, now); assert x["age_min"] == 1.3 and x["kick_min"] == 126.0


def test_week6_pull_regression():
    if not os.path.exists(W6):
        print("  (week-6 pull not present, regression skipped)"); return
    rej = {}
    board, hold = YP.ladder_board(json.load(open(W6)), rejected=rej)
    w = norm_name("Emanuel Wilson")
    dk_alts = [k for k, books in board.items() if k[0] == "rush" and k[1] == w and "DraftKings" in books and "under" not in books["DraftKings"]]
    assert dk_alts == [], f"DraftKings one-sided rungs for Wilson survived: {dk_alts}"
    assert YP.offered_at(board, "rush", "Emanuel Wilson", 19.5) == []
    assert YP.offered_at(board, "rush", "Emanuel Wilson", 29.5) == [("FanDuel", -1450)]
    assert ("rush", w, "DraftKings") in rej
    # every DraftKings one-sided yardage ladder in this pull was a different market: all rejected, none of FanDuel's
    books_rej = {bk for _, _, bk in rej}
    assert "DraftKings" in books_rej and "FanDuel" not in books_rej, books_rej
    # verified ladders and pooled curves are monotone for every player
    lad = {}
    for (k, n, line), books in board.items():
        for bk, c in books.items():
            if "over" not in c: continue
            p = YP.implied(c["over"]) / (YP.implied(c["over"]) + YP.implied(c["under"])) if "under" in c else YP.implied(c["over"]) / hold.get((k, bk), 1.06)
            lad.setdefault((k, n, bk), []).append((line, p))
    bad = {k: YP.monotone_violations([x for x, _ in v], [p for _, p in v], tol=YP.STEP_TOL) for k, v in lad.items()}
    bad = {k: v for k, v in bad.items() if v}
    assert not bad, f"verified ladders that rise with the line: {list(bad.items())[:5]}"
    players = {(k, n) for k, n, _ in lad}
    for k, n in players:
        c = YP.market_curve(board, hold, k, n)
        if c: assert not YP.monotone_violations(c["lines"], c["probs"]), (k, n, c["lines"], c["probs"])
    print(f"  week-6 pull: {len(rej)} ladders rejected ({', '.join(sorted(books_rej))}), {len(players)} player curves monotone")


def test_kalshi_quotes_carry_capture_time():
    """Kalshi quotes have no timestamp of their own; attach() stamps the fetch time so the price-age rule can judge them
       (without it every Kalshi-best edge is held for 'price age unknown' once MAX_PRICE_AGE_MIN is set)."""
    import kalshi as K
    ev = dict(away_team="Seattle Seahawks", home_team="Denver Broncos", props=dict(bookmakers=[]))
    row = dict(player="Tory Horton", yes_ask=0.10, yes_bid=0.09, ask_size=2000, dollars_at_ask=200.0, volume=5000, volume_24h=500, ticker="KX-TEST")
    board = {(K.TEAM_ABBR["Seattle Seahawks"], K.TEAM_ABBR["Denver Broncos"]): {"player_anytime_td": [row]}}
    n, liquid = K.attach([ev], board=board)
    oc = ev["props"]["bookmakers"][-1]["markets"][0]["outcomes"][0]
    assert n == 1 and oc["description"] == "Tory Horton" and oc.get("ts"), oc
    age = RU.price_age_min(oc["ts"], pd.Timestamp.now(tz="UTC"))
    assert age is not None and 0 <= age < 2, age
    e = dict(kick_utc=(pd.Timestamp.now(tz="UTC") + pd.Timedelta(hours=3)).strftime("%Y-%m-%dT%H:%MZ"), captured=oc["ts"])
    assert RU.not_actionable(e, pd.Timestamp.now(tz="UTC"), max_age=15) == []


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t(); print("PASS", t.__name__)
        except AssertionError as e:
            failed += 1; print("FAIL", t.__name__, e)
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
