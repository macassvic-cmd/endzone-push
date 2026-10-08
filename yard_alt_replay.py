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
        found = []
        # (1) main line, original rule: 2+ books at the book's main line, EV >= 5% at the median and best book
        ml = y.get("main_line")
        if ml is not None:
            prob = YP.interp_logit(xs, ps, float(ml))
            ps_main = YP.price_rung(yb, yh, kind, name, float(ml), model_p=prob) if prob is not None else None   # same model-anchored cross-book check as run_week
            if ps_main and prob is not None and ps_main["n_books"] >= 2 and ml >= Y.MIN_EDGE_LINE[kind]:
                cb = float(BL.predict(coef.get(kind), [prob], [ps_main["market_p"]])[0]); ev = cb * O.decimal(ps_main["best"]) - 1; evm = cb * O.decimal(ps_main["median"]) - 1
                if ev >= 0.05 and evm >= 0.05:
                    found.append(dict(name=name, pid=y["pid"], kind=kind, line=float(ml), model_p=prob, curve_p=ps_main["market_p"], blend=cb, price=ps_main["best"], book=ps_main["book"], ev=ev,
                                      cat="main", books=ps_main["n_books"], rungs=curve["n_rungs"], role=y.get("role")))
        # (2) alt rungs against the curve: flagged at prices shorter than +300 with model <= 2x curve; the rest are research
        best = None
        for line in YP.offered_lines(yb, kind, name):
            if ml is not None and line == ml: continue
            prob = YP.interp_logit(xs, ps, line)
            cp = YP.curve_at(curve, line); offered = [(bk, pr) for bk, pr, _ in curve["quotes"].get(line, [])]
            if prob is None or cp is None or not offered: continue
            cb = float(BL.predict(coef.get(kind), [prob], [cp])[0]); obk, opr = max(offered, key=lambda x: O.decimal(x[1])); ev = cb * O.decimal(opr) - 1
            if not (curve["eligible"] and line >= Y.MIN_EDGE_LINE[kind] and ev >= 0.05): continue
            cand = dict(name=name, pid=y["pid"], kind=kind, line=line, model_p=prob, curve_p=cp, blend=cb, price=opr, book=obk, ev=ev, books=len(curve["books"]), rungs=curve["n_rungs"], role=y.get("role"))
            if opr >= Y.ALT_MAX_PRICE or prob > Y.ALT_MAX_RATIO * cp: found.append(dict(cand, cat="research")); continue
            if best is None or ev > best["ev"]: best = dict(cand, cat="alt")
        if best: found.append(best)
        for e in found:
            act = ymap.get((week, e["pid"], kind)); e["actual"] = act
            e["won"] = None if act is None else act >= e["line"]
            e["profit"] = None if act is None else (O.decimal(e["price"]) - 1 if e["won"] else -1.0)
            e["alt"] = e["cat"] != "main"
            edges.append(e)
    df = pd.DataFrame(edges)
    band = lambda pr: "shorter than -150" if pr <= -150 else "-150 to +150" if pr < 150 else "+150 to +300" if pr < 300 else "longer than +300"
    if len(df): df["band"] = df.price.apply(band)
    line = lambda tag, g: print(f"   {tag:34} n {len(g):3} | {int(g.won.sum())}-{int((~g.won.astype(bool)).sum())} | units {g.profit.sum():+6.1f} | expected {g.ev.sum():+6.1f} | avg EV {g.ev.mean()*100:5.1f}% | avg price {g.price.mean():+.0f}") if len(g) else print(f"   {tag:34} n   0")
    g = df[df.won.notna()] if len(df) else df
    print(f"pull {st} | {len(slate.get('yards', []))} player-stats, {n_curve} with an eligible curve, {n_dropped} quotes dropped by the cross-check | "
          f"main-line edges {int((df.cat == 'main').sum()) if len(df) else 0}, alt-rung edges {int((df.cat == 'alt').sum()) if len(df) else 0}, research candidates {int((df.cat == 'research').sum()) if len(df) else 0} (graded {len(g)})")
    if len(g):
        line("main line", g[g.cat == "main"]); line("alt rungs (flagged)", g[g.cat == "alt"])
        for b in ("shorter than -150", "-150 to +150", "+150 to +300", "longer than +300"): line(f"   alt flagged · {b}", g[(g.cat == "alt") & (g.band == b)])
        line("research (not flagged)", g[g.cat == "research"])
        for b in ("shorter than -150", "-150 to +150", "+150 to +300", "longer than +300"): line(f"   research · {b}", g[(g.cat == "research") & (g.band == b)])
        if "--list" in sys.argv:
            for r in g[g.cat != "research"].sort_values("ev", ascending=False).itertuples():
                print(f"   {r.name:22} Over {r.line:5g} {r.kind:4} {r.cat:4} | model {r.model_p:.2f} curve {r.curve_p:.2f} blend {r.blend:.2f} | {r.price:+d} {r.book:9} EV {r.ev*100:+.0f}% | actual {r.actual} {'W' if r.won else 'L'}")
    return df


if __name__ == "__main__":
    a = sys.argv[1:]; g = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    main(int(g("--season", 2026)), int(g("--week", 4)), g("--pull"))
