"""Fast walk-forward evaluation over 2025 (depth-chart active set) for hyperparameter and feature tests.

    python search.py --weeks 4-11 --n 3000 --grid --out runs/grid_pick.csv [--shard 0/3]
    python search.py --weeks 12-18 --params '[{"DECAY":0.9,"PRIOR_K":3}, {...}]' --out runs/confirm.csv
    python search.py --weeks 4-18 --params '[{}]' --tag base          # baseline with current model.py values
    python search.py --pre '{"QB_OWN_XTD": false}' ...               # settings that must apply before the cache is built

Caches the hyperparameter-free work per week (prep_week, depth, snaps, active) once, then evaluates each
parameter set by setting model attributes, building the slate and simulating. Writes one row per parameter set:
brier, top-15 hits/expected per week, 15-50% calibration buckets, plus QB-only calibration.
"""
import sys, json, itertools, os, time, numpy as np, pandas as pd, model as M
from priors import depth_for_week
from results import calib_buckets
import backtest as B

GRID = dict(DECAY=[0.80, 0.85, 0.90, 0.95], PRIOR_SEASON_W=[0.3, 0.55, 0.8], PRIOR_K=[1.5, 3.0, 5.0],
            REC_SLOPE=[0.25, 0.35, 0.45, 0.60])


def prep(p, s, weeks, season=2025):
    dc = pd.read_parquet("data/dc_hist.parquet")
    out = {}
    for wk in weeks:
        d = depth_for_week(dc, s, season, wk)
        depth = {pid: (pos, rk) for pid, (tm, pos, rk) in d.items()}
        _, snaps = B.week_inputs(season, wk, s)
        active = B.depth_active(d, B.week_snaps(season, wk))
        act = p[(p.season == season) & (p.week == wk)]
        qbo = {t: (g.passer_player_id.value_counts().index[0], '') for t, g in act[act.pass_attempt == 1].groupby('posteam')}
        sc = act[(act.touchdown == 1) & act.td_player_id.notna()]
        out[wk] = dict(cache=M.prep_week(p, s, season, wk), depth=depth, snaps=snaps, active=active, qbo=qbo,
                       ytd=set(sc.td_player_id), qb_ids=set(q[0] for q in qbo.values()))
    return out


def evaluate(p, s, pre, params, n=3000, season=2025, seed=7):
    saved = {k: getattr(M, k) for k in params}
    for k, v in params.items():
        setattr(M, k, type(getattr(M, k))(v) if isinstance(getattr(M, k), (int, float)) and not isinstance(getattr(M, k), bool) else v)
    M.RNG = np.random.default_rng(seed)                 # same draws for every parameter set
    rows = []
    try:
        for wk, w in pre.items():
            teams, pl, qbs, sh = M.build_slate(p, s, season, wk, active=w["active"], qb_override=w["qbo"],
                                               depth=w["depth"], snaps=w["snaps"], cache=w["cache"])
            sim = M.simulate(teams, pl, qbs, n=n)
            out = M.summarize(teams, pl, qbs, sim, {})
            out["y_any"] = out.pid.isin(w["ytd"]); out["week"] = wk
            out["is_qb"] = out.pid.isin(w["qb_ids"])
            qt = sh.drop_duplicates("pid").set_index("pid").get("qb_type")
            out["qb_type"] = out.pid.map(qt) if qt is not None else None
            rows.append(out)
    finally:
        for k, v in saved.items(): setattr(M, k, v)
    return pd.concat(rows)


def score(r):
    p, y = r.p_any.astype(float), r.y_any.astype(float)
    top = r.sort_values("p_any", ascending=False).groupby("week").head(15)
    b = {c["bucket"]: c for c in calib_buckets(p, y)}
    row = dict(props=len(r), brier=round(float(((p - y) ** 2).mean()), 5),
               sum_p=round(float(p.sum() / r.week.nunique()), 2), sum_y=round(float(y.sum() / r.week.nunique()), 2),
               top15_hit=round(float(top.y_any.mean() * 15), 3), top15_exp=round(float(top.p_any.mean() * 15), 3))
    for lo in range(15, 50, 5):
        c = b.get(f"{lo}–{lo + 5}%")
        row[f"b{lo}"] = f"{c['pred'] * 100:.1f}/{c['act'] * 100:.1f}/{c['n']}" if c else "-"
    q = r[r.is_qb]
    if len(q):
        row["qb_n"] = len(q); row["qb_pred"] = round(float(q.p_any.mean()), 4); row["qb_act"] = round(float(q.y_any.mean()), 4)
        row["qb_brier"] = round(float(((q.p_any - q.y_any) ** 2).mean()), 5)
        for t in ("mobile", "pocket"):
            qq = q[q.qb_type == t]
            if len(qq): row[f"qb_{t}"] = f"{qq.p_any.mean() * 100:.1f}/{qq.y_any.mean() * 100:.1f}/{len(qq)}"
    return row


def main():
    args = sys.argv[1:]
    get = lambda k, d=None: args[args.index(k) + 1] if k in args else d
    lo, hi = map(int, get("--weeks", "4-18").split("-")); weeks = list(range(lo, hi + 1))
    n = int(get("--n", 3000)); out = get("--out"); tag = get("--tag")
    if "--grid" in args:
        keys = list(GRID); sets = [dict(zip(keys, v)) for v in itertools.product(*GRID.values())]
    else:
        sets = json.loads(get("--params", "[{}]"))
    if "--shard" in args:
        i, k = map(int, get("--shard").split("/")); sets = sets[i::k]
    for k, v in json.loads(get("--pre", "{}")).items():        # applied BEFORE prep: for things baked into the cache (xTD tables)
        setattr(M, k, v); print("pre-set", k, "=", v)
    p, s = M.load()
    t0 = time.time(); pre = prep(p, s, weeks); print(f"prepared {len(weeks)} weeks in {time.time() - t0:.0f}s; {len(sets)} parameter sets", flush=True)
    rows = []
    for i, ps in enumerate(sets):
        t0 = time.time(); r = evaluate(p, s, pre, ps, n=n)
        row = dict(**{k: ps[k] for k in ps}, **score(r)); rows.append(row)
        print(f"[{i + 1}/{len(sets)}] {ps} brier={row['brier']} top15={row['top15_hit']}/{row['top15_exp']} ({time.time() - t0:.0f}s)", flush=True)
        if tag: r.to_parquet(f"bt2025_{tag}.parquet")
        if out:
            os.makedirs(os.path.dirname(out) or ".", exist_ok=True); pd.DataFrame(rows).to_csv(out, index=False)
    pd.set_option("display.width", 250); print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
