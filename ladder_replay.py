"""Ladder replay (research only): for every player-stat with a flagged alt rung in the +300 v2 replay (alt_cap_study.py),
price a ladder of rungs every 10 yards from the lowest flagged rung up to the highest alternate line the books offered,
at the pull the rung was flagged in, and grade it under different caps and staking rules.

    python ladder_replay.py [--weeks 4,5] [--season 2026] [--json out.json]

Ladder: targets = lowest flagged line + 10k; each target takes the offered line nearest to it within 2.5 yards (a step with
no quote that close is skipped); price = best surviving quote at that line in the flag pull; model P(>= line) from the
slate's ladder (log-odds interpolation), curve P from the pooled DK+FD curve, blend 50/50, EV at the price.
Caps: "+300" drops rungs priced +300 or longer; "uncapped" keeps them all.
Staking: (a) flat 1u per rung; (b) 1u per player-stat split evenly across its rungs; (c) only rungs with EV >= 5%, 1u each.
Graded on actual yards; a player-stat with no actual (did not play / game not final) is skipped and counted.
"""
import json, sys
import pandas as pd
import odds as O, yards_backtest as YB
import alt_cap_study as A

STEP, TOL = 10.0, 2.5


def ladders(per_pull, flagged):
    """flagged (+300 replay, one week) -> {(pid, kind): dict(name, pull, lowest, rungs=[rung dicts])}"""
    by_pull = dict(per_pull); out = {}
    for b in sorted(flagged, key=lambda b: (b["line"], b["pull"])):
        key = (b["pid"], b["kind"])
        if key in out: continue                                   # lowest flagged rung (first in sort) sets the ladder and its pull
        rungs = by_pull[b["pull"]].get(key, [])
        if not rungs: continue
        offered = sorted({r["line"] for r in rungs}); lo, hi = b["line"], max(offered); by_line = {r["line"]: r for r in rungs}
        lad, t, skipped = [], lo, 0
        while t <= hi + 1e-9:
            near = min(offered, key=lambda l: abs(l - t))
            if abs(near - t) <= TOL and by_line[near] not in lad: lad.append(by_line[near])
            else: skipped += 1
            t += STEP
        out[key] = dict(name=b["name"], kind=b["kind"], week=b["week"], pull=b["pull"], lowest=lo, highest=hi, skipped=skipped, rungs=lad)
    return out


def main(season, weeks, json_out=None):
    p = pd.read_parquet("data/pbp.parquet")
    ymap = {(int(r.week), r.pid, r.kind): float(r.y) for r in YB.actual_yards(p, season).itertuples()}
    L = {}
    for wk in weeks:
        rows, per_pull = A.pull_candidates(season, wk)
        flagged = A.replay(per_pull, 300, wk)
        lad = ladders(per_pull, flagged)
        void = [k for k in lad if ymap.get((wk, k[0], k[1])) is None]
        for k in void: lad.pop(k)
        print(f"week {wk}: {len({(b['pid'], b['kind']) for b in flagged})} player-stats with a flagged alt rung, {len(lad)} graded ladders, {len(void)} ungraded (no actual yards), "
              f"{sum(len(v['rungs']) for v in lad.values())} rungs, {sum(v['skipped'] for v in lad.values())} 10-yard steps with no quote within {TOL} yards")
        for k, v in lad.items():
            act = ymap[(wk, k[0], k[1])]
            for r in v["rungs"]: r["won"] = act >= r["line"]; r["profit"] = O.decimal(r["price"]) - 1 if r["won"] else -1.0
            L[(wk,) + k] = v
    caps = {"+300 cap": lambda r: r["price"] < 300, "uncapped": lambda r: True}
    stakes = {"(a) flat 1u per rung": lambda rs: [1.0] * len(rs), "(b) 1u per player split across rungs": lambda rs: [1.0 / len(rs)] * len(rs),
              "(c) only rungs with EV >= 5%, 1u each": lambda rs: [1.0 if r["ev"] >= 0.05 else 0.0 for r in rs]}
    results = {}
    print(f"\n== ladder replay, weeks {weeks}: {len(L)} player-stat ladders ==")
    print(f"{'cap | staking | week':52} | rungs | record  | units    | expected  | ROI     | players | best player | worst player")
    for cn, cf in caps.items():
        for sn, sf in stakes.items():
            def run(keys):
                per, n, won, units, exp, staked = {}, 0, 0, 0.0, 0.0, 0.0
                for k in keys:
                    v = L[k]; rs = [r for r in v["rungs"] if cf(r)]
                    if not rs: continue
                    st = sf(rs); pu = 0.0
                    for r, s_ in zip(rs, st):
                        if s_ <= 0: continue
                        n += 1; won += r["won"]; units += s_ * r["profit"]; exp += s_ * r["ev"]; staked += s_; pu += s_ * r["profit"]
                    if any(s_ > 0 for s_ in st): per[f"{v['name']} {v['kind']} W{v['week']}"] = round(pu, 2)
                top = sorted(per.items(), key=lambda x: -x[1]);
                return dict(n=n, won=won, units=round(units, 2), expected=round(exp, 2), roi=round(units / staked, 4) if staked else None, players=len(per),
                            best=top[0] if top else None, worst=top[-1] if top else None, top5=top[:5], bottom5=top[-5:][::-1], per_player=per)
            allr = run(list(L)); results[(cn, sn)] = dict(all=allr, by_week={wk: run([k for k in L if k[0] == wk]) for wk in weeks})
            def line(lbl, t):
                bw = lambda x: f"{x[0]} {x[1]:+.2f}" if x else "-"
                print(f"{lbl:52} | {t['n']:5} | {t['won']}-{t['n'] - t['won']:<4} | {t['units']:+7.2f}u | {t['expected']:+7.2f}u | {(t['roi'] or 0) * 100:+6.1f}% | {t['players']:7} | {bw(t['best'])} | {bw(t['worst'])}")
            line(f"{cn} | {sn}", allr)
            for wk in weeks: line(f"   week {wk}", results[(cn, sn)]["by_week"][wk])
    print("\n== per-player net units, top / bottom 5 per combination ==")
    for (cn, sn), v in results.items():
        print(f"{cn} | {sn}: top {v['all']['top5']} | bottom {v['all']['bottom5']}")
    if json_out:
        json.dump({f"{cn} | {sn}": dict(all={k: x for k, x in v["all"].items() if k != "per_player"}, by_week={wk: {k: x for k, x in w.items() if k != "per_player"} for wk, w in v["by_week"].items()})
                   for (cn, sn), v in results.items()}, open(json_out, "w"), indent=1, default=str)
    return L, results


if __name__ == "__main__":
    a = sys.argv[1:]; g = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    main(int(g("--season", 2026)), [int(x) for x in g("--weeks", "4,5").split(",")], g("--json"))
