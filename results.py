"""Grade every saved slate against what actually happened. Writes results.json."""
import glob, json, os, numpy as np, pandas as pd
import clv as C, parlay as PL, blend as BL

CAL_EDGES = [i / 100 for i in range(0, 75, 5)] + [1.0]     # 0–5, 5–10, …, 65–70, 70+
CAL_MIN_N = 10


def calib_buckets(p, y, min_n=CAL_MIN_N):
    """5-point calibration buckets. ci = 1.96*sqrt(act*(1-act)/n); thin = fewer than min_n props."""
    p, y = np.asarray(p, float), np.asarray(y, float)
    out = []
    for lo, hi in zip(CAL_EDGES[:-1], CAL_EDGES[1:]):
        m = (p >= lo) & (p < hi) if hi < 1 else (p >= lo)
        n = int(m.sum())
        if n == 0:
            continue
        act = float(y[m].mean())
        out.append(dict(bucket=f"{int(lo * 100)}–{int(hi * 100)}%" if hi < 1 else f"{int(lo * 100)}%+", lo=lo, n=n,
                        pred=round(float(p[m].mean()), 3), act=round(act, 3),
                        ci=round(1.96 * (act * (1 - act) / n) ** 0.5, 3), thin=n < min_n))
    return out


BLEND_W, MIN_BOOKS, MIN_EV = 0.5, 2, 0.05            # same rules as run_week.py's Edge Board


def price_backfill(pl, season, week):
    """Backfilled slates carry no prices. If a saved pull exists for the week (e.g. the OddsPapi pre-kickoff backfill),
       price every player from it with the live method (no-vig per book, median book) so model-vs-market Brier and the
       bet record extend to those weeks on real closing prices. Edges are built later with the fitted blend."""
    import odds as O
    pulls = C.pulls(season, week)
    if not pulls or pl.empty:
        return pl
    events = json.load(open(pulls[0][1]))                       # earliest pull of the week = the pre-kickoff backfill
    board, hold = O.prop_board(events), O.measure_hold(events)
    cols = {}
    for market, point, pre in [("player_anytime_td", None, "any_"), ("player_first_td", None, "first_"), ("player_tds_over", 1.5, "two_")]:
        for i, r in pl.iterrows():
            ps = O.price_summary(board, market, r["name"], point, hold)
            if not ps:
                continue
            cols.setdefault(pre + "mkt_p", {})[i] = round(ps["market_p"], 4); cols.setdefault(pre + "best", {})[i] = ps["best"]
            cols.setdefault(pre + "book", {})[i] = ps["book"]; cols.setdefault(pre + "nbooks", {})[i] = ps["n_books"]; cols.setdefault(pre + "med", {})[i] = ps["median"]
    for c, d in cols.items():
        pl[c] = pd.Series(d)
    pl["backfill_price"] = True
    return pl


def rescore_edges(allp, models):
    """Edge Board rule applied to every graded, priced row with the fitted blend: EV >= MIN_EV at the median book
       AND at the best book, 2+ books, no weak-spot players. Live weeks use flag-time prices stored in the slate;
       backfilled weeks use closing prices. Returns bet records."""
    import odds as O
    out = []
    for mk, (pcol, kcol, ycol, pre, lbl) in {"any": ("p_any", "any_mkt_p", "hit", "any_", "Anytime TD"), "first": ("p_first", "first_mkt_p", "first_hit", "first_", "First TD"),
                                             "two": ("p_2plus", "two_mkt_p", "two_hit", "two_", "2+ TD")}.items():
        if kcol not in allp or pre + "best" not in allp:
            continue
        d = allp[allp[kcol].notna() & allp[pre + "best"].notna() & allp[pcol].notna()].copy()
        if d.empty: continue
        coef = BL.coef_for(models, mk)
        d["blend"] = BL.predict(coef, d[pcol].values, d[kcol].values)
        for _, r in d.iterrows():
            if r.get("weak_spot") or (r.get(pre + "nbooks") or 0) < MIN_BOOKS or pd.isna(r.get(pre + "med")):
                continue
            ev, ev_med = float(r.blend) * O.decimal(r[pre + "best"]) - 1, float(r.blend) * O.decimal(r[pre + "med"]) - 1
            if ev < MIN_EV or ev_med < MIN_EV:
                continue
            won = bool(r[ycol]); dec = O.decimal(r[pre + "best"])
            out.append(dict(season=int(r.season), week=int(r.week), bet=f"{r['name']} {lbl}", price=int(r[pre + "best"]), book=r.get(pre + "book"),
                            ev=round(ev, 4), ev_med=round(ev_med, 4), blend_p=round(float(r.blend), 4), model_p=round(float(r[pcol]), 4), mkt_p=round(float(r[kcol]), 4),
                            won=won, profit=round(dec - 1 if won else -1.0, 3), role=r.get("role", "Unknown"), market=mk,
                            longshot=int(r[pre + "best"]) >= 1000, backfill_price=bool(r.get("backfill_price", False)), fitted=coef is not None))
    return out


def main():
    p = pd.read_parquet("data/pbp.parquet")
    s = pd.read_parquet("data/sched.parquet")
    final = set(s[s.home_score.notna()].game_id)
    td = p[(p.touchdown == 1) & p.td_player_id.notna()]
    off = td[(td.rush_touchdown == 1) | (td.pass_touchdown == 1)]
    scored = off.groupby("game_id").td_player_id.apply(set).to_dict()
    multi = off.groupby(["game_id", "td_player_id"]).size()
    two = multi[multi >= 2].reset_index().groupby("game_id").td_player_id.apply(set).to_dict()
    first = td.sort_values(["game_id", "play_id"]).groupby("game_id").first()
    first_scorer = first.td_player_id.to_dict()
    first_name = first.td_player_name.to_dict()

    weeks, players_all, bets, top15_weeks, top15_frames, parlays = [], [], [], [], [], []
    for fn in sorted(glob.glob("slate_*_w*.json")):
        d = json.load(open(fn))
        pl = pd.DataFrame(d["players"])
        if "pid" not in pl or pl.empty:
            continue
        total_games = len(d.get("games", [])) or int(pl.game_id.nunique())
        pl = pl[pl.game_id.isin(final)].copy()
        if pl.empty:
            continue
        pl["hit"] = [r.pid in scored.get(r.game_id, set()) for r in pl.itertuples()]
        pl["first_hit"] = [first_scorer.get(r.game_id) == r.pid for r in pl.itertuples()]
        pl["two_hit"] = [r.pid in two.get(r.game_id, set()) for r in pl.itertuples()]
        pl["season"], pl["week"] = d["season"], d["week"]
        if d.get("backfill") and "any_mkt_p" not in pl:
            pl = price_backfill(pl, d["season"], d["week"])
            if "any_mkt_p" in pl:
                print(f"  {d['season']} w{d['week']}: priced from the saved pull, {int(pl.any_mkt_p.notna().sum())} players")
        players_all.append(pl)
        games = sorted(pl.game_id.unique())
        # first TD: rank of the actual scorer inside his game
        ft = []
        for g in games:
            gp = pl[pl.game_id == g].sort_values("p_first", ascending=False).reset_index(drop=True)
            who = first_scorer.get(g)
            hit = gp.index[gp.pid == who]
            ft.append(dict(game=g, scorer=first_name.get(g), model_p=float(gp.p_first[hit[0]]) if len(hit) else 0.0,
                           rank=int(hit[0]) + 1 if len(hit) else None, top3_p=round(float(gp.p_first.head(3).sum()), 4)))
        top = pl.nlargest(15, "p_any")
        strong = pl[pl.p_any >= 0.40]
        weeks.append(dict(season=d["season"], week=d["week"], backfill=d.get("backfill", False), games=len(games),
                          games_total=total_games, partial=len(games) < total_games,
                          players=len(pl), exp_scorers=round(pl.p_any.sum(), 1), act_scorers=int(pl.hit.sum()),
                          brier=round(float(((pl.p_any - pl.hit) ** 2).mean()), 4),
                          top15_exp=round(top.p_any.sum(), 1), top15_hit=int(top.hit.sum()),
                          strong_n=len(strong), strong_hit=int(strong.hit.sum()), strong_exp=round(strong.p_any.sum(), 1),
                          first_top3=sum(1 for f in ft if f["rank"] and f["rank"] <= 3), first_games=len(ft),
                          first_top3_exp=round(sum(f["top3_p"] for f in ft), 1),
                          first_detail=ft))
        top15_weeks.append(dict(season=d["season"], week=d["week"], backfill=d.get("backfill", False),
                                hit=int(top.hit.sum()), exp=round(float(top.p_any.sum()), 1),
                                rows=top[["pid", "name", "team", "pos", "p_any", "hit"]].round(4).to_dict("records")))
        top15_frames.append(top[["pid", "name", "team", "pos", "p_any", "hit", "season", "week"]])
        th = {r.pid: bool(r.two_hit) for r in pl.itertuples()}
        for mode, v in d.get("parlays", {}).get("modes", {}).items():
            paper = [c for c in v.get("parlays", []) if c.get("paper")]
            if paper and all(any(l["team"] == t for t in set(pl.team)) for c in paper for l in c["legs"]):
                graded, legs = PL.grade(paper, th)
                parlays.append(dict(season=d["season"], week=d["week"], mode=mode, parlays=graded, **legs))
    allp = pd.concat(players_all) if players_all else pd.DataFrame(columns=["p_any", "hit", "season", "week"])

    # ---- fitted model+market blend (refit every run on all graded priced rows), then the bet record under the live rule ----
    models = {mk: rec for mk in BL.MARKETS if (rec := BL.fit_market(allp, mk))}
    BL.save(models)
    for mk, m in models.items():
        print(f"  blend {mk}: coef {m['coef']} n {m['n']} | Brier model {m['brier']['model']} market {m['brier']['market']} 50/50 {m['brier']['half']} fitted {m['brier']['fitted']}"
              + (f" | leave-one-week-out: fitted {m['loo']['fitted']} 50/50 {m['loo']['half']} market {m['loo']['market']} model {m['loo']['model']}" if m.get("loo") else ""))
    bets = rescore_edges(allp, models)

    # ---- top-15 regulars: everyone who has made any week's top 15 ----
    regulars = []
    if top15_frames:
        t15 = pd.concat(top15_frames).sort_values(["season", "week"])
        ident = t15.groupby("pid").tail(1).set_index("pid")[["name", "team", "pos"]]
        agg = t15.groupby("pid").agg(n=("hit", "size"), hits=("hit", "sum"), avg_p=("p_any", "mean"))
        agg["hit_rate"] = agg.hits / agg.n
        agg["diff"] = agg.hit_rate - agg.avg_p
        agg = agg.join(ident).sort_values(["n", "hit_rate", "avg_p"], ascending=[False, False, False])
        regulars = [dict(pid=i, name=r["name"], team=r.team, pos=r.pos, n=int(r.n), hits=int(r.hits),
                         hit_rate=round(float(r.hit_rate), 3), avg_p=round(float(r.avg_p), 3), diff=round(float(r["diff"]), 3))
                    for i, r in agg.iterrows()]

    # ---- calibration, current season, 5-point buckets ----
    cal, cal_season = [], None
    if len(allp):
        cal_season = int(allp.season.max())
        cur = allp[allp.season == cal_season]
        cal = calib_buckets(cur.p_any, cur.hit)

    # season total across graded weeks (Brier weighted by players)
    season_total = None
    if weeks:
        cur = [w for w in weeks if w["season"] == max(x["season"] for x in weeks)]
        n_pl = sum(w["players"] for w in cur)
        season_total = dict(season=cur[0]["season"], weeks=len(cur), games=sum(w["games"] for w in cur), games_total=sum(w["games_total"] for w in cur),
                            exp_scorers=round(sum(w["exp_scorers"] for w in cur), 1), act_scorers=sum(w["act_scorers"] for w in cur),
                            top15_hit=sum(w["top15_hit"] for w in cur), top15_n=15 * len(cur), top15_exp=round(sum(w["top15_exp"] for w in cur), 1),
                            strong_hit=sum(w["strong_hit"] for w in cur), strong_n=sum(w["strong_n"] for w in cur), strong_exp=round(sum(w["strong_exp"] for w in cur), 1),
                            first_top3=sum(w["first_top3"] for w in cur), first_games=sum(w["first_games"] for w in cur), first_top3_exp=round(sum(w["first_top3_exp"] for w in cur), 1),
                            brier=round(sum(w["brier"] * w["players"] for w in cur) / n_pl, 4) if n_pl else None)
    last = weeks[-1] if weeks else None
    detail = []
    if last:
        lp = allp[(allp.season == last["season"]) & (allp.week == last["week"])].nlargest(40, "p_any")
        detail = lp[["name", "team", "pos", "p_any", "hit", "p_first", "first_hit", "p_2plus", "two_hit"]].to_dict("records")
    b = pd.DataFrame(bets)

    def tally(g):
        return dict(n=int(len(g)), won=int(g.won.sum()) if len(g) else 0,
                    units=round(float(g.profit.sum()), 2) if len(g) else 0.0,
                    expected_units=round(float(g.ev.sum()), 2) if len(g) else 0.0,     # sum of EV at the price taken
                    roi=round(float(g.profit.mean()), 4) if len(g) else None)
    by_role = {k: tally(g) for k, g in b.groupby("role")} if len(b) else {}
    by_market = {k: tally(g) for k, g in b.groupby("market")} if len(b) else {}
    by_price = {"under_1000": tally(b[~b.longshot]), "1000_plus": tally(b[b.longshot])} if len(b) else {}

    # ---- market Brier: score the no-vig median-book probability the same way as the model, where a price existed ----
    COLS = {"any": ("any_mkt_p", "hit", "p_any"), "first": ("first_mkt_p", "first_hit", "p_first"), "two": ("two_mkt_p", "two_hit", "p_2plus")}
    def mb(m, mk):
        c = COLS[mk]; mp = m[c[0]].astype(float); y = m[c[1]].astype(float); pm = m[c[2]].astype(float)
        return dict(n=int(len(m)), model=round(float(((pm - y) ** 2).mean()), 4), market=round(float(((mp - y) ** 2).mean()), 4),
                    blend=round(float(((0.5 * pm + 0.5 * mp - y) ** 2).mean()), 4))
    market_brier = {}
    for mk, pcol in [("any", "any_mkt_p"), ("first", "first_mkt_p"), ("two", "two_mkt_p")]:
        if pcol in allp:
            m = allp[allp[pcol].notna()]
            if len(m):
                market_brier[mk] = dict(**mb(m, mk), weeks=int(m[["season", "week"]].drop_duplicates().shape[0]),
                                        by_week=[dict(season=int(sn), week=int(wk), **mb(g, mk)) for (sn, wk), g in m.groupby(["season", "week"])])

    # ---- closing line value for logged edges (flag-time vs last pull before kickoff) ----
    clv_rows = []
    for fn in sorted(glob.glob(f"{C.EDGE_DIR}/*_w*.json")):
        sn, wk = map(int, os.path.basename(fn)[:-5].split("_w"))
        if not any(w["season"] == sn and w["week"] == wk for w in weeks):
            continue                                             # only graded weeks
        log = C.closing_summary(sn, wk)
        clv_rows += [dict(season=sn, week=wk, **e) for e in log.values()]
    clv_summary = C.aggregate(clv_rows) if clv_rows else None
    # written once by `python backtest.py 0.35` (walk-forward over 2025), committed alongside the code
    backtest = json.load(open("backtest_2025.json")) if os.path.exists("backtest_2025.json") else None
    def psum(ws):
        allp_ = [c for w in ws for c in w["parlays"]]
        return dict(weeks=len({(w["season"], w["week"]) for w in ws}), n=len(allp_), won=sum(c["won"] for c in allp_),
                    units=round(sum(c["profit"] for c in allp_), 2), expected_units=round(sum(c["ev"] or 0 for c in allp_), 2),
                    expected_wins=round(sum(c["prob"] for c in allp_), 2), legs=sum(w["legs"] for w in ws),
                    legs_hit=sum(w["legs_hit"] for w in ws), legs_expected=round(sum(w["legs_expected"] for w in ws), 2))
    parlay_summary = {m: psum([w for w in parlays if w["mode"] == m]) for m in PL.MODES if any(w["mode"] == m for w in parlays)} if parlays else None
    if parlay_summary: parlay_summary["by_week"] = parlays
    out = dict(weeks=weeks, season_total=season_total, parlays=parlay_summary, calibration=cal, cal_season=cal_season, last_week=detail, bets=bets,
               top15_by_week=sorted(top15_weeks, key=lambda w: (-w["season"], -w["week"])),
               top15_regulars=regulars,
               bet_summary=dict(**tally(b), by_role=by_role, by_market=by_market, by_price=by_price), market_brier=market_brier,
               last_week_meta=dict(season=last["season"], week=last["week"], graded=last["games"], total=last["games_total"]) if last else None,
               clv=clv_summary, clv_rows=[{k: v for k, v in r.items() if k in ("season", "week", "bet", "role", "market", "book", "best", "mkt_p", "close_best", "close_mkt_p", "clv", "beat_close")} for r in clv_rows if "clv" in r],
               backtest_2025=backtest, blend=models)
    json.dump(out, open("results.json", "w"), default=lambda o: o.item() if hasattr(o, "item") else str(o))
    print("graded weeks:", [(w["season"], w["week"]) for w in weeks], "bets:", len(bets),
          "| top-15 regulars:", len(regulars), "| backtest:", "yes" if backtest else "missing backtest_2025.json")


if __name__ == "__main__":
    main()
