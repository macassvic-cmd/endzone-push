"""Rebuild pre-game slates for already-played weeks (walk-forward, no odds) so Results has history.
    python backfill.py 2026 1 2"""
import sys, json, numpy as np, pandas as pd, model as M

season, weeks = int(sys.argv[1]), [int(w) for w in sys.argv[2:]]
p, s = M.load()
names = pd.read_parquet("data/rosters.parquet").drop_duplicates("gsis_id", keep="last").set_index("gsis_id")
for wk in weeks:
    act = p[(p.season == season) & (p.week == wk) & (p.season_type == "REG")]
    active = {t: set(g.rusher_player_id.dropna()) | set(g.receiver_player_id.dropna()) for t, g in act.groupby("posteam")}
    qbo = {t: (g.passer_player_id.value_counts().index[0], g.passer_player_name.value_counts().index[0])
           for t, g in act[act.pass_attempt == 1].groupby("posteam")}
    teams, pl, qbs, sh = M.build_slate(p, s, season, wk, active=active, qb_override=qbo)
    sim = M.simulate(teams, pl, qbs, n=30000)
    nm = {i: names.at[i, "full_name"] for i in set(pl.pid) if i in names.index}
    df = M.summarize(teams, pl, qbs, sim, nm)
    df["pos"] = df.pid.map(lambda i: names.at[i, "position"] if i in names.index else None)
    games = [dict(game_id=g, home=teams[t]["opp"] if not teams[t]["home"] else t,
                  away=t if not teams[t]["home"] else teams[t]["opp"]) for t, g in
             {t: v["game_id"] for t, v in teams.items() if v["home"]}.items()]
    out = dict(season=season, week=wk, backfill=True, players=df.round(4).replace({np.nan: None}).to_dict("records"),
               games=games, edges=[])
    json.dump(out, open(f"slate_{season}_w{wk}.json", "w"), default=lambda o: o.item() if hasattr(o, "item") else None)
    print("backfilled", season, wk, len(df))
