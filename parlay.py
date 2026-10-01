"""2+ TD parlay builder.

Leg pool: Starter-role players with a priced 2+ TD market (2+ books or a liquid Kalshi quote), blended probability
(50/50 model / no-vig market, or 25/75 for thin-sample outliers as on the Edge Board), EV >= MIN_EV at the best book
with real liquidity, and no "model weak spot" QBs. Parlays: best 2-, 3- and 4-leg combinations with every leg from a
different game. Parlay probability = product of leg probabilities (legs from different games are treated as
independent); offered price = product of each leg's decimal odds at its best book (a book's actual parlay or SGP
price can be typed on the page). Stake = 1/8 Kelly as a fraction of bankroll, capped at MAX_STAKE.
"""
import itertools, math

MIN_EV, MIN_BOOKS, KELLY_FRACTION, MAX_STAKE, TOP_N = 0.10, 2, 1 / 8, 0.0025, 5


def decimal(price):
    return 1 + (price / 100 if price > 0 else 100 / -price)


def american(dec):
    return int(round((dec - 1) * 100)) if dec >= 2 else int(round(-100 / (dec - 1)))


def leg_pool(players):
    """Legs that pass the rules, best first by EV at the price taken."""
    out = []
    for p in players:
        if p.get("role") != "Starter" or p.get("weak_spot") or p.get("two_best") is None:
            continue
        liquid_kalshi = p.get("two_kalshi_liquid") and p.get("two_book") == "Kalshi"
        if (p.get("two_nbooks") or 0) < MIN_BOOKS and not liquid_kalshi:
            continue
        prob, price = p["two_blend_p"], int(p["two_best"])
        ev = prob * decimal(price) - 1
        if ev < MIN_EV:
            continue
        out.append(dict(pid=p["pid"], name=p["name"], team=p["team"], game_id=p["game_id"], prob=round(prob, 4),
                        model_p=round(p["p_2plus"], 4), mkt_p=p.get("two_mkt_p"), price=price, book=p.get("two_book"),
                        dec=round(decimal(price), 4), ev=round(ev, 4)))
    return sorted(out, key=lambda l: -l["ev"])


def kelly(prob, dec):
    """Fractional Kelly stake as a fraction of bankroll, capped."""
    b = dec - 1
    f = (prob * b - (1 - prob)) / b if b > 0 else 0.0
    return round(min(max(f, 0.0) * KELLY_FRACTION, MAX_STAKE), 5)


def build(players, sizes=(2, 3, 4), top_n=TOP_N, max_pool=12):
    """Best parlays per size (different games), each with prob, fair and offered odds, EV, variance note, stake."""
    legs = leg_pool(players)[:max_pool]
    out = []
    for n in sizes:
        cands = []
        for combo in itertools.combinations(legs, n):
            if len({l["game_id"] for l in combo}) < n:
                continue
            prob = math.prod(l["prob"] for l in combo); dec = math.prod(l["dec"] for l in combo)
            ev = prob * dec - 1
            cands.append(dict(legs=[dict(l) for l in combo], n=n, prob=round(prob, 5), fair=american(1 / prob) if prob > 0 else None,
                              offered=american(dec), dec=round(dec, 2), ev=round(ev, 4), stake=kelly(prob, dec),
                              hits_every=int(round(1 / prob)) if prob > 0 else None))
        out += sorted(cands, key=lambda c: -c["ev"])[:top_n]
    return dict(pool=legs, parlays=out, rules=dict(min_ev=MIN_EV, min_books=MIN_BOOKS, kelly=KELLY_FRACTION, max_stake=MAX_STAKE))


def grade(parlays, two_hit):
    """two_hit: pid -> bool (scored 2+). Returns graded parlays and leg-level tallies."""
    graded, legs_n, legs_hit, legs_p = [], 0, 0, 0.0
    for c in parlays:
        hits = [bool(two_hit.get(l["pid"], False)) for l in c["legs"]]
        won = all(hits)
        graded.append(dict(**{k: v for k, v in c.items() if k != "legs"}, legs=[dict(**l, hit=h) for l, h in zip(c["legs"], hits)],
                           won=won, profit=round(c["dec"] - 1 if won else -1.0, 3)))
        legs_n += len(hits); legs_hit += sum(hits); legs_p += sum(l["prob"] for l in c["legs"])
    return graded, dict(legs=legs_n, legs_hit=legs_hit, legs_expected=round(legs_p, 2))
