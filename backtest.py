"""Walk-forward backtest over 2025 weeks 4–18.

    python backtest.py 0.35                       # run the model (REC_SLOPE=0.35), write bt2025_0.35.parquet + backtest_2025.json
    python backtest.py --from bt2025_0.35.parquet # only rebuild backtest_2025.json from a saved run
backtest_2025.json (Brier, constant-rate baseline, 5-point calibration buckets) is committed and read by results.py.
"""
import sys, json, re, numpy as np, pandas as pd, model as M
from results import calib_buckets


def summary(r, slope, fn="backtest_2025.json"):
    p, y = r.p_any.astype(float), r.y_any.astype(float)
    brier = float(((p - y) ** 2).mean())
    baseline = float(((y.mean() - y) ** 2).mean())          # predict the overall hit rate for everyone
    out = dict(note=f"Walk-forward, 2025 weeks {int(r.week.min())}–{int(r.week.max())}, REC_SLOPE {slope}",
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
    if len(sys.argv) > 1: M.REC_SLOPE = float(sys.argv[1])
    p, s = M.load()
    res = []
    for wk in range(4, 19):
        act = p[(p.season == 2025) & (p.week == wk)]
        active = {}
        for t, g in act.groupby('posteam'):
            active[t] = set(g.rusher_player_id.dropna()) | set(g.receiver_player_id.dropna())
        qbo = {t: (g.passer_player_id.value_counts().index[0], '') for t, g in act[act.pass_attempt == 1].groupby('posteam')}
        teams, pl, qbs, sh = M.build_slate(p, s, 2025, wk, active=active, qb_override=qbo)
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

    r = pd.concat(res); r.to_parquet(f'bt2025_{M.REC_SLOPE}.parquet')
    summary(r, M.REC_SLOPE)
