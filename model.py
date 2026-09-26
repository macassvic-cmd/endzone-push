"""NFL TD projection model.

Pipeline for a (season, week):
  1. Team offensive TD expectation from Vegas implied points (fit on 2023-25).
  2. Team pass/rush TD split from recency-weighted team xTD mix + game script.
  3. Player share of team rush-xTD and rec-xTD (recency weighted, opportunity-based
     expected TDs by field position, not raw TDs -> less noise).
  4. Monte Carlo game sim (correlated team TD counts, TD order) ->
     anytime TD, first TD (game + team), QB pass TD ladders, QB-WR stack joints.
Only data strictly before the target week is used for shares.
"""
import numpy as np, pandas as pd

RNG = np.random.default_rng(7)
TD_N, GAME_SIGMA = 7, 0.20          # binomial drive cap / shared game factor (fit to data)
DST_TD_RATE = 0.13                  # non-offensive TDs per team-game
DECAY, PRIOR_SEASON_W = 0.88, 0.55  # per-game recency decay, prior-season multiplier
REC_KNEE, REC_SLOPE = 0.25, 0.35   # receiving-share compression above knee (set by backtest)
WIND_FLOOR, WIND_SLOPE = 10.0, 0.003  # pass-TD share drops ~0.3 pts per mph above 10 (2023-25 fit)
# First TD of game: logit P(home scores first) = a + b*recv + c*spread_line   (recv = +1 home receives, -1 away)
FIRST_A, FIRST_RECV, FIRST_SPREAD = 0.0131, 0.3828, 0.0868


def load():
    p = pd.read_parquet("data/pbp.parquet")
    s = pd.read_parquet("data/sched.parquet")
    p = p[p.season_type == "REG"].copy()
    return p, s


# ---------- expected-TD tables by field position ----------
def xtd_tables(p):
    ru = p[(p.rush_attempt == 1) & p.yardline_100.notna()]
    ru_b = ru.assign(b=ru.yardline_100.clip(1, 99).astype(int)).groupby("b").rush_touchdown.mean()
    pa = p[(p.pass_attempt == 1) & (p.sack == 0) & p.receiver_player_id.notna() & p.yardline_100.notna()].copy()
    pa["ez"] = (pa.air_yards.fillna(0) >= pa.yardline_100).astype(int)
    pa["b"] = pa.yardline_100.clip(1, 99).astype(int) // 5
    tg_b = pa.groupby(["b", "ez"]).pass_touchdown.mean()
    return ru_b.rolling(3, center=True, min_periods=1).mean(), tg_b


def add_xtd(p, ru_b, tg_b):
    p = p.copy()
    yl = p.yardline_100.clip(1, 99).fillna(50).astype(int)
    p["rush_xtd"] = np.where(p.rush_attempt == 1, ru_b.reindex(yl).values, 0.0)
    ez = (p.air_yards.fillna(0) >= p.yardline_100).astype(int)
    key = pd.MultiIndex.from_arrays([yl // 5, ez])
    p["rec_xtd"] = np.where((p.pass_attempt == 1) & p.receiver_player_id.notna(),
                            tg_b.reindex(key).fillna(0).values, 0.0)
    return p


# ---------- player / team shares ----------
def game_index(p, season, week):
    past = p[(p.season < season) | ((p.season == season) & (p.week < week))]
    past = past[past.season >= season - 1]
    return past


def shares(past, season):
    """Recency-weighted per-player share of team rush/rec xTD, plus 1st-quarter share."""
    rows = []
    for kind, pid, name in [("rush", "rusher_player_id", "rusher_player_name"),
                            ("rec", "receiver_player_id", "receiver_player_name")]:
        col = f"{kind}_xtd"
        d = past[past[col] > 0]
        pg = d.groupby(["season", "week", "game_id", "posteam", pid, name])[col].sum().reset_index()
        q1 = d[d.qtr == 1].groupby(["game_id", pid])[col].sum().rename("q1").reset_index()
        tg = d.groupby(["game_id", "posteam"])[col].sum().rename("team").reset_index()
        tq = d[d.qtr == 1].groupby(["game_id", "posteam"])[col].sum().rename("teamq1").reset_index()
        pg = pg.merge(tg, on=["game_id", "posteam"]).merge(q1, on=["game_id", pid], how="left") \
               .merge(tq, on=["game_id", "posteam"], how="left").fillna(0)
        pg["kind"] = kind
        pg = pg.rename(columns={pid: "pid", name: "name"})
        rows.append(pg)
    pg = pd.concat(rows)
    pg["xtd"] = pg["rush_xtd"].fillna(0) + pg["rec_xtd"].fillna(0)
    pg["share"] = pg.xtd / pg.team
    pg["q1share"] = np.where(pg.teamq1 > 0, pg.q1 / pg.teamq1.replace(0, np.nan), np.nan)
    # recency weights: order player's games newest->oldest
    pg["ord"] = pg.season * 100 + pg.week
    out = []
    for (pid, kind), g in pg.groupby(["pid", "kind"]):
        g = g.sort_values("ord", ascending=False)
        w = DECAY ** np.arange(len(g)) * np.where(g.season < season, PRIOR_SEASON_W, 1.0)
        q = g.q1share.notna()
        out.append(dict(pid=pid, kind=kind, name=g.name.iloc[0], last_team=g.posteam.iloc[0],
                        share=np.average(g.share, weights=w), n=len(g), wsum=w.sum(), last_ord=g.ord.iloc[0],
                        q1share=np.average(g.q1share[q], weights=w[q]) if q.any() else np.nan))
    sh = pd.DataFrame(out)
    # shrink toward 0 for small samples (a 1-game outlier shouldn't own the red zone)
    sh["share"] = sh.share * sh.wsum / (sh.wsum + 0.5)
    # extreme receiving shares regress: compress above the knee
    rec = sh.kind == "rec"
    over = (sh.share - REC_KNEE).clip(lower=0)
    sh.loc[rec, "share"] = sh.share[rec] - over[rec] * (1 - REC_SLOPE)
    return sh


def team_pass_frac(past, season):
    t = past.groupby(["season", "week", "posteam"]).agg(r=("rush_xtd", "sum"), c=("rec_xtd", "sum")).reset_index()
    t["ord"] = t.season * 100 + t.week
    out = {}
    for team, g in t.groupby("posteam"):
        g = g.sort_values("ord", ascending=False)
        w = DECAY ** np.arange(len(g)) * np.where(g.season < season, PRIOR_SEASON_W, 1.0)
        pf = np.average(g.c / (g.c + g.r), weights=w)
        out[team] = 0.6 * pf + 0.4 * 0.615          # regress to league
    return out


def passer_share(past):
    """Share of team pass TDs thrown by each QB (for starter lookup)."""
    d = past[past.pass_attempt == 1]
    return d.groupby(["posteam", "passer_player_id", "passer_player_name"]).size().rename("att").reset_index()


# ---------- slate build ----------
def build_slate(p, s, season, week, active=None, qb_override=None, wind=None):
    """active: dict team -> set(pid) allowed (None = anyone whose last team matches).
       qb_override: dict team -> (pid, name)."""
    ru_b, tg_b = xtd_tables(p[p.season < season] if season > 2023 else p)
    past = add_xtd(game_index(p, season, week), ru_b, tg_b)
    sh = shares(past, season)
    pf = team_pass_frac(past, season)
    games = s[(s.season == season) & (s.week == week) & (s.game_type == "REG")].dropna(subset=["total_line"])
    teams = {}
    for _, g in games.iterrows():
        for side, opp, sgn in [("home", "away", 1), ("away", "home", -1)]:
            t = g[f"{side}_team"]
            imp = g.total_line / 2 + sgn * g.spread_line / 2
            fav = sgn * g.spread_line
            lam = max(0.4, 0.1517 * imp - 0.973)
            w = (wind or {}).get(g.game_id)
            wadj = WIND_SLOPE * max(0.0, (w or 0) - WIND_FLOOR)
            pfr = float(np.clip(pf.get(t, 0.615) - 0.0027 * fav - wadj, 0.35, 0.85))
            teams[t] = dict(game_id=g.game_id, opp=g[f"{opp}_team"], implied=imp, lam=lam, pass_frac=pfr,
                            home=side == "home", gametime=g.gameday + " " + str(g.gametime), spread=g.spread_line,
                            wind=w, wind_adj=wadj)
    players = []
    for t, info in teams.items():
        allow = active.get(t) if active else None
        for kind in ["rush", "rec"]:
            k = sh[(sh.kind == kind)]
            k = k[k.pid.isin(allow)] if allow is not None else k[k.last_team == t]
            k = k.copy(); k["share"] = k.share.clip(lower=0.012)
            tot = k.share.sum()
            # leave residual mass for unlisted players; cap the pool at 95%
            target = min(0.98, max(tot, 0.92)) if allow is not None else min(tot, 0.95)
            k["share"] = k.share * (target / tot if tot > 0 else 0)
            k["team"] = t
            players.append(k)
    pl = pd.concat(players)
    qbs = {}
    ps = passer_share(past)
    for t in teams:
        if qb_override and t in qb_override:
            qbs[t] = qb_override[t]
        else:
            q = ps[ps.posteam == t].sort_values("att", ascending=False)
            qbs[t] = (q.passer_player_id.iloc[0], q.passer_player_name.iloc[0]) if len(q) else (None, "?")
    return teams, pl, qbs, sh


# ---------- simulation ----------
def simulate(teams, pl, qbs, n=40000):
    """Returns per-player outcome matrix and per-team/QB outputs."""
    team_list = list(teams)
    tix = {t: i for i, t in enumerate(team_list)}
    # player index
    pl = pl.copy()
    pids = list(dict.fromkeys(pl.pid.tolist() + [q[0] for q in qbs.values() if q[0]]))
    pix = {p: i for i, p in enumerate(pids)}
    P = len(pids)
    tds = np.zeros((n, P), dtype=np.int8)        # scoring TDs (rush+rec)
    first_game = np.zeros((n, P), dtype=bool)
    first_team = np.zeros((n, P), dtype=bool)
    qb_ptd = {}
    game_ids = sorted({v["game_id"] for v in teams.values()})
    game_tdcount = {}
    for gid in game_ids:
        tt = [t for t in team_list if teams[t]["game_id"] == gid]
        f = np.exp(RNG.normal(-GAME_SIGMA ** 2 / 2, GAME_SIGMA, n))
        events = []   # list of (team, order_time array, scorer idx array)
        for t in tt:
            lam = teams[t]["lam"]
            k = RNG.binomial(TD_N, np.clip(lam * f / TD_N, 0, 0.95))
            kd = RNG.poisson(DST_TD_RATE, n)
            maxk = k.max()
            ispass = RNG.random((n, maxk)) < teams[t]["pass_frac"]
            valid = np.arange(maxk)[None, :] < k[:, None]
            scorer = np.full((n, maxk), -1)
            for kind, mask in [("rec", ispass), ("rush", ~ispass)]:
                sub = pl[(pl.team == t) & (pl.kind == kind)]
                probs = np.append(sub.share.values, max(0, 1 - sub.share.sum()))
                idx = np.append([pix[x] for x in sub.pid], -1)
                draw = idx[RNG.choice(len(probs), size=(n, maxk), p=probs / probs.sum())]
                scorer = np.where(mask & valid, draw, scorer)
            qpid = qbs[t][0]
            qb_ptd[t] = ((ispass & valid & (RNG.random((n, maxk)) < 0.97))).sum(1)
            for j in range(maxk):
                sc = scorer[:, j]
                ok = valid[:, j] & (sc >= 0)
                np.add.at(tds, (np.where(ok)[0], sc[ok]), 1)
            times = np.where(valid, RNG.random((n, maxk)), np.inf)
            events.append((t, times, scorer, kd))
        # first TD of game: min time across both teams' offensive TDs + DST TDs
        allt, alls, allteam = [], [], []
        for t, times, scorer, kd in events:
            allt.append(times); alls.append(scorer); allteam.append(np.full(times.shape, tix[t]))
            dt = np.where(kd > 0, RNG.random(n) ** (1 / np.maximum(kd, 1)), np.inf)[:, None]  # min of kd uniforms
            allt.append(dt); alls.append(np.full((n, 1), -1)); allteam.append(np.full((n, 1), tix[t]))
            # team-first TD
            ft = np.argmin(np.concatenate([times, dt], 1), 1)
            fs = np.concatenate([scorer, np.full((n, 1), -1)], 1)[np.arange(n), ft]
            ok = fs >= 0
            first_team[np.where(ok)[0], fs[ok]] = True
        T = np.concatenate(allt, 1); S = np.concatenate(alls, 1)
        a = np.argmin(T, 1); hasany = np.isfinite(T[np.arange(n), a])
        fs = S[np.arange(n), a]; ok = hasany & (fs >= 0)
        first_game[np.where(ok)[0], fs[ok]] = True
    return dict(pids=pids, pix=pix, tds=tds, first_game=first_game, first_team=first_team, qb_ptd=qb_ptd, n=n)


def first_td_split(spread):
    """P(home team scores the game's first TD | a TD happens) for home-receives, away-receives, unknown."""
    sig = lambda x: 1 / (1 + np.exp(-x))
    hr = sig(FIRST_A + FIRST_RECV + FIRST_SPREAD * spread)
    ar = sig(FIRST_A - FIRST_RECV + FIRST_SPREAD * spread)
    return hr, ar, (hr + ar) / 2


def fair_american(p):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return np.where(p >= 0.5, -100 * p / (1 - p), 100 * (1 - p) / p).round().astype(int)


def summarize(teams, pl, qbs, sim, names):
    tds, pix = sim["tds"], sim["pix"]
    rows = []
    meta = pl.groupby("pid").agg(team=("team", "first")).team.to_dict()
    for t, (qid, qn) in qbs.items():
        if qid: meta.setdefault(qid, t)
    for pid, i in pix.items():
        t = meta.get(pid)
        if t is None: continue
        rs = pl[(pl.pid == pid) & (pl.kind == "rush")].share.sum()
        rc = pl[(pl.pid == pid) & (pl.kind == "rec")].share.sum()
        q1 = pl[pl.pid == pid].q1share.mean()
        rows.append(dict(pid=pid, name=names.get(pid, pid), team=t, opp=teams[t]["opp"],
                         game_id=teams[t]["game_id"], rush_share=rs, rec_share=rc,
                         xtd=tds[:, i].mean(), p_any=(tds[:, i] > 0).mean(), p_2plus=(tds[:, i] > 1).mean(),
                         p_first=sim["first_game"][:, i].mean(), p_team_first=sim["first_team"][:, i].mean(),
                         q1_share=q1))
    return pd.DataFrame(rows)
