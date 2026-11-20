"""NaN-safety tests: a column missing from one slate (NaN after concat) must never silently drop or admit rows.

    python test_nan_safety.py          # fast: helper semantics, the Edge Board rule, the re-scorer, parlay legs
    python test_nan_safety.py --slow   # also runs results.main() twice on the real slates (one with a column removed)
                                       # and asserts identical graded-row and bet counts per week (needs data/)

No pytest dependency; each test_* function raises on failure and the script prints PASS/FAIL. The workflow runs
the fast tests before every projection run.
"""
import json, os, sys, glob, shutil, tempfile
import numpy as np, pandas as pd
import nansafe as NS, rules as RU


def test_helper_semantics():
    for x in (None, float("nan"), np.nan, pd.NA, pd.NaT):
        assert NS.isnan(x), x
    for x in ("", "x", 0, 1, False, True, [], {}, 0.0):
        assert not NS.isnan(x), x
    row = pd.Series(dict(a=np.nan, b="mobile-qb", c=0, d="", e=3.0))
    assert NS.val(row, "a", "dflt") == "dflt" and NS.val(row, "zzz", 7) == 7 and NS.val(row, "e") == 3.0
    assert NS.flag(row, "a") is False and NS.flag(row, "b") is True and NS.flag(row, "d") is False and NS.flag(row, "zzz") is False
    assert NS.text(row, "a") is None and NS.text(row, "b") == "mobile-qb" and NS.text(row, "d") is None
    assert NS.num(row, "a", 0) == 0 and NS.num(row, "e") == 3.0 and NS.num(row, "b", -1) == -1
    nt = next(pd.DataFrame([dict(x=np.nan, y=2)]).itertuples()); assert NS.val(nt, "x", 5) == 5 and NS.val(nt, "y") == 2
    assert NS.val({"k": None}, "k", 1) == 1 and NS.flag({"k": "y"}, "k") is True and NS.val(None, "k", 9) == 9


def _priced(n, week, prefix="any_", weak=None, with_weak_col=True, with_kalshi=True):
    """n qualifying rows (EV 10% at median and best, 3 books)."""
    rows = []
    for i in range(n):
        r = dict(season=2026, week=week, pid=f"p{week}_{i}", name=f"P{week}_{i}", team="BUF", role="Starter", games=3, p_any=0.4, p_first=0.05, p_2plus=0.05)
        r.update({prefix + "best": 200, prefix + "med": 180, prefix + "ev": 0.12, prefix + "ev_med": 0.10, prefix + "nbooks": 3, prefix + "book": "DK",
                  prefix + "mkt_p": 0.3, prefix + "blend_p": 0.35, prefix + "w": 0.5, "hit": i % 2 == 0, "first_hit": False, "two_hit": False})
        if with_kalshi: r.update({prefix + "kalshi": 210, prefix + "kalshi_liquid": True})
        if with_weak_col: r["weak_spot"] = weak
        rows.append(r)
    return pd.DataFrame(rows)


def test_td_edge_rule_missing_columns():
    a = _priced(5, 1, with_weak_col=False, with_kalshi=False)             # week with no weak_spot / kalshi columns at all
    b = _priced(4, 4, weak=None); b.loc[0, "weak_spot"] = "mobile-qb"     # one tagged row, three None
    both = pd.concat([a, b], ignore_index=True)                           # a's weak_spot and kalshi become NaN here
    assert both["weak_spot"].isna().sum() == 8
    ea, eb, eboth = RU.td_edges(a), RU.td_edges(b), RU.td_edges(both)
    assert len(ea) == 5 and len(eb) == 3, (len(ea), len(eb))
    assert len(eboth) == len(ea) + len(eb) == RU.qualifying_rows(both), (len(eboth), RU.qualifying_rows(both))
    assert all(e["kalshi_liquid"] is False and e["kalshi"] is None for e in eboth if e["pid"].startswith("p1_")), "NaN kalshi must read as absent, not liquid"
    assert all(isinstance(e["games"], int) for e in eboth)
    # NaN in the numeric gate columns must drop only that row, loudly countable
    c = both.copy(); c.loc[2, "any_nbooks"] = np.nan; c.loc[3, "any_med"] = np.nan
    assert len(RU.td_edges(c)) == len(eboth) - 2 == RU.qualifying_rows(c)


def test_rescore_edges_missing_columns():
    import results as R
    a = _priced(6, 1, with_weak_col=False); a["any_mkt_p"] = 0.2; a["any_best"] = 300; a["any_med"] = 280   # 50/50 blend 0.30 at +300 -> EV +20%, +280 -> +14%
    b = _priced(3, 4, weak=None); b["any_mkt_p"] = 0.2; b["any_best"] = 300; b["any_med"] = 280; b.loc[0, "weak_spot"] = "mobile-qb"
    both = pd.concat([a, b], ignore_index=True)
    assert both["weak_spot"].isna().sum() == 8          # 6 missing-column NaN + 2 None
    na, nb, nboth = len(R.rescore_edges(a, {})), len(R.rescore_edges(b, {})), len(R.rescore_edges(both, {}))
    assert na == 6 and nb == 2 and nboth == na + nb, (na, nb, nboth)
    bets = R.rescore_edges(both, {})
    assert all(x["role"] == "Starter" and x["backfill_price"] is False for x in bets)
    both2 = both.copy(); both2.loc[0, "role"] = np.nan                        # a NaN role must become "Unknown", never drop from a groupby
    assert [x["role"] for x in R.rescore_edges(both2, {}) if x["bet"].startswith("P1_0 ")] == ["Unknown"]


def test_parlay_leg_pool_nan_fields():
    import parlay as PL
    players = _priced(4, 4, prefix="two_", weak=None).to_dict("records")
    for p in players: p.update(game_id=p["pid"], two_blend_p=0.3, two_best=400, two_nbooks=3, two_book="DK", p_2plus=0.2)
    players[0]["weak_spot"] = float("nan"); players[1]["two_kalshi_liquid"] = float("nan"); players[2]["two_nbooks"] = float("nan"); players[3]["role"] = "Starter"
    pool = PL.leg_pool(players, "value")
    assert [l["pid"] for l in pool] == ["p4_0", "p4_1", "p4_3"], [l["pid"] for l in pool]   # NaN weak spot keeps the leg; NaN books drops it
    assert len(PL.leg_pool(players, "likely")) == 4


def test_results_end_to_end_missing_column():
    """results.main() on the real slates vs the same slates with weak_spot removed from the first one and added (None)
       to the others: graded players and bets per week must be identical. Needs data/pbp.parquet; skipped otherwise."""
    if not os.path.exists("data/pbp.parquet") or not glob.glob("slate_*_w*.json"):
        print("  (slow test skipped: no data)"); return
    import results as R
    root = os.getcwd(); real_read = pd.read_parquet
    pd.read_parquet = lambda path, *a, **k: real_read(os.path.join(root, path) if not os.path.isabs(path) else path, *a, **k)

    def run(mutate):
        tmp = tempfile.mkdtemp(prefix="ezl_nan_")
        for fn in glob.glob("slate_*_w*.json") + glob.glob("blend_model.json") + glob.glob("backtest_2025.json"):
            shutil.copy(fn, tmp)
        for d in ("odds_history", "edge_log"):
            if os.path.isdir(d): shutil.copytree(d, os.path.join(tmp, d))
        slates = sorted(glob.glob(os.path.join(tmp, "slate_*_w*.json")))
        if mutate:
            for i, fn in enumerate(slates):
                d = json.load(open(fn))
                for p in d["players"]:
                    if i == 0: p.pop("weak_spot", None)                 # column entirely absent from the first slate
                    else: p.setdefault("weak_spot", None)
                json.dump(d, open(fn, "w"))
        os.chdir(tmp)
        try:
            import io, contextlib
            with contextlib.redirect_stdout(io.StringIO()): R.main()
            r = json.load(open("results.json"))
        finally:
            os.chdir(root)
        shutil.rmtree(tmp, ignore_errors=True)
        return {(w["season"], w["week"]): w["players"] for w in r["weeks"]}, {(w["season"], w["week"]): w["all"]["n"] for w in r["bets_by_week"]}, r["rescore_current"]["n"] if r.get("rescore_current") else 0

    try:
        base, mut = run(False), run(True)
    finally:
        pd.read_parquet = real_read
    assert base[0] == mut[0], ("graded players differ", base[0], mut[0])
    assert base[1] == mut[1], ("bets per week differ", base[1], mut[1])
    assert base[2] == mut[2] and base[2] > 0, ("re-score count differs or empty", base[2], mut[2])
    print(f"  end to end: {sum(base[0].values())} graded rows, {base[2]} bets, identical with the column removed")


if __name__ == "__main__":
    slow = "--slow" in sys.argv
    tests = [test_helper_semantics, test_td_edge_rule_missing_columns, test_rescore_edges_missing_columns, test_parlay_leg_pool_nan_fields] + ([test_results_end_to_end_missing_column] if slow else [])
    failed = 0
    for t in tests:
        try:
            t(); print(f"PASS {t.__name__}")
        except Exception as e:
            failed += 1; print(f"FAIL {t.__name__}: {e!r}")
    sys.exit(1 if failed else 0)
