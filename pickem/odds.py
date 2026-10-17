"""Odds conversion and devig methods.

All probabilities are floats in [0, 1]. American odds are ints/floats like -113 or +210.
"""
from __future__ import annotations

import math
from scipy import optimize


def american_to_decimal(odds: float) -> float:
    return 1 + (odds / 100 if odds > 0 else 100 / -odds)


def american_to_implied(odds: float) -> float:
    return 100 / (odds + 100) if odds > 0 else -odds / (-odds + 100)


def prob_to_american(p: float) -> float:
    if p <= 0 or p >= 1:
        raise ValueError("probability must be in (0, 1)")
    return (1 / p - 1) * 100 if p < 0.5 else -p / (1 - p) * 100


def fmt_american(p: float) -> str:
    a = prob_to_american(p)
    return f"+{a:.0f}" if a > 0 else f"{a:.0f}"


# ---------------------------------------------------------------- two-way devig

def devig_multiplicative(o1: float, o2: float) -> float:
    a, b = american_to_implied(o1), american_to_implied(o2)
    return a / (a + b)


def devig_additive(o1: float, o2: float) -> float:
    a, b = american_to_implied(o1), american_to_implied(o2)
    return a - (a + b - 1) / 2


def devig_power(o1: float, o2: float) -> float:
    a, b = american_to_implied(o1), american_to_implied(o2)
    k = optimize.brentq(lambda k: a ** k + b ** k - 1, 1e-6, 50)
    return a ** k


def devig_shin(o1: float, o2: float) -> float:
    a, b = american_to_implied(o1), american_to_implied(o2)
    s = a + b

    def p_of(z, q):
        return (math.sqrt(z * z + 4 * (1 - z) * q * q / s) - z) / (2 * (1 - z))

    z = optimize.brentq(lambda z: p_of(z, a) + p_of(z, b) - 1, 0, 0.99)
    return p_of(z, a)


METHODS = {
    "multiplicative": devig_multiplicative,
    "additive": devig_additive,
    "power": devig_power,
    "shin": devig_shin,
}


def devig(o1: float, o2: float, method: str = "multiplicative") -> float:
    """Fair probability of side 1. method='worst_case' returns the lowest of all methods."""
    if method == "worst_case":
        return min(f(o1, o2) for f in METHODS.values())
    return METHODS[method](o1, o2)


def devig_one_sided(odds: float, juice: float = 0.048) -> float:
    """One-sided market (alt line, anytime TD) with an assumed total juice.

    Mirrors the crazyninjamike devigger's `-110/4.8%` syntax: the market is assumed to
    carry `juice` of total overround, split multiplicatively.
    """
    return american_to_implied(odds) / (1 + juice)


def overround(o1: float, o2: float) -> float:
    return american_to_implied(o1) + american_to_implied(o2) - 1
