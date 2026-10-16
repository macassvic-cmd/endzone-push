"""Alt-rung cap study (research only, nothing on the board changes): replay the v2 alt-rung rule over a week's saved pulls
with the price cap at +300, +350 and +400 and grade the result.

    python alt_cap_study.py [--weeks 4,5] [--season 2026] [--json out.json]

Replay mirrors the live first-flag log: pulls oldest first; events whose kickoff is before the pull stamp are skipped (the
game had started) and only the week's own games count (a Monday pull already carries next week's board); per pull, every offered alternate rung of every slate player-stat (no weak spots, not the book's main
line) is priced against the pooled DK+FD curve (yard_prices.market_curve, bad rows cross-checked), model P(>= line) is the
slate's ladder interpolated in log-odds, blend = 50/50 model and curve (what the live week-5 flags used), EV at the best
surviving quote. A rung qualifies when the curve is eligible, line >= yards.MIN_EDGE_LINE, EV >= 5%, model <= 2x curve and
price < cap. The best-EV qualifying rung per player-stat is recorded the first time its (pid, kind, line) key appears, at
that pull's price and book. Graded on actual yards (yards_backtest.actual_yards), 1u per bet; players with no actual
(did not play / game not final) are left ungraded. Pre-registered gate (CHANGELOG 2026-10-12): the cap rises only if the
incremental band holds >= 30 bets across >= 3 weeks with actual units >= expected.
"""
import json, sys, os, io, contextlib
import numpy as np, pandas as pd
import odds as O, yard_prices as YP, yards as Y, yards_backtest as YB, clv as C
from nansafe import text

CAPS = (300, 350, 400)


def pull_candidates(season, week):
    """-> (slate_rows, [(stamp, {(pid, kind): [rung dicts]})]) : every priced alt rung per pull (not yet filtered by cap / EV)."""
    slate = json.load(open(f"slate_{season}_w{week}.json"))
    rows = {}
    for y in slate.get("yards", []):
        if text(y, "weak_spot"): continue
        rows[(y["pid"], y["kind"])] = y
    games = {frozenset((g["home"], g["away"])) for g in slate.get("games", [])}      # a Monday pull can already carry next week's games
    per_pull = []
    for stamp, fn in C.pulls(season, week):
        events = [e for e in json.load(open(fn)) if e["commence_time"].replace("-", "").replace(":", "")[:13] >= stamp
                  and frozenset((O.TEAM_ABBR.get(e["home_team"], e["home_team"]), O.TEAM_ABBR.get(e["away_team"], e["away_team"]))) in games]
        if not events: continue
        with contextlib.redirect_stdout(io.StringIO()):
            yb, yh = YP.ladder_board(events)
        out = {}
        for (pid, kind), y in rows.items():
            name = y["name"]; xs = [r["line"] for r in y["rungs"]]; ps = [r["p"] for r in y["rungs"]]
            lines = YP.offered_lines(yb, kind, name)
            if not lines: continue
            curve = YP.market_curve(yb, yh, kind, name, model=(xs, ps))
            if not curve: continue
            ml = y.get("main_line"); rungs = []
            for line in lines:
                prob = YP.interp_logit(xs, ps, line); cp = YP.curve_at(curve, line)
                offered = [(bk, pr) for bk, pr, _ in curve["quotes"].get(line, [])]
                if prob is None or cp is None or not offered: continue
                obk, opr = max(offered, key=lambda x: O.decimal(x[1])); blend = 0.5 * (prob + cp); ev = blend * O.decimal(opr) - 1
                rungs.append(dict(pid=pid, name=name, kind=kind, role=y.get("role"), line=float(line), is_main=(ml is not None and line == ml), model_p=prob, curve_p=cp, blend=blend,
                                  price=int(opr), book=obk, ev=ev, eligible=bool(curve["eligible"]), floor_ok=line >= Y.MIN_EDGE_LINE[kind], ratio_ok=prob <= Y.ALT_MAX_RATIO * cp))
            if rungs: out[(pid, kind)] = rungs
        per_pull.append((stamp, out))
    return rows, per_pull


def qualifies(r, cap):
    return r["eligible"] and r["floor_ok"] and not r["is_main"] and r["ev"] >= 0.05 and r["ratio_ok"] and r["price"] < cap


def replay(per_pull, cap, week):
    """First-flag replay under one cap -> list of flagged bets with the pull stamp."""
    seen, flagged = set(), []
    for stamp, out in per_pull:
        for (pid, kind), rungs in out.items():
            q = [r for r in rungs if qualifies(r, cap)]
            if not q: continue
            best = max(q, key=lambda r: r["ev"]); key = (pid, kind, best["line"])
            if key in seen: continue
            seen.add(key); flagged.append(dict(best, week=week, pull=stamp, cap=cap))
    return flagged


def grade(bets, ymap):
    for b in bets:
        act = ymap.get((b["week"], b["pid"], b["kind"])); b["actual"] = act
        b["won"] = None if act is None else bool(act >= b["line"])
        b["profit"] = None if act is None else (O.decimal(b["price"]) - 1 if b["won"] else -1.0)
    return bets


def tally(bets, stake=None):
    g = [b for b in bets if b["won"] is not None]
    st = (lambda b: 1.0) if stake is None else stake
    units = sum(st(b) * b["profit"] for b in g); staked = sum(st(b) for b in g); exp = sum(st(b) * b["ev"] for b in g)
    return dict(n=len(g), void=len(bets) - len(g), won=sum(b["won"] for b in g), units=round(units, 2), expected=round(exp, 2),
                roi=round(units / staked, 4) if staked else None, players=len({(b["pid"], b["kind"]) for b in g}), weeks=len({b["week"] for b in g}))


def fmt(t):
    return f"{t['n']:4} | {t['won']}-{t['n'] - t['won']} | {t['units']:+7.2f}u | exp {t['expected']:+6.2f}u | ROI {t['roi'] * 100 if t['roi'] is not None else float('nan'):+5.1f}% | players {t['players']:3} | weeks {t['weeks']}" + (f" | ungraded {t['void']}" if t["void"] else "")


def main(season, weeks, json_out=None):
    p = pd.read_parquet("data/pbp.parquet")
    ymap = {(int(r.week), r.pid, r.kind): float(r.y) for r in YB.actual_yards(p, season).itertuples()}
    data = {}
    for wk in weeks:
        rows, per_pull = pull_candidates(season, wk)
        print(f"week {wk}: {len(rows)} player-stats in the slate, {len(per_pull)} pulls with games still to kick off")
        data[wk] = (rows, per_pull)
    runs = {cap: grade([b for wk in weeks for b in replay(data[wk][1], cap, wk)], ymap) for cap in CAPS}
    print("\n== v2 alt-rung rule by price cap (first-flag replay, 1u per bet) ==")
    print(f"{'cap / week':14} | {'n':>4} | record | units    | expected    | ROI        | players     | weeks")
    for cap in CAPS:
        print(f"cap +{cap:<9} | {fmt(tally(runs[cap]))}")
        for wk in weeks: print(f"   week {wk:<7} | {fmt(tally([b for b in runs[cap] if b['week'] == wk]))}")
    print("\n== incremental bands (bets flagged under the +400 cap, by price) ==")
    bands = [("+300 to +349 (what a +350 cap adds)", 300, 350), ("+350 to +399 (what a +400 cap adds)", 350, 400)]
    cap300 = {(b["pid"], b["kind"], b["pull"]): b for b in runs[300]}
    out_bands = {}
    for lbl, lo, hi in bands:
        bb = [b for b in runs[400] if lo <= b["price"] < hi]
        displaced = sum(1 for b in bb if (b["pid"], b["kind"], b["pull"]) in cap300)
        t = tally(bb); out_bands[lbl] = dict(t, displaced=displaced)
        print(f"{lbl:38} | {fmt(t)} | displaced a sub-+300 rung flagged in the same pull: {displaced}")
        for wk in weeks: print(f"   week {wk:<34} | {fmt(tally([b for b in bb if b['week'] == wk]))}")
        ok = t["n"] >= 30 and t["weeks"] >= 3 and t["units"] >= t["expected"]
        print(f"   gate (>= 30 bets, >= 3 weeks, units >= expected): {'MET' if ok else 'NOT MET'} -> cap {'could rise' if ok else 'stays at +300'}")
    # sanity: week-5 +300 replay vs the live v2 edge log
    if 5 in weeks and os.path.exists("edge_log/2026_w5.json"):
        log = {k: v for k, v in json.load(open("edge_log/2026_w5.json")).items() if v.get("market") == "yds_alt" and not v.get("void") and str(v.get("rule", "")).startswith("alt rung v2")}
        live = {(v["pid"], v["kind"], float(v["line"])) for v in log.values()}
        rep = {(b["pid"], b["kind"], b["line"]) for b in runs[300] if b["week"] == 5}
        lb = grade([dict(week=5, pid=v["pid"], kind=v["kind"], line=float(v["line"]), price=int(v["best"]), ev=v["ev"]) for v in log.values()], ymap)
        print(f"\n== week-5 sanity: live v2 edge log {len(live)} keys vs +300 replay {len(rep)} keys | shared {len(live & rep)} | log only {len(live - rep)} | replay only {len(rep - live)}")
        print(f"   live log graded : {fmt(tally(lb))}")
        print(f"   replay graded   : {fmt(tally([b for b in runs[300] if b['week'] == 5]))}")
        for k in sorted(live - rep): print("   log only   ", k, log[next(kk for kk, v in log.items() if (v['pid'], v['kind'], float(v['line'])) == k)]["best"])
        for b in sorted((b for b in runs[300] if b["week"] == 5 and (b["pid"], b["kind"], b["line"]) not in live), key=lambda b: b["name"]): print("   replay only", b["name"], b["kind"], b["line"], b["price"], b["pull"])
    if json_out:
        json.dump(dict(caps={cap: dict(all=tally(v), by_week={wk: tally([b for b in v if b["week"] == wk]) for wk in weeks}, bets=v) for cap, v in runs.items()}, bands=out_bands),
                  open(json_out, "w"), indent=1, default=str)
    return data, runs


if __name__ == "__main__":
    a = sys.argv[1:]; g = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    main(int(g("--season", 2026)), [int(x) for x in g("--weeks", "4,5").split(",")], g("--json"))
