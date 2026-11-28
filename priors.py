"""Estimate baseline TD-opportunity shares by position and depth-chart rank (the prior in model.shares).

    python priors.py            # prints the SHARE_PRIOR table from 2024-25 regular-season data

For every listed skill player in every team-game, share = his rush (or rec) xTD / team rush (or rec) xTD that game,
counting zero when he had no opportunities. Averaged by (kind, position, rank bucket). Needs data/pbp.parquet,
data/sched.parquet and data/dc_hist.parquet (nflreadpy depth charts for 2024-25).
"""
import pandas as pd, model as M

RANK_CAP = {"RB": 3, "WR": 4, "TE": 2, "QB": 2}          # ranks at/above the cap share one bucket ("3+" etc.)
POS_MAP = {"FB": "RB", "HB": "RB"}


def bucket(pos, rank):
    pos = POS_MAP.get(pos, pos)
    if pos not in RANK_CAP or rank is None or not rank == rank:
        return None
    return pos, int(min(rank, RANK_CAP[pos]))


def depth_for_week(dc, sched, season, week):
    """pid -> (team, pos, rank) as listed before the week's first game."""
    if season >= 2025:                                    # dt-stamped snapshots
        first = sched[(sched.season == season) & (sched.week == week)].gameday.min()
        snap = dc[dc.dt.notna() & (dc.dt < f"{first}T00:00:00Z")]
        if snap.empty:
            return {}
        snap = snap[snap.dt == snap.dt.max()]
        snap = snap[snap.pos_abb.isin(list(RANK_CAP) + list(POS_MAP))].dropna(subset=["gsis_id"])
        snap = snap.sort_values("pos_rank").drop_duplicates("gsis_id")
        return {r.gsis_id: (r.team, r.pos_abb, r.pos_rank) for r in snap.itertuples()}
    wk = dc[(dc.season == season) & (dc.week == week) & dc.position.isin(list(RANK_CAP) + list(POS_MAP))].copy()
    wk["rank"] = pd.to_numeric(wk.depth_team, errors="coerce")
    wk = wk.dropna(subset=["gsis_id", "rank"]).sort_values("rank").drop_duplicates("gsis_id")
    return {r.gsis_id: (r.club_code, r.position, r.rank) for r in wk.itertuples()}


def main():
    p, s = M.load()
    dc = pd.read_parquet("data/dc_hist.parquet")
    ru_b, tg_b = M.xtd_tables(p[p.season <= 2023])
    rows = []
    for season in (2024, 2025):
        px = M.add_xtd(p[p.season == season], ru_b, tg_b)
        for week in sorted(px.week.unique()):
            depth = depth_for_week(dc, s, season, week)
            if not depth:
                continue
            g = px[px.week == week]
            for kind, idc in [("rush", "rusher_player_id"), ("rec", "receiver_player_id")]:
                col = f"{kind}_xtd"
                team = g.groupby("posteam")[col].sum()
                player = g.groupby(["posteam", idc])[col].sum()
                for t, tot in team.items():
                    if tot <= 0:
                        continue
                    for pid, (tm, pos, rk) in depth.items():          # every listed player counts, zero if unused
                        b = bucket(pos, rk)
                        if tm != t or not b:
                            continue
                        rows.append(dict(season=season, week=week, kind=kind, pos=b[0], rank=b[1],
                                         share=float(player.get((t, pid), 0.0)) / float(tot)))
    d = pd.DataFrame(rows)
    tab = d.groupby(["kind", "pos", "rank"]).share.agg(["mean", "median", "size"]).round(4)
    print(tab.to_string())
    print("\nby season (mean):"); print(d.groupby(["kind", "pos", "rank", "season"]).share.mean().unstack().round(3).to_string())
    prior = {(k, pos, rk): round(float(v), 3) for (k, pos, rk), v in tab["mean"].items()}
    print("\nSHARE_PRIOR =", prior)


if __name__ == "__main__":
    main()
