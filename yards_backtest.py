"""Yard-ladder backtest gate: 2024 + 2025 walk-forward, weeks 4-18, depth-chart regime.

    python yards_backtest.py [--seasons 2024,2025] [--weeks 4-18] [--n 3000] [--dist normal|gamma] [--tag name]

Per player-game: simulated mean / median / P(>= rung) for the default ladder vs actual yards from play-by-play, and a
season-average baseline (mean yards over the player's previous games this season, else last season's mean).
Reports: ladder calibration in 5-point buckets with 1.96*sqrt(p(1-p)/n) ranges (pooled over rungs and per kind),
median absolute error vs the baseline, by kind. Writes runs/yards_bt_<tag>.parquet. `python -c "import yards_backtest as B, pandas as pd; print(B.report(pd.read_parquet('runs/yards_bt_gamma.parquet')))"` re-reports.
"""
import sys, numpy as np, pandas as pd, model as M, search as S, yards as Y


STAT_RUNGS = {"recn": [2, 3, 4, 5, 6, 7, 8], "fpts": [5, 8, 10, 12, 15, 18, 20, 25], "att": [25, 30, 35, 40]}   # single-leg slip stats


def actual_stats(p, season):
    """Per (week, pid): receptions, rec / rush / pass yards, pass attempts (sacks excluded), half-PPR fantasy points."""
    g = p[p.season == season]
    tg = g[(g.pass_attempt == 1) & (g.sack == 0) & g.receiver_player_id.notna()]
    rc = tg.groupby(["week", "receiver_player_id"]).agg(rec=("complete_pass", "sum"), rec_yds=("receiving_yards", lambda x: x.fillna(0).sum()), rec_td=("pass_touchdown", "sum"))
    ru = g[(g.rush_attempt == 1) & (g.qb_kneel != 1)].groupby(["week", "rusher_player_id"]).agg(rush_yds=("rushing_yards", "sum"), rush_td=("rush_touchdown", "sum"))
    qa = g[(g.pass_attempt == 1) & (g.sack == 0) & g.passer_player_id.notna()].groupby(["week", "passer_player_id"]).agg(pass_att=("pass_attempt", "sum"), pass_yds=("passing_yards", lambda x: x.fillna(0).sum()), pass_td=("pass_touchdown", "sum"), ints=("interception", "sum"))
    fl = g[(g.fumble_lost == 1) & g.fumbled_1_player_id.notna()].groupby(["week", "fumbled_1_player_id"]).size().rename("fum")
    for d in (rc, ru, qa): d.index.names = ["week", "pid"]
    fl.index.names = ["week", "pid"]
    a = pd.concat([rc, ru, qa, fl], axis=1).fillna(0.0).reset_index()
    F = Y.FPTS
    a["fpts"] = F["rec"] * a.rec + F["yd"] * (a.rec_yds + a.rush_yds) + F["td"] * (a.rec_td + a.rush_td) + F["pass_yd"] * a.pass_yds + F["pass_td"] * a.pass_td + F["int"] * a.ints + F["fum"] * a.fum
    return a


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
        if STATS:   # single-leg slip stats as extra kinds: recn (receptions), fpts (half-PPR), att (pass attempts)
            sa, sp = actual_stats(p, season), actual_stats(p, season - 1)
            for kind, col in (("recn", "rec"), ("fpts", "fpts"), ("att", "pass_att")):
                act = pd.concat([act, sa[["week", "pid", col]].rename(columns={col: "y"}).assign(kind=kind)])
                prev.update({(k, kind): v for k, v in sp.groupby("pid")[col].mean().items()})
        pre = S.prep(p, s, weeks, season)
        for wk in weeks:
            w = pre[wk]; cache = w["cache"]; yc = Y.prep(cache["past"], season)
            teams, pl, qbs, sh = M.build_slate(p, s, season, wk, active=w["active"], qb_override=w["qbo"], depth=w["depth"], snaps=w["snaps"], cache=cache, rookies=pre["_rookies"])
            tv, plv, qv = Y.build(p, s, season, wk, w["active"], w["qbo"], w["depth"], w["snaps"], pre["_rookies"], cache, yc)
            M.RNG = np.random.default_rng(7); sim = M.simulate(teams, pl, qbs, n=n)
            pos = {pid: (w["depth"][pid][0] if pid in w["depth"] else "WR") for pid in set(plv.pid) | {q[0] for q in qbs.values() if q[0]}}
            posf = lambda pid: M.POS_MAP.get(pos.get(pid, "WR"), pos.get(pid, "WR"))
            yd = Y.simulate(teams, plv, qbs, yc, sim, posf)
            a = act[act.week == wk].set_index(["pid", "kind"]).y.to_dict()
            base_src = act[act.week < wk].groupby(["pid", "kind"]).y.mean().to_dict()
            rows_wk = Y.summarize(yd)
            if STATS:
                stv = Y.stats(yd, sim, qbs, posf)
                for pid, d in stv.items():
                    for kind, col in (("recn", "rec"), ("fpts", "fpts"), ("att", "pass_att")):
                        if col not in d: continue
                        v = d[col]
                        if kind == "att" and v.mean() < 5: continue
                        rows_wk.append(dict(pid=pid, kind=kind, mean=float(v.mean()), median=float(np.median(v)), sd=float(v.std()), p_zero=float((v <= 0).mean()),
                                            ladder={str(r): float((v > r - 0.5).mean()) for r in STAT_RUNGS[kind]}))   # P(stat >= rung) for integer lines; P(> rung - 0.5) for fantasy
            for r in rows_wk:
                key = (r["pid"], r["kind"]); y = float(a.get(key, 0.0))
                base = base_src.get(key, prev.get(key, np.nan))
                rows.append(dict(season=season, week=wk, pid=r["pid"], kind=r["kind"], mean=r["mean"], median=r["median"], sd=r["sd"], y=y, base=base,
                                 **{f"p{k}": v for k, v in r["ladder"].items()}))
            print(f"{season} w{wk}: {len(yd)} player-markets", flush=True)
    df = pd.DataFrame(rows); df.to_parquet(f"runs/yards_bt_{tag}.parquet"); return df


def report(df):
    out = []
    for kind in ("rush", "rec", "pass", "recn", "fpts", "att"):
        d = df[df.kind == kind]; rungs = Y.DEFAULT_LADDER.get(kind) or STAT_RUNGS[kind]
        if d.empty: continue
        pairs = [(d[f"p{r}"].dropna().values, (d.y.values[d[f"p{r}"].notna()] > r - 0.5).astype(float)) for r in rungs if f"p{r}" in d]
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


STATS = True   # include receptions / fantasy / pass attempts (--no-stats to skip)

if __name__ == "__main__":
    a = sys.argv[1:]; get = lambda k, d: a[a.index(k) + 1] if k in a else d
    STATS = "--no-stats" not in a
    seasons = [int(x) for x in get("--seasons", "2024,2025").split(",")]; lo, hi = map(int, get("--weeks", "4-18").split("-"))
    Y.DIST = get("--dist", Y.DIST); tag = get("--tag", Y.DIST)
    df = run(seasons, list(range(lo, hi + 1)), int(get("--n", 3000)), tag)
    print(report(df))
