"""Grade every saved slate against what actually happened. Writes results.json."""
import glob, json, os, re, numpy as np, pandas as pd
import clv as C, parlay as PL, blend as BL
from nansafe import val, flag, text, num, isnan

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
RULES_WEEK = 4                                        # first week graded under the current Edge Board rules (yards, 2+ TD, parlay log)


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
            if text(r, "weak_spot") or num(r, pre + "nbooks", 0) < MIN_BOOKS or isnan(val(r, pre + "med")):
                continue
            ev, ev_med = float(r.blend) * O.decimal(r[pre + "best"]) - 1, float(r.blend) * O.decimal(r[pre + "med"]) - 1
            if ev < MIN_EV or ev_med < MIN_EV:
                continue
            won = bool(r[ycol]); dec = O.decimal(r[pre + "best"])
            out.append(dict(season=int(r.season), week=int(r.week), bet=f"{r['name']} {lbl}", price=int(r[pre + "best"]), book=val(r, pre + "book"),
                            ev=round(ev, 4), ev_med=round(ev_med, 4), blend_p=round(float(r.blend), 4), model_p=round(float(r[pcol]), 4), mkt_p=round(float(r[kcol]), 4),
                            won=won, profit=round(dec - 1 if won else -1.0, 3), role=text(r, "role") or "Unknown", market=mk,
                            longshot=int(r[pre + "best"]) >= 1000, backfill_price=flag(r, "backfill_price"), fitted=coef is not None))
    return out


def main():
    p = pd.read_parquet("data/pbp.parquet")
    s = pd.read_parquet("data/sched.parquet")
    final = set(s[s.home_score.notna()].game_id)
    td = p[(p.touchdown == 1) & p.td_player_id.notna()]
    off = td[(td.rush_touchdown == 1) | (td.pass_touchdown == 1)]
    scored = off.groupby("game_id").td_player_id.apply(set).to_dict()
    import yards_backtest as YB
    ymap = {(int(r.week), r.pid, r.kind): float(r.y) for r in YB.actual_yards(p, int(s.season.max())).itertuples()} if "rushing_yards" in p else {}
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
        plog_fn = f"{PL.LOG_DIR}/{d['season']}_w{d['week']}.json"      # paper parlays frozen at first flag (both modes); slate as fallback
        plog = json.load(open(plog_fn)) if os.path.exists(plog_fn) else None
        modes_src = {m: {"parlays": v["parlays"]} for m, v in plog.items()} if plog else d.get("parlays", {}).get("modes", {})
        for mode, v in modes_src.items():
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
    # yard edges are graded from the edge log (first flag, flag-time prices; both the main-line and alt-rung categories, whichever
    # rule flagged them), so a later run that changes the rule cannot drop them; the slate's edge list is the fallback
    def yard_edge_source(d):
        fn = f"{C.EDGE_DIR}/{d['season']}_w{d['week']}.json"
        if os.path.exists(fn):
            log = json.load(open(fn)); rows = [v for v in log.values() if v.get("market") in ("yds", "yds_alt")]
            if rows: return rows
        return [e for e in d.get("edges", []) if e.get("market") in ("yds", "yds_alt")]
    for fn in sorted(glob.glob("slate_*_w*.json")):
        d = json.load(open(fn))
        if not any(w["season"] == d["season"] and w["week"] == d["week"] for w in weeks): continue
        for e in yard_edge_source(d):
            if (d["week"], e["pid"], e.get("kind")) not in ymap: continue
            won = ymap[(d["week"], e["pid"], e["kind"])] >= float(e["line"]); dec = 1 + (e["best"] / 100 if e["best"] > 0 else 100 / -e["best"])
            bets.append(dict(season=d["season"], week=d["week"], bet=e["bet"], price=e["best"], book=e["book"], ev=e["ev"], ev_med=e.get("ev_med"), blend_p=e.get("blend_p"),
                             model_p=val(e, "model_p"), mkt_p=val(e, "mkt_p"), won=bool(won), profit=round(dec - 1 if won else -1.0, 3), role=text(e, "role") or "Unknown", market=e["market"],
                             longshot=e["best"] >= 1000, backfill_price=False, fitted=False, rule=e.get("rule")))
    # research: alt-rung candidates that failed the price / ratio filters, graded by price band (never bets)
    research = []
    for fn in sorted(glob.glob("research/alt_rungs_*_w*.json")):
        sn, wk = (int(x) for x in re.findall(r"(\d+)_w(\d+)", fn)[0])
        if not any(w["season"] == sn and w["week"] == wk for w in weeks): continue
        for c in json.load(open(fn)).values():
            y = ymap.get((wk, c["pid"], c["kind"]))
            if y is None: continue
            dec = 1 + (c["best"] / 100 if c["best"] > 0 else 100 / -c["best"]); won = y >= float(c["line"])
            research.append(dict(week=wk, price=c["best"], won=won, profit=dec - 1 if won else -1.0, ev=c["ev"], reasons=c.get("reasons", [])))
    def band(pr): return "shorter than -150" if pr <= -150 else "-150 to +150" if pr < 150 else "+150 to +300" if pr < 300 else "longer than +300"
    research_alt = None
    if research:
        rd = pd.DataFrame(research); rd["band"] = rd.price.apply(band)
        t = lambda g: dict(n=int(len(g)), won=int(g.won.sum()), units=round(float(g.profit.sum()), 2), expected=round(float(g.ev.sum()), 2))
        research_alt = dict(all=t(rd), by_band={k: t(g) for k, g in rd.groupby("band")}, by_week={int(k): t(g) for k, g in rd.groupby("week")})

    # ---- freeze the bet record: once a week is fully graded its bets are stored in bets_frozen.json under the rule in force
    #      then and never recomputed; "under current rules" is a separate re-score of every week with today's rule and blend ----
    FROZEN = "bets_frozen.json"
    frozen = json.load(open(FROZEN)) if os.path.exists(FROZEN) else {}
    bets_current = list(bets)                                           # today's rule applied to every week
    partial = {(w["season"], w["week"]) for w in weeks if w.get("partial")}
    rule_now = ("EV >= 5% at the median and best book, 2+ books, no weak-spot players, " + ("fitted" if any(BL.coef_for(models, m) for m in ("any", "first", "two")) else "50/50")
                + " model-market blend; closing prices for backfilled weeks, flag-time prices for live weeks; yard edges from the slate's edge list at a 10-yard floor")
    by_wk = {}
    for b_ in bets: by_wk.setdefault(f"{b_['season']}-{b_['week']}", []).append(b_)
    for w in weeks:
        key = f"{w['season']}-{w['week']}"
        if key not in frozen and (w["season"], w["week"]) not in partial:
            frozen[key] = dict(rule=rule_now, frozen_at=pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%dT%H:%MZ"), bets=by_wk.get(key, []))
    json.dump(frozen, open(FROZEN, "w"), indent=0)
    bets = [b_ for key in sorted(frozen) for b_ in frozen[key]["bets"]] + [b_ for key, rows in by_wk.items() if key not in frozen for b_ in rows]
    print(f"  bet record: {len(bets)} bets ({sum(len(v['bets']) for v in frozen.values())} frozen over {len(frozen)} weeks); under current rules {len(bets_current)}")

    # ---- yards head-to-head at the books' main lines, from week 4 on: model P(over) vs no-vig book P(over) vs 50/50,
    #      and whether our median or the book line was closer to the actual yards ----
    yrows = []
    for fn in sorted(glob.glob("slate_*_w*.json")):
        d = json.load(open(fn))
        if not any(w["season"] == d["season"] and w["week"] == d["week"] for w in weeks): continue
        for r in d.get("yards", []):
            if isnan(val(r, "main_line")) or isnan(val(r, "main_mkt_p")) or isnan(val(r, "main_p")): continue
            y = ymap.get((d["week"], r["pid"], r["kind"]))
            if y is None or y == float(r["main_line"]): continue              # no touches (books void) or a push
            yrows.append(dict(season=d["season"], week=d["week"], kind=r["kind"], pid=r["pid"], line=float(r["main_line"]), p_yds=float(r["main_p"]),
                              yds_mkt_p=float(r["main_mkt_p"]), median=float(r["median"]), y=float(y), yds_hit=int(y > float(r["main_line"]))))
    ydf = pd.DataFrame(yrows)

    def yh2h(g):
        pm, pk, yv = g.p_yds.values, g.yds_mkt_p.values, g.yds_hit.values.astype(float)
        dm, db = np.abs(g["median"].values - g.y.values), np.abs(g.line.values - g.y.values)
        return dict(n=int(len(g)), model=round(BL.brier(pm, yv), 4), book=round(BL.brier(pk, yv), 4), blend=round(BL.brier(0.5 * pm + 0.5 * pk, yv), 4),
                    median_closer=int((dm < db).sum()), book_closer=int((db < dm).sum()), ties=int((dm == db).sum()),
                    median_closer_pct=round(float((dm < db).sum() / max(1, (dm != db).sum())), 4))
    yards_h2h = None
    if len(ydf):
        yards_h2h = dict(season=dict(**yh2h(ydf), weeks=int(ydf[["season", "week"]].drop_duplicates().shape[0])),
                         by_week=[dict(season=int(sn), week=int(wk), **yh2h(g)) for (sn, wk), g in ydf.groupby(["season", "week"])],
                         by_kind={k: yh2h(g) for k, g in ydf.groupby("kind")})
        # yard edges stay paper-only until the model beats the book on Brier over at least 3 graded weeks
        yards_h2h["paper_only"] = not (yards_h2h["season"]["weeks"] >= 3 and yards_h2h["season"]["model"] < yards_h2h["season"]["book"])
        if yards_h2h["season"]["weeks"] >= 3:                                # fitted blend for yards, same gate as the TD markets
            rec = BL.fit_market(ydf, "yds")
            if rec: models["yds"] = rec; BL.save(models)
        s_ = yards_h2h["season"]; print(f"  yards vs book: n {s_['n']} weeks {s_['weeks']} | Brier model {s_['model']} book {s_['book']} 50/50 {s_['blend']} | median closer {s_['median_closer']} of {s_['median_closer'] + s_['book_closer']} | paper only: {yards_h2h['paper_only']}")

    # ---- actual stats per fully graded week, for grading slips in the browser (Slips tab log) ----
    #      columns: rec, rec_yds, rush_yds, pass_yds, pass_att, fpts (half-PPR); a player with no touches has no row (a void leg)
    actuals = {}
    cur_season = int(s.season.max())
    _sa = YB.actual_stats(p, cur_season) if "complete_pass" in p else None
    for fn in sorted(glob.glob(f"slate_{cur_season}_w*.json")):
        d = json.load(open(fn))
        w = next((w for w in weeks if w["season"] == d["season"] and w["week"] == d["week"]), None)
        if _sa is None or w is None or w.get("partial"): continue
        pids = {r["pid"] for r in d.get("players", [])} | {r["pid"] for r in (d.get("draws") or {}).get("players", [])}
        wk = _sa[(_sa.week == d["week"]) & _sa.pid.isin(pids)]
        actuals[f"{d['season']}-{d['week']}"] = {r.pid: [int(r.rec), int(r.rec_yds), int(r.rush_yds), int(r.pass_yds), int(r.pass_att), round(float(r.fpts), 1)] for r in wk.itertuples()}

    # ---- slips log (slips_log.jsonl, appended by `python slips.py ... --log`): graded on actual stats once the week is complete ----
    import slips as SL
    slips_rows = []
    if os.path.exists(SL.LOG):
        for line in open(SL.LOG, encoding="utf-8"):
            line = line.strip()
            if not line: continue
            try: sl = json.loads(line)
            except Exception: continue
            key = f"{sl['season']}-{sl['week']}"
            A = actuals.get(key)
            g = SL.grade(sl, A) if A is not None else dict(status="pending")
            slips_rows.append({**{k: sl.get(k) for k in ("id", "ts", "season", "week", "n_legs", "p_joint", "p_prod", "p_joint_ex", "payout", "ev", "legs", "same_player")}, **g})
    done = [r for r in slips_rows if r["status"] in ("won", "lost")]
    slips_summary = dict(n=len(slips_rows), graded=len(done), won=sum(1 for r in done if r["status"] == "won"), units=round(sum(r["units"] for r in done), 2),
                         expected=round(sum((r["ev"] or 0) for r in done), 2), refund=sum(1 for r in slips_rows if r["status"] == "refund"), pending=sum(1 for r in slips_rows if r["status"] == "pending"))

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
    # live weeks = graded from a slate built before the games with flag-time prices; backfill weeks were rebuilt after the fact
    # (weeks 1-2, closing prices). RULES_WEEK = first week under the current Edge Board rules (yards, 2+ TD, parlays frozen).
    backfill_weeks = {(w["season"], w["week"]) for w in weeks if w.get("backfill")}
    live_from = min([w["week"] for w in weeks if not w.get("backfill")], default=None)
    record_scopes = dict(live_from=live_from, rules_from=RULES_WEEK, backfill_weeks=sorted(wk for _, wk in backfill_weeks))
    for b_ in bets: b_["backfill"] = (int(b_["season"]), int(b_["week"])) in backfill_weeks      # tag: rebuilt after the fact at closing prices, never in the live record
    cal, cal_live, cal_season = [], [], None
    if len(allp):
        cal_season = int(allp.season.max())
        cur = allp[allp.season == cal_season]
        cal = calib_buckets(cur.p_any, cur.hit)
        live = cur[~cur.week.isin([wk for sn, wk in backfill_weeks if sn == cal_season])]
        cal_live = calib_buckets(live.p_any, live.hit) if len(live) else []

    # season total across graded weeks (Brier weighted by players), all weeks and live weeks only
    def _season_total(cur, label):
        n_pl = sum(w["players"] for w in cur)
        return dict(season=cur[0]["season"], weeks=len(cur), label=label, games=sum(w["games"] for w in cur), games_total=sum(w["games_total"] for w in cur),
                    exp_scorers=round(sum(w["exp_scorers"] for w in cur), 1), act_scorers=sum(w["act_scorers"] for w in cur),
                    top15_hit=sum(w["top15_hit"] for w in cur), top15_n=15 * len(cur), top15_exp=round(sum(w["top15_exp"] for w in cur), 1),
                    strong_hit=sum(w["strong_hit"] for w in cur), strong_n=sum(w["strong_n"] for w in cur), strong_exp=round(sum(w["strong_exp"] for w in cur), 1),
                    first_top3=sum(w["first_top3"] for w in cur), first_games=sum(w["first_games"] for w in cur), first_top3_exp=round(sum(w["first_top3_exp"] for w in cur), 1),
                    brier=round(sum(w["brier"] * w["players"] for w in cur) / n_pl, 4) if n_pl else None)
    season_total = season_total_live = None
    if weeks:
        cur = [w for w in weeks if w["season"] == max(x["season"] for x in weeks)]
        season_total = _season_total(cur, "all weeks")
        live = [w for w in cur if not w.get("backfill")]
        season_total_live = _season_total(live, "live weeks only") if live else None
    last = weeks[-1] if weeks else None
    detail = []
    if last:
        lp = allp[(allp.season == last["season"]) & (allp.week == last["week"])].nlargest(40, "p_any")
        detail = lp[["name", "team", "pos", "p_any", "hit", "p_first", "first_hit", "p_2plus", "two_hit"]].to_dict("records")
    b = pd.DataFrame(bets)
    if len(b): b["role"] = b["role"].apply(lambda v: v if isinstance(v, str) and v else "Unknown")

    def tally(g):
        return dict(n=int(len(g)), won=int(g.won.sum()) if len(g) else 0,
                    units=round(float(g.profit.sum()), 2) if len(g) else 0.0,
                    expected_units=round(float(g.ev.sum()), 2) if len(g) else 0.0,     # sum of EV at the price taken
                    roi=round(float(g.profit.mean()), 4) if len(g) else None)
    bc = pd.DataFrame(bets_current)
    rescore_current = dict(**tally(bc), short=tally(bc[~bc.longshot]) if len(bc) else tally(bc), rule=rule_now,
                           by_market={k: tally(g) for k, g in bc.groupby("market")} if len(bc) else {}) if len(bc) else None
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
                                        by_week=[dict(season=int(sn), week=int(wk), backfill=(int(sn), int(wk)) in backfill_weeks, **mb(g, mk)) for (sn, wk), g in m.groupby(["season", "week"])])

    # TD singles stay paper-only until the season-to-date 50/50 blend beats the market by at least 0.001 Brier (anytime TD)
    _a = market_brier.get("any")
    paper_only = dict(td=not (_a and _a["market"] - _a["blend"] >= 0.001), yds=yards_h2h["paper_only"] if yards_h2h else True,
                      td_rule="season-to-date 50/50 blend beats the no-vig market by 0.001 Brier on anytime TD",
                      yds_rule="model P(over) beats the no-vig book on Brier at the main lines over at least 3 graded weeks")
    print(f"  paper only: TD {paper_only['td']} (blend {_a['blend'] if _a else None} vs market {_a['market'] if _a else None}) | yards {paper_only['yds']}")

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
    slips_bt = json.load(open("slips_backtest.json")) if os.path.exists("slips_backtest.json") else None
    def psum(ws):
        allp_ = [c for w in ws for c in w["parlays"]]
        return dict(weeks=len({(w["season"], w["week"]) for w in ws}), n=len(allp_), won=sum(c["won"] for c in allp_),
                    units=round(sum(c["profit"] for c in allp_), 2), expected_units=round(sum(c["ev"] or 0 for c in allp_), 2),
                    expected_wins=round(sum(c["prob"] for c in allp_), 2), legs=sum(w["legs"] for w in ws),
                    legs_hit=sum(w["legs_hit"] for w in ws), legs_expected=round(sum(w["legs_expected"] for w in ws), 2))
    parlay_summary = {m: psum([w for w in parlays if w["mode"] == m]) for m in PL.MODES if any(w["mode"] == m for w in parlays)} if parlays else None
    if parlay_summary: parlay_summary["by_week"] = parlays
    # ---- bets by week: edges by market plus parlay paper trades, with a cumulative row; priced = closing (backfill) or live ----
    rows_bw = [dict(season=b["season"], week=b["week"], market=b["market"], won=b["won"], profit=b["profit"], ev=b["ev"], longshot=b["longshot"],
                    closing=bool(b.get("backfill_price"))) for b in bets]
    for w in (parlays or []):
        for c in w["parlays"]:
            if c.get("dec"): rows_bw.append(dict(season=w["season"], week=w["week"], market="parlay", won=c["won"], profit=c["profit"], ev=c["ev"] or 0.0, longshot=False, closing=False))
    def agg(rs):
        return dict(n=len(rs), won=sum(r["won"] for r in rs), units=round(sum(r["profit"] for r in rs), 2), expected=round(sum(r["ev"] for r in rs), 2),
                    roi=round(sum(r["profit"] for r in rs) / len(rs), 4) if rs else None)
    bets_by_week = []
    for (sn, wk) in sorted({(r["season"], r["week"]) for r in rows_bw}):
        rs = [r for r in rows_bw if r["season"] == sn and r["week"] == wk]
        bets_by_week.append(dict(season=sn, week=wk, priced="closing" if all(r["closing"] for r in rs if r["market"] != "parlay") else "live" if not any(r["closing"] for r in rs) else "mixed",
                                 all=agg(rs), short=agg([r for r in rs if not r["longshot"]]),
                                 by_market={m: dict(all=agg([r for r in rs if r["market"] == m]), short=agg([r for r in rs if r["market"] == m and not r["longshot"]]))
                                            for m in ("any", "first", "two", "yds", "yds_alt", "parlay") if any(r["market"] == m for r in rs)}))
    bets_cum = dict(all=agg(rows_bw), short=agg([r for r in rows_bw if not r["longshot"]]),
                    by_market={m: dict(all=agg([r for r in rows_bw if r["market"] == m]), short=agg([r for r in rows_bw if r["market"] == m and not r["longshot"]]))
                               for m in ("any", "first", "two", "yds", "yds_alt", "parlay") if any(r["market"] == m for r in rows_bw)}) if rows_bw else None
    out = dict(weeks=weeks, season_total=season_total, season_total_live=season_total_live, record_scopes=record_scopes, parlays=parlay_summary, bets_by_week=bets_by_week, bets_cum=bets_cum,
               calibration=cal, calibration_live=cal_live, cal_season=cal_season, last_week=detail, bets=bets,
               top15_by_week=sorted(top15_weeks, key=lambda w: (-w["season"], -w["week"])),
               top15_regulars=regulars,
               bet_summary=dict(**tally(b), by_role=by_role, by_market=by_market, by_price=by_price), market_brier=market_brier,
               last_week_meta=dict(season=last["season"], week=last["week"], graded=last["games"], total=last["games_total"]) if last else None,
               clv=clv_summary, clv_rows=[{k: v for k, v in r.items() if k in ("season", "week", "bet", "role", "market", "book", "best", "mkt_p", "close_best", "close_mkt_p", "clv", "beat_close")} for r in clv_rows if "clv" in r],
               backtest_2025=backtest, blend=models, yards_h2h=yards_h2h, paper_only=paper_only,
               actuals=actuals, actuals_cols=["rec", "rec_yds", "rush_yds", "pass_yds", "pass_att", "fpts"],
               rescore_current=rescore_current, frozen_rules={k: dict(rule=v["rule"], frozen_at=v["frozen_at"], n=len(v["bets"])) for k, v in frozen.items()},
               slips=dict(rows=slips_rows, summary=slips_summary), slips_backtest=slips_bt, research_alt=research_alt)
    json.dump(out, open("results.json", "w"), default=lambda o: o.item() if hasattr(o, "item") else str(o))
    print("graded weeks:", [(w["season"], w["week"]) for w in weeks], "bets:", len(bets),
          "| top-15 regulars:", len(regulars), "| backtest:", "yes" if backtest else "missing backtest_2025.json")


if __name__ == "__main__":
    main()
