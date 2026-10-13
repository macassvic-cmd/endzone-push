"""Reconciliation smoke check for results.json: the headline, its breakdown and the week table must agree.

    python test_reconcile.py            # after python results.py; the workflow runs it after both grading steps

Checks, for the live and since-current-rules scopes:
  1. record.<scope>.main.all  == sum of record.<scope>.by_market[*].all   (headline = its market breakdown)
  2. record.<scope>.<group>   == sum over bets_by_week rows in scope       (headline = week table cumulative), for main, bench, parlay
  3. record.<scope>.main/bench == an independent recount of results.json `bets` (singles) by role group; parlay == parlays.by_week
  4. the same for the shorter-than-+1000 split
  5. record_current_rules.cum == sum of its own week rows
Fails loudly (exit 1) with the numbers that disagree. Units compare to 0.01u, expected to 0.01u.
"""
import json, sys

TOL = 0.011


def close(a, b):
    return abs(float(a) - float(b)) <= TOL


def same(label, a, b, keys=("n", "won", "units", "expected")):
    bad = [(k, a.get(k), b.get(k)) for k in keys if not close(a.get(k) or 0, b.get(k) or 0)]
    if bad:
        raise AssertionError(f"{label}: {bad}")


def add(acc, v):
    for k in ("n", "won", "units", "expected", "staked", "var"):
        acc[k] = acc.get(k, 0) + (v.get(k) or 0)
    return acc


def zero():
    return dict(n=0, won=0, units=0.0, expected=0.0, staked=0.0, var=0.0)


def recount(bets, pred):
    acc = zero()
    for b in bets:
        if not pred(b): continue
        stake = float(b.get("stake") or 1.0)
        add(acc, dict(n=1, won=int(bool(b["won"])), units=float(b["profit"]), expected=stake * float(b.get("ev") or 0.0), staked=stake))
    return acc


def main():
    R = json.load(open("results.json"))
    rec = R.get("record") or {}
    scopes = R.get("record_scopes") or {}
    bf = set(scopes.get("backfill_weeks") or [])
    live_from, rules_from = scopes.get("live_from"), scopes.get("rules_from", 4)
    checks = 0
    for sc, rows_ok in (("live", lambda w: w not in bf), ("rules", lambda w: w not in bf and w >= rules_from)):
        S = rec.get(sc)
        if not S:
            continue
        rows = [r for r in R["bets_by_week"] if rows_ok(r["week"])]
        for split in ("all", "short"):
            # 1. headline == sum of its market breakdown
            bm = zero()
            for v in S["by_market"].values(): add(bm, v[split])
            same(f"{sc}/{split}: main vs by_market", S["main"][split], bm); checks += 1
            bb = zero()
            for v in S["bench_by_market"].values(): add(bb, v[split])
            same(f"{sc}/{split}: bench vs bench_by_market", S["bench"][split], bb); checks += 1
            # 2. headline == week-table cumulative, per group and per market
            for g in ("main", "bench", "parlay"):
                acc = zero()
                for r in rows: add(acc, r[g][split])
                same(f"{sc}/{split}: {g} vs week rows", S[g][split], acc); checks += 1
            for m, v in S["by_market"].items():
                acc = zero()
                for r in rows: add(acc, (r["by_market"].get(m) or {}).get(split) or zero())
                same(f"{sc}/{split}: market {m} vs week rows", v[split], acc); checks += 1
        # 3. independent recount from the bet list (singles) and the parlay log
        in_scope = lambda b: rows_ok(int(b["week"]))
        same(f"{sc}: main vs recount of bets", S["main"]["all"], recount(R["bets"], lambda b: in_scope(b) and b.get("role") != "Bench")); checks += 1
        same(f"{sc}: bench vs recount of bets", S["bench"]["all"], recount(R["bets"], lambda b: in_scope(b) and b.get("role") == "Bench")); checks += 1
        same(f"{sc}: short main vs recount", S["main"]["short"], recount(R["bets"], lambda b: in_scope(b) and b.get("role") != "Bench" and not b.get("longshot"))); checks += 1
        pw = [w for w in ((R.get("parlays") or {}).get("by_week") or []) if rows_ok(w["week"])]
        par = zero()
        for w in pw:
            for c in w["parlays"]:
                if c.get("dec"): add(par, dict(n=1, won=int(bool(c["won"])), units=float(c["profit"]), expected=float(c.get("ev") or 0)))
        same(f"{sc}: parlays vs parlay log", S["parlay"]["all"], par); checks += 1
        # the singles headline must not contain parlays
        assert S["main"]["all"]["n"] + S["bench"]["all"]["n"] == recount(R["bets"], in_scope)["n"], f"{sc}: singles count differs from the bet list"
        checks += 1
    CR = R.get("record_current_rules")
    if CR and CR.get("cum"):
        for g in ("main", "bench", "parlay"):
            for split in ("all", "short"):
                acc = zero()
                for r in CR["weeks"]: add(acc, r[g][split])
                same(f"current rules/{split}: {g} vs its week rows", CR["cum"][g][split], acc); checks += 1
    print(f"PASS test_reconcile: {checks} checks, headline singles {rec['live']['main']['all']['won']}-{rec['live']['main']['all']['n'] - rec['live']['main']['all']['won']} "
          f"{rec['live']['main']['all']['units']:+.2f}u | bench {rec['live']['bench']['all']['n']} | parlays {rec['live']['parlay']['all']['n']}" if rec.get("live") else "PASS test_reconcile: no live record yet")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("FAIL test_reconcile:", e); sys.exit(1)
