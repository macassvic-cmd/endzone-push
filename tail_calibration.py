"""Research 13 (2026-10-29): yardage tail calibration, weeks 4-6. Every PRICED yard rung (two-sided book price or a pooled-curve price),
flagged or not, bucketed by how far the rung sits above our median in model SDs ((line - median) / sd: <0.5, 0.5-1, 1-1.5, 1.5-2, >2) x
rush / receiving. Per bucket: count, mean model P(over), mean market P(over), actual hit rate (and the model - actual gap).
Question: does the model overstate P(over) more as the rung moves further above the median?

    python tail_calibration.py [--weeks 4,5,6] [--write research/tail_calibration_2026_w4-6.json]

Reads the committed slates (slate_<season>_w<week>.json: yards rows with rungs, median, sd) and actual yards from data/pbp.parquet
(yards_backtest.actual_yards). Rungs at or below the median are reported in a separate "<=0" row for reference. A rung with no touches
(no actual row) is skipped; a rung exactly at the actual yards is a push and is skipped. Market P = the two-sided no-vig P at that rung
when a book posts it, else the pooled DK + FD curve P (curve_p). Research only: nothing here changes the board or the record.
"""
import json, os, sys
import pandas as pd
import yards_backtest as YB

EDGES = [(-99, 0, "<=0 (at/below median)"), (0, 0.5, "<0.5"), (0.5, 1, "0.5-1"), (1, 1.5, "1-1.5"), (1.5, 2, "1.5-2"), (2, 99, ">2")]


def bucket(z):
    for lo, hi, lbl in EDGES:
        if lo < z <= hi: return lbl
    return None


def rows_for(weeks, season=None):
    p = pd.read_parquet("data/pbp.parquet")
    season = season or int(p.season.max())
    ymap = {(int(r.week), r.pid, r.kind): float(r.y) for r in YB.actual_yards(p, season).itertuples()}
    out = []
    for wk in weeks:
        fn = f"slate_{season}_w{wk}.json"
        if not os.path.exists(fn): continue
        d = json.load(open(fn))
        for r in d.get("yards") or []:
            if r.get("kind") not in ("rush", "rec") or not r.get("sd"): continue
            y = ymap.get((wk, r["pid"], r["kind"]))
            if y is None: continue
            for g in r.get("rungs") or []:
                mp = g.get("mkt_p") if g.get("mkt_p") is not None else g.get("curve_p")
                if mp is None or g.get("p") is None: continue
                line = float(g["line"])
                if y == line: continue
                z = (line - float(r["median"])) / float(r["sd"])
                out.append(dict(week=wk, pid=r["pid"], name=r.get("name"), kind=r["kind"], line=line, median=r["median"], sd=r["sd"], z=round(z, 3), bucket=bucket(z),
                                model_p=float(g["p"]), market_p=float(mp), priced_by="book" if g.get("mkt_p") is not None else "curve", hit=int(y > line), actual=y,
                                flagged=bool(g.get("ev") is not None and g.get("ev") >= 0.05 and g.get("ev_med") is not None and g.get("ev_med") >= 0.05)))
    return out


def table(rows):
    df = pd.DataFrame(rows)
    if df.empty: return []
    out = []
    for kind in ("rush", "rec"):
        for _, _, lbl in EDGES:
            g = df[(df.kind == kind) & (df.bucket == lbl)]
            if g.empty: continue
            out.append(dict(kind=kind, bucket=lbl, n=int(len(g)), model_p=round(float(g.model_p.mean()), 4), market_p=round(float(g.market_p.mean()), 4),
                            hit_rate=round(float(g.hit.mean()), 4), model_minus_actual=round(float(g.model_p.mean() - g.hit.mean()), 4),
                            market_minus_actual=round(float(g.market_p.mean() - g.hit.mean()), 4), flagged=int(g.flagged.sum()), players=int(g.pid.nunique())))
    return out


def main(argv):
    g = lambda k, d=None: argv[argv.index(k) + 1] if k in argv else d
    weeks = [int(x) for x in (g("--weeks") or "4,5,6").split(",")]
    rows = rows_for(weeks)
    tab = table(rows)
    print(f"{'kind':5} {'bucket (SDs above median)':26} {'n':>5} {'model P':>8} {'market P':>9} {'actual':>7} {'model-act':>10} {'mkt-act':>8} {'flagged':>8}")
    for r in tab:
        print(f"{r['kind']:5} {r['bucket']:26} {r['n']:5d} {r['model_p']:8.3f} {r['market_p']:9.3f} {r['hit_rate']:7.3f} {r['model_minus_actual']:+10.3f} {r['market_minus_actual']:+8.3f} {r['flagged']:8d}")
    fn = g("--write") or f"research/tail_calibration_2026_w{min(weeks)}-{max(weeks)}.json"
    os.makedirs(os.path.dirname(fn), exist_ok=True)
    json.dump(dict(weeks=weeks, question="does the model overstate P(over) more as the rung moves further above the median?", buckets=tab, n_rungs=len(rows),
                   note="priced rungs only (book two-sided price or pooled DK+FD curve), pushes and no-touch games skipped; research only"), open(fn, "w"), indent=1)
    print("written", fn, f"({len(rows)} rungs)")


if __name__ == "__main__":
    main(sys.argv[1:])
