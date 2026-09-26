"""Grade every saved slate against what actually happened. Writes results.json."""
import glob, json, numpy as np, pandas as pd

p = pd.read_parquet("data/pbp.parquet")
s = pd.read_parquet("data/sched.parquet")
final = set(s[s.home_score.notna()].game_id)
td = p[(p.touchdown == 1) & p.td_player_id.notna()]
off = td[(td.rush_touchdown == 1) | (td.pass_touchdown == 1)]
scored = off.groupby("game_id").td_player_id.apply(set).to_dict()
first = td.sort_values(["game_id", "play_id"]).groupby("game_id").first()
first_scorer = first.td_player_id.to_dict()
first_name = first.td_player_name.to_dict()

weeks, players_all, bets = [], [], []
for fn in sorted(glob.glob("slate_*_w*.json")):
    d = json.load(open(fn))
    pl = pd.DataFrame(d["players"])
    if "pid" not in pl or pl.empty:
        continue
    pl = pl[pl.game_id.isin(final)].copy()
    if pl.empty:
        continue
    pl["hit"] = [r.pid in scored.get(r.game_id, set()) for r in pl.itertuples()]
    pl["first_hit"] = [first_scorer.get(r.game_id) == r.pid for r in pl.itertuples()]
    pl["season"], pl["week"] = d["season"], d["week"]
    players_all.append(pl)
    games = sorted(pl.game_id.unique())
    # first TD: rank of the actual scorer inside his game
    ft = []
    for g in games:
        gp = pl[pl.game_id == g].sort_values("p_first", ascending=False).reset_index(drop=True)
        who = first_scorer.get(g)
        hit = gp.index[gp.pid == who]
        ft.append(dict(game=g, scorer=first_name.get(g), model_p=float(gp.p_first[hit[0]]) if len(hit) else 0.0,
                       rank=int(hit[0]) + 1 if len(hit) else None))
    top = pl.nlargest(15, "p_any")
    strong = pl[pl.p_any >= 0.40]
    weeks.append(dict(season=d["season"], week=d["week"], backfill=d.get("backfill", False), games=len(games),
                      players=len(pl), exp_scorers=round(pl.p_any.sum(), 1), act_scorers=int(pl.hit.sum()),
                      brier=round(float(((pl.p_any - pl.hit) ** 2).mean()), 4),
                      top15_exp=round(top.p_any.sum(), 1), top15_hit=int(top.hit.sum()),
                      strong_n=len(strong), strong_hit=int(strong.hit.sum()), strong_exp=round(strong.p_any.sum(), 1),
                      first_top3=sum(1 for f in ft if f["rank"] and f["rank"] <= 3), first_games=len(ft),
                      first_detail=ft))
    for e in d.get("edges", []):
        if e.get("pid") is None:
            continue
        row = pl[pl.pid == e["pid"]]
        if row.empty:
            continue
        won = bool(row.first_hit.iloc[0] if e.get("market") == "first" else row.hit.iloc[0])
        dec = 1 + (e["best"] / 100 if e["best"] > 0 else 100 / -e["best"])
        bets.append(dict(season=d["season"], week=d["week"], bet=e["bet"], price=e["best"], book=e["book"],
                         ev=e["ev"], won=won, profit=round(dec - 1 if won else -1.0, 3)))

allp = pd.concat(players_all) if players_all else pd.DataFrame(columns=["p_any", "hit"])
bins = [0, .1, .2, .3, .4, .5, 1]
cal = []
if len(allp):
    allp["b"] = pd.cut(allp.p_any, bins)
    for b, g in allp.groupby("b", observed=True):
        cal.append(dict(bucket=f"{int(b.left*100)}–{int(b.right*100)}%", n=len(g), pred=round(g.p_any.mean(), 3),
                        act=round(g.hit.mean(), 3)))
last = weeks[-1] if weeks else None
detail = []
if last:
    lp = allp[(allp.season == last["season"]) & (allp.week == last["week"])].nlargest(40, "p_any")
    detail = lp[["name", "team", "pos", "p_any", "hit", "p_first", "first_hit"]].to_dict("records")
b = pd.DataFrame(bets)
out = dict(weeks=weeks, calibration=cal, last_week=detail, bets=bets,
           bet_summary=dict(n=len(b), won=int(b.won.sum()) if len(b) else 0,
                            units=round(float(b.profit.sum()), 2) if len(b) else 0.0,
                            roi=round(float(b.profit.mean()), 4) if len(b) else None),
           backtest_2025=dict(note="Walk-forward, weeks 4–18", brier=0.1527, baseline=0.1662,
                              calibration=[["10–15%", .125, .131], ["15–20%", .175, .176], ["20–30%", .247, .238],
                                           ["30–40%", .344, .338], ["40–50%", .443, .424], ["50–70%", .563, .517]]))
json.dump(out, open("results.json", "w"), default=lambda o: o.item() if hasattr(o, "item") else str(o))
print("graded weeks:", [(w["season"], w["week"]) for w in weeks], "bets:", len(bets))
