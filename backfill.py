"""Rebuild pre-game slates for already-played weeks with the current model, walk-forward, using only what was known
before each week's first game and the same active-player rules as run_week.py (depth-chart top-N minus Out/Doubtful
and inactive-roster players, snap share over the previous 3 games, rookies' draft buckets, redistribution).
    python backfill.py 2026 1 2
Lines are the nflverse schedule lines (closing, not a live pull); no odds, no weather. Slates carry backfill=True."""
import sys, json, numpy as np, pandas as pd, model as M

DATA = "data"
LIMITS = {"QB": 2, "RB": 3, "WR": 6, "TE": 4}

season, weeks = int(sys.argv[1]), [int(w) for w in sys.argv[2:]]
p, s = M.load()
s["kick"] = pd.to_datetime(s.gameday + " " + s.gametime.fillna("13:00"))
dc_all = pd.read_parquet(f"{DATA}/dc.parquet")
inj_all = pd.read_parquet(f"{DATA}/inj.parquet")
ros_all = pd.read_parquet(f"{DATA}/rosters.parquet")
snaps_all = pd.read_parquet(f"{DATA}/snaps.parquet")
names_all = ros_all.drop_duplicates("gsis_id", keep="last").set_index("gsis_id")

for wk in weeks:
    sched = s[(s.season == season) & (s.week == wk) & (s.game_type == "REG")]
    first = sched.gameday.min()
    dc = dc_all[dc_all.dt < f"{first}T00:00:00Z"]; dc = dc[dc.dt == dc.dt.max()]          # last depth chart before the week
    inj = inj_all[inj_all.week == wk]
    out_ids = set(inj[inj.report_status.isin(["Out", "Doubtful"])].gsis_id)
    q_ids = set(inj[inj.report_status == "Questionable"].gsis_id)
    ros = ros_all[ros_all.week == wk] if (ros_all.week == wk).any() else ros_all[ros_all.week == ros_all.week.max()]
    inactive = set(ros[ros.status.isin(["RES", "INA", "CUT", "RET", "EXE"])].gsis_id)
    active, qbo, pos = {}, {}, {}
    for t, g in dc[dc.pos_abb.isin(LIMITS)].groupby("team"):
        g = g[g.pos_rank <= g.pos_abb.map(LIMITS)]
        ids = set(g.gsis_id) - out_ids - inactive
        for _, r in g.iterrows(): pos[r.gsis_id] = r.pos_abb
        qb = g[(g.pos_abb == "QB") & g.gsis_id.isin(ids)].sort_values("pos_rank")
        for side in ["home", "away"]:
            row = sched[sched[f"{side}_team"] == t]
            if len(row):
                m = qb[qb.player_name == row[f"{side}_qb_name"].iloc[0]]
                if len(m): qb = m
        if len(qb): qbo[t] = (qb.gsis_id.iloc[0], qb.player_name.iloc[0])
        active[t] = {i for i in ids if pos.get(i) != "QB" or i == qbo.get(t, (None,))[0]}
    depth = {r.gsis_id: (r.pos_abb, r.pos_rank) for r in dc.dropna(subset=["gsis_id"]).sort_values("pos_rank").drop_duplicates("gsis_id").itertuples()}
    sn = snaps_all[(snaps_all.season == season) & (snaps_all.game_type == "REG") & (snaps_all.week < wk)]
    idmap = ros.dropna(subset=["pfr_id"]).set_index("pfr_id").gsis_id.to_dict()
    sn = sn.assign(gsis_id=sn.pfr_player_id.map(idmap)).dropna(subset=["gsis_id"])
    snaps3 = sn.sort_values("week").groupby("gsis_id").tail(3).groupby("gsis_id").offense_pct.mean().to_dict() if len(sn) else None
    rk = ros[ros.rookie_year == season].drop_duplicates("gsis_id")
    rookies = {g: M.draft_bucket(d) for g, d in zip(rk.gsis_id, rk.draft_number)}
    # only games of this week, with schedule lines
    s_wk = s[~((s.season == season) & (s.week > wk))]
    teams, pl, qbs, sh = M.build_slate(p, s_wk, season, wk, active=active, qb_override=qbo, depth=depth, snaps=snaps3, rookies=rookies)
    pl.loc[pl.pid.isin(q_ids), "share"] *= 0.85
    sim = M.simulate(teams, pl, qbs, n=60000)
    nm = {i: names_all.at[i, "full_name"] for i in set(pl.pid) | {q[0] for q in qbs.values() if q[0]} if i in names_all.index}
    df = M.summarize(teams, pl, qbs, sim, nm)
    df["pos"] = df.pid.map(pos).fillna(df.pid.map(lambda i: names_all.at[i, "position"] if i in names_all.index else None))
    df["questionable"] = df.pid.isin(q_ids)
    df["snap_3"] = df.pid.map(snaps3) if snaps3 else np.nan
    def role_of(pid, snap3):
        d = depth.get(pid); ps = M.POS_MAP.get(d[0], d[0]) if d else None
        top = d is not None and ps in M.RANK_CAP and d[1] <= 1
        deep = d is None or ps not in M.RANK_CAP or d[1] >= M.RANK_CAP[ps]
        known = snap3 is not None and snap3 == snap3
        return "Starter" if top or (known and snap3 >= 0.60) else "Bench" if deep or (known and snap3 < 0.30) else "Rotational"
    df["role"] = [role_of(r.pid, r.snap_3) for r in df.itertuples()]
    games = [dict(game_id=g.game_id, away=g.away_team, home=g.home_team, kickoff=g.gameday + " " + str(g.gametime),
                  lines="nflverse") for _, g in sched.iterrows() if g.home_team in teams]
    out = dict(season=season, week=wk, backfill=True, generated=pd.Timestamp.now(tz="America/Los_Angeles").strftime("%a %b %d %I:%M %p PT"),
               players=df.round(4).replace({np.nan: None}).to_dict("records"), games=games, edges=[])
    json.dump(out, open(f"slate_{season}_w{wk}.json", "w"), default=lambda o: o.item() if hasattr(o, "item") else None)
    print(f"backfilled {season} week {wk}: {len(games)} games, {len(df)} players, active/team {np.mean([len(v) for v in active.values()]):.1f}, roles {df.role.value_counts().to_dict()}")
