"""Grade every saved slate against what actually happened. Writes results.json."""
import glob, json, os, re, numpy as np, pandas as pd
import clv as C, parlay as PL, blend as BL, rules as RU, yards as Y, decision as DEC, placed_bets as PB
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


def rescore_edges(allp, models, rules_from=RU.BOARD_RULES_WEEK):
    """Edge Board rule applied to every graded, priced row with the fitted blend: EV >= MIN_EV at the median book
       AND at the best book, 2+ books, no weak-spot players. Live weeks use flag-time prices stored in the slate;
       backfilled weeks use closing prices. Returns bet records.
       Board rules (rules.py: Bench at 0.25u, Rotational blocked over 2.5x the market) apply from week rules_from
       (BOARD_RULES_WEEK for the record; 0 = every week; None = never, the plain 1u blend re-score)."""
    import odds as O
    out = []
    for mk, (pcol, kcol, ycol, pre, lbl) in {"any": ("p_any", "any_mkt_p", "hit", "any_", "Anytime TD"), "first": ("p_first", "first_mkt_p", "first_hit", "first_", "First TD"),
                                             "two": ("p_2plus", "two_mkt_p", "two_hit", "two_", "2+ TD")}.items():
        if kcol not in allp or pre + "best" not in allp:
            continue
        d = allp[allp[kcol].notna() & allp[pre + "best"].notna() & allp[pcol].notna()].copy()
        if d.empty: continue
        coef = BL.coef_for(models, mk)
        d["blend"] = BL.blend(d[pcol].values, d[kcol].values, coef)
        for _, r in d.iterrows():
            if text(r, "weak_spot") or num(r, pre + "nbooks", 0) < MIN_BOOKS or isnan(val(r, pre + "med")):
                continue
            ev, ev_med = float(r.blend) * O.decimal(r[pre + "best"]) - 1, float(r.blend) * O.decimal(r[pre + "med"]) - 1
            if ev < MIN_EV or ev_med < MIN_EV:
                continue
            role = text(r, "role") or "Unknown"; rules_on = rules_from is not None and int(r.week) >= rules_from
            if rules_on and RU.ratio_blocked(role, float(r[pcol]), float(r[kcol])):
                continue
            stake = RU.stake_for(role) if rules_on else 1.0
            won = bool(r[ycol]); dec = O.decimal(r[pre + "best"])
            out.append(dict(season=int(r.season), week=int(r.week), bet=f"{r['name']} {lbl}", pid=val(r, "pid"), price=int(r[pre + "best"]), book=val(r, pre + "book"),
                            ev=round(ev, 4), ev_med=round(ev_med, 4), blend_p=round(float(r.blend), 4), model_p=round(float(r[pcol]), 4), mkt_p=round(float(r[kcol]), 4),
                            won=won, profit=round(stake * (dec - 1) if won else -stake, 3), stake=stake, ratio=RU.ratio_of(float(r[pcol]), float(r[kcol])),
                            role=role, market=mk, longshot=int(r[pre + "best"]) >= 1000, backfill_price=flag(r, "backfill_price"), fitted=coef is not None))
    return out


def kind_of(b):
    """Yard kind of a bet (rush / rec / pass) from its field or its text; None for TD markets."""
    k = b.get("kind") if isinstance(b.get("kind"), str) else None
    if k: return k
    m = re.search(r" (rush|rec|pass) yds", b.get("bet") or "")
    return m.group(1) if m else None


def under_current_rules(b):
    """The recorded bet as today's board rules would carry it: passing-yard rungs out, Rotational players over 2.5x the
       market out, Bench at BENCH_STAKE units (profit and expected rescaled), everything else unchanged. None = dropped."""
    role = b.get("role") if isinstance(b.get("role"), str) and b.get("role") else "Unknown"; kind = kind_of(b)
    if kind in RU.HIDDEN_KINDS: return None
    if RU.ratio_blocked(role, b.get("model_p"), b.get("mkt_p")): return None
    old = float(b.get("stake") or 1.0); stake = RU.stake_for(role)
    return dict(b, stake=stake, profit=round(float(b["profit"]) / old * stake, 3), kind=kind, role=role)


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
            log = json.load(open(fn)); rows = [v for v in log.values() if v.get("market") in ("yds", "yds_alt") and not v.get("void")]   # void = {reason, at}: never placeable, out of every record
            if rows: return rows
        return [e for e in d.get("edges", []) if e.get("market") in ("yds", "yds_alt")]
    def yard_bets(rules_from=RU.BOARD_RULES_WEEK):
        out = []
        for fn in sorted(glob.glob("slate_*_w*.json")):
            d = json.load(open(fn))
            if not any(w["season"] == d["season"] and w["week"] == d["week"] for w in weeks): continue
            for e in yard_edge_source(d):
                if (d["week"], e["pid"], e.get("kind")) not in ymap: continue
                role = text(e, "role") or "Unknown"; rules_on = rules_from is not None and int(d["week"]) >= rules_from
                if rules_on and (e.get("kind") in RU.HIDDEN_KINDS or RU.ratio_blocked(role, val(e, "model_p"), val(e, "mkt_p"))): continue
                stake = (RU.stake_for(role) if rules_on else 1.0) if rules_from != RU.BOARD_RULES_WEEK else float(val(e, "stake", 1.0))   # the record keeps the stake the edge was logged at
                won = ymap[(d["week"], e["pid"], e["kind"])] >= float(e["line"]); dec = 1 + (e["best"] / 100 if e["best"] > 0 else 100 / -e["best"])
                out.append(dict(season=d["season"], week=d["week"], bet=e["bet"], pid=e["pid"], kind=e.get("kind"), price=e["best"], book=e["book"], ev=e["ev"], ev_med=e.get("ev_med"), blend_p=e.get("blend_p"),
                                model_p=val(e, "model_p"), mkt_p=val(e, "mkt_p"), won=bool(won), profit=round(stake * (dec - 1) if won else -stake, 3), stake=stake,
                                ratio=RU.ratio_of(val(e, "model_p"), val(e, "mkt_p")), role=role, market=e["market"],
                                longshot=e["best"] >= 1000, backfill_price=False, fitted=False, rule=e.get("rule")))
        return out
    bets += yard_bets()
    # research logs (never bets): alt-rung candidates that failed the price / ratio filters, hidden passing-yard rungs, ratio-blocked
    # Rotational edges. Yard rows grade on actual yards, TD rows on the graded slate rows.
    tdhit = {(int(r.week), r.pid): (bool(r.hit), bool(r.first_hit), bool(r.two_hit)) for r in allp.itertuples()} if len(allp) else {}
    # ---- primary record (ruling 7, 2026-10-29): for every graded week with a decision log, the bets are the capped card at each game's decision
    #      pull at that pull's prices (decision.grade); slate re-scoring and the first-seen edge log no longer feed the record for those weeks ----
    dec_weeks = {}
    for fn in sorted(glob.glob(f"{DEC.DIR}/*_w*.json")):
        sn, wk = (int(x) for x in re.findall(r"(\d+)_w(\d+)", os.path.basename(fn))[0])
        if not any(w["season"] == sn and w["week"] == wk for w in weeks): continue
        log = json.load(open(fn))
        dec_weeks[(sn, wk)] = dict(bets=DEC.grade(log, sn, wk, tdhit, ymap, final_games=final), summary=DEC.summary(log))
    if dec_weeks:
        bets = [b_ for b_ in bets if (int(b_["season"]), int(b_["week"])) not in dec_weeks] + [b_ for v in dec_weeks.values() for b_ in v["bets"]]
        print("  decision-pull record:", ", ".join(f"{sn} w{wk}: {len(v['bets'])} bets from {v['summary']['decided']} decided game(s), {v['summary']['missed']} missed" for (sn, wk), v in sorted(dec_weeks.items())))
    decision_out = dict(rule="primary record = the capped card at each game's decision pull (Sunday early / late: first pull at or after 9:23 AM PT; other games: last pull before the kickoff cutoff), at that pull's prices",
                        weeks={f"{sn}-{wk}": v["summary"] for (sn, wk), v in dec_weeks.items()})
    # ---- secondary research record: every first-seen edge-log entry graded as logged (first flag, flag-time price, logged stake); never the headline ----
    def first_seen_rows():
        rows = []
        for fn in sorted(glob.glob(f"{C.EDGE_DIR}/*_w*.json")):
            sn, wk = (int(x) for x in re.findall(r"(\d+)_w(\d+)", os.path.basename(fn))[0])
            if not any(w["season"] == sn and w["week"] == wk for w in weeks): continue
            for e in json.load(open(fn)).values():
                if e.get("void") or e.get("best") is None: continue
                if e.get("market") in ("yds", "yds_alt"):
                    y = ymap.get((wk, e["pid"], e.get("kind")))
                    if y is None: continue
                    won = y >= float(e["line"])
                else:
                    h = tdhit.get((wk, e["pid"]))
                    if h is None: continue
                    won = {"any": h[0], "first": h[1], "two": h[2]}.get(e.get("market"), h[0])
                price = int(e["best"]); dec = 1 + (price / 100 if price > 0 else 100 / -price); stake = float(e.get("stake") or 1.0)
                rows.append(dict(season=sn, week=wk, bet=e.get("bet"), pid=e["pid"], market=e.get("market"), kind=e.get("kind"), role=e.get("role"), price=price, book=e.get("book"),
                                 ev=float(e.get("ev") or 0), stake=stake, won=bool(won), profit=round(stake * (dec - 1) if won else -stake, 3), flagged=e.get("flagged")))
        return rows
    def fs_tally(g):
        stk = sum(r["stake"] for r in g)
        return dict(n=len(g), won=sum(1 for r in g if r["won"]), units=round(sum(r["profit"] for r in g), 2), expected=round(sum(r["stake"] * r["ev"] for r in g), 2), staked=round(stk, 2),
                    roi=round(sum(r["profit"] for r in g) / stk, 4) if stk else None)
    _fs = first_seen_rows()
    research_first_seen = dict(all=fs_tally(_fs), by_week={int(k): fs_tally([r for r in _fs if r["week"] == k]) for k in sorted({r["week"] for r in _fs})},
                               by_market={k: fs_tally([r for r in _fs if r["market"] == k]) for k in sorted({r["market"] for r in _fs if r["market"]})},
                               rule="every first-seen edge-log entry at its flag-time price and logged stake (secondary research record, never the headline)") if _fs else None
    def research_rows(pattern):
        rows = []
        for fn in sorted(glob.glob(pattern)):
            sn, wk = (int(x) for x in re.findall(r"(\d+)_w(\d+)", fn)[0])
            if not any(w["season"] == sn and w["week"] == wk for w in weeks): continue
            for c in json.load(open(fn)).values():
                if c.get("market") in ("yds", "yds_alt"):
                    y = ymap.get((wk, c["pid"], c["kind"]))
                    if y is None: continue
                    won = y >= float(c["line"])
                else:
                    h = tdhit.get((wk, c["pid"]))
                    if h is None: continue
                    won = {"any": h[0], "first": h[1], "two": h[2]}.get(c.get("market"), h[0])
                dec = 1 + (c["best"] / 100 if c["best"] > 0 else 100 / -c["best"])
                rows.append(dict(week=wk, pid=c["pid"], bet=c.get("bet"), market=c.get("market"), kind=c.get("kind"), role=c.get("role"), price=c["best"], won=bool(won),
                                 profit=dec - 1 if won else -1.0, ev=c["ev"], reasons=c.get("reasons", [])))
        return rows
    research = research_rows("research/alt_rungs_*_w*.json")
    def band(pr): return "shorter than -150" if pr <= -150 else "-150 to +150" if pr < 150 else "+150 to +300" if pr < 300 else "longer than +300"
    # alt-rung edges by the rule that flagged them (week 5 carries both: the first curve rule, untagged, and "alt rung v2")
    alt_rules = None
    ab = [b_ for b_ in bets if b_.get("market") == "yds_alt"]
    if ab:
        ad = pd.DataFrame(ab); ad["rule"] = ad["rule"].apply(lambda v: v if isinstance(v, str) and v else "old rule (curve-judged, no cap)"); ad["band"] = ad.price.apply(band)
        t2 = lambda g: dict(n=int(len(g)), won=int(g.won.sum()), units=round(float(g.profit.sum()), 2), expected=round(float(g.ev.sum()), 2))
        alt_rules = {rule: dict(all=t2(g), by_band={k: t2(x) for k, x in g.groupby("band")}, by_week={int(k): t2(x) for k, x in g.groupby("week")}) for rule, g in ad.groupby("rule")}
    research_alt = None
    t = lambda g: dict(n=int(len(g)), won=int(g.won.sum()), units=round(float(g.profit.sum()), 2), expected=round(float(g.ev.sum()), 2))
    if research:
        rd = pd.DataFrame(research); rd["band"] = rd.price.apply(band)
        research_alt = dict(all=t(rd), by_band={k: t(g) for k, g in rd.groupby("band")}, by_week={int(k): t(g) for k, g in rd.groupby("week")})
    def research_summary(rows):
        if not rows: return None
        rd = pd.DataFrame(rows)
        return dict(all=t(rd), by_week={int(k): t(g) for k, g in rd.groupby("week")}, by_market={k: t(g) for k, g in rd.groupby("market")},
                    rows=[{k: v for k, v in r.items() if k != "reasons"} for r in rows])
    research_pass = research_summary(research_rows("research/pass_yds_*_w*.json"))       # hidden passing-yard rungs, graded silently
    research_ratio = research_summary(research_rows("research/ratio_block_*_w*.json"))   # Rotational edges over 2.5x the market

    # ---- research (2026-10-18, never in the headline): Underdog pick'em flags graded on actual yards / TD outcomes, hit rate vs break-even by
    #      payout type; line-shop improvements graded as the units the best venue would have added over the flagging book ----
    import pickem_ud as PU, lineshop as LS
    ud_rows = []
    for fn in sorted(glob.glob("research/ud_flags_*_w*.json")):
        sn, wk = (int(x) for x in re.findall(r"(\d+)_w(\d+)", fn)[0])
        if not any(w["season"] == sn and w["week"] == wk for w in weeks): continue
        for c in json.load(open(fn)).values():
            if c.get("market") == "yds":
                y = ymap.get((wk, c["pid"], c["kind"]))
                if y is None or y == float(c["line"]): continue                      # no touches = void; a whole-number tie voids the leg
                hit = y > float(c["line"]) if c["side"] == "higher" else y < float(c["line"])
            else:
                h = tdhit.get((wk, c["pid"]))
                if h is None: continue
                scored = h[1] if c.get("market") == "first" else h[0]
                hit = scored if c["side"] == "higher" else not scored
            ud_rows.append(dict(week=wk, pid=c["pid"], bet=c.get("bet"), market=c.get("market"), kind=c.get("kind"), stat=c.get("stat"), line=c.get("line"), side=c["side"],
                                blend=c.get("blend"), breakeven=c.get("breakeven") or {}, margins=c.get("margins") or {}, mult=c.get("mult"), hit=bool(hit)))
    def ud_agg(g):
        return dict(n=len(g), hits=int(sum(r["hit"] for r in g)), hit_rate=round(sum(r["hit"] for r in g) / len(g), 4), mean_blend=round(sum(r["blend"] for r in g) / len(g), 4)) if g else None
    research_ud = None
    if ud_rows:
        by_type = {}
        for t in PU.payouts():
            g = [r for r in ud_rows if r["margins"].get(t) is not None and r["margins"][t] >= PU.FLAG_MARGIN - 1e-9]
            if g: by_type[t] = dict(ud_agg(g), mean_breakeven=round(sum(r["breakeven"].get(t) for r in g) / len(g), 4), beats_breakeven=bool(sum(r["hit"] for r in g) / len(g) > sum(r["breakeven"].get(t) for r in g) / len(g)))
        research_ud = dict(all=ud_agg(ud_rows), by_type=by_type, by_market={k: ud_agg([r for r in ud_rows if r["market"] == k]) for k in sorted({r["market"] for r in ud_rows})},
                           by_week={int(k): ud_agg([r for r in ud_rows if r["week"] == k]) for k in sorted({r["week"] for r in ud_rows})}, flag_margin=PU.FLAG_MARGIN, ref=PU.FLAG_REF,
                           payouts=PU.payouts(), payouts_confirmed=False, rows=ud_rows)
    ls_rows = []
    for fn in sorted(glob.glob("research/lineshop_*_w*.json")):
        sn, wk = (int(x) for x in re.findall(r"(\d+)_w(\d+)", fn)[0])
        if not any(w["season"] == sn and w["week"] == wk for w in weeks): continue
        for c in json.load(open(fn)).values():
            if c.get("market") in ("yds", "yds_alt"):
                y = ymap.get((wk, c["pid"], c["kind"]))
                if y is None: continue
                won = y >= float(c["line"])
            else:
                h = tdhit.get((wk, c["pid"]))
                if h is None: continue
                won = {"any": h[0], "first": h[1], "two": h[2]}.get(c.get("market"), h[0])
            dec = lambda a: 1 + (a / 100 if a > 0 else 100 / -a)
            stake = float(c.get("stake") or 1.0)
            ls_rows.append(dict(week=wk, pid=c["pid"], bet=c.get("bet"), market=c.get("market"), venue=c.get("venue"), book=c.get("book"), best=c.get("best"), venue_net=c.get("venue_net"),
                                cents=c.get("cents"), stake=stake, won=bool(won), units_added=round(stake * (dec(c["venue_net"]) - dec(c["best"])) if won else 0.0, 3),
                                fee_confirmed=c.get("fee_confirmed", True)))
    def ls_agg(g):
        return dict(n=len(g), won=int(sum(r["won"] for r in g)), mean_cents=round(sum(r["cents"] for r in g) / len(g), 1), units_added=round(sum(r["units_added"] for r in g), 3)) if g else None
    research_lineshop = None
    if ls_rows:
        research_lineshop = dict(all=ls_agg(ls_rows), by_venue={k: ls_agg([r for r in ls_rows if r["venue"] == k]) for k in sorted({r["venue"] for r in ls_rows})},
                                 by_week={int(k): ls_agg([r for r in ls_rows if r["week"] == k]) for k in sorted({r["week"] for r in ls_rows})}, rows=ls_rows)
    # ungraded counts (this week's logs before grading), so the report can say how many legs are waiting
    pending = dict(ud_flags=sum(len(json.load(open(f))) for f in glob.glob("research/ud_flags_*_w*.json")) - len(ud_rows),
                   lineshop=sum(len(json.load(open(f))) for f in glob.glob("research/lineshop_*_w*.json")) - len(ls_rows))

    # ---- freeze the bet record: once a week is fully graded its bets are stored in bets_frozen.json under the rule in force
    #      then and never recomputed; "under current rules" is a separate re-score of every week with today's rule and blend ----
    FROZEN = "bets_frozen.json"
    frozen = json.load(open(FROZEN)) if os.path.exists(FROZEN) else {}
    # voided edge-log entries (edge_log/*.json, `void = {reason, at}`; 2026-10-16: prices from DraftKings "alternate" yardage ladders that
    # were another market) were never placeable: out of the live weeks above (yard_edge_source), out of the frozen weeks here, listed on the Results tab
    voided = []
    for fn in sorted(glob.glob(f"{C.EDGE_DIR}/*_w*.json")):
        sn, wk = (int(x) for x in re.findall(r"(\d+)_w(\d+)", fn)[0])
        for k, v in json.load(open(fn)).items():
            if v.get("void"): voided.append(dict(season=sn, week=wk, key=k, bet=v.get("bet"), market=v.get("market"), book=v.get("book"), best=v.get("best"), flagged=v.get("flagged"), reason=v["void"].get("reason"), at=v["void"].get("at")))
    void_keys = {(v["season"], v["week"], v["bet"]) for v in voided}
    backfill_weeks_or = {(w["season"], w["week"]) for w in weeks if w.get("backfill")}
    for key, fz in frozen.items():
        kept = [b_ for b_ in fz["bets"] if (b_["season"], b_["week"], b_["bet"]) not in void_keys]
        if len(kept) != len(fz["bets"]): print(f"  frozen week {key}: {len(fz['bets']) - len(kept)} voided bet(s) removed from the frozen record"); fz["bets"] = kept
    bets_current = rescore_edges(allp, models, rules_from=None) + yard_bets(rules_from=None)   # plain blend re-score: today's blend and edge rule, 1u for every role, no board rules
    partial = {(w["season"], w["week"]) for w in weeks if w.get("partial")}
    rule_now = ("EV >= 5% at the median and best book, 2+ books, no weak-spot players, " + ("fitted" if any(BL.coef_for(models, m) for m in ("any", "first", "two")) else "50/50")
                + " model-market blend; closing prices for backfilled weeks, flag-time prices for live weeks; yard edges from the slate's edge list at a 10-yard floor"
                + ("; from week 8 the record is the capped card at each game's decision pull at that pull's prices (alt rungs 0.25u, first-TD longshots +2000 with model > 2x market "
                   "routed to the bench at 0.25u, 25 bets per kickoff window)" if dec_weeks else ""))
    by_wk = {}
    for b_ in bets: by_wk.setdefault(f"{b_['season']}-{b_['week']}", []).append(b_)
    for w in weeks:
        key = f"{w['season']}-{w['week']}"
        if key not in frozen and (w["season"], w["week"]) not in partial:
            frozen[key] = dict(rule=rule_now, frozen_at=pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%dT%H:%MZ"), bets=by_wk.get(key, []))
    json.dump(frozen, open(FROZEN, "w"), indent=0)
    bets = [b_ for key in sorted(frozen) for b_ in frozen[key]["bets"]] + [b_ for key, rows in by_wk.items() if key not in frozen for b_ in rows]
    for b_ in bets:                                                      # frozen weeks predate the stake / kind fields: 1u, kind from the text
        b_.setdefault("stake", 1.0); b_["kind"] = kind_of(b_); b_.setdefault("ratio", RU.ratio_of(b_.get("model_p"), b_.get("mkt_p")))
        b_["group"] = "bench" if b_.get("role") == "Bench" else "main"
    print(f"  bet record: {len(bets)} bets ({sum(len(v['bets']) for v in frozen.values())} frozen over {len(frozen)} weeks); under current rules {len(bets_current)}")
    # ---- "My bets" (placed_bets.jsonl, assignment 2026-10-20): the bets actually placed, graded on their own card, never in the record ----
    graded_weeks = {(w["season"], w["week"]) for w in weeks if not w.get("partial")}
    my_bets = PB.grade(PB.load(), tdhit, ymap, graded_weeks)
    # ---- one-ruler regrade, weeks 3-6 (ruling 3 / item 9): the frozen TD bets re-flagged with the 50/50 blend (the one ruler) under the same flag rule
    #      and the stakes of their era (1u before week 6; Bench 0.25u and the ratio block from week 6); yard bets were always 50/50 and carry over.
    #      Shown beside the frozen record, never replacing it; the drop list is every frozen bet the one ruler does not flag (W4 / W5 first TD). ----
    OR_WEEKS = (3, 4, 5, 6)
    one_ruler = None
    if len(allp) and any(int(w["week"]) in OR_WEEKS and not w.get("backfill") for w in weeks):
        orp = allp[allp.week.isin(OR_WEEKS)]
        fz_rows = [b_ for k in sorted(frozen) for b_ in frozen[k]["bets"] if int(b_["week"]) in OR_WEEKS and (int(b_["season"]), int(b_["week"])) not in backfill_weeks_or]
        keyf = lambda b_: (int(b_["week"]), b_["bet"])
        fz_td = [b_ for b_ in fz_rows if b_["market"] in ("any", "first", "two")]; fz_y = [b_ for b_ in fz_rows if b_["market"] not in ("any", "first", "two")]
        fz_keys = {keyf(b_) for b_ in fz_td}
        def half_row(b_):
            """The frozen bet re-judged by the one ruler from its OWN stored fields (model P, market P, price, EV at the median): the median price is
               recovered from the stored EV@median and blend, so a slate file rewritten later (Kalshi off in replays) can never fake a drop."""
            m_, k_ = float(b_["model_p"]), float(b_["mkt_p"]); half = float(BL.blend([m_], [k_])[0]); bf = float(b_["blend_p"]) if b_.get("blend_p") else None
            dec_ = 1 + (b_["price"] / 100 if b_["price"] > 0 else 100 / -b_["price"])
            dec_m = (float(b_["ev_med"]) + 1) / bf if bf and b_.get("ev_med") is not None else None
            ev_h, evm_h = half * dec_ - 1, (half * dec_m - 1) if dec_m else None
            med = None if dec_m is None else (int(round((dec_m - 1) * 100)) if dec_m >= 2 else int(round(-100 / (dec_m - 1))))
            return dict(week=int(b_["week"]), bet=b_["bet"], market=b_["market"], role=b_.get("role"), price=b_["price"], book=b_.get("book"), med=med,
                        model_p=round(m_, 4), mkt_p=round(k_, 4), blend_fitted=b_.get("blend_p"), blend_half=round(half, 4), ev_fitted=b_.get("ev"), ev_med_fitted=b_.get("ev_med"),
                        ev_half=round(ev_h, 4), ev_med_half=None if evm_h is None else round(evm_h, 4), fitted=bool(b_.get("fitted")), won=bool(b_["won"]),
                        profit=float(b_["profit"]), stake=float(b_.get("stake") or 1.0),
                        flag_half=bool(bf is not None and abs(bf - half) < 1e-4) or bool(ev_h >= MIN_EV - 1e-9 and (evm_h is None or evm_h >= MIN_EV - 1e-9)))   # a 50/50-era bet keeps its flag: no rounding artefact
        judged = [half_row(b_) for b_ in fz_td]
        kept_td = [dict(b_, ev=j["ev_half"], ev_med=j["ev_med_half"], blend_p=j["blend_half"]) for b_, j in zip(fz_td, judged) if j["flag_half"]]
        dropped = [j for j in judged if not j["flag_half"]]
        # bets the one ruler flags that the fitted ruler did not: re-scored from the slate rows, only where the stored blend was a fitted one
        or_td = rescore_edges(orp, {}, rules_from=RU.BOARD_RULES_WEEK)
        stored = {}
        for r in orp.itertuples():
            for mk, pre, pcol, kcol in (("any", "any_", "p_any", "any_mkt_p"), ("first", "first_", "p_first", "first_mkt_p"), ("two", "two_", "p_2plus", "two_mkt_p")):
                b0, m0, k0 = getattr(r, pre + "blend_p", None), getattr(r, pcol, None), getattr(r, kcol, None)
                if b0 is not None and m0 is not None and k0 is not None and b0 == b0 and m0 == m0 and k0 == k0:
                    stored[(int(r.week), r.name, mk)] = abs(float(b0) - 0.5 * (float(m0) + float(k0))) > 1e-4
        name_of = lambda b_: b_["bet"].rsplit(" Anytime TD", 1)[0].rsplit(" First TD", 1)[0].rsplit(" 2+ TD", 1)[0]
        added_src = [b_ for b_ in or_td if keyf(b_) not in fz_keys and stored.get((int(b_["week"]), name_of(b_), b_["market"]), False)]
        added = [dict(week=int(b_["week"]), bet=b_["bet"], market=b_["market"], role=b_.get("role"), price=b_["price"], book=b_.get("book"), blend_half=b_["blend_p"], ev_half=b_["ev"], ev_med_half=b_.get("ev_med"),
                      won=bool(b_["won"]), profit=float(b_["profit"]), stake=float(b_.get("stake") or 1.0)) for b_ in added_src]
        or_bets = kept_td + fz_y + added_src
        def tl(g):
            stk = sum(float(b_.get("stake") or 1.0) for b_ in g)
            return dict(n=len(g), won=sum(1 for b_ in g if b_["won"]), units=round(sum(float(b_["profit"]) for b_ in g), 2), expected=round(sum(float(b_.get("stake") or 1.0) * float(b_["ev"]) for b_ in g), 2),
                        staked=round(stk, 2), roi=round(sum(float(b_["profit"]) for b_ in g) / stk, 4) if stk else None)
        by_week = []
        for w_ in OR_WEEKS:
            fw = [b_ for b_ in fz_rows if int(b_["week"]) == w_]; ow = [b_ for b_ in or_bets if int(b_["week"]) == w_]
            if not fw and not ow: continue
            by_week.append(dict(week=w_, frozen=tl(fw), one_ruler=tl(ow), dropped=sum(1 for d_ in dropped if d_["week"] == w_), added=sum(1 for a_ in added if a_["week"] == w_),
                                first_td=dict(frozen=tl([b_ for b_ in fw if b_["market"] == "first"]), one_ruler=tl([b_ for b_ in ow if b_["market"] == "first"])),
                                fitted_in_frozen=sum(1 for b_ in fw if b_.get("fitted"))))
        one_ruler = dict(weeks=[w_["week"] for w_ in by_week], by_week=by_week, cum=dict(frozen=tl(fz_rows), one_ruler=tl(or_bets)), dropped=dropped, added=added,
                         rule="weeks 3-6 re-judged with the 50/50 blend (the one ruler) under the same flag rule (EV >= 5% at the median and best book, 2+ books) at the frozen prices "
                              "and stakes; dropped = frozen bets the one ruler does not flag; added = players the fitted blend left unflagged that the one ruler flags (same slate "
                              "prices); yard bets carry over unchanged (always 50/50). A view beside the frozen record, never replacing it")
        print(f"  one ruler W3-6: frozen {tl(fz_rows)['n']} bets {tl(fz_rows)['units']:+.2f}u vs one ruler {tl(or_bets)['n']} bets {tl(or_bets)['units']:+.2f}u; dropped {len(dropped)} (first TD {sum(1 for d_ in dropped if d_['market'] == 'first')}), added {len(added)}")

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
        return dict(n=int(len(g)), model=round(BL.brier(pm, yv), 4), book=round(BL.brier(pk, yv), 4), blend=round(BL.brier(BL.blend(pm, pk), yv), 4),
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
        pdf = ydf[ydf.kind == "pass"]
        if len(pdf):                                                         # passing-yard gate (rules.HIDDEN_KINDS): back on the board only when the
            pw = int(pdf[["season", "week"]].drop_duplicates().shape[0]); ph = yh2h(pdf)   # passing model beats the books over 3+ graded weeks combined
            yards_h2h["pass_gate"] = dict(weeks=pw, **ph, open=bool(pw >= 3 and ph["model"] < ph["book"]),
                                          rule="passing-yard rungs return to the board when the passing model's Brier beats the books at the main lines over 3 or more graded weeks combined",
                                          by_week=[dict(season=int(sn), week=int(wk), **yh2h(g)) for (sn, wk), g in pdf.groupby(["season", "week"])])
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
        stk = float(g.stake.sum()) if len(g) and "stake" in g else float(len(g))
        return dict(n=int(len(g)), won=int(g.won.sum()) if len(g) else 0,
                    units=round(float(g.profit.sum()), 2) if len(g) else 0.0,
                    expected_units=round(float((g.ev * (g.stake if "stake" in g else 1.0)).sum()), 2) if len(g) else 0.0,     # sum of stake x EV at the price taken
                    roi=round(float(g.profit.sum()) / stk, 4) if len(g) and stk else None, staked=round(stk, 2))
    bc = pd.DataFrame(bets_current)
    rescore_current = dict(**tally(bc), short=tally(bc[~bc.longshot]) if len(bc) else tally(bc), rule=rule_now + "; 1u for every role, no board rules (the board-rules view is record_current_rules)",
                           by_market={k: tally(g) for k, g in bc.groupby("market")} if len(bc) else {},
                           by_market_short={k: tally(g) for k, g in bc[~bc.longshot].groupby("market")} if len(bc) else {},
                           by_role={k: tally(g) for k, g in bc.groupby("role")} if len(bc) else {}) if len(bc) else None
    by_role = {k: tally(g) for k, g in b.groupby("role")} if len(b) else {}
    by_market = {k: tally(g) for k, g in b.groupby("market")} if len(b) else {}
    by_price = {"under_1000": tally(b[~b.longshot]), "1000_plus": tally(b[b.longshot])} if len(b) else {}

    # ---- market Brier: score the no-vig median-book probability the same way as the model, where a price existed ----
    COLS = {"any": ("any_mkt_p", "hit", "p_any"), "first": ("first_mkt_p", "first_hit", "p_first"), "two": ("two_mkt_p", "two_hit", "p_2plus")}
    def mb(m, mk):
        c = COLS[mk]; mp = m[c[0]].astype(float); y = m[c[1]].astype(float); pm = m[c[2]].astype(float)
        return dict(n=int(len(m)), model=round(float(((pm - y) ** 2).mean()), 4), market=round(float(((mp - y) ** 2).mean()), 4),
                    blend=round(float(((BL.blend(pm.values, mp.values) - y.values) ** 2).mean()), 4))
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
    clv_by_week = {}                                                     # {role: {week: n / avg_clv / beat_close}} for the bench CLV gate
    for r in clv_rows:
        if "clv" not in r: continue
        rl = r.get("role") if isinstance(r.get("role"), str) and r.get("role") else "Unknown"
        clv_by_week.setdefault(rl, {}).setdefault(int(r["week"]), []).append(r)
    clv_by_week = {rl: {wk: dict(n=len(g), avg_clv=round(sum(x["clv"] for x in g) / len(g), 4), beat_close=round(sum(x["beat_close"] for x in g) / len(g), 3)) for wk, g in sorted(v.items())}
                   for rl, v in clv_by_week.items()}
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
    # ---- bets by week. Singles (the headline, 2026-10-17: Starter + Rotational + Bench at each bet's stake) split by group (main = Starter + Rotational; bench = Bench plays) and market,
    #      parlays as their own group, never mixed into the singles. Every aggregate carries n, record, units, expected
    #      (stake x EV), ROI (units / staked) and var = sum of stake^2 x (decimal - 1): the variance of a bet that is exactly
    #      fair at its price, so +-2 sqrt(var) is the luck band around expected. alt_players combines a player's alt rungs
    #      into one line per player-week (rungs on the same player are correlated). ----
    def bet_row(b, market=None):
        stake = float(b.get("stake") or 1.0); price = b.get("price")
        dec = float(b["dec"]) if b.get("dec") else 1 + (price / 100 if price > 0 else 100 / -price)
        role = b.get("role") if isinstance(b.get("role"), str) and b.get("role") else "Unknown"
        return dict(season=int(b["season"]), week=int(b["week"]), market=market or b["market"], group="parlay" if market == "parlay" else "bench" if role == "Bench" else "main",
                    role=role, pid=b.get("pid"), bet=b.get("bet"), won=bool(b["won"]), profit=float(b["profit"]), expected=stake * float(b.get("ev") or 0.0),
                    var=stake * stake * (dec - 1), stake=stake, longshot=bool(b.get("longshot")), closing=bool(b.get("backfill_price")))
    def parlay_rows(ws):
        return [bet_row(dict(c, season=w["season"], week=w["week"], stake=1.0, role=None, backfill_price=False, longshot=False), market="parlay")
                for w in ws for c in w["parlays"] if c.get("dec")]
    def agg(rs):
        staked = sum(r["stake"] for r in rs); units = sum(r["profit"] for r in rs)
        return dict(n=len(rs), won=sum(r["won"] for r in rs), units=round(units, 2), expected=round(sum(r["expected"] for r in rs), 2),
                    roi=round(units / staked, 4) if staked else None, staked=round(staked, 2), var=round(sum(r["var"] for r in rs), 4))
    def alt_by_player(rs):
        per = {}
        for r in rs:
            if r["market"] != "yds_alt": continue
            per.setdefault((r["week"], r["pid"] or (r["bet"] or "").split(" Over ")[0]), []).append(r)
        rows = [dict(week=wk, player=(g[0]["bet"] or "").split(" Over ")[0], rungs=len(g), won=sum(x["won"] for x in g), units=round(sum(x["profit"] for x in g), 2),
                     expected=round(sum(x["expected"] for x in g), 2), staked=round(sum(x["stake"] for x in g), 2), var=round(sum(x["var"] for x in g), 4)) for (wk, _), g in per.items()]
        units = sum(x["units"] for x in rows); staked = sum(x["staked"] for x in rows)
        return dict(players=len(rows), won=sum(1 for x in rows if x["units"] > 0), lost=sum(1 for x in rows if x["units"] < 0), flat=sum(1 for x in rows if x["units"] == 0),
                    units=round(units, 2), expected=round(sum(x["expected"] for x in rows), 2), roi=round(units / staked, 4) if staked else None, staked=round(staked, 2),
                    var=round(sum(x["var"] for x in rows), 4), rows=sorted(rows, key=lambda x: (x["week"], -x["units"])))
    MKTS = ("any", "first", "two", "yds", "yds_alt")
    both = lambda rs: dict(all=agg(rs), short=agg([r for r in rs if not r["longshot"]]))
    def summarize(rs, keep_rows=True):
        main = [r for r in rs if r["group"] == "main"]; bench = [r for r in rs if r["group"] == "bench"]; par = [r for r in rs if r["group"] == "parlay"]
        singles = main + bench            # the headline (2026-10-17): Starter + Rotational + Bench at each bet's actual stake; parlays stay out
        ap = dict(all=alt_by_player(singles), short=alt_by_player([r for r in singles if not r["longshot"]]))
        if not keep_rows:
            for v in ap.values(): v.pop("rows")
        return dict(singles=both(singles), main=both(main), bench=both(bench), parlay=both(par),
                    by_market={m: both([r for r in singles if r["market"] == m]) for m in MKTS if any(r["market"] == m for r in singles)},
                    main_by_market={m: both([r for r in main if r["market"] == m]) for m in MKTS if any(r["market"] == m for r in main)},
                    bench_by_market={m: both([r for r in bench if r["market"] == m]) for m in MKTS if any(r["market"] == m for r in bench)},
                    by_role={k: both([r for r in rs if r["role"] == k]) for k in sorted({r["role"] for r in rs if r["group"] != "parlay"})}, alt_players=ap)
    def week_table(rows):
        out = []
        for (sn, wk) in sorted({(r["season"], r["week"]) for r in rows}):
            rs = [r for r in rows if r["season"] == sn and r["week"] == wk]; singles = [r for r in rs if r["group"] != "parlay"]
            out.append(dict(season=sn, week=wk, priced="closing" if singles and all(r["closing"] for r in singles) else "live" if not any(r["closing"] for r in singles) else "mixed",
                            **summarize(rs, keep_rows=False)))
        return out, (summarize(rows) if rows else None)
    rows_all = [bet_row(b) for b in bets] + parlay_rows(parlays or [])
    bets_by_week, bets_cum = week_table(rows_all)
    live_rows = [r for r in rows_all if (r["season"], r["week"]) not in backfill_weeks]
    # the headline figures, computed here from the same rows the week table is built from (test_reconcile.py checks they agree)
    record = {sc: dict(weeks=sorted({r["week"] for r in rs}), **summarize(rs)) if rs else None
              for sc, rs in (("live", live_rows), ("rules", [r for r in live_rows if r["week"] >= RULES_WEEK]))}
    # the same live bets under today's board rules (passing yards out, Rotational > 2.5x out, Bench at 0.25u, parlays separate): a view, not the record
    cr_src = [b for b in bets if (int(b["season"]), int(b["week"])) not in backfill_weeks]
    cr_kept = [(b, under_current_rules(b)) for b in cr_src]
    cr_rows = [bet_row(x) for _, x in cr_kept if x] + parlay_rows([w for w in (parlays or []) if (w["season"], w["week"]) not in backfill_weeks])
    cr_weeks, cr_cum = week_table(cr_rows) if cr_rows else ([], None)
    record_current_rules = dict(weeks=cr_weeks, cum=cr_cum,
                                dropped=dict(pass_yds=sum(1 for b, x in cr_kept if not x and kind_of(b) in RU.HIDDEN_KINDS),
                                             ratio=sum(1 for b, x in cr_kept if not x and kind_of(b) not in RU.HIDDEN_KINDS)),
                                rule=f"same bets at the same prices; passing-yard rungs removed, Rotational players over {RU.MAX_RATIO_ROT}x the no-vig market removed, Bench at {RU.BENCH_STAKE}u, parlays separate") if cr_rows else None
    bench_bets = [b for b in bets if b.get("role") == "Bench" and (int(b["season"]), int(b["week"])) not in backfill_weeks]
    big = max((float(b["profit"]) for b in bench_bets), default=0.0)
    _bclv = ((clv_summary or {}).get("by_role", {}) or {}).get("Bench")
    bench_gate = dict(n=len(bench_bets), units=round(sum(float(b["profit"]) for b in bench_bets), 2), units_ex_best=round(sum(float(b["profit"]) for b in bench_bets) - big, 2), biggest_win=round(big, 2),
                      clv=_bclv, clv_by_week=clv_by_week.get("Bench", {}),
                      rule="Bench returns to 1u only after 100+ bench bets with positive CLV and positive units once the single biggest win is removed",
                      open=bool(len(bench_bets) >= 100 and (_bclv or {}).get("avg_clv") is not None and _bclv["avg_clv"] > 0 and sum(float(b["profit"]) for b in bench_bets) - big > 0))
    # ---- pre-registered gate (CHANGELOG 2026-10-12): anytime TD singles priced +1000 or longer, live weeks (no backfill), every role.
    #      Decision after week 8 grades: still below expected units -> research-only (logged, not on the Edge Board). Tracked here; nothing changes yet. ----
    LONGSHOT_PRICE, LONGSHOT_DECISION_WEEK = 1000, 8
    ls_rows = [r for r in live_rows if r["market"] == "any" and r["longshot"]]
    ls_clv = [r for r in clv_rows if "clv" in r and r["market"] == "any" and int(r.get("best") or 0) >= LONGSHOT_PRICE and (int(r["season"]), int(r["week"])) not in backfill_weeks]
    def clv_agg(g):
        return dict(n=len(g), avg_clv=round(sum(x["clv"] for x in g) / len(g), 4), beat_close=round(sum(x["beat_close"] for x in g) / len(g), 3)) if g else None
    ls_all = agg(ls_rows)
    longshot_gate = dict(market="any", min_price=LONGSHOT_PRICE, decision_week=LONGSHOT_DECISION_WEEK, status="pre-registered, tracking only",
                         all=ls_all, main=agg([r for r in ls_rows if r["group"] == "main"]), bench=agg([r for r in ls_rows if r["group"] == "bench"]),
                         clv=clv_agg(ls_clv), clv_main=clv_agg([r for r in ls_clv if r.get("role") != "Bench"]), clv_bench=clv_agg([r for r in ls_clv if r.get("role") == "Bench"]),
                         by_week=[dict(week=wk, **agg([r for r in ls_rows if r["week"] == wk]), clv=clv_agg([r for r in ls_clv if int(r["week"]) == wk])) for wk in sorted({r["week"] for r in ls_rows})],
                         below_expected=bool(ls_rows) and ls_all["units"] < ls_all["expected"],
                         rule=f"anytime TD singles priced +{LONGSHOT_PRICE} or longer, live weeks only (no backfill), every role, graded as logged. If after week {LONGSHOT_DECISION_WEEK} grades "
                              f"the group's units are still below its expected units it moves to research-only: logged, graded, not on the Edge Board. Pre-registered 2026-10-12; nothing changes before then")
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
               slips=dict(rows=slips_rows, summary=slips_summary), slips_backtest=slips_bt, research_alt=research_alt, alt_rules=alt_rules,
               record=record, record_current_rules=record_current_rules, bench_gate=bench_gate, longshot_gate=longshot_gate, clv_by_week=clv_by_week,
               research_pass=research_pass, research_ratio=research_ratio, research_ud=research_ud, research_lineshop=research_lineshop, research_pending=pending,
               research_first_seen=research_first_seen, decision=decision_out, my_bets=my_bets, one_ruler=one_ruler,
               board_rules=dict(week=RU.BOARD_RULES_WEEK, board_rules_week=RU.BOARD_RULES_WEEK, bench_stake=RU.BENCH_STAKE, max_ratio_rotational=RU.MAX_RATIO_ROT, hidden_kinds=list(RU.HIDDEN_KINDS),
                                min_ev=RU.MIN_EV, min_books=RU.MIN_BOOKS, alt_max_price=Y.ALT_MAX_PRICE, alt_max_ratio=Y.ALT_MAX_RATIO, min_edge_line=Y.MIN_EDGE_LINE, alt_rule=Y.ALT_RULE,
                                alt_stake=RU.ALT_STAKE, longshot_first_price=RU.LONGSHOT_FIRST_PRICE, longshot_first_ratio=RU.LONGSHOT_FIRST_RATIO, card_cap=RU.CARD_CAP))
    out["voided"] = voided
    json.dump(out, open("results.json", "w"), default=lambda o: o.item() if hasattr(o, "item") else str(o))
    print("graded weeks:", [(w["season"], w["week"]) for w in weeks], "bets:", len(bets),
          "| top-15 regulars:", len(regulars), "| backtest:", "yes" if backtest else "missing backtest_2025.json")


if __name__ == "__main__":
    main()
