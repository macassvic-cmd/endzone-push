"""Project the upcoming slate.

    python run_week.py                 # auto: current season, next unplayed week
    python run_week.py 2026 4          # explicit
Writes docs/data/slate_<season>_w<week>.json and docs/data/latest.json
"""
import sys, os, json, numpy as np, pandas as pd
import model as M, odds as O, weather as W, kalshi as K, clv as C, parlay as PL, blend as BL, sharpapi as SA, yards as Y, yard_prices as YP

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
if sched.empty:
    print(f"nothing to project: every {season} week {week} game has kicked off; keeping the existing files"); sys.exit(0)

# ---------- live lines (Odds API) ----------
def merge_sources(oa, sa):
    """Merge Odds API events (oa) with SharpAPI events (sa) game by game. Same book from both sources: keep the
       fresher quote per market (Odds API market last_update vs SharpAPI row timestamp). Every outcome keeps its source."""
    def key(e): return (O.TEAM_ABBR.get(e["away_team"], e["away_team"]), O.TEAM_ABBR.get(e["home_team"], e["home_team"]))
    for e in oa or []:
        for b in e.get("props", {}).get("bookmakers", []):
            for m in b["markets"]:
                for o in m["outcomes"]:
                    o.setdefault("source", "oddsapi"); o.setdefault("ts", m.get("last_update") or b.get("last_update"))
    out = {key(e): e for e in (oa or [])}
    for e in sa or []:
        k = key(e)
        if k not in out:
            out[k] = e; continue
        tgt = out[k]
        if not tgt.get("bookmakers"): tgt["bookmakers"] = e.get("bookmakers", [])
        tb = {b["title"]: b for b in tgt.setdefault("props", {}).setdefault("bookmakers", [])}
        for b in e.get("props", {}).get("bookmakers", []):
            if b["title"] not in tb:
                tgt["props"]["bookmakers"].append(b); continue
            tm = {m["key"]: m for m in tb[b["title"]]["markets"]}
            for m in b["markets"]:
                if m["key"] not in tm:
                    tb[b["title"]]["markets"].append(m); continue
                old = tm[m["key"]]; old_ts = max((o.get("ts") or "" for o in old["outcomes"]), default=""); new_ts = max((o.get("ts") or "" for o in m["outcomes"]), default="")
                if new_ts >= old_ts:
                    old["outcomes"] = m["outcomes"]                 # fresher SharpAPI quote replaces the Odds API one for this book+market
    return list(out.values())


sharp = None
if os.environ.get("SHARPAPI_KEY") and not os.environ.get("ODDS_MOCK"):
    try:
        sharp = SA.fetch_week(); print(f"sharpapi: {len(sharp)} fixtures, {SA.REQS} requests, DK/FD anytime+first+yardage")
    except Exception as e:
        print("sharpapi failed, falling back to the full Odds API market set:", e); sharp = None
events = O.fetch_all(markets=None if sharp else O.PROP_MARKETS)
if sharp:
    events = merge_sources(events, sharp)
odds_asof = None
if events is None:
    # live odds off: reuse the newest saved pull for games that have not kicked off, and say so on the board
    _pulls = C.pulls(season, week)
    if _pulls:
        _stamp, _fn = _pulls[-1]
        _now = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
        events = [e for e in json.load(open(_fn)) if e["commence_time"] > _now]
        odds_asof = pd.Timestamp(f"{_stamp[:4]}-{_stamp[4:6]}-{_stamp[6:8]}T{_stamp[9:11]}:{_stamp[11:13]}Z").tz_convert("America/Los_Angeles").strftime("%a %b %d %I:%M %p PT")
        print(f"live odds off: reusing {_fn} ({odds_asof}) for {len(events)} games not yet kicked off")
if events and os.environ.get("KALSHI", "on") != "off":
    try:
        _n, _liq = K.attach(events)                                  # Kalshi joins the board as one more book (fee-adjusted prices)
        print(f"kalshi markets attached: {_n} ({_liq} with >= ${K.MIN_DOLLARS_AT_ASK:.0f} at the ask; the rest are reference-only)")
    except Exception as e:
        print("kalshi unavailable:", e)
if events and not os.environ.get("ODDS_MOCK") and not odds_asof:
    # keep every real pull: odds_history/<season>_w<week>_<UTC stamp>.json (committed by the workflow) for blend fitting and closing-line value
    os.makedirs("odds_history", exist_ok=True)
    _fn = f"odds_history/{season}_w{week}_{pd.Timestamp.now(tz='UTC'):%Y%m%dT%H%M}Z.json"
    json.dump(events, open(_fn, "w")); print("saved odds pull to", _fn)
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
_rk = ros[ros.rookie_year == season].drop_duplicates("gsis_id")
rookies = {g: M.draft_bucket(d) for g, d in zip(_rk.gsis_id, _rk.draft_number)}
_cache = M.prep_week(p, s2, season, week)
teams, pl, qbs, sh = M.build_slate(p, s2, season, week, active=active, qb_override=qbo, wind=wind, depth=depth, snaps=snaps3, rookies=rookies, cache=_cache)
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

# ---------- role: Starter / Rotational / Bench (last-3-game snap % first, depth-chart slot when snaps are unknown) ----------
ROLE_STARTER_SNAPS, ROLE_BENCH_SNAPS = 0.60, 0.30
def role_of(pid, snap3):
    d = depth.get(pid); pos = M.POS_MAP.get(d[0], d[0]) if d else None
    top = d is not None and pos in M.RANK_CAP and d[1] <= 1
    deep = d is None or pos not in M.RANK_CAP or d[1] >= M.RANK_CAP[pos]
    known = snap3 is not None and snap3 == snap3
    if top or (known and snap3 >= ROLE_STARTER_SNAPS):
        return "Starter"
    if deep or (known and snap3 < ROLE_BENCH_SNAPS):
        return "Bench"
    return "Rotational"
df["role"] = [role_of(r.pid, getattr(r, "snap_3", None)) for r in df.itertuples()]
print("roles:", df.role.value_counts().to_dict())
# model weak spot: mobile QBs are under-predicted (CHANGELOG, mobile-QB round); tag them and keep them off the Edge Board
WEAK_QB_DR = 1.5
df["qb_dr"] = df.pid.map(sh.drop_duplicates("pid").set_index("pid").qb_dr)
df["weak_spot"] = np.where(df.qb_dr.fillna(0) >= WEAK_QB_DR, "mobile-qb", None)
print("weak-spot QBs (off the edge board):", df[df.weak_spot.notna()].name.tolist())

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
BLENDS = BL.load()                                   # fitted model+market blend per market (results.py refits weekly); 50/50 when not in use
BLEND_COEF = {pre: BL.coef_for(BLENDS, mk) for pre, mk in (("any_", "any"), ("first_", "first"), ("two_", "two"))}
print("blend in use:", {k[:-1]: ("fitted " + str(v) if v else "50/50") for k, v in BLEND_COEF.items()})
def attach(row, market, prob, point=None, prefix=""):
    ps = O.price_summary(board, market, row["name"], point, hold) if board else None
    if not ps: return {}
    ev = prob * O.decimal(ps["best"]) - 1
    coef = BLEND_COEF.get(prefix); w = 0.5 if coef is None else None
    blend = float(BL.predict(coef, [prob], [ps["market_p"]])[0])   # fitted blend when validated, else 50/50
    kal = board.get((market, O.norm_name(row["name"]), point), {}).get(K.BOOK, {}).get("yes")
    kref = O.REFERENCE.get((market, O.norm_name(row["name"]), point), {}).get(K.BOOK)
    return {f"{prefix}best": ps["best"], f"{prefix}book": ps["book"], f"{prefix}mkt_p": round(ps["market_p"], 4),
            f"{prefix}kalshi": kal if kal is not None else kref, f"{prefix}kalshi_liquid": kal is not None,
            f"{prefix}ev": round(blend * O.decimal(ps["best"]) - 1, 4), f"{prefix}model_ev": round(ev, 4),
            f"{prefix}med": ps["median"], f"{prefix}ev_med": round(blend * O.decimal(ps["median"]) - 1, 4),
            f"{prefix}blend_p": round(blend, 4), f"{prefix}nbooks": ps["n_books"], f"{prefix}w": w}

ext = [dict(**attach(r, "player_anytime_td", r.p_any, prefix="any_"),
            **attach(r, "player_first_td", r.p_first, prefix="first_"),
            **attach(r, O.TWO_PLUS[0], r.p_2plus, point=O.TWO_PLUS[1], prefix="two_")) for _, r in df.iterrows()]
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

def kick_utc(et):
    """'2026-10-04 13:00' (Eastern, as nflverse lists it) -> '2026-10-04T17:00Z' so the page can sort and show local time."""
    return pd.Timestamp(et).tz_localize("America/New_York").tz_convert("UTC").strftime("%Y-%m-%dT%H:%MZ")


games = []
for gid in sorted({v["game_id"] for v in teams.values()}):
    tt = [t for t in teams if teams[t]["game_id"] == gid]
    home = [t for t in tt if teams[t]["home"]][0]; away = [t for t in tt if not teams[t]["home"]][0]
    hr, ar, mix = M.first_td_split(teams[home]["spread"])
    games.append(dict(game_id=gid, away=away, home=home, kickoff=teams[home]["gametime"], kick_utc=kick_utc(teams[home]["gametime"]),
                      away_imp=teams[away]["implied"], home_imp=teams[home]["implied"],
                      exp_td=teams[away]["lam"] + teams[home]["lam"] + 2 * M.DST_TD_RATE,
                      lines=line_src.get(gid), wx=wx.get(gid),
                      indoor=bool(sched[sched.game_id == gid].roof.isin(['dome', 'closed']).any()), home_first_if_home_recv=hr,
                      home_first_if_away_recv=ar, home_first=mix))

vac = sh[sh.pid.isin(out_ids | inactive) & (sh.share > 0.08) & (sh.last_ord >= season * 100)]
vac = vac.assign(team=vac.last_team, name=vac.pid.map(names).fillna(vac.name))
vac = vac[vac.team.isin(teams)][["name", "team", "kind", "share"]]

# ---------- yard ladders: rushing / receiving / passing yards from the same sim games ----------
YARD_KINDS = [k for k, ok in Y.SHIP.items() if ok]        # kinds that passed the backtest gate (yards.SHIP)
yard_rows, yard_edges = [], []
if YARD_KINDS:
    _yc = Y.prep(_cache["past"], season)
    _tv, _plv, _qv = Y.build(p, s2, season, week, active, qbo, depth, snaps3, rookies, _cache, _yc, wind=wind)
    _posof = lambda pid: M.POS_MAP.get(pos.get(pid, "WR"), pos.get(pid, "WR"))
    _yd = Y.simulate(teams, _plv, qbs, _yc, sim, _posof)
    _yb, _yh = YP.ladder_board(events) if events else ({}, {})
    _name = {r.pid: r["name"] for _, r in df.iterrows()}; _meta = {r.pid: r for _, r in df.iterrows()}
    _lines = {(pid, k): YP.offered_lines(_yb, k, _name.get(pid, "")) for (pid, k) in _yd if pid in _name}
    _coef = {k: BL.coef_for(BLENDS, "yds_" + k) or BL.coef_for(BLENDS, "yds") for k in Y.DEFAULT_LADDER}   # pooled yards fit when gated in (3+ weeks, beats 50/50 held out)
    for r in Y.summarize({k: v for k, v in _yd.items() if k[1] in YARD_KINDS}, _lines):
        if r["pid"] not in _meta: continue
        m = _meta[r["pid"]]; rungs = []; best_edge = None
        for line, prob in sorted(((float(l), pr) for l, pr in r["ladder"].items())):
            ps = YP.price_rung(_yb, _yh, r["kind"], _name[r["pid"]], line, model_p=prob) if _yb else None
            rung = dict(line=line, p=round(prob, 4), fair=int(M.fair_american(np.array([prob]))[0]))
            if ps:
                blend = float(BL.predict(_coef.get(r["kind"]), [prob], [ps["market_p"]])[0])
                ev, ev_med = blend * O.decimal(ps["best"]) - 1, blend * O.decimal(ps["median"]) - 1
                rung.update(best=ps["best"], book=ps["book"], med=ps["median"], nbooks=ps["n_books"], mkt_p=round(ps["market_p"], 4), blend=round(blend, 4),
                            ev=round(ev, 4), ev_med=round(ev_med, 4), two_sided=ps["two_sided"])
                if line >= Y.MIN_EDGE_LINE[r["kind"]] and ev >= 0.05 and ev_med >= 0.05 and ps["n_books"] >= 2 and not (isinstance(m.get("weak_spot"), str) and m.get("weak_spot")) and (best_edge is None or ev_med > best_edge["ev_med"]):
                    best_edge = dict(bet=f"{_name[r['pid']]} Over {line:g} {r['kind']} yds", pid=r["pid"], market="yds", kind=r["kind"], line=line, team=m["team"], model_p=round(prob, 4),
                                     mkt_p=round(ps["market_p"], 4), blend_p=round(blend, 4), best=int(ps["best"]), book=ps["book"], ev=round(ev, 4), med=int(ps["median"]),
                                     ev_med=round(ev_med, 4), nbooks=int(ps["n_books"]), games=int(m.get("games", 0)), w=0.5, role=m["role"], kalshi=None, kalshi_liquid=False)
            rungs.append(rung)
        main = [g for g in rungs if g.get("two_sided")]
        main = min(main, key=lambda g: abs(g["line"] - r["median"])) if main else None   # the book's main line sits nearest our median
        yard_rows.append(dict(pid=r["pid"], name=_name[r["pid"]], team=m["team"], pos=m.get("pos"), role=m["role"], kind=r["kind"], mean=round(r["mean"], 1),
                              median=round(r["median"], 1), sd=round(r["sd"], 1), main_line=main["line"] if main else None, main_book=main.get("book") if main else None,
                              main_p=main["p"] if main else None, main_mkt_p=main.get("mkt_p") if main else None, rungs=rungs, weak_spot=m.get("weak_spot") if isinstance(m.get("weak_spot"), str) else None))
        if best_edge: yard_edges.append(best_edge)
    print(f"yards: {len(yard_rows)} player-stats, {sum(1 for r in yard_rows if r['main_line'] is not None)} with a book line, {len(yard_edges)} edges")

# ---------- sim draws for the slip pricer: N sim games per player, every stat from the same game-environment draw ----------
import slips as SL
DRAWS_N = 2000
draws_idx = None
if YARD_KINDS:
    _st = Y.stats(_yd, sim, qbs, _posof)
    LAYOUT = {"rec": dict(bytes=1, scale=1), "rec_yds": dict(bytes=1, scale=1), "rush_yds": dict(bytes=1, scale=1),
              "pass_att": dict(bytes=1, scale=1), "pass_yds": dict(bytes=1, scale=0.5), "fpts": dict(bytes=2, scale=10, offset=200)}   # fantasy can be negative
    _keep = [pid for pid in _st if pid in _meta and (_meta[pid]["role"] != "Bench" or _posof(pid) == "QB")]
    _buf = bytearray(); _plist = []
    for pid in sorted(_keep, key=lambda x: _name[x]):
        d = _st[pid]; order = [k for k in ("pass_att", "pass_yds", "rec", "rec_yds", "rush_yds", "fpts") if k in d]
        _plist.append(dict(pid=pid, name=_name[pid], team=_meta[pid]["team"], pos=_meta[pid].get("pos"), stats=order, offset=len(_buf)))
        for k in order:
            sp = LAYOUT[k]; v = np.asarray(d[k][:DRAWS_N], float) * sp["scale"] + sp.get("offset", 0)
            _buf += np.clip(np.rint(v), 0, 65535 if sp["bytes"] == 2 else 255).astype("<u2" if sp["bytes"] == 2 else "u1").tobytes()
    open("draws.bin", "wb").write(bytes(_buf))
    draws_idx = dict(file="draws.bin", n=int(min(DRAWS_N, sim["n"])), layout=LAYOUT, players=_plist, payout=SL.PAYOUT,
                     cols=["rec", "rec_yds", "rush_yds", "pass_yds", "pass_att", "fpts"])
    print(f"draws: {len(_plist)} players x {draws_idx['n']} sim games -> draws.bin ({len(_buf) / 1e6:.1f} MB)")

# ---------- edge board: priced by 2+ books and EV >= 5% at the median book (not just the best one) ----------
MIN_BOOKS, MIN_EV = 2, 0.05
edges = []
for _, r in df.iterrows():
    for m, lbl, pr in [("any_", "Anytime TD", r.p_any), ("first_", "First TD", r.p_first), ("two_", "2+ TD", r.p_2plus)]:
        ev, ev_med = r.get(m + "ev"), r.get(m + "ev_med")
        if isinstance(r.get("weak_spot"), str) and r.get("weak_spot"):      # None/NaN = not a weak spot (NaN is truthy in Python)
            continue
        if pd.notna(ev_med) and ev_med >= MIN_EV and ev >= MIN_EV and r.get(m + "nbooks", 0) >= MIN_BOOKS:
            edges.append(dict(bet=f"{r['name']} {lbl}", pid=r.pid, market=m[:-1], team=r.team, model_p=pr, mkt_p=r[m + "mkt_p"], blend_p=r[m + "blend_p"],
                              best=int(r[m + "best"]), book=r[m + "book"], ev=ev, med=int(r[m + "med"]), ev_med=ev_med,
                              nbooks=int(r[m + "nbooks"]), games=int(r.get("games", 0)), w=r[m + "w"], role=r.role,
                              kalshi=None if pd.isna(r.get(m + "kalshi")) else int(r[m + "kalshi"]), kalshi_liquid=bool(r.get(m + "kalshi_liquid", False))))
edges += yard_edges
clean = lambda d: d.replace({np.nan: None})
if edges and not os.environ.get("ODDS_MOCK"):
    _flag = _stamp if odds_asof else f"{pd.Timestamp.now(tz='UTC'):%Y%m%dT%H%M}Z"     # flag time = the pull the prices came from
    print("edges newly logged for CLV:", C.log_edges(edges, season, week, _flag))
_pl = PL.build(clean(df.round(4)).to_dict("records"))        # "likely" works without prices; "value" needs them
print("parlays:", {m: f"{len(v['pool'])} legs / {len(v['parlays'])} parlays" for m, v in _pl["modes"].items()})
data = dict(season=season, week=week, odds_live=bool(board), odds_asof=odds_asof, n_events=len(events or []), parlays=_pl, yards=yard_rows, draws=draws_idx,
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
    for g in locked_games:
        g["locked"] = True
        if not g.get("kick_utc") and g.get("kickoff"): g["kick_utc"] = kick_utc(g["kickoff"])
    data["games"] = locked_games + data["games"]
    data["players"] = [r for r in old.get("players", []) if r.get("game_id") in lg] + data["players"]
    lteams = {t for g in locked_games for t in (g["home"], g["away"])}
    data["edges"] = [e for e in old.get("edges", []) if e.get("team") in lteams] + data["edges"]
    data["yards"] = [y for y in old.get("yards", []) if y.get("team") in lteams] + data["yards"]
    # parlays whose legs have all kicked off are locked with their flag-time prices
    for m, v in data["parlays"]["modes"].items():
        v["parlays"] = [c for c in old.get("parlays", {}).get("modes", {}).get(m, {}).get("parlays", []) if all(l["team"] in lteams for l in c["legs"])] + v["parlays"]
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
