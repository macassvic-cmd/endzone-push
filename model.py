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
# Game-to-game role variability: each sim game draws every player's share from Beta(mean*k, (1-mean)*k), renormalised per
# team. 2024+2025 weeks 1-18 (n=3000): k=0 under-predicts 2+ TD games (2.48% vs 3.10% actual in 2025, 2.36% vs 2.98% in
# 2024); k=15 gives 2.78% / 2.66% with the best 2+ Brier, anytime Brier flat within seed noise (-0.00005 / +0.00012) and
# top-15 hits 7.67 -> 7.61 (2025), 7.72 -> 8.06 (2024). k=8 matches the 2+ rate but costs 0.0003 anytime Brier in 2024.
SHARE_VAR_KAPPA = 15.0
TD_N, GAME_SIGMA = 7, 0.20          # binomial drive cap / shared game factor (fit to data)
DST_TD_RATE = 0.13                  # non-offensive TDs per team-game
# Round 1 grid (search.py --grid, 144 cells, DECAY 0.80-0.95 x PRIOR_SEASON_W 0.3-0.8 x PRIOR_K 1.5-5 x REC_SLOPE 0.25-0.6):
# picked on 2025 weeks 4-11, confirmed on 12-18. Best cell (0.85/0.55/K=5/0.60) beat the current values by 0.00027 Brier
# on the pick weeks but only 0.0001 on the confirmation weeks (seed noise ~0.0001) with top-15 hits 8.00 -> 7.71/wk,
# so the values below stay. REC_SLOPE was flat everywhere; PRIOR_K=5 was the only consistent (small) signal.
DECAY, PRIOR_SEASON_W = 0.88, 0.55  # per-game recency decay, prior-season multiplier
REC_KNEE, REC_SLOPE = 0.25, 0.35   # receiving-share compression above knee (set by backtest)
WIND_FLOOR, WIND_SLOPE = 10.0, 0.003  # pass-TD share drops ~0.3 pts per mph above 10 (2023-25 fit)
# First TD of game: logit P(home scores first) = a + b*recv + c*spread_line   (recv = +1 home receives, -1 away)
FIRST_A, FIRST_RECV, FIRST_SPREAD = 0.0131, 0.3828, 0.0868
# Share prior by (kind, position, depth-chart rank), estimated by priors.py from 2025 (mean share of team xTD for
# every listed player, zero when unused). Shrinkage: share = (wsum*share + PRIOR_K*prior) / (wsum + PRIOR_K).
PRIOR_K, PRIOR_DEFAULT = 3.0, 0.02       # prior weight in games; prior for players not at a listed skill slot
# Early season: a player with fewer than EARLY_MIN_GAMES current-season games gets K * (1 + EARLY_K_EXTRA * (5 - week) / 4)
# in weeks 1-4 (full extra in week 1, none from week 5). 0 = off.
# Tested on 2024+2025 weeks 1-4 (pooled 2,684 rows): EARLY_K_EXTRA 0 / 1 / 2 / 3 -> Brier 0.13655 / 0.13680 / 0.13727 /
# 0.13774, top-15 hits 8.00 / 7.88 / 7.75 / 7.62 per week, and the low buckets stay under-projected, so it is off.
EARLY_K_EXTRA, EARLY_MIN_GAMES = 0.0, 3
RANK_CAP = {"RB": 3, "WR": 4, "TE": 2, "QB": 2}
POS_MAP = {"FB": "RB", "HB": "RB"}
SHARE_PRIOR = {('rec', 'QB', 1): 0.001, ('rec', 'QB', 2): 0.0, ('rec', 'RB', 1): 0.056, ('rec', 'RB', 2): 0.033,
               ('rec', 'RB', 3): 0.007, ('rec', 'TE', 1): 0.153, ('rec', 'TE', 2): 0.033, ('rec', 'WR', 1): 0.222,
               ('rec', 'WR', 2): 0.181, ('rec', 'WR', 3): 0.099, ('rec', 'WR', 4): 0.025,
               ('rush', 'QB', 1): 0.042, ('rush', 'QB', 2): 0.011,   # QB1 rush uses the median: bimodal (mean 0.145 is all mobile QBs) ('rush', 'RB', 1): 0.346, ('rush', 'RB', 2): 0.216,
               ('rush', 'RB', 3): 0.031, ('rush', 'TE', 1): 0.006, ('rush', 'TE', 2): 0.002, ('rush', 'WR', 1): 0.005,
               ('rush', 'WR', 2): 0.005, ('rush', 'WR', 3): 0.002, ('rush', 'WR', 4): 0.002}
# 2025 walk-forward backtest (depth-chart active set, 4956 props): Brier old 0.1329 -> prior 0.1314 -> prior+snap cap
# 0.1316 (tie) with better 5-35% calibration and top-15 hits 7.60 -> 7.73/wk. Turning the pool inflation off
# under-predicts total scorers by ~10% (Brier 0.1527 vs 0.1514 in the touch-based run), so it stays on.
# QB rushing: QB carries get their own TD-rate tables (sneak / designed / scramble by yardline, qb_xtd_tables), QBs
# are excluded from the pool stretch, and the QB1 rush prior depends on type: mobile = 2+ designed runs per game over
# the window (2025 QB1 mean share 0.22 mobile vs 0.11 pocket).
# 2025 backtest (depth-chart regime, n=3000): before Brier 0.1315 / QB Brier 0.1322 / pocket QBs 10.5% pred vs 10.7%
# actual, mobile 24.2 vs 28.9 / top-15 7.60 hits per week. After (QBs out of the pool, priors below, generic rush
# table): 0.1316 / 0.1302 / pocket 9.7 vs 10.7, mobile 25.5 vs 28.9 / 7.93. The QB-specific sneak/designed/scramble
# TD table (QB_OWN_XTD) over-predicted pocket QBs (12.2 vs 10.8) and worsened Brier to 0.1317, so it stays off.
QB_MIN_ATT, QB_MOBILE_RUNS = 20, 2.0                  # QB = 20+ attempts in a season; "mobile" label = 2+ designed runs/game
QB_PRIOR_A, QB_PRIOR_B, QB_PRIOR_DR_CAP = 0.068, 0.054, 4.0   # QB1 rush-share prior = A + B * designed runs per game (2025 fit, kneels excluded)
QB_PRIOR_K = 3.0                                     # prior weight (games) for QB rush shares; one sneak at the 1 is a third of a team's weekly rush xTD
# Mobile-QB round (2025 + 2024 backtests, pick weeks 4-11 / confirm 12-18, shared rows): mobile QBs are under-predicted
# in aggregate (2025 designed-runs >= 1.5: ~25% projected vs ~31% actual; 2024: ~22% vs ~41%) but the tiers are 15-30
# QB-games each and flip between halves (2025 2.5+ tier: 37.5% actual in weeks 4-11, 15.0% in 12-18; 2024 mid tier:
# 37.5% then 6.7%). None of (a)-(d) below improved the mid tier on held-out weeks without hurting overall or QB Brier,
# so all four stay off. A steeper single prior slope (QB_PRIOR_B 0.08 / 0.10) was queued but the runs were stopped
# by a low-memory event before finishing; untested.
QB_PRIOR_K_VET, QB_VET_GAMES = 3.0, 20               # (a) lighter prior for QBs with 2+ seasons of games in the window (tested: 1.5)
MOBILE_PF_SLOPE, MOBILE_PF_KNEE = 0.0, 1.0           # (b) pass_frac -= slope * max(0, designed runs/game - knee) for the team's QB1
QB_PRIOR_HI = None                                   # (c) (A, B, split): separate prior line A + B*dr for dr >= split
QB_OWN_XTD_TYPES = ("sneak", "designed", "scramble") # (d) which QB carry types use the QB-specific TD-rate table when QB_OWN_XTD
QB_OWN_XTD, QB_OUT_OF_POOL = False, True
# Round 2 (2025 backtest, shared rows, n=3000; base Brier 0.13194, top-15 7.87 hits/wk):
#   redistribution alone 0.13170 (kept); fitted xTD 0.13214 (off); no-history players + rookie/mover priors add nothing
#   on top of redistribution (0.13168) and cut top-15 hits to 7.47 (7.27 with the 0.5 discount), so they stay off;
#   everything together 0.13215. Rookies / new arrivals and redistribution when a meaningful player is out:
ROOKIE_PRIOR = {("rush", "RB"): {"R1": 0.35, "R2-3": 0.22, "R4-7": 0.11, "UDFA": 0.03},     # 2022-25 rookie-season share of
                ("rec", "WR"): {"R1": 0.21, "R2-3": 0.084, "R4-7": 0.043, "UDFA": 0.015},   # team xTD by draft round (fit_rookies
                ("rec", "TE"): {"R1": 0.12, "R2-3": 0.09, "R4-7": 0.05, "UDFA": 0.015}}     # in priors.py); blended 50/50 with slot
ROOKIE_W, MOVER_W = 0.0, 1.0             # rookie prior weight vs slot prior; sample weight kept by a veteran on a new team
NEW_PLAYERS = False                      # add depth-listed active players with no history at their prior
NEW_PLAYER_W = 1.0                       # multiplier on that prior for players with no history at all (2025: they hit 2.2% vs 4.9% predicted at 1.0)
REDIST = True                            # position-aware redistribution of an absent player's share (instead of proportional)
REDIST_MIN_SHARE, REDIST_RECENT = 0.08, 6   # absent player counts if his share >= 8% and he played within 6 weeks
REDIST_SAME, REDIST_NEXT, REDIST_OTHER = 0.35, 0.20, 0.15   # of the freed share: same-position listed (proportional), next man up, other positions; rest -> "other"
KNEELS_ARE_CARRIES = False                           # True reproduces the old behaviour (kneels credited as rush attempts) for A/B tests
INFLATE_POOL = True                      # scale listed shares up to >= 92% of team xTD (False leaves the rest as "other")
SNAP_CAP, SNAP_CAP_SHARE, SNAP_CAP_MIN_RZ = 0.25, 0.04, 2   # <25% snaps over last 3 games -> rush+rec share <= 4% combined ...
SNAP_CAP_EXEMPT_SNAPS = 0.15             # ... unless 2+ inside-10 carries / end-zone targets AND at least 15% snaps
SNAP_CAP_AFTER_RESCALE = False           # False: the capped mass is redistributed to teammates (True sends it to "other": total scorers -6%)


def qb_types(past):
    """pid -> designed runs per game played (kneels excluded) for every QB in the window."""
    if "qb_rush" not in past:
        return {}
    games = past[past.pass_attempt == 1].groupby("passer_player_id").game_id.nunique()
    runs = past[past.qb_rush & (past.rush_typ == "designed")].groupby("rusher_player_id").size()
    return {pid: runs.get(pid, 0) / max(g, 1) for pid, g in games.items()}


def qb_label(dr):
    return None if dr is None or dr != dr else ("mobile" if dr >= QB_MOBILE_RUNS else "pocket")


def draft_bucket(draft_number):
    dn = draft_number
    return "UDFA" if dn is None or dn != dn else "R1" if dn <= 32 else "R2-3" if dn <= 105 else "R4-7"


def share_prior(kind, d, qb_dr=None, rookie=None):
    """d = (pos, rank) from the depth chart, or None. qb_dr = designed runs per game sets the QB1 rush prior.
       rookie = draft bucket ("R1", "R2-3", "R4-7", "UDFA") for a first-year player: nudges the slot prior."""
    if not d or d[1] is None or d[1] != d[1]:
        return PRIOR_DEFAULT
    pos = POS_MAP.get(d[0], d[0])
    if pos not in RANK_CAP:
        return PRIOR_DEFAULT
    if kind == "rush" and pos == "QB" and d[1] <= 1 and qb_dr is not None and qb_dr == qb_dr:
        if QB_PRIOR_HI and qb_dr >= QB_PRIOR_HI[2]:
            return QB_PRIOR_HI[0] + QB_PRIOR_HI[1] * min(qb_dr, QB_PRIOR_DR_CAP)
        return QB_PRIOR_A + QB_PRIOR_B * min(qb_dr, QB_PRIOR_DR_CAP)
    prior = SHARE_PRIOR.get((kind, pos, int(min(d[1], RANK_CAP[pos]))), PRIOR_DEFAULT)
    if rookie and (kind, pos) in ROOKIE_PRIOR:
        prior = (1 - ROOKIE_W) * prior + ROOKIE_W * ROOKIE_PRIOR[(kind, pos)].get(rookie, prior)
    return prior


def rz_recent(past, n=3):
    """pid -> inside-10 carries + end-zone targets over the player's last n games in `past`."""
    ru = past[(past.rush_attempt == 1) & (past.qb_kneel != 1) & past.rusher_player_id.notna()]
    pa = past[(past.pass_attempt == 1) & past.receiver_player_id.notna()]
    d = pd.concat([pd.DataFrame(dict(pid=ru.rusher_player_id, ord=ru.season * 100 + ru.week, hot=(ru.yardline_100 <= 10).astype(int))),
                   pd.DataFrame(dict(pid=pa.receiver_player_id, ord=pa.season * 100 + pa.week,
                                     hot=(pa.air_yards.fillna(-99) >= pa.yardline_100).astype(int)))])
    g = d.groupby(["pid", "ord"]).hot.sum().reset_index().sort_values("ord", ascending=False)
    return g.groupby("pid").head(n).groupby("pid").hot.sum().to_dict()


def load():
    p = pd.read_parquet("data/pbp.parquet")
    s = pd.read_parquet("data/sched.parquet")
    p = p[p.season_type == "REG"].copy()
    return p, s


# ---------- expected-TD tables by field position ----------
QB_YB = [0, 1, 2, 3, 5, 10, 20, 50, 100]          # yardline bins for the QB rush tables


def qb_ids(p):
    """(season, pid) pairs with QB_MIN_ATT+ pass attempts: how we recognise a QB carry in play-by-play."""
    att = p[p.pass_attempt == 1].groupby(["season", "passer_player_id"]).size()
    return set(att[att >= QB_MIN_ATT].index)


def tag_qb_rush(p):
    q = qb_ids(p)
    p = p.copy()
    p["is_rush"] = (p.rush_attempt == 1) & ((p.qb_kneel != 1) | KNEELS_ARE_CARRIES)   # kneel-downs are not carries (a kneel at the 1 is not a 45% TD chance)
    p["qb_rush"] = p.is_rush & pd.Series([(a, b) in q for a, b in zip(p.season, p.rusher_player_id)], index=p.index)
    p["rush_typ"] = np.where(~p.qb_rush, "", np.where(p.qb_scramble == 1, "scramble",
                             np.where((p.ydstogo <= 1) & (p.yardline_100 <= 3), "sneak", "designed")))
    return p


# ---------- fitted xTD (round 2): logistic models from fit_xtd.py, coefficients in xtd_model.json ----------
XTD_FITTED = False                                   # True: add_xtd uses the fitted models instead of the bucket tables
XTD_FILE = "xtd_model.json"
_XTD = None


def rush_features(d):
    yl = d.yardline_100.clip(1, 99).astype(float)
    X = pd.DataFrame(dict(yl=yl, log_yl=np.log(yl), in5=(yl <= 5).astype(float), in1=(yl <= 1).astype(float),
                          in10=(yl <= 10).astype(float), ydstogo=d.ydstogo.fillna(10).clip(0, 30).astype(float),
                          goal_to_go=d.goal_to_go.fillna(0).astype(float), shotgun=d.shotgun.fillna(0).astype(float),
                          qb=d.qb_rush.astype(float) if "qb_rush" in d else 0.0), index=d.index)
    for k in (1, 2, 3, 4): X[f"down{k}"] = (d.down == k).astype(float)
    return X


def rec_features(d):
    yl = d.yardline_100.clip(1, 99).astype(float); ay = d.air_yards.fillna(0).clip(-10, 60).astype(float)
    X = pd.DataFrame(dict(yl=yl, log_yl=np.log(yl), in10=(yl <= 10).astype(float), in20=(yl <= 20).astype(float),
                          air=ay, ez=(ay >= d.yardline_100).astype(float), deep=(ay >= 20).astype(float),
                          ydstogo=d.ydstogo.fillna(10).clip(0, 30).astype(float), goal_to_go=d.goal_to_go.fillna(0).astype(float)), index=d.index)
    for loc in ("left", "middle", "right"): X[f"loc_{loc}"] = (d.pass_location == loc).astype(float)
    for k in (1, 2, 3, 4): X[f"down{k}"] = (d.down == k).astype(float)
    return X


def fitted_xtd(d, kind):
    """Fitted logistic xTD for rows d (already filtered to carries or targets)."""
    global _XTD
    if _XTD is None:
        import json, os
        _XTD = json.load(open(XTD_FILE)) if os.path.exists(XTD_FILE) else {}
    m = _XTD.get(kind)
    if not m or len(d) == 0:
        return None
    X = (rush_features if kind == "rush" else rec_features)(d)[m["features"]].values
    z = X @ np.array(m["coef"]) + m["intercept"]
    return 1 / (1 + np.exp(-z))


def xtd_tables(p):
    p = tag_qb_rush(p)
    ru = p[p.is_rush & p.yardline_100.notna()]
    ru_b = ru.assign(b=ru.yardline_100.clip(1, 99).astype(int)).groupby("b").rush_touchdown.mean()
    pa = p[(p.pass_attempt == 1) & (p.sack == 0) & p.receiver_player_id.notna() & p.yardline_100.notna()].copy()
    pa["ez"] = (pa.air_yards.fillna(0) >= pa.yardline_100).astype(int)
    pa["b"] = pa.yardline_100.clip(1, 99).astype(int) // 5
    tg_b = pa.groupby(["b", "ez"]).pass_touchdown.mean()
    # QB carries by type x yardline bin, backed off to the generic rate where a cell has < 20 carries
    q = ru[ru.qb_rush].assign(yb=pd.cut(ru[ru.qb_rush].yardline_100, QB_YB, labels=False))
    qb_b = q.groupby(["rush_typ", "yb"]).rush_touchdown.agg(["mean", "size"])   # index (typ, bin) -> mean, n
    return ru_b.rolling(3, center=True, min_periods=1).mean(), tg_b, qb_b


def add_xtd(p, ru_b, tg_b, qb_b=None):
    p = tag_qb_rush(p)
    yl = p.yardline_100.clip(1, 99).fillna(50).astype(int)
    p["rush_xtd"] = np.where(p.is_rush, ru_b.reindex(yl).values, 0.0)
    if QB_OWN_XTD and qb_b is not None and p.qb_rush.any():
        tbl = {(t, int(b)): (m, n) for (t, b), (m, n) in zip(qb_b.index, qb_b.values) if b == b}
        q = p[p.qb_rush]
        yb = pd.cut(q.yardline_100.fillna(50), QB_YB, labels=False).fillna(-1).astype(int)
        cells = [tbl.get((t, b)) for t, b in zip(q.rush_typ, yb)]
        new = [c[0] if c and c[1] >= 20 and t in QB_OWN_XTD_TYPES else x for c, x, t in zip(cells, q.rush_xtd, q.rush_typ)]
        p.loc[q.index, "rush_xtd"] = new
    ez = (p.air_yards.fillna(0) >= p.yardline_100).astype(int)
    key = pd.MultiIndex.from_arrays([yl // 5, ez])
    p["rec_xtd"] = np.where((p.pass_attempt == 1) & p.receiver_player_id.notna(),
                            tg_b.reindex(key).fillna(0).values, 0.0)
    if XTD_FITTED:
        ru = p[p.is_rush & p.yardline_100.notna()]
        f = fitted_xtd(ru, "rush")
        if f is not None: p.loc[ru.index, "rush_xtd"] = f
        pa = p[(p.pass_attempt == 1) & (p.sack == 0) & p.receiver_player_id.notna() & p.yardline_100.notna()]
        f = fitted_xtd(pa, "rec")
        if f is not None: p.loc[pa.index, "rec_xtd"] = f
    return p


# ---------- player / team shares ----------
def game_index(p, season, week):
    past = p[(p.season < season) | ((p.season == season) & (p.week < week))]
    past = past[past.season >= season - 1]
    return past


def player_games(past):
    """Per player-game rows: share of team rush/rec xTD and 1st-quarter share. Hyperparameter-free (cache it)."""
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
    pg["ord"] = pg.season * 100 + pg.week
    return pg


def shares(past, season, depth=None, pg=None, qb_type=None, team_of=None, rookies=None, week=None):
    """Recency-weighted per-player share of team rush/rec xTD, plus 1st-quarter share.
       depth: pid -> (pos, rank) from the depth chart; enables the position/rank prior.
       pg: precomputed player_games(past).
       team_of: pid -> current team for this week's active players. Veterans whose history is with another team keep
       only MOVER_W of their sample weight; depth-listed active players with no history are added at their prior.
       rookies: pid -> draft bucket for first-year players (nudges the prior)."""
    g = (player_games(past) if pg is None else pg).sort_values("ord", ascending=False).copy()
    g["rank"] = g.groupby(["pid", "kind"]).cumcount()                  # 0 = newest game
    g["w"] = DECAY ** g["rank"] * np.where(g.season < season, PRIOR_SEASON_W, 1.0)
    g["ws"] = g.w * g.share
    q = g.q1share.notna()
    g["wq"] = np.where(q, g.w, 0.0); g["wqs"] = np.where(q, g.w * g.q1share.fillna(0), 0.0)
    g["cur"] = (g.season == season).astype(int)
    sh = g.groupby(["pid", "kind"]).agg(name=("name", "first"), last_team=("posteam", "first"), share=("ws", "sum"),
                                        wsum=("w", "sum"), n=("w", "size"), last_ord=("ord", "first"),
                                        n_cur=("cur", "sum"), wq=("wq", "sum"), wqs=("wqs", "sum")).reset_index()
    sh["share"] = sh.share / sh.wsum
    sh["q1share"] = np.where(sh.wq > 0, sh.wqs / sh.wq.replace(0, np.nan), np.nan)
    sh = sh.drop(columns=["wq", "wqs"])
    qb_type = qb_types(past) if qb_type is None else qb_type
    rookies = rookies or {}
    if team_of and depth is not None and NEW_PLAYERS:
        # depth-listed active skill players with no history at all: one row per kind at zero sample (prior only)
        have = set(zip(sh.pid, sh.kind)); add = []
        for pid, t in team_of.items():
            d = depth.get(pid)
            if not d or POS_MAP.get(d[0], d[0]) not in RANK_CAP: continue
            for k in ("rush", "rec"):
                if (pid, k) not in have:
                    add.append(dict(pid=pid, kind=k, name=pid, last_team=t, share=0.0, wsum=0.0, n=0, n_cur=0, last_ord=season * 100, q1share=np.nan))
        if add: sh = pd.concat([sh, pd.DataFrame(add)], ignore_index=True)
    sh["qb_dr"] = sh.pid.map(qb_type)
    sh["qb_type"] = sh.qb_dr.map(qb_label)
    if team_of:
        # a veteran on a new team keeps MOVER_W of his sample weight; his current team becomes last_team
        cur = sh.pid.map(team_of)
        moved = cur.notna() & (cur != sh.last_team)
        sh.loc[moved, "wsum"] = sh.wsum[moved] * MOVER_W
        sh.loc[cur.notna(), "last_team"] = cur[cur.notna()]
    if PRIOR_K > 0 and depth is not None:
        # shrink toward the position/rank baseline: K games' worth of prior vs the player's weighted sample (QBs: QB_PRIOR_K)
        sh["prior"] = [share_prior(k, depth.get(pid), dr, rookies.get(pid)) for pid, k, dr in zip(sh.pid, sh.kind, sh.qb_dr)]
        K = np.where(sh.qb_dr.notna() & (sh.kind == "rush"), np.where(sh.n >= QB_VET_GAMES, QB_PRIOR_K_VET, QB_PRIOR_K), PRIOR_K)
        if EARLY_K_EXTRA > 0 and week is not None and week < 5:
            K = K * np.where(sh.n_cur < EARLY_MIN_GAMES, 1 + EARLY_K_EXTRA * (5 - week) / 4, 1.0)
        sh.loc[sh.n == 0, "prior"] = sh.prior[sh.n == 0] * NEW_PLAYER_W
        sh["share"] = (sh.wsum * sh.share + K * sh.prior) / (sh.wsum + K)
    else:
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


DEF_K = 6.0                  # games of league-average shrinkage for defensive tendencies
# Round 1 test (2025, shared rows): pass-share adjustment hurt Brier at 0.5 (0.13201) and 1.0 (0.13217) vs 0.13194 off;
# RZ-rate lambda adjustment 0.5 gave 0.13180 (inside seed noise) with top-15 hits 7.87 -> 7.67/wk, 1.0 gave 0.13208. Both off.
DEF_PF_W, DEF_LAM_W = 0.0, 0.0   # weights on the opponent's pass-share-allowed deviation and RZ-TD-rate-allowed ratio


def defense_table(past, season):
    """Per defense: recency-weighted pass share of offensive TDs allowed (deviation from league) and
       red-zone TD rate allowed (ratio to league), each shrunk to league average with DEF_K games."""
    d = past[past.defteam.notna()]
    td = d[(d.touchdown == 1) & ((d.rush_touchdown == 1) | (d.pass_touchdown == 1))]
    g = td.groupby(["season", "week", "defteam"]).agg(pass_td=("pass_touchdown", "sum"), td=("touchdown", "size"))
    rz = d[d.yardline_100 <= 20].drop_duplicates(["game_id", "drive"]).groupby(["season", "week", "defteam"]).size().rename("rz_trips")
    g = g.join(rz, how="outer").fillna(0).reset_index()
    g["ord"] = g.season * 100 + g.week
    lg_pf = g.pass_td.sum() / max(g.td.sum(), 1)
    lg_rz = g.td.sum() / max(g.rz_trips.sum(), 1)
    out = {}
    for t, x in g.groupby("defteam"):
        x = x.sort_values("ord", ascending=False)
        w = DECAY ** np.arange(len(x)) * np.where(x.season < season, PRIOR_SEASON_W, 1.0)
        ws = w.sum()
        pf = (w * x.pass_td).sum() / max((w * x.td).sum(), 1e-9)
        rzr = (w * x.td).sum() / max((w * x.rz_trips).sum(), 1e-9)
        pf = (ws * pf + DEF_K * lg_pf) / (ws + DEF_K)
        rzr = (ws * rzr + DEF_K * lg_rz) / (ws + DEF_K)
        out[t] = dict(pf_dev=pf - lg_pf, rz_ratio=rzr / lg_rz if lg_rz > 0 else 1.0)
    return out


def prep_week(p, s, season, week):
    """Everything build_slate needs that does not depend on hyperparameters (for repeated evaluation)."""
    ru_b, tg_b, qb_b = xtd_tables(p[p.season < season] if season > 2023 else p)
    past = add_xtd(game_index(p, season, week), ru_b, tg_b, qb_b)
    return dict(past=past, pg=player_games(past), qb_type=qb_types(past))


def passer_share(past):
    """Share of team pass TDs thrown by each QB (for starter lookup)."""
    d = past[past.pass_attempt == 1]
    return d.groupby(["posteam", "passer_player_id", "passer_player_name"]).size().rename("att").reset_index()


# ---------- slate build ----------
def build_slate(p, s, season, week, active=None, qb_override=None, wind=None, depth=None, snaps=None, cache=None, rookies=None):
    """active: dict team -> set(pid) allowed (None = anyone whose last team matches).
       qb_override: dict team -> (pid, name).
       depth: pid -> (pos, rank) before this week (share prior). snaps: pid -> offensive snap % over his last 3 games.
       cache: prep_week() output, to skip the hyperparameter-free work."""
    cache = cache or prep_week(p, s, season, week)
    past = cache["past"]
    team_of = {pid: t for t, ids in (active or {}).items() for pid in ids} if active else None
    sh = shares(past, season, depth, pg=cache["pg"], qb_type=cache.get("qb_type"), team_of=team_of, rookies=rookies, week=week)
    all_active = set(team_of) if team_of else set()
    posof = lambda pid: POS_MAP.get(depth[pid][0], depth[pid][0]) if depth and pid in depth else None
    dfn = defense_table(past, season) if (DEF_PF_W or DEF_LAM_W) else {}
    rz3 = rz_recent(past) if snaps is not None and SNAP_CAP > 0 else {}
    pf = team_pass_frac(past, season)
    games = s[(s.season == season) & (s.week == week) & (s.game_type == "REG")].dropna(subset=["total_line"])
    ps = passer_share(past); qbs = {}
    for t in set(games.home_team) | set(games.away_team):
        if qb_override and t in qb_override:
            qbs[t] = qb_override[t]
        else:
            q = ps[ps.posteam == t].sort_values("att", ascending=False)
            qbs[t] = (q.passer_player_id.iloc[0], q.passer_player_name.iloc[0]) if len(q) else (None, "?")
    qdr = cache.get("qb_type") or qb_types(past)
    teams = {}
    for _, g in games.iterrows():
        for side, opp, sgn in [("home", "away", 1), ("away", "home", -1)]:
            t = g[f"{side}_team"]
            imp = g.total_line / 2 + sgn * g.spread_line / 2
            fav = sgn * g.spread_line
            lam = max(0.4, 0.1517 * imp - 0.973)
            w = (wind or {}).get(g.game_id)
            wadj = WIND_SLOPE * max(0.0, (w or 0) - WIND_FLOOR)
            od = dfn.get(g[f"{opp}_team"], {})                      # opponent's defensive tendencies
            lam *= 1 + DEF_LAM_W * (od.get("rz_ratio", 1.0) - 1)
            mob = MOBILE_PF_SLOPE * max(0.0, qdr.get(qbs.get(t, (None,))[0], 0.0) - MOBILE_PF_KNEE)   # mobile QB1: TDs skew to the run
            pfr = float(np.clip(pf.get(t, 0.615) - 0.0027 * fav - wadj + DEF_PF_W * od.get("pf_dev", 0.0) - mob, 0.35, 0.85))
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
            low = None
            if snaps is not None and SNAP_CAP > 0:
                # bit-part players: under 25% of snaps lately and no real goal-line role -> tiny share
                sn = k.pid.map(snaps)
                exempt = (k.pid.map(rz3).fillna(0) >= SNAP_CAP_MIN_RZ) & (sn >= SNAP_CAP_EXEMPT_SNAPS)
                low = (sn < SNAP_CAP) & ~exempt
            cap = lambda: k.share.where(~low, k.share.clip(upper=SNAP_CAP_SHARE / 2))   # half per kind -> combined cap
            if low is not None and not SNAP_CAP_AFTER_RESCALE:
                k["share"] = cap()
            tot = float(k.share.sum())
            # cap the pool; what is left is "other/unlisted" in the sim
            if allow is not None:
                target = min(0.98, max(tot, 0.92)) if INFLATE_POOL else min(tot, 0.98)
            else:
                target = min(tot, 0.95)
            # absent regulars: this team's players not active this week, meaningful share, seen recently
            out = pd.DataFrame()
            if REDIST and allow is not None:
                cand = sh[(sh.kind == kind) & (sh.last_team == t) & ~sh.pid.isin(all_active) & (sh.share >= REDIST_MIN_SHARE)
                          & (sh.last_ord >= season * 100 + week - REDIST_RECENT)]
                out = cand
                tot += float(out.share.sum())            # the stretch factor is set as if they were playing
                if allow is not None:
                    target = min(0.98, max(tot, 0.92)) if INFLATE_POOL else min(tot, 0.98)
            f = target / tot if tot > 0 else 0
            isqb = k.qb_type.notna() if QB_OUT_OF_POOL else pd.Series(False, index=k.index)
            # QBs keep their own share (never stretched); everyone else gets the same factor as before, the rest is "other"
            k.loc[~isqb, "share"] = k.share[~isqb] * f
            for _, o in out.iterrows():
                # position-aware redistribution of the absent player's (stretched) share, 2023-26 measurement:
                # listed same-position teammates take ~35% (proportional), the next man up ~20%, other positions ~15%, the rest is not replaced
                freed = float(o.share) * f; opos = posof(o.pid)
                same = k.index[[posof(x) == opos for x in k.pid]] if opos else k.index[[]]
                if len(same):
                    w = k.share[same]; k.loc[same, "share"] += REDIST_SAME * freed * (w / w.sum() if w.sum() > 0 else 1 / len(same))
                    nxt = k.loc[same].sort_values(["n", "share"]).index[0]     # least-sampled same-position player
                    k.loc[nxt, "share"] += REDIST_NEXT * freed
                others = k.index.difference(same)
                if len(others):
                    w = k.share[others]; k.loc[others, "share"] += REDIST_OTHER * freed * (w / w.sum() if w.sum() > 0 else 1 / len(others))
            if low is not None and SNAP_CAP_AFTER_RESCALE:
                k["share"] = cap()
            k["team"] = t
            players.append(k)
    pl = pd.concat(players)
    qbs = {t: qbs.get(t, (None, "?")) for t in teams}
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
    game_tdcount = {}; game_f = {}
    for gid in game_ids:
        tt = [t for t in team_list if teams[t]["game_id"] == gid]
        f = np.exp(RNG.normal(-GAME_SIGMA ** 2 / 2, GAME_SIGMA, n)); game_f[gid] = f      # shared game environment (yards sim reuses it)
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
                if SHARE_VAR_KAPPA > 0 and len(sub):
                    # game-to-game role variability: per sim game, shares ~ Beta around the mean, then renormalised
                    m = np.clip(probs / probs.sum(), 1e-4, 1 - 1e-4)
                    S = RNG.beta(m * SHARE_VAR_KAPPA, (1 - m) * SHARE_VAR_KAPPA, size=(n, len(m)))
                    S /= S.sum(1, keepdims=True)
                    cum = np.cumsum(S, 1); u = RNG.random((n, maxk))
                    draw = idx[np.minimum((u[:, :, None] > cum[:, None, :]).sum(2), len(m) - 1)]
                else:
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
    return dict(pids=pids, pix=pix, tds=tds, first_game=first_game, first_team=first_team, qb_ptd=qb_ptd, n=n, game_f=game_f)


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
    meta = pl.groupby("pid").agg(team=("team", "first")).team.to_dict()
    for t, (qid, qn) in qbs.items():
        if qid: meta.setdefault(qid, t)
    rs = pl[pl.kind == "rush"].groupby("pid").share.sum().to_dict()
    rc = pl[pl.kind == "rec"].groupby("pid").share.sum().to_dict()
    q1 = pl.groupby("pid").q1share.mean().to_dict()
    gm = pl.groupby("pid").n.max().to_dict()
    pids = [pid for pid in pix if pid in meta]
    idx = [pix[pid] for pid in pids]
    T = tds[:, idx]
    p_any, p2, xtd = (T > 0).mean(0), (T > 1).mean(0), T.mean(0)
    pf, ptf = sim["first_game"][:, idx].mean(0), sim["first_team"][:, idx].mean(0)
    rows = []
    for i, pid in enumerate(pids):
        t = meta[pid]
        rows.append(dict(pid=pid, name=names.get(pid, pid), team=t, opp=teams[t]["opp"],
                         game_id=teams[t]["game_id"], rush_share=float(rs.get(pid, 0.0)), rec_share=float(rc.get(pid, 0.0)),
                         games=int(gm.get(pid, 0)), xtd=float(xtd[i]), p_any=float(p_any[i]), p_2plus=float(p2[i]),
                         p_first=float(pf[i]), p_team_first=float(ptf[i]), q1_share=float(q1.get(pid, np.nan))))
    return pd.DataFrame(rows)
