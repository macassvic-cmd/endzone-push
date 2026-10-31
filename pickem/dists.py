"""Fit a per-stat distribution to devigged sportsbook props.

Each fitted distribution exposes .ppf(u) (vectorised quantile function) and .sf(x),
so it can be sampled through a Gaussian copula.

Supported families
  gamma    yardage (right skewed). Fit: median/mean from main line, CV default or from alt ladder.
  poisson  counts (receptions, pass TDs, INTs, anytime TDs).
  normal   high-volume counts (pass attempts, completions, rush attempts).
"""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np
from scipy import stats, optimize

from .odds import devig, devig_one_sided, american_to_implied, overround

# stat -> (family, default shape). shape = CV for gamma, SD for normal.
STAT_FAMILY = {
    "rec_yds": ("gamma", 0.75),
    "rush_yds": ("gamma", 0.60),
    "pass_yds": ("normal", 60.0),
    "receptions": ("poisson", None),
    "rush_att": ("normal", 4.0),
    "pass_att": ("normal", 6.0),
    "pass_cmp": ("normal", 4.5),
    "pass_tds": ("poisson", None),
    "ints": ("poisson", None),
    "anytime_td": ("poisson", None),
}


@dataclass
class Fitted:
    family: str
    params: dict

    def ppf(self, u):
        u = np.clip(u, 1e-9, 1 - 1e-9)
        f, p = self.family, self.params
        if f == "gamma":
            return stats.gamma.ppf(u, p["k"], scale=p["theta"])
        if f == "poisson":
            return stats.poisson.ppf(u, p["lam"])
        if f == "normal":
            return np.maximum(0, np.round(stats.norm.ppf(u, p["mu"], p["sd"])))
        raise ValueError(f)

    def sf(self, x):
        """P(stat > x) for a half-point line x."""
        f, p = self.family, self.params
        if f == "gamma":
            return stats.gamma.sf(x, p["k"], scale=p["theta"])
        if f == "poisson":
            return stats.poisson.sf(np.floor(x), p["lam"])
        if f == "normal":
            return stats.norm.sf(np.floor(x) + 0.5, p["mu"], p["sd"])
        raise ValueError(f)

    def mean(self):
        f, p = self.family, self.params
        return {"gamma": p.get("k", 0) * p.get("theta", 0), "poisson": p.get("lam"), "normal": p.get("mu")}[f]


# ------------------------------------------------------------------ fitting

def _fit_gamma(points, cv):
    """points: list of (line, p_over). One point -> fixed CV; 2+ -> fit mean & CV."""
    def make(mean, cv_):
        k = 1 / cv_ ** 2
        return k, mean / k

    if len(points) == 1:
        line, p = points[0]
        k = 1 / cv ** 2
        theta = optimize.brentq(lambda t: stats.gamma.sf(line, k, scale=t) - p, 1e-3, 1e4)
        return {"k": k, "theta": theta}

    def loss(x):
        m, c = x
        if m <= 0 or c <= 0.1 or c > 3:
            return 1e9
        k, t = make(m, c)
        return sum((stats.gamma.sf(l, k, scale=t) - p) ** 2 for l, p in points)

    start_line = points[len(points) // 2][0]
    res = optimize.minimize(loss, [start_line * 1.1, cv], method="Nelder-Mead")
    k, t = make(*res.x)
    return {"k": k, "theta": t}


def _fit_poisson(points):
    if len(points) == 1:
        line, p = points[0]
        lam = optimize.brentq(lambda l: stats.poisson.sf(np.floor(line), l) - p, 1e-4, 100)
        return {"lam": lam}
    res = optimize.minimize_scalar(
        lambda l: sum((stats.poisson.sf(np.floor(x), l) - p) ** 2 for x, p in points),
        bounds=(1e-3, 100), method="bounded")
    return {"lam": res.x}


def _fit_normal(points, sd):
    if len(points) == 1:
        line, p = points[0]
        return {"mu": np.floor(line) + 0.5 - sd * stats.norm.ppf(1 - p), "sd": sd}

    def loss(x):
        mu, s = x
        if s <= 0.5:
            return 1e9
        return sum((stats.norm.sf(np.floor(l) + 0.5, mu, s) - p) ** 2 for l, p in points)

    res = optimize.minimize(loss, [points[0][0], sd], method="Nelder-Mead")
    return {"mu": res.x[0], "sd": res.x[1]}


def prop_points(stat: str, prop: dict, method: str, td_juice: float):
    """Turn one stat's book prices into [(line, fair P(over))].

    prop formats
      two-way:  {"line": 58.5, "over": -113, "under": -113, "alts": {"40": -250, "60": +120}}
      one-way:  {"odds": +210}            (anytime TD: P(>=1))
      one-way:  {"line": 31.5, "over": -110, "juice": 0.048}
    Alt ladder keys are 'N+' thresholds (N+ == over N-0.5). Alts are devigged using the main
    line's overround (same book, same market), falling back to `juice`.
    """
    if prop.get("fair_points"):            # already-devigged consensus [(line, p_over)]
        return sorted((float(l), float(p)) for l, p in prop["fair_points"] if 0.02 < p < 0.98)
    pts = []
    juice = prop.get("juice", 0.048)
    if stat == "anytime_td":
        return [(0.5, devig_one_sided(prop["odds"], prop.get("juice", td_juice)))]
    if "over" in prop and "under" in prop:
        pts.append((prop["line"], devig(prop["over"], prop["under"], method)))
        juice = overround(prop["over"], prop["under"])
    elif "over" in prop:
        pts.append((prop["line"], devig_one_sided(prop["over"], juice)))
    for thr, odds in (prop.get("alts") or {}).items():
        line = float(str(thr).rstrip("+")) - 0.5
        p = devig_one_sided(odds, juice)
        if 0.02 < p < 0.98:  # extreme rungs carry mostly juice/longshot bias
            pts.append((line, p))
    pts.sort()
    return pts


def fit_stat(stat: str, prop: dict, method: str = "multiplicative", td_juice: float = 0.136) -> Fitted:
    """Anchor on the main line. Only a real alt ladder (3+ rungs spanning a meaningful range) is
    allowed to move the spread, and then only within sane bounds; several books posting a point
    or two apart is noise, and fitting a spread to it blows the distribution up."""
    family, shape = STAT_FAMILY.get(stat, ("normal", None))
    shape = prop.get("shape", shape)
    pts = prop_points(stat, prop, method, td_juice)
    if not pts:
        raise ValueError(f"no usable prices for {stat}")
    main = prop.get("line")
    anchor = min(pts, key=lambda lp: abs(lp[0] - main)) if main is not None else pts[len(pts) // 2]
    span = pts[-1][0] - pts[0][0]
    ladder = len(pts) >= 3 and span >= max(3.0, 0.25 * abs(anchor[0]))
    use = pts if ladder else [anchor]
    if family == "gamma":
        f = Fitted("gamma", _fit_gamma(use, shape))
        cv = 1 / np.sqrt(f.params["k"])
        if not (0.35 <= cv <= 1.4):                      # ladder fit went somewhere silly
            f = Fitted("gamma", _fit_gamma([anchor], shape))
        return f
    if family == "poisson":
        return Fitted("poisson", _fit_poisson(use))
    if shape is None:
        shape = max(1.0, 0.2 * anchor[0])
    f = Fitted("normal", _fit_normal(use, shape))
    if not (0.5 * shape <= f.params["sd"] <= 2 * shape):
        f = Fitted("normal", _fit_normal([anchor], shape))
    return f
