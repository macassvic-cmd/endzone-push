"""Price pick'em entries (Underdog / PrizePicks) off sportsbook props.

Pipeline
  1. fit a distribution for every priced stat of every player         (dists.py)
  2. simulate each player's fantasy score with intra-player correlation
  3. build a leg-level correlation matrix from team / role heuristics,
     scaled by `s` (optionally calibrated to a real same-game-parlay price)
  4. simulate the entry through a Gaussian copula -> P(k legs hit) -> EV
"""
from __future__ import annotations

from dataclasses import dataclass, field
import numpy as np
from scipy import stats, optimize

from .dists import fit_stat, Fitted, prop_points
from .odds import devig, devig_one_sided, american_to_implied, fmt_american

SCORING = {
    "underdog": {"rec_yds": 0.1, "receptions": 0.5, "anytime_td": 6, "rush_yds": 0.1,
                 "pass_yds": 0.04, "pass_tds": 4, "ints": -1},
    "prizepicks": {"rec_yds": 0.1, "receptions": 1.0, "anytime_td": 6, "rush_yds": 0.1,
                   "pass_yds": 0.04, "pass_tds": 4, "ints": -1},
}

# latent correlations between stats of the SAME player (Gaussian copula)
INTRA = {
    frozenset(("rec_yds", "receptions")): 0.70,
    frozenset(("rec_yds", "anytime_td")): 0.30,
    frozenset(("receptions", "anytime_td")): 0.25,
    frozenset(("rush_yds", "anytime_td")): 0.35,
    frozenset(("rush_yds", "rec_yds")): 0.10,
    frozenset(("rush_yds", "receptions")): 0.10,
    frozenset(("pass_yds", "pass_tds")): 0.55,
    frozenset(("pass_yds", "ints")): 0.10,
    frozenset(("pass_tds", "ints")): -0.05,
    frozenset(("pass_yds", "rush_yds")): -0.10,
}

VOLUME_STATS = {"pass_att", "pass_cmp", "pass_yds"}

# latent correlations BETWEEN legs, before the global scale `s`
LEG_CORR = {
    "qbvol_own_receiver": 0.35,   # QB attempts/yards  <-> own WR/TE
    "qbvol_own_rb": 0.15,
    "qbvol_opp_skill": 0.05,      # shootout spill-over
    "qbvol_qbvol_opp": 0.00,      # shootout (+) vs game script (-) roughly cancel
    "teammates": -0.05,           # compete for targets
    "opp_skill": 0.08,            # game total
    "rb_rush_own_qbvol": -0.15,   # run-heavy script = fewer attempts
}
FANTASY_SHRINK = 0.90  # TD component dilutes fantasy's link to QB volume


def nearest_psd(C):
    w, v = np.linalg.eigh(C)
    C2 = v @ np.diag(np.clip(w, 1e-6, None)) @ v.T
    d = np.sqrt(np.diag(C2))
    return C2 / np.outer(d, d)


# ------------------------------------------------------------------ players

@dataclass
class Player:
    name: str
    team: str
    pos: str
    props: dict
    fits: dict = field(default_factory=dict)
    game: str = ""

    def fit(self, method, td_juice):
        for stat, prop in self.props.items():
            self.fits[stat] = fit_stat(stat, prop, method, td_juice)


def simulate_fantasy(p: Player, scoring: dict, n: int, rng) -> tuple[np.ndarray, list[str]]:
    comps = [s for s in scoring if s in p.fits]
    if not comps:
        raise ValueError(f"{p.name}: no priced stats that count for fantasy")
    k = len(comps)
    C = np.eye(k)
    for i in range(k):
        for j in range(i + 1, k):
            C[i, j] = C[j, i] = INTRA.get(frozenset((comps[i], comps[j])), 0.0)
    C = nearest_psd(C)
    u = stats.norm.cdf(rng.multivariate_normal(np.zeros(k), C, n))
    draws = {s: p.fits[s].ppf(u[:, i]) for i, s in enumerate(comps)}
    if "receptions" in draws and "rec_yds" in draws:
        draws["rec_yds"] = np.where(draws["receptions"] == 0, 0.0, draws["rec_yds"])
    fp = sum(scoring[s] * draws[s] for s in comps)
    return np.sort(fp), comps


# ------------------------------------------------------------------ legs

@dataclass
class Leg:
    player: Player
    stat: str          # "fantasy" or a stat key
    line: float
    side: str          # "over" / "under"
    samples: np.ndarray | None = None   # sorted marginal samples (continuous legs)
    p_over: float | None = None          # for legs priced directly as a probability
    comps: list | None = None

    @property
    def p_hit(self):
        if self.samples is not None:
            po = float((self.samples > self.line).mean())
        else:
            po = self.p_over
        return po if self.side == "over" else 1 - po

    @property
    def label(self):
        s = "fantasy pts" if self.stat == "fantasy" else self.stat
        return f"{self.player.name} {self.side} {self.line} {s}"


def _kind(leg: Leg):
    pos = leg.player.pos.upper()
    if pos == "QB" and leg.stat in VOLUME_STATS:
        return "qbvol"
    if pos == "RB" and leg.stat in {"rush_yds", "rush_att"}:
        return "rb_rush"
    if pos == "QB":
        return "qb_other"
    return "skill"


def pair_corr(a: Leg, b: Leg) -> float:
    ka, kb = _kind(a), _kind(b)
    same = a.player.team == b.player.team
    if a.player is b.player:
        return 0.0
    if a.player.game != b.player.game:      # different games: independent
        return 0.0
    ks = {ka, kb}
    r = 0.0
    if ks == {"qbvol", "skill"}:
        sk = a if ka == "skill" else b
        if same:
            r = LEG_CORR["qbvol_own_rb"] if sk.player.pos.upper() == "RB" else LEG_CORR["qbvol_own_receiver"]
        else:
            r = LEG_CORR["qbvol_opp_skill"]
        if sk.stat == "fantasy":
            r *= FANTASY_SHRINK
    elif ks == {"qbvol"}:
        r = 0.0 if same else LEG_CORR["qbvol_qbvol_opp"]
    elif ks == {"skill"}:
        r = LEG_CORR["teammates"] if same else LEG_CORR["opp_skill"]
    elif ks == {"qbvol", "rb_rush"}:
        r = LEG_CORR["rb_rush_own_qbvol"] if same else 0.0
    elif ks == {"rb_rush", "skill"}:
        r = LEG_CORR["teammates"] if same else LEG_CORR["opp_skill"]
    elif "qb_other" in ks:
        other = b if ka == "qb_other" else a
        r = (LEG_CORR["qbvol_own_receiver"] * FANTASY_SHRINK if same and _kind(other) == "skill"
             else LEG_CORR["opp_skill"])
    return r


def corr_matrix(legs, s=1.0):
    n = len(legs)
    C = np.eye(n)
    for i in range(n):
        for j in range(i + 1, n):
            C[i, j] = C[j, i] = max(-0.95, min(0.95, s * pair_corr(legs[i], legs[j])))
    return nearest_psd(C)


# ------------------------------------------------------------------ joint probabilities

def all_hit_exact(legs, C):
    """P(all binary legs hit) via multivariate normal CDF (used for calibration)."""
    p = np.array([l.p_hit for l in legs])
    d = np.array([-1.0 if l.side == "over" else 1.0 for l in legs])
    Cd = C * np.outer(d, d)
    return float(stats.multivariate_normal(np.zeros(len(legs)), Cd, allow_singular=True).cdf(stats.norm.ppf(p)))


def simulate_entry(legs, C, n, rng):
    """Returns array: prob of exactly k hits, k = 0..len(legs)."""
    u = stats.norm.cdf(rng.multivariate_normal(np.zeros(len(legs)), C, n))
    hits = np.zeros(n, dtype=int)
    for i, l in enumerate(legs):
        if l.samples is not None:
            v = l.samples[np.minimum((u[:, i] * len(l.samples)).astype(int), len(l.samples) - 1)]
            h = v > l.line if l.side == "over" else v < l.line
        else:
            h = u[:, i] > 1 - l.p_over if l.side == "over" else u[:, i] <= 1 - l.p_over
        hits += h
    return np.bincount(hits, minlength=len(legs) + 1) / n


def entry_ev(dist_k, payouts):
    """payouts: {k_hits: multiplier}. Returns (EV per $1, P(win anything))."""
    ret = sum(dist_k[int(k)] * m for k, m in payouts.items())
    return ret - 1, sum(dist_k[int(k)] for k in payouts)


def kelly_single(p, mult):
    b = mult - 1
    return max(0.0, (b * p - (1 - p)) / b)


# ------------------------------------------------------------------ board

class Board:
    def __init__(self, data: dict):
        self.data = data
        cfg = data.get("config", {})
        self.method = cfg.get("devig_method", "multiplicative")
        self.td_juice = cfg.get("td_juice", 0.136)
        self.n = int(cfg.get("sims", 200_000))
        self.seed = cfg.get("seed", 7)
        self.rng = np.random.default_rng(self.seed)
        sc = cfg.get("scoring", "underdog")
        self.scoring = SCORING[sc] if isinstance(sc, str) else sc
        self.players = {}
        for name, p in data["players"].items():
            pl = Player(name, p["team"], p.get("pos", "WR"), p.get("props", {}), game=p.get("game", ""))
            pl.fit(self.method, self.td_juice)
            self.players[name] = pl
        self._fantasy_cache = {}

    def fantasy(self, name, scoring=None):
        scoring = scoring or self.scoring
        key = (name, tuple(sorted(scoring.items())))
        if key not in self._fantasy_cache:
            self._fantasy_cache[key] = simulate_fantasy(self.players[name], scoring, self.n, self.rng)
        return self._fantasy_cache[key]

    def make_leg(self, spec, scoring=None) -> Leg:
        pl = self.players[spec["player"]]
        stat, line, side = spec["stat"], float(spec["line"]), spec.get("side", "over")
        if stat == "fantasy":
            samples, comps = self.fantasy(pl.name, scoring)
            return Leg(pl, stat, line, side, samples=samples, comps=comps)
        # explicit book odds on the leg (SGP legs) take priority
        if "odds" in spec:
            if "odds_other" in spec:
                p_side = devig(spec["odds"], spec["odds_other"], self.method)
            else:
                p_side = devig_one_sided(spec["odds"], spec.get("juice", 0.048))
            p_over = p_side if side == "over" else 1 - p_side
            return Leg(pl, stat, line, side, p_over=p_over)
        fit = pl.fits[stat]
        prop = pl.props[stat]
        if prop.get("line") == line and "over" in prop and "under" in prop:
            p_over = devig(prop["over"], prop["under"], self.method)
        else:
            p_over = float(fit.sf(line))
        return Leg(pl, stat, line, side, p_over=p_over)

    # -------- SGP calibration of the global correlation scale
    def calibrate(self):
        cal = self.data.get("sgp_calibration") or []
        rows = []
        for c in cal:
            legs = [self.make_leg(s) for s in c["legs"]]
            indep = float(np.prod([l.p_hit for l in legs]))
            implied = american_to_implied(c["price"])
            for hold in c.get("holds", [c.get("hold", 0.25)]):
                target = implied / (1 + hold)
                f = lambda s: all_hit_exact(legs, corr_matrix(legs, s)) - target
                lo, hi = 0.0, 2.5
                if f(lo) >= 0:
                    s = 0.0
                elif f(hi) <= 0:
                    s = hi
                else:
                    s = optimize.brentq(f, lo, hi, xtol=1e-3)
                rows.append({"sgp": c.get("name", ""), "price": c["price"], "hold": hold,
                             "indep": indep, "fair_joint": target, "ratio": target / indep,
                             "scale": s, "use": c.get("use", True) and hold == c.get("hold", 0.25)})
        return rows

    def price(self):
        cal = self.calibrate()
        used = [r["scale"] for r in cal if r["use"]]
        cfg_scale = self.data.get("config", {}).get("corr_scale")
        scale = cfg_scale if cfg_scale is not None else (float(np.mean(used)) if used else 1.0)
        out = {"calibration": cal, "corr_scale": scale, "entries": []}
        for e in self.data.get("entries", []):
            sc = e.get("scoring")
            scoring = (SCORING[sc] if isinstance(sc, str) else sc) if sc else None
            legs = [self.make_leg(s, scoring) for s in e["legs"]]
            payouts = {int(k): float(v) for k, v in e["payout"].items()}
            res = {"name": e.get("name", ""), "payout": payouts, "legs": [], "scenarios": {}}
            for l in legs:
                row = {"leg": l.label, "p_hit": l.p_hit, "fair": fmt_american(l.p_hit)}
                if l.samples is not None:
                    row["model_median"] = float(np.median(l.samples))
                    row["model_mean"] = float(l.samples.mean())
                    row["components"] = l.comps
                res["legs"].append(row)
            scen = {"independent": 0.0, "calibrated": scale}
            for extra in e.get("extra_scales", []):
                scen[f"scale_{extra}"] = extra
            for label, s in scen.items():
                dk = simulate_entry(legs, corr_matrix(legs, s), self.n, self.rng)
                ev, p_any = entry_ev(dk, payouts)
                top = len(legs)
                r = {"scale": s, "p_all": float(dk[top]), "p_k": [float(x) for x in dk],
                     "ev": ev, "p_cash": p_any, "fair_all": fmt_american(dk[top]) if dk[top] > 0 else "n/a"}
                if len(payouts) == 1:
                    (k, m), = payouts.items()
                    r["kelly"] = kelly_single(dk[k], m)
                    r["breakeven_mult"] = 1 / dk[k] if dk[k] > 0 else float("inf")
                res["scenarios"][label] = r
            out["entries"].append(res)
        return out
