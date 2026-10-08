"""Replay the yard-ladder edge rule on a saved pull and grade it (no re-simulation: model P(over) per rung comes from the
week's slate, interpolated in log-odds between its rungs).

    python yard_alt_replay.py --season 2026 --week 4 [--pull 20261003T1852Z]

Rule (same as run_week.py): pooled DK+FD market curve per player and stat (main + alternates, no-vig, after the bad-row
filters, PAV-monotone, log-odds interpolation, no extrapolation), 50/50 (or fitted) model+curve blend at the exact
line, EV >= 5% at the offered price, curve from 2+ books or 4+ rungs from one book, line floor 10 (100 passing), no
weak spots, best-EV rung per player and stat. Reports edges, how many are alternate rungs, and the graded result.
"""
import json, sys, glob, re
import numpy as np, pandas as pd
import odds as O, yard_prices as YP, yards as Y, blend as BL, yards_backtest as YB, clv as C
from nansafe import val, text


def main(season, week, pull=None):
    slate = json.load(open(f"slate_{season}_w{week}.json"))
    pulls = C.pulls(season, week)
    if pull: pulls = [(st, fn) for st, fn in pulls if st.startswith(pull.replace("Z", ""))]
    if not pulls: raise SystemExit("no saved pull")
    st, fn = pulls[0]; events = json.load(open(fn)); yb, yh = YP.ladder_board(events)
    models = BL.load(); coef = {k: BL.coef_for(models, "yds_" + k) or BL.coef_for(models, "yds") for k in Y.DEFAULT_LADDER}
    p = pd.read_parquet("data/pbp.parquet"); ymap = {(int(r.week), r.pid, r.kind): float(r.y) for r in YB.actual_yards(p, season).itertuples()}
    edges, n_curve, n_dropped = [], 0, 0
    for y in slate.get("yards", []):
        if text(y, "weak_spot"): continue
        name, kind = y["name"], y["kind"]
        xs = [r["line"] for r in y["rungs"]]; ps = [r["p"] for r in y["rungs"]]
        curve = YP.market_curve(yb, yh, kind, name, model=(xs, ps))
        if not curve: continue
        n_curve += curve["eligible"]; n_dropped += curve["dropped"]
        best = None
        for line in YP.offered_lines(yb, kind, name):
            prob = YP.interp_logit(xs, ps, line)
            cp = YP.curve_at(curve, line); offered = [(bk, pr) for bk, pr, _ in curve["quotes"].get(line, [])]
            if prob is None or cp is None or not offered: continue
            cb = float(BL.predict(coef.get(kind), [prob], [cp])[0]); obk, opr = max(offered, key=lambda x: O.decimal(x[1])); ev = cb * O.decimal(opr) - 1
            if curve["eligible"] and line >= Y.MIN_EDGE_LINE[kind] and ev >= 0.05 and (best is None or ev > best["ev"]):
                best = dict(name=name, pid=y["pid"], kind=kind, line=line, model_p=prob, curve_p=cp, blend=cb, price=opr, book=obk, ev=ev,
                            alt=(y.get("main_line") is None or line != y["main_line"]), books=len(curve["books"]), rungs=curve["n_rungs"], role=y.get("role"))
        if best:
            act = ymap.get((week, best["pid"], kind)); best["actual"] = act
            best["won"] = None if act is None else act >= best["line"]
            best["profit"] = None if act is None else (O.decimal(best["price"]) - 1 if best["won"] else -1.0)
            edges.append(best)
    df = pd.DataFrame(edges)
    print(f"pull {st} | {len(slate.get('yards', []))} player-stats, {n_curve} with an eligible curve, {n_dropped} quotes dropped by the cross-check | edges {len(df)} ({int(df.alt.sum()) if len(df) else 0} alt rungs)")
    for tag, g in (("all", df), ("alt rungs", df[df.alt]), ("main line", df[~df.alt])) if len(df) else []:
        gg = g[g.won.notna()]
        if len(gg): print(f"   {tag:10} n {len(gg):3} | {int(gg.won.sum())}-{int((~gg.won.astype(bool)).sum())} | units {gg.profit.sum():+.1f} | expected {gg.ev.sum():+.1f} | avg EV {gg.ev.mean()*100:.1f}% | avg price {gg.price.mean():+.0f}")
    if len(df):
        for r in df.sort_values("ev", ascending=False).itertuples():
            print(f"   {r.name:22} Over {r.line:5g} {r.kind:4} {'alt' if r.alt else 'main'} | model {r.model_p:.2f} curve {r.curve_p:.2f} blend {r.blend:.2f} | {r.price:+d} {r.book:9} EV {r.ev*100:+.0f}% | {r.books} books {r.rungs} rungs | actual {r.actual} {'W' if r.won else 'L' if r.won is not None else '?'}")
    return df


if __name__ == "__main__":
    a = sys.argv[1:]; g = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    main(int(g("--season", 2026)), int(g("--week", 4)), g("--pull"))
