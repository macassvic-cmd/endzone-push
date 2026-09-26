"""Project the upcoming slate.

    python run_week.py                 # auto: current season, next unplayed week
    python run_week.py 2026 4          # explicit
Writes docs/data/slate_<season>_w<week>.json and docs/data/latest.json
"""
import sys, os, json, numpy as np, pandas as pd
import model as M, odds as O, weather as W

DATA, OUT = "data", "."
os.makedirs(OUT, exist_ok=True)
p, s = M.load()
now_et = pd.Timestamp.now(tz="America/New_York").tz_localize(None)
s["kick"] = pd.to_datetime(s.gameday + " " + s.gametime.fillna("13:00"))

if len(sys.argv) >= 3:
    season, week = int(sys.argv[1]), int(sys.argv[2])
else:
    fut = s[(s.game_type == "REG") & s.home_score.isna() & (s.kick > now_et)].sort_values("kick")
    season, week = int(fut.season.iloc[0]), int(fut.week.iloc[0])
print(f"== projecting {season} week {week}")

# ---------- availability ----------
dc = pd.read_parquet(f"{DATA}/dc.parquet"); dc = dc[dc.dt == dc.dt.max()]
inj = pd.read_parquet(f"{DATA}/inj.parquet"); inj = inj[inj.week == week]
out_ids = set(inj[inj.report_status.isin(["Out", "Doubtful"])].gsis_id)
q_ids = set(inj[inj.report_status == "Questionable"].gsis_id)
ros = pd.read_parquet(f"{DATA}/rosters.parquet"); ros = ros[ros.week == ros.week.max()]
inactive = set(ros[ros.status.isin(["RES", "INA", "CUT", "RET", "EXE"])].gsis_id)

wk = s[(s.season == season) & (s.week == week)]
done = wk[wk.home_score.notna() | (wk.kick <= now_et)].game_id     # played or already kicked off
s2 = s[~s.game_id.isin(done)].copy()
sched = s2[(s2.season == season) & (s2.week == week)]

# ---------- live lines (Odds API) ----------
events = O.fetch_all()
lines = O.game_lines(events)
board = O.prop_board(events)
hold = O.measure_hold(events)          # per-book overround measured from this week's boards
for k, v in sorted(hold.items(), key=str):
    if isinstance(k, tuple): print(f"hold {k[0]:18} {k[1]:13} {v:.3f}")
line_src = {}
for i, g in sched.iterrows():
    l = lines.get((g.away_team, g.home_team))
    if l:
        s2.loc[i, ["spread_line", "total_line"]] = [l["spread_line"], l["total_line"]]
        line_src[g.game_id] = f"live ({l['books']} books)"
    else:
        line_src[g.game_id] = "nflverse"

# ---------- weather ----------
wx = W.kickoff_weather(sched)
wind = {k: v["wind"] for k, v in wx.items()}

# ---------- active players ----------
limits = {"QB": 2, "RB": 3, "WR": 6, "TE": 4}
active, qbo, names, pos = {}, {}, {}, {}
for t, g in dc[dc.pos_abb.isin(limits)].groupby("team"):
    g = g[g.pos_rank <= g.pos_abb.map(limits)]
    ids = set(g.gsis_id) - out_ids - inactive
    for _, r in g.iterrows():
        names[r.gsis_id] = r.player_name; pos[r.gsis_id] = r.pos_abb
    qb = g[(g.pos_abb == "QB") & g.gsis_id.isin(ids)].sort_values("pos_rank")
    for side in ["home", "away"]:
        row = sched[sched[f"{side}_team"] == t]
        if len(row):
            m = qb[qb.player_name == row[f"{side}_qb_name"].iloc[0]]
            if len(m): qb = m
    if len(qb): qbo[t] = (qb.gsis_id.iloc[0], qb.player_name.iloc[0])
    active[t] = {i for i in ids if pos.get(i) != "QB" or i == qbo.get(t, (None,))[0]}

# depth-chart slot for the share prior, and recent snap share for the bit-part cap
depth = {r.gsis_id: (r.pos_abb, r.pos_rank) for r in dc.dropna(subset=["gsis_id"]).sort_values("pos_rank").drop_duplicates("gsis_id").itertuples()}
snaps3 = None
try:
    _sn = pd.read_parquet(f"{DATA}/snaps.parquet")
    _sn = _sn[(_sn.season == season) & (_sn.game_type == "REG") & (_sn.week < week)]
    _idmap = ros.dropna(subset=["pfr_id"]).set_index("pfr_id").gsis_id.to_dict()
    _sn = _sn.assign(gsis_id=_sn.pfr_player_id.map(_idmap)).dropna(subset=["gsis_id"])
    snaps3 = _sn.sort_values("week").groupby("gsis_id").tail(3).groupby("gsis_id").offense_pct.mean().to_dict()
except Exception as e:
    print("snaps unavailable for cap:", e)
teams, pl, qbs, sh = M.build_slate(p, s2, season, week, active=active, qb_override=qbo, wind=wind, depth=depth, snaps=snaps3)
pl.loc[pl.pid.isin(q_ids), "share"] *= 0.85
sim = M.simulate(teams, pl, qbs, n=60000)
df = M.summarize(teams, pl, qbs, sim, names)
df["pos"] = df.pid.map(pos)
df["questionable"] = df.pid.isin(q_ids)

# ---------- snap share (role check) ----------
try:
    sn = pd.read_parquet(f"{DATA}/snaps.parquet")
    sn = sn[(sn.season == season) & (sn.game_type == "REG")]
    idmap = ros.dropna(subset=["pfr_id"]).set_index("pfr_id").gsis_id.to_dict()
    sn["gsis_id"] = sn.pfr_player_id.map(idmap)
    last = sn.sort_values("week").groupby("gsis_id").tail(1).set_index("gsis_id").offense_pct
    avg3 = sn.sort_values("week").groupby("gsis_id").tail(3).groupby("gsis_id").offense_pct.mean()
    df["snap_last"] = df.pid.map(last); df["snap_3"] = df.pid.map(avg3)
    df["snap_trend"] = df.snap_last - df.snap_3
except Exception as e:
    print("snaps unavailable:", e)

# ---------- first TD: conditional on who receives the opening kickoff ----------
fmult = {}
for t, info in teams.items():
    hr, ar, mix = M.first_td_split(info["spread"])
    if info["home"]:
        fmult[t] = dict(recv=hr / mix, kick=ar / mix)
    else:
        fmult[t] = dict(recv=(1 - ar) / (1 - mix), kick=(1 - hr) / (1 - mix))
df["first_if_recv"] = df.p_first * df.team.map(lambda t: fmult[t]["recv"])
df["first_if_kick"] = df.p_first * df.team.map(lambda t: fmult[t]["kick"])
for c in ["p_any", "p_first", "p_2plus", "first_if_recv", "first_if_kick"]:
    df["fair_" + c.replace("p_", "")] = M.fair_american(df[c].values)


# ---------- book prices + edge ----------
BLEND_W = 0.5   # weight on model vs market consensus for EV
BLEND_W_THIN, THIN_GAMES, THIN_RATIO = 0.25, 4, 3.0   # safety net: thin sample + model odds > 3x market -> lean on the market
odds_ratio = lambda a, b: (a / (1 - a)) / (b / (1 - b)) if 0 < a < 1 and 0 < b < 1 else 1.0
def attach(row, market, prob, point=None, prefix=""):
    ps = O.price_summary(board, market, row["name"], point, hold) if board else None
    if not ps: return {}
    ev = prob * O.decimal(ps["best"]) - 1
    w = BLEND_W
    if row.get("games", 99) < THIN_GAMES and odds_ratio(prob, ps["market_p"]) > THIN_RATIO:
        w = BLEND_W_THIN
    blend = w * prob + (1 - w) * ps["market_p"]      # meet the market halfway (or lean on it for thin outliers)
    return {f"{prefix}best": ps["best"], f"{prefix}book": ps["book"], f"{prefix}mkt_p": round(ps["market_p"], 4),
            f"{prefix}ev": round(blend * O.decimal(ps["best"]) - 1, 4), f"{prefix}model_ev": round(ev, 4),
            f"{prefix}med": ps["median"], f"{prefix}ev_med": round(blend * O.decimal(ps["median"]) - 1, 4),
            f"{prefix}blend_p": round(blend, 4), f"{prefix}nbooks": ps["n_books"], f"{prefix}w": w}

ext = [dict(**attach(r, "player_anytime_td", r.p_any, prefix="any_"),
            **attach(r, "player_first_td", r.p_first, prefix="first_")) for _, r in df.iterrows()]
df = pd.concat([df.reset_index(drop=True), pd.DataFrame(ext)], axis=1)

# ---------- QB ladders + stacks ----------
tds, pix = sim["tds"], sim["pix"]
qrows, stacks, bring = [], [], []
for t, (qid, qn) in qbs.items():
    ptd = sim["qb_ptd"][t]
    q = dict(team=t, opp=teams[t]["opp"], qb=qn, implied=teams[t]["implied"], team_td=teams[t]["lam"],
             pass_frac=teams[t]["pass_frac"], exp_pass_td=ptd.mean(), p1=(ptd >= 1).mean(), p2=(ptd >= 2).mean(),
             p3=(ptd >= 3).mean(), p_rush_td=(tds[:, pix[qid]] > 0).mean() if qid in pix else 0,
             wind=teams[t]["wind"])
    for pt, key in [(0.5, "p1"), (1.5, "p2"), (2.5, "p3")]:
        a = attach({"name": qn}, "player_pass_tds", q[key], point=pt)
        if a: q[f"o{pt}"] = dict(line=pt, best=a["best"], book=a["book"], ev=a["ev"], mkt_p=a["mkt_p"], blend_p=a["blend_p"])
    qrows.append(q)
    mates = df[(df.team == t) & (df.pos.isin(["WR", "TE", "RB"])) & (df.rec_share > 0.06)]
    for _, w in mates.iterrows():
        wt = tds[:, pix[w.pid]] > 0
        for k in (1, 2, 3):
            qk = ptd >= k; j = (wt & qk).mean()
            stacks.append(dict(team=t, qb=qn, wr=w["name"], pos=w.pos, k=k, p_wr=wt.mean(), p_qb=qk.mean(),
                               joint=j, lift=j / (wt.mean() * qk.mean()), fair=int(M.fair_american(np.array([j]))[0]),
                               naive_fair=int(M.fair_american(np.array([wt.mean() * qk.mean()]))[0])))
    o = teams[t]["opp"]
    if t < o:
        a = df[(df.team == t) & (df.rec_share > 0)].nlargest(2, "rec_share")
        b = df[(df.team == o) & (df.rec_share > 0)].nlargest(2, "rec_share")
        for _, x in a.iterrows():
            for _, y in b.iterrows():
                xa, yb = tds[:, pix[x.pid]] > 0, tds[:, pix[y.pid]] > 0
                j = (xa & yb).mean()
                bring.append(dict(game=f"{t}-{o}", a=x["name"], a_team=t, b=y["name"], b_team=o, joint=j,
                                  lift=j / (xa.mean() * yb.mean()), fair=int(M.fair_american(np.array([j]))[0])))

games = []
for gid in sorted({v["game_id"] for v in teams.values()}):
    tt = [t for t in teams if teams[t]["game_id"] == gid]
    home = [t for t in tt if teams[t]["home"]][0]; away = [t for t in tt if not teams[t]["home"]][0]
    hr, ar, mix = M.first_td_split(teams[home]["spread"])
    games.append(dict(game_id=gid, away=away, home=home, kickoff=teams[home]["gametime"],
                      away_imp=teams[away]["implied"], home_imp=teams[home]["implied"],
                      exp_td=teams[away]["lam"] + teams[home]["lam"] + 2 * M.DST_TD_RATE,
                      lines=line_src.get(gid), wx=wx.get(gid),
                      indoor=bool(sched[sched.game_id == gid].roof.isin(['dome', 'closed']).any()), home_first_if_home_recv=hr,
                      home_first_if_away_recv=ar, home_first=mix))

vac = sh[sh.pid.isin(out_ids | inactive) & (sh.share > 0.08) & (sh.last_ord >= season * 100)]
vac = vac.assign(team=vac.last_team, name=vac.pid.map(names).fillna(vac.name))
vac = vac[vac.team.isin(teams)][["name", "team", "kind", "share"]]

# ---------- edge board: priced by 2+ books and EV >= 5% at the median book (not just the best one) ----------
MIN_BOOKS, MIN_EV = 2, 0.05
edges = []
for _, r in df.iterrows():
    for m, lbl, pr in [("any_", "Anytime TD", r.p_any), ("first_", "First TD", r.p_first)]:
        ev, ev_med = r.get(m + "ev"), r.get(m + "ev_med")
        if pd.notna(ev_med) and ev_med >= MIN_EV and ev >= MIN_EV and r.get(m + "nbooks", 0) >= MIN_BOOKS:
            edges.append(dict(bet=f"{r['name']} {lbl}", pid=r.pid, market=m[:-1], team=r.team, model_p=pr, mkt_p=r[m + "mkt_p"], blend_p=r[m + "blend_p"],
                              best=int(r[m + "best"]), book=r[m + "book"], ev=ev, med=int(r[m + "med"]), ev_med=ev_med,
                              nbooks=int(r[m + "nbooks"]), games=int(r.get("games", 0)), w=r[m + "w"]))
clean = lambda d: d.replace({np.nan: None})
data = dict(season=season, week=week, odds_live=bool(board), n_events=len(events or []),
            generated=pd.Timestamp.now(tz="America/Los_Angeles").strftime("%a %b %d %I:%M %p PT"),
            players=clean(df.round(4)).to_dict("records"),
            qbs=qrows, stacks=pd.DataFrame(stacks).round(4).to_dict("records"),
            bring=pd.DataFrame(bring).round(4).to_dict("records"), games=games,
            vacated=vac.round(3).to_dict("records"),
            edges=sorted(edges, key=lambda e: -e["ev"]))
# ---------- lock in games that already kicked off (keeps pre-game projections for grading) ----------
slate_fn = f"{OUT}/slate_{season}_w{week}.json"
if os.path.exists(slate_fn):
    old = json.load(open(slate_fn))
    live = {g["game_id"] for g in games}
    keep = lambda rows, key="game_id": [r for r in rows if r.get(key) not in live and r.get(key) in set(done)]
    locked_games = keep(old.get("games", []))
    lg = {g["game_id"] for g in locked_games}
    for g in locked_games: g["locked"] = True
    data["games"] = locked_games + data["games"]
    data["players"] = [r for r in old.get("players", []) if r.get("game_id") in lg] + data["players"]
    lteams = {t for g in locked_games for t in (g["home"], g["away"])}
    data["edges"] = [e for e in old.get("edges", []) if e.get("team") in lteams] + data["edges"]
    data["bring"] = [b for b in old.get("bring", []) if b.get("a_team") in lteams] + data["bring"]

# ---------- red zone / end zone usage (2025 + current season) ----------
import redzone
rz = redzone.table(p, season)
_r = pd.read_parquet(f"{DATA}/rosters.parquet").drop_duplicates("gsis_id", keep="last").set_index("gsis_id")
for row in rz:
    if row["pid"] in _r.index:
        row["name"] = _r.at[row["pid"], "full_name"]; row["pos"] = _r.at[row["pid"], "position"]
data["redzone"] = rz

enc = lambda o: o.item() if hasattr(o, "item") else (None if o != o else str(o))
txt = json.dumps(data, default=enc).replace("NaN", "null")
for fn in [f"{OUT}/slate_{season}_w{week}.json", f"{OUT}/latest.json"]:
    open(fn, "w").write(txt)
print(df.sort_values("p_any", ascending=False)[["name", "team", "pos", "p_any", "p_first"]].head(10).to_string())
print("edges:", len(edges), "| weather games:", len(wx), "| live lines:", sum(v != 'nflverse' for v in line_src.values()))
