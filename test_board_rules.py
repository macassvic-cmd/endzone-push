"""Board workflow rules (2026-10-18): BET DOWN TO, the loop-only price-age limit, Discord message formatting.

    python test_board_rules.py        # CI runs it in the "Tests and data" steps
"""
import os
import rules as RU
import alerts as AL


def _ev(p, a):
    return p * (1 + (a / 100 if a > 0 else 100 / -a)) - 1


def test_bet_down_to_is_the_worst_price_with_five_percent_ev():
    for p in (0.05, 0.12, 0.25, 0.333, 0.45, 0.5, 0.52, 0.6, 0.75, 0.9):
        a = RU.bet_down_to(p)
        assert a is not None and _ev(p, a) >= RU.MIN_EV - 1e-9, (p, a, _ev(p, a))
        worse = a - 1 if a > 100 else (a - 1 if a < -100 else -101)        # one tick worse for the bettor
        assert _ev(p, worse) < RU.MIN_EV + 0.011, (p, a, worse, _ev(p, worse))   # the tick below fails (or sits within rounding of) the threshold
    assert RU.bet_down_to(0.5) == 110 and RU.bet_down_to(0.25) == 320, (RU.bet_down_to(0.5), RU.bet_down_to(0.25))
    assert RU.bet_down_to(0.7) == -200, RU.bet_down_to(0.7)                 # 1.05 / 0.7 = 1.5 decimal = -200
    assert RU.bet_down_to(None) is None and RU.bet_down_to(0) is None and RU.bet_down_to(1.2) is None
    print("  bet down to: +110 at 50%, +320 at 25%")


def test_price_age_limit_only_inside_loops():
    old = os.environ.pop("IN_LOOP", None)
    try:
        assert RU.price_age_limit() is None, "outside kickoff loops: no age limit"
        os.environ["IN_LOOP"] = "1"; assert RU.price_age_limit() == RU.MAX_PRICE_AGE_MIN == 15
        import pandas as pd
        now = pd.Timestamp("2026-10-18T15:00:00Z")
        e = dict(kick_utc="2026-10-18T17:00Z", captured="2026-10-18T14:00:00Z")      # 60 min old, 120 min to kickoff
        assert RU.not_actionable(dict(e), now, max_age=RU.price_age_limit()) == ["price captured 60 min ago (over 15)"]
        os.environ.pop("IN_LOOP")
        assert RU.not_actionable(dict(e), now, max_age=RU.price_age_limit()) == [], "outside loops the stale price stays, labelled"
        assert RU.not_actionable(dict(e, kick_utc="2026-10-18T15:20Z"), now, max_age=None) == ["kickoff in 20 min (under 30)"], "kickoff cutoff everywhere"
    finally:
        if old is not None: os.environ["IN_LOOP"] = old
    print("  age limit 15 min in loops, none outside; 30-min cutoff everywhere")


def test_split_board_sets_bet_down_to():
    edges = [dict(bet="A B Anytime TD", pid="x", market="any", team="T", role="Starter", model_p=0.3, mkt_p=0.2, blend_p=0.25, best=400, book="DraftKings", ev=0.25, med=350, ev_med=0.125, nbooks=3),
             dict(bet="C D Over 49.5 rush yds", pid="y", market="yds", kind="rush", line=49.5, team="T", role="Bench", model_p=0.55, mkt_p=0.5, blend_p=0.525, best=-105, book="FanDuel", ev=0.025, med=-110, ev_med=0.0, nbooks=2)]
    parts = RU.split_board(edges)
    assert parts["main"][0]["bet_down_to"] == 320 and parts["bench"][0]["bet_down_to"] == 100, (parts["main"][0]["bet_down_to"], parts["bench"][0]["bet_down_to"])
    print("  split_board carries bet_down_to on every row")


def test_discord_messages():
    es = [dict(bet="A B Anytime TD", best=400, book="DraftKings", bet_down_to=320, ev=0.25, role="Starter", stake=1.0),
          dict(bet="C D Over 49.5 rush yds (alt)", best=250, book="BetRivers", bet_down_to=200, ev=0.08, role="Bench", stake=0.25)]
    m = AL.messages(es, 2026, 6, "20261018T1500Z")
    assert len(m) == 1 and "2 new edges" in m[0] and "A B Anytime TD" in m[0] and "DraftKings +400" in m[0] and "bet down to +320" in m[0] and "EV +25.0%" in m[0] and "Bench 0.25u" in m[0], m
    assert m[0].index("A B") < m[0].index("C D"), "sorted by EV"
    assert "BET DOWN TO" in m[0]
    big = [dict(es[0], bet=f"Player {i} Anytime TD") for i in range(60)]
    ms = AL.messages(big, 2026, 6, "s"); assert len(ms) >= 2 and all(len(x) <= 2000 for x in ms), [len(x) for x in ms]
    os.environ.pop("DISCORD_WEBHOOK_URL", None)
    assert AL.notify_new_edges(es, 2026, 6, "s").startswith("no DISCORD_WEBHOOK_URL") and AL.notify_new_edges([], 2026, 6, "s") == "nothing new"
    print(f"  discord: 1 message for 2 edges, {len(ms)} chunks for 60, no-op without the webhook")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try: t(); print("PASS", t.__name__)
        except Exception as e: failed += 1; print("FAIL", t.__name__, "->", repr(e))
    print(f"{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
