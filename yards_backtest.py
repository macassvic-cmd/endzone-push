"""Yard-ladder backtest gate: 2024 + 2025 walk-forward, weeks 4-18, depth-chart regime.

    python yards_backtest.py [--seasons 2024,2025] [--weeks 4-18] [--n 3000] [--dist normal|gamma] [--tag name]

Per player-game: simulated mean / median / P(>= rung) for the default ladder vs actual yards from play-by-play, and a
season-average baseline (mean yards over the player's previous games this season, else last season's mean).
Reports: ladder calibration in 5-point buckets with 1.96*sqrt(p(1-p)/n) ranges (pooled over rungs and per kind),
median absolute error vs the baseline, by kind. Writes runs/yards_bt_<tag>.parquet. `python -c "import yards_backtest as B, pandas as pd; print(B.report(pd.read_parquet('runs/yards_bt_gamma.parquet')))"` re-reports.
"""
import sys, numpy as np, pandas as pd, model as M, search as S, yards as Y


def actual_yards(p, season):
    g = p[p.season == season]
    ru = g[(g.rush_attempt == 1) & (g.qb_kneel != 1)].groupby(["week", "rusher_player_id"]).rushing_yards.sum().rename("y").reset_index().rename(columns={"rusher_player_id": "pid"}).assign(kind="rush")
    rc = g[(g.pass_attempt == 1) & (g.sack == 0) & g.receiver_player_id.notna()].groupby(["week", "receiver_player_id"]).receiving_yards.apply(lambda x: x.fillna(0).sum()).rename("y").reset_index().rename(columns={"receiver_player_id": "pid"}).assign(kind="rec")
    qa = g[(g.pass_attempt == 1) & (g.sack == 0) & g.passer_player_id.notna()].groupby(["week", "passer_player_id"]).passing_yards.apply(lambda x: x.fillna(0).sum()).rename("y").reset_index().rename(columns={"passer_player_id": "pid"}).assign(kind="pass")
    return pd.concat([ru, rc, qa])


def run(seasons, weeks, n, tag):
    p, s = M.load(); rows = []
    for season in seasons:
        act = actual_yards(p, season); prev = actual_yards(p, season - 1).groupby(["pid", "kind"]).y.mean().to_dict()
        pre = S.prep(p, s, weeks, season)
        for wk in weeks:
            w = pre[wk]; cache = w["cache"]; yc = Y.prep(cache["past"], season)
            teams, pl, qbs, sh = M.build_slate(p, s, season, wk, active=w["active"], qb_override=w["qbo"], depth=w["depth"], snaps=w["snaps"], cache=cache, rookies=pre["_rookies"])
            tv, plv, qv = Y.build(p, s, season, wk, w["active"], w["qbo"], w["depth"], w["snaps"], pre["_rookies"], cache, yc)
            M.RNG = np.random.default_rng(7); sim = M.simulate(teams, pl, qbs, n=n)
            pos = {pid: (w["depth"][pid][0] if pid in w["depth"] else "WR") for pid in set(plv.pid) | {q[0] for q in qbs.values() if q[0]}}
            yd = Y.simulate(teams, plv, qbs, yc, sim, lambda pid: M.POS_MAP.get(pos.get(pid, "WR"), pos.get(pid, "WR")))
            a = act[act.week == wk].set_index(["pid", "kind"]).y.to_dict()
            base_src = act[act.week < wk].groupby(["pid", "kind"]).y.mean().to_dict()
            for r in Y.summarize(yd):
                key = (r["pid"], r["kind"]); y = float(a.get(key, 0.0))
                base = base_src.get(key, prev.get(key, np.nan))
                rows.append(dict(season=season, week=wk, pid=r["pid"], kind=r["kind"], mean=r["mean"], median=r["median"], sd=r["sd"], y=y, base=base,
                                 **{f"p{k}": v for k, v in r["ladder"].items()}))
            print(f"{season} w{wk}: {len(yd)} player-markets", flush=True)
    df = pd.DataFrame(rows); df.to_parquet(f"runs/yards_bt_{tag}.parquet"); return df


def report(df):
    out = []
    for kind in ("rush", "rec", "pass"):
        d = df[df.kind == kind]; rungs = Y.DEFAULT_LADDER[kind]
        pairs = [(d[f"p{r}"].values, (d.y.values >= r).astype(float)) for r in rungs if f"p{r}" in d]
        P = np.concatenate([a for a, _ in pairs]); Yv = np.concatenate([b for _, b in pairs])
        cal = []
        for lo in np.arange(0, 1.0, 0.05):
            m = (P >= lo) & (P < lo + 0.05); nn = int(m.sum())
            if nn >= 30:
                act = Yv[m].mean(); cal.append(f"{int(round(lo*100))}-{int(round(lo*100))+5}:{P[m].mean()*100:.0f}/{act*100:.0f}±{196*np.sqrt(act*(1-act)/nn):.0f}(n{nn})")
        dd = d[d.base.notna()]
        mae_m = np.abs(dd["median"] - dd.y).mean(); mae_b = np.abs(dd.base - dd.y).mean(); mae_mean = np.abs(dd["mean"] - dd.y).mean()
        within = sum(1 for c in cal if abs(int(c.split(":")[1].split("/")[0]) - int(c.split("/")[1].split("±")[0])) <= int(c.split("±")[1].split("(")[0]))
        out.append(f"{kind:5} n{len(d)} | MAE median {mae_m:.1f} vs season-avg baseline {mae_b:.1f} (mean {mae_mean:.1f}) | ladder buckets within range {within} of {len(cal)}\n      {' '.join(cal)}")
    return "\n".join(out)


if __name__ == "__main__":
    a = sys.argv[1:]; get = lambda k, d: a[a.index(k) + 1] if k in a else d
    seasons = [int(x) for x in get("--seasons", "2024,2025").split(",")]; lo, hi = map(int, get("--weeks", "4-18").split("-"))
    Y.DIST = get("--dist", Y.DIST); tag = get("--tag", Y.DIST)
    df = run(seasons, list(range(lo, hi + 1)), int(get("--n", 3000)), tag)
    print(report(df))
