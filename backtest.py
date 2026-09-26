"""Walk-forward backtest over 2025 weeks 4–18.

    python backtest.py 0.35                       # run the model (REC_SLOPE=0.35), write bt2025_0.35.parquet + backtest_2025.json
    python backtest.py --from bt2025_0.35.parquet # only rebuild backtest_2025.json from a saved run
    python backtest.py 0.35 --tag old --set PRIOR_K=0 INFLATE_POOL=1 SNAP_CAP=0   # ablation: writes bt2025_old.parquet only
    python backtest.py 0.35 --active touch        # older regime: active = players who got a touch that week (leaks participation)
backtest_2025.json (Brier, constant-rate baseline, 5-point calibration buckets) is committed and read by results.py.
Depth charts and snap counts for 2025 come from data/dc_hist.parquet, data/snaps_hist.parquet, data/rosters_hist.parquet
(nflreadpy load_depth_charts / load_snap_counts / load_rosters_weekly for [2024, 2025]).
"""
import sys, os, json, re, numpy as np, pandas as pd, model as M
from results import calib_buckets
from priors import depth_for_week


LIMITS = {"QB": 2, "RB": 3, "WR": 6, "TE": 4}      # same depth-chart cut as run_week.py


def depth_active(depth, snaps_week):
    """Production-like active set: depth-chart top-N per position who saw at least one offensive snap that week
    (the snap check stands in for the injury report). Leaks far less than 'players who got a touch'."""
    played = {pid for pid, pct in snaps_week.items() if pct > 0}
    out = {}
    for pid, (tm, pos, rk) in depth.items():
        pos = M.POS_MAP.get(pos, pos)
        if pos in LIMITS and rk <= LIMITS[pos] and pid in played:
            out.setdefault(tm, set()).add(pid)
    return out


def week_snaps(season, wk):
    """pid -> offensive snap % in this week's game (for the depth-based active set)."""
    sn = pd.read_parquet("data/snaps_hist.parquet")
    sn = sn[(sn.season == season) & (sn.week == wk) & (sn.game_type == "REG")]
    ro = pd.read_parquet("data/rosters_hist.parquet").dropna(subset=["pfr_id"]).drop_duplicates("pfr_id", keep="last")
    sn = sn.assign(gsis_id=sn.pfr_player_id.map(ro.set_index("pfr_id").gsis_id)).dropna(subset=["gsis_id"])
    return sn.groupby("gsis_id").offense_pct.max().to_dict()


def week_inputs(season, wk, sched):
    """depth: pid -> (pos, rank); snaps: pid -> mean offensive snap % over the last 3 games before this week."""
    depth, snaps = None, None
    if os.path.exists("data/dc_hist.parquet"):
        dc = pd.read_parquet("data/dc_hist.parquet")
        depth = {pid: (pos, rk) for pid, (tm, pos, rk) in depth_for_week(dc, sched, season, wk).items()}
    if os.path.exists("data/snaps_hist.parquet") and os.path.exists("data/rosters_hist.parquet"):
        sn = pd.read_parquet("data/snaps_hist.parquet")
        sn = sn[(sn.season == season) & (sn.week < wk) & (sn.game_type == "REG")]
        ro = pd.read_parquet("data/rosters_hist.parquet").dropna(subset=["pfr_id"]).drop_duplicates("pfr_id", keep="last")
        sn = sn.assign(gsis_id=sn.pfr_player_id.map(ro.set_index("pfr_id").gsis_id)).dropna(subset=["gsis_id"])
        snaps = sn.sort_values("week").groupby("gsis_id").tail(3).groupby("gsis_id").offense_pct.mean().to_dict()
    return depth, snaps


def summary(r, slope, fn="backtest_2025.json", active_mode="depth"):
    p, y = r.p_any.astype(float), r.y_any.astype(float)
    brier = float(((p - y) ** 2).mean())
    baseline = float(((y.mean() - y) ** 2).mean())          # predict the overall hit rate for everyone
    regime = "depth-chart top-N who dressed" if active_mode == "depth" else "players who got a touch"
    out = dict(note=f"Walk-forward, 2025 weeks {int(r.week.min())}–{int(r.week.max())}, REC_SLOPE {slope}, active = {regime}",
               rec_slope=slope, props=int(len(r)), weeks=int(r.week.nunique()), hit_rate=round(float(y.mean()), 4),
               brier=round(brier, 4), baseline=round(baseline, 4), baseline_note="constant hit-rate prediction",
               calibration=calib_buckets(p, y))
    json.dump(out, open(fn, "w"), indent=1)
    print(f"wrote {fn}: {len(r)} props, brier {brier:.4f} vs baseline {baseline:.4f}")
    return out


if __name__ == "__main__":
    if sys.argv[1:2] == ["--from"]:
        m = re.search(r"bt2025_([\d.]+)\.parquet", sys.argv[2])
        summary(pd.read_parquet(sys.argv[2]), float(m.group(1)) if m else None)
        sys.exit()
    args = sys.argv[1:]
    tag = args[args.index("--tag") + 1] if "--tag" in args else None
    active_mode = args[args.index("--active") + 1] if "--active" in args else "depth"   # depth (production-like) | touch (players who got a touch: leaks participation)
    if "--set" in args:
        for kv in args[args.index("--set") + 1:]:
            if kv.startswith("--") or "=" not in kv: break
            k, v = kv.split("="); setattr(M, k, type(getattr(M, k))(float(v)) if isinstance(getattr(M, k), (int, float)) else v)
            print("set", k, "=", getattr(M, k))
    if args and not args[0].startswith("--"): M.REC_SLOPE = float(args[0])
    p, s = M.load()
    res = []
    for wk in range(4, 19):
        act = p[(p.season == 2025) & (p.week == wk)]
        active = {}
        for t, g in act.groupby('posteam'):
            active[t] = set(g.rusher_player_id.dropna()) | set(g.receiver_player_id.dropna())
        qbo = {t: (g.passer_player_id.value_counts().index[0], '') for t, g in act[act.pass_attempt == 1].groupby('posteam')}
        depth, snaps = week_inputs(2025, wk, s)
        if active_mode == "depth":
            dc = pd.read_parquet("data/dc_hist.parquet")
            active = depth_active(depth_for_week(dc, s, 2025, wk), week_snaps(2025, wk))
        teams, pl, qbs, sh = M.build_slate(p, s, 2025, wk, active=active, qb_override=qbo, depth=depth, snaps=snaps)
        sim = M.simulate(teams, pl, qbs, n=8000)
        out = M.summarize(teams, pl, qbs, sim, {})
        # actual
        sc = act[(act.touchdown == 1) & (act.td_player_id.notna())]
        ytd = sc.groupby('td_player_id').size()
        first = sc.sort_values(['game_id', 'play_id']).groupby('game_id').first()
        firsttd = set(first.td_player_id)
        # first TD incl. non-offense: use all TD plays
        out['y_any'] = out.pid.map(ytd).fillna(0) > 0
        out['y_first'] = out.pid.isin(firsttd)
        out['week'] = wk
        res.append(out)

    r = pd.concat(res)
    if tag:
        r.to_parquet(f'bt2025_{tag}.parquet'); print("wrote", f'bt2025_{tag}.parquet', "(no backtest_2025.json for tagged runs)")
    else:
        r.to_parquet(f'bt2025_{M.REC_SLOPE}.parquet'); summary(r, M.REC_SLOPE, active_mode=active_mode)
