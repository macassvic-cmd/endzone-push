"""Yard ladders: rushing, receiving and passing yards simulated inside the game sim so yards and TDs share the same
game environment.

Volume: team plays from recency-weighted pace, pass rate from the team's recency-weighted pass share adjusted by the
spread (favorites run more), both tied to the TD sim's game factor; player carries / targets from recency-weighted
shares of team attempts / targets with the same shrinkage, snap cap, role tags and redistribution as the TD shares
(model.build_slate is re-run on an attempts/targets version of the play-by-play with volume priors).
Efficiency: yards per carry / target / attempt shrunk toward position averages (RATE_K touches); game-to-game
spread = iid per-touch noise (TOUCH_SD by position, fit on 2022-24) plus a per-game rate shock (RATE_TAU).
Output per player: mean, median and P(>= rung) for every rung the books offer plus a default ladder.
Fits: 2022-2024 regular season (fit script in CHANGELOG); the backtest gate decides whether this ships.
"""
import numpy as np, pandas as pd, model as M

TOUCH_SD = {("rush", "RB"): 6.1, ("rush", "QB"): 6.8, ("rush", "WR"): 7.0, ("rush", "TE"): 6.5,
            ("rec", "RB"): 7.7, ("rec", "TE"): 8.4, ("rec", "WR"): 11.0, ("rec", "QB"): 8.0, ("pass", "QB"): 9.9}
RATE_PRIOR = {("rush", "RB"): 4.2, ("rush", "QB"): 5.8, ("rush", "WR"): 6.0, ("rush", "TE"): 4.0,
              ("rec", "RB"): 5.8, ("rec", "TE"): 7.4, ("rec", "WR"): 8.0, ("rec", "QB"): 6.0, ("pass", "QB"): 7.0}
RATE_K, RATE_TAU = 60.0, 0.22                 # shrinkage in touches; per-game multiplicative rate shock (sd) for carries / targets
RATE_TAU_PASS = 0.15                          # passing: per-game rate sd 2.1 on 7.0 yds/att, less the iid per-attempt part (9.9/sqrt(30))
SACK_RATE = 0.065                             # team pass plays include sacks; attempts that can gain yards are (1 - SACK_RATE) of them
PLAYS_MEAN, PLAYS_SD, PLAYS_K = 62.9, 8.3, 4.0 # league plays per team-game, game-to-game sd, games of shrinkage for team pace
PASS_A, PASS_B = 0.571, -0.0038               # league pass rate and its slope per point favored (2022-24)
PLAYS_RHO = 0.3                               # correlation of team plays with the TD sim's game factor
PASS_RATE_SD = 0.06                           # game-to-game pass-rate shock (game script); 0 = fixed pass rate
VOL_KAPPA = {"rush": 15.0, "rec": 60.0}       # game-to-game share variability (Beta concentration) for carries / targets; carries swing more than targets. 0 = fixed
QB_ATT_SHARE = 0.97
# receptions, turnovers and half-PPR fantasy (slip pricer): catch rate per player shrunk CATCH_K targets toward the position
# rate, INT rate per QB shrunk INT_K attempts toward the league rate, fumbles lost at league rates per touch / dropback (2023-25 pbp)
CATCH_PRIOR, CATCH_K = {"RB": 0.786, "TE": 0.718, "WR": 0.633, "QB": 0.70}, 40.0
INT_RATE, INT_K = 0.0222, 300.0
FUM_RATE = {"RB": 0.0047, "WR": 0.007, "TE": 0.006, "QB": 0.0057}
FPTS = dict(rec=0.5, yd=0.1, td=6.0, pass_yd=0.04, pass_td=4.0, int=-2.0, fum=-2.0)   # half-PPR
VOL_PRIOR = {('rec', 'QB', 1): 0.001, ('rec', 'QB', 2): 0.0, ('rec', 'RB', 1): 0.074, ('rec', 'RB', 2): 0.046, ('rec', 'RB', 3): 0.01,
             ('rec', 'TE', 1): 0.149, ('rec', 'TE', 2): 0.03, ('rec', 'WR', 1): 0.221, ('rec', 'WR', 2): 0.158, ('rec', 'WR', 3): 0.098,
             ('rec', 'WR', 4): 0.025, ('rush', 'QB', 1): 0.109, ('rush', 'QB', 2): 0.011, ('rush', 'RB', 1): 0.372, ('rush', 'RB', 2): 0.206,
             ('rush', 'RB', 3): 0.034, ('rush', 'TE', 1): 0.003, ('rush', 'TE', 2): 0.001, ('rush', 'WR', 1): 0.006, ('rush', 'WR', 2): 0.006,
             ('rush', 'WR', 3): 0.005, ('rush', 'WR', 4): 0.003}
VOL_QB_PRIOR = (0.05, 0.04)                   # QB1 carries share = a + b * designed runs per game
DEFAULT_LADDER = {"rush": [25, 40, 50, 60, 75, 100], "rec": [25, 40, 50, 60, 75, 100], "pass": [200, 225, 250, 275, 300]}
MIN_EDGE_LINE = {"rush": 10, "rec": 10, "pass": 100}   # rungs below this are shown on the ladder but never flagged as edges
ALT_MAX_PRICE, ALT_MAX_RATIO = 300, 2.0                # alt-rung edges: price shorter than +300 and model P <= 2x the market curve (tails are overconfident);
                                                       # candidates outside that are logged for research (research/alt_rungs_<season>_w<week>.json), not flagged
ALT_RULE = "alt rung v2: curve-judged, price < +300, model <= 2x curve"
DIST = "gamma"                                # "normal" (clipped) or "gamma": chosen by the backtest (gamma)
SHIP = {"rush": True, "rec": True, "pass": True}  # per-kind backtest gate (yards_backtest.py); False keeps a kind off the page and board


def volume_past(past):
    """Play-by-play where rush_xtd = 1 per carry and rec_xtd = 1 per target, so model.player_games / shares give
       shares of team attempts and targets instead of xTD."""
    v = past.copy()
    v["rush_xtd"] = v["is_rush"].astype(float) if "is_rush" in v else ((v.rush_attempt == 1) & (v.qb_kneel != 1)).astype(float)
    v["rec_xtd"] = ((v.pass_attempt == 1) & (v.sack == 0) & v.receiver_player_id.notna()).astype(float)
    return v


def prep(past, season):
    """Cache for a week: volume player-games, per-player efficiency, team pace / pass rate, QB attempts."""
    vp = volume_past(past)
    pg = M.player_games(vp)
    # efficiency: recency-weighted yards and touches per player & kind
    ru = vp[vp.rush_xtd > 0].groupby(["season", "week", "rusher_player_id"]).agg(touch=("rush_xtd", "sum"), yds=("rushing_yards", "sum")).reset_index().rename(columns={"rusher_player_id": "pid"}).assign(kind="rush")
    rc = vp[vp.rec_xtd > 0].groupby(["season", "week", "receiver_player_id"]).agg(touch=("rec_xtd", "sum"), yds=("receiving_yards", lambda x: x.fillna(0).sum()), extra=("complete_pass", "sum")).reset_index().rename(columns={"receiver_player_id": "pid"}).assign(kind="rec")
    qa = vp[(vp.pass_attempt == 1) & (vp.sack == 0) & vp.passer_player_id.notna()].groupby(["season", "week", "passer_player_id"]).agg(touch=("pass_attempt", "sum"), yds=("passing_yards", lambda x: x.fillna(0).sum()), extra=("interception", "sum")).reset_index().rename(columns={"passer_player_id": "pid"}).assign(kind="pass")
    e = pd.concat([ru, rc, qa]); e["extra"] = e["extra"].fillna(0.0); e["ord"] = e.season * 100 + e.week      # extra = receptions (rec) / interceptions (pass)
    e = e.sort_values("ord", ascending=False); e["rank"] = e.groupby(["pid", "kind"]).cumcount()
    e["w"] = M.DECAY ** e["rank"] * np.where(e.season < season, M.PRIOR_SEASON_W, 1.0)
    eff = e.groupby(["pid", "kind"]).apply(lambda g: pd.Series(dict(wtouch=float((g.w * g.touch).sum()), wyds=float((g.w * g.yds).sum()), wextra=float((g.w * g.extra).sum()), games=len(g)))).reset_index()
    # team pace and pass rate (plays per game, recency weighted; QB attempts per game)
    plays = vp[(vp.rush_attempt == 1) | (vp.pass_attempt == 1)].groupby(["season", "week", "posteam"]).agg(plays=("play_id", "size"), passes=("pass_attempt", "sum")).reset_index()
    plays["ord"] = plays.season * 100 + plays.week; plays = plays.sort_values("ord", ascending=False); plays["rank"] = plays.groupby("posteam").cumcount()
    plays["w"] = M.DECAY ** plays["rank"] * np.where(plays.season < season, M.PRIOR_SEASON_W, 1.0)
    team = plays.groupby("posteam").apply(lambda g: pd.Series(dict(pace=float((g.w * g.plays).sum() / g.w.sum()), pr=float((g.w * g.passes).sum() / (g.w * g.plays).sum()), wsum=float(g.w.sum())))).reset_index()
    team["pace"] = (team.wsum * team.pace + PLAYS_K * PLAYS_MEAN) / (team.wsum + PLAYS_K)
    team["pr"] = (team.wsum * team.pr + PLAYS_K * PASS_A) / (team.wsum + PLAYS_K)
    return dict(vpast=vp, vpg=pg, eff=eff.set_index(["pid", "kind"]), team=team.set_index("posteam"))


def catch_rate(eff, pid, pos):
    prior = CATCH_PRIOR.get(pos, CATCH_PRIOR["WR"])
    if (pid, "rec") in eff.index:
        r = eff.loc[(pid, "rec")]; return (r.wextra + CATCH_K * prior) / (r.wtouch + CATCH_K)
    return prior


def int_rate(eff, pid):
    if (pid, "pass") in eff.index:
        r = eff.loc[(pid, "pass")]; return (r.wextra + INT_K * INT_RATE) / (r.wtouch + INT_K)
    return INT_RATE


def rate_for(eff, pid, kind, pos):
    prior = RATE_PRIOR.get((kind, pos), RATE_PRIOR.get((kind, "WR")))
    if (pid, kind) in eff.index:
        r = eff.loc[(pid, kind)]; return (r.wyds + RATE_K * prior) / (r.wtouch + RATE_K), float(r.wtouch)
    return prior, 0.0


def build(p, s, season, week, active, qb_override, depth, snaps, rookies, cache, ycache, wind=None):
    """Volume slate: shares of team carries / targets through model.build_slate with volume priors."""
    saved = {k: getattr(M, k) for k in ("SHARE_PRIOR", "QB_PRIOR_A", "QB_PRIOR_B")}
    M.SHARE_PRIOR, (M.QB_PRIOR_A, M.QB_PRIOR_B) = VOL_PRIOR, VOL_QB_PRIOR
    try:
        teams, plv, qbs, shv = M.build_slate(p, s, season, week, active=active, qb_override=qb_override, wind=wind, depth=depth, snaps=snaps,
                                             cache=dict(past=ycache["vpast"], pg=ycache["vpg"], qb_type=cache.get("qb_type")), rookies=rookies)
    finally:
        for k, v in saved.items(): setattr(M, k, v)
    return teams, plv, qbs


def simulate(teams, plv, qbs, ycache, sim, pos_of, rng=None):
    """Yards per sim game for every player in plv (rush and rec) and each QB (pass), tied to sim['game_f']."""
    rng = rng or M.RNG
    n = sim["n"]; eff = ycache["eff"]; team = ycache["team"]
    out = {}
    for t, info in teams.items():
        tp = team.loc[t] if t in team.index else None
        pace = float(tp.pace) if tp is not None else PLAYS_MEAN
        pr = float(tp.pr) if tp is not None else PASS_A
        fav = -info["spread"] if info["home"] else info["spread"]          # points this team is favored by
        pr = float(np.clip(pr + PASS_B * fav, 0.35, 0.75))
        pr = np.clip(pr + PASS_RATE_SD * rng.normal(size=n), 0.25, 0.85) if PASS_RATE_SD > 0 else pr
        f = sim.get("game_f", {}).get(info["game_id"]); z = (np.log(f) + M.GAME_SIGMA ** 2 / 2) / M.GAME_SIGMA if f is not None else rng.normal(size=n)
        plays = np.clip(pace + PLAYS_SD * (PLAYS_RHO * z + np.sqrt(1 - PLAYS_RHO ** 2) * rng.normal(size=n)), 40, 95)
        passes = rng.binomial(plays.astype(int), pr); rushes = plays.astype(int) - passes
        targets = rng.binomial(passes, 1 - SACK_RATE)                       # sacks are pass plays but not targets
        for kind, tot in (("rush", rushes), ("rec", targets)):
            sub = plv[(plv.team == t) & (plv.kind == kind)]
            for _, r in sub.iterrows():
                if r.share <= 0: continue
                sh = min(float(r.share), 0.95)
                kap = VOL_KAPPA.get(kind, 0)
                if kap > 0: sh = rng.beta(sh * kap, (1 - sh) * kap, size=n)                           # this game's share of the team's volume
                vol = rng.binomial(tot, sh)
                rate, _ = rate_for(eff, r.pid, kind, pos_of(r.pid))
                out[(r.pid, kind)] = _yards(vol, rate, TOUCH_SD.get((kind, pos_of(r.pid)), 8.0), rng)
                out[(r.pid, "tgt" if kind == "rec" else "car")] = vol                 # volume draws, kept for receptions / fumbles
                if kind == "rec": out[(r.pid, "recn")] = rng.binomial(vol, catch_rate(eff, r.pid, pos_of(r.pid)))
        qid = qbs.get(t, (None,))[0]
        if qid:
            att = rng.binomial(passes, QB_ATT_SHARE * (1 - SACK_RATE)); rate, _ = rate_for(eff, qid, "pass", "QB")
            out[(qid, "pass")] = _yards(att, rate, TOUCH_SD[("pass", "QB")], rng, tau=RATE_TAU_PASS)
            out[(qid, "att")] = att; out[(qid, "int")] = rng.binomial(att, int_rate(eff, qid)); out[(qid, "db")] = passes
    return out


YARD_KINDS_ALL = ("rush", "rec", "pass")      # keys of simulate() that are yards; the rest are volume / count draws


def stats(ydraws, sim, qbs, pos_of, rng=None):
    """Per player: receptions, yards, pass attempts and half-PPR fantasy points per sim game, from the same draws.
       {pid: {"rec", "rec_yds", "rush_yds", "fpts"}} for skill players, {"pass_att", "pass_yds", "rush_yds", "fpts"} for QBs.
       TDs come from the TD sim (sim["tds"] rush+rec per player, sim["qb_ptd"] passing TDs per team)."""
    rng = rng or M.RNG; n = sim["n"]; pix = sim["pix"]; zero = np.zeros(n)
    qb_team = {q[0]: t for t, q in qbs.items() if q[0]}
    out = {}
    for pid in {k[0] for k in ydraws}:
        pos = pos_of(pid); g = lambda kind: ydraws.get((pid, kind), zero)
        tds = sim["tds"][:, pix[pid]].astype(float) if pid in pix else zero
        rec_yds, rush_yds, recn = g("rec"), g("rush"), g("recn")
        if pid in qb_team:
            fum = rng.binomial(g("db").astype(int), FUM_RATE["QB"]) if (pid, "db") in ydraws else zero
            fpts = FPTS["pass_yd"] * g("pass") + FPTS["pass_td"] * sim["qb_ptd"][qb_team[pid]] + FPTS["int"] * g("int") \
                 + FPTS["yd"] * (rush_yds + rec_yds) + FPTS["td"] * tds + FPTS["rec"] * recn + FPTS["fum"] * fum
            out[pid] = dict(pass_att=g("att"), pass_yds=g("pass"), rush_yds=rush_yds, fpts=fpts)
        else:
            touches = (g("car") + recn).astype(int)
            fum = rng.binomial(touches, FUM_RATE.get(pos, FUM_RATE["WR"]))
            fpts = FPTS["rec"] * recn + FPTS["yd"] * (rush_yds + rec_yds) + FPTS["td"] * tds + FPTS["fum"] * fum
            out[pid] = dict(rec=recn, rec_yds=rec_yds, rush_yds=rush_yds, fpts=fpts)
    return out


def _yards(vol, rate, sd_touch, rng, tau=None):
    tau = RATE_TAU if tau is None else tau
    n = len(vol); shock = rate * np.exp(tau * rng.normal(size=n) - tau ** 2 / 2)
    mean = vol * shock; var = vol * sd_touch ** 2
    if DIST == "gamma":
        k = np.where(mean > 0, mean ** 2 / np.maximum(var, 1e-6), 1.0); th = np.where(mean > 0, var / np.maximum(mean, 1e-6), 1.0)
        y = np.where(vol > 0, rng.gamma(np.maximum(k, 1e-3), th), 0.0)
    else:
        y = np.clip(mean + np.sqrt(var) * rng.normal(size=n), 0, None)
    return np.where(vol > 0, y, 0.0)


def summarize(ydraws, lines=None):
    """{(pid, kind): draws} -> rows with mean, median, sd and P(>= rung) for the default ladder and any offered lines."""
    rows = []
    for (pid, kind), y in ydraws.items():
        if kind not in DEFAULT_LADDER: continue                                 # volume / count draws are not ladders
        rungs = sorted(set(DEFAULT_LADDER[kind]) | set(lines.get((pid, kind), []) if lines else []))
        rows.append(dict(pid=pid, kind=kind, mean=float(y.mean()), median=float(np.median(y)), sd=float(y.std()), p_zero=float((y <= 0).mean()),
                         ladder={str(r): float((y >= r).mean()) for r in rungs}))
    return rows
