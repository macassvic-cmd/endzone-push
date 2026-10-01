"""2+ TD parlay builder, two modes.

"likely": leg pool = Starter-role players ranked by the model's 2+ TD probability, market disagreement ignored
          (a leg still needs a price to be priced); parlays ranked by joint probability.
"value":  leg pool = Starter-role players with a priced 2+ TD market (2+ books or a liquid Kalshi quote), blended
          probability (model/market as on the Edge Board) and EV >= MIN_EV at the best book; parlays ranked by EV.
Both: no "model weak spot" QBs, every leg from a different game. Parlay probability = product of leg probabilities
(different games treated as independent); offered price = product of each leg's decimal odds at its best book (a
book's parlay/SGP price can be typed on the page). Per leg: model and market probability, best price/book, leg EV,
and the break-even (fair) price below which the leg is not worth taking. Stake = 1/8 Kelly as a fraction of
bankroll, capped at MAX_STAKE; 0 when EV <= 0 ("no bet at this price, needs at least +X").
The top TOP_PAPER parlays of each mode are paper-traded at 1 unit each week (results.py).
"""
import itertools, math

MIN_EV, MIN_BOOKS, KELLY_FRACTION, MAX_STAKE, TOP_N, TOP_PAPER, MAX_POOL = 0.10, 2, 1 / 8, 0.0025, 5, 3, 12
MODES = ("likely", "value")


def decimal(price):
    return 1 + (price / 100 if price > 0 else 100 / -price)


def american(dec):
    return int(round((dec - 1) * 100)) if dec >= 2 else int(round(-100 / (dec - 1)))


def fair_price(prob):
    return american(1 / prob) if 0 < prob < 1 else None


def _leg(p, prob):
    price = int(p["two_best"]) if p.get("two_best") is not None else None
    dec = decimal(price) if price else None
    return dict(pid=p["pid"], name=p["name"], team=p["team"], game_id=p["game_id"], prob=round(prob, 4),
                model_p=round(p["p_2plus"], 4), mkt_p=p.get("two_mkt_p"), price=price, book=p.get("two_book"),
                dec=round(dec, 4) if dec else None, ev=round(prob * dec - 1, 4) if dec else None,
                fair=fair_price(prob))                      # minimum price for this leg to break even


def leg_pool(players, mode):
    out = []
    for p in players:
        if p.get("role") != "Starter" or p.get("weak_spot"):
            continue
        if mode == "likely":
            if p.get("p_2plus", 0) <= 0:
                continue
            out.append(_leg(p, p["p_2plus"]))
        else:
            if p.get("two_best") is None or p.get("two_blend_p") is None:
                continue
            liquid_kalshi = p.get("two_kalshi_liquid") and p.get("two_book") == "Kalshi"
            if (p.get("two_nbooks") or 0) < MIN_BOOKS and not liquid_kalshi:
                continue
            leg = _leg(p, p["two_blend_p"])
            if leg["ev"] is None or leg["ev"] < MIN_EV:
                continue
            out.append(leg)
    key = (lambda l: -l["prob"]) if mode == "likely" else (lambda l: -l["ev"])
    return sorted(out, key=key)


def kelly(prob, dec):
    """Fractional Kelly stake as a fraction of bankroll, capped; 0 when the price is not worth it."""
    if not dec or dec <= 1:
        return 0.0
    b = dec - 1
    f = (prob * b - (1 - prob)) / b
    return round(min(max(f, 0.0) * KELLY_FRACTION, MAX_STAKE), 5)


def build_mode(players, mode, sizes=(2, 3, 4), top_n=TOP_N):
    legs = leg_pool(players, mode)[:MAX_POOL]
    out = []
    for n in sizes:
        cands = []
        for combo in itertools.combinations(legs, n):
            if len({l["game_id"] for l in combo}) < n:
                continue
            prob = math.prod(l["prob"] for l in combo)
            priced = all(l["dec"] for l in combo)
            dec = math.prod(l["dec"] for l in combo) if priced else None
            ev = prob * dec - 1 if dec else None
            cands.append(dict(mode=mode, legs=[dict(l) for l in combo], n=n, prob=round(prob, 5), fair=fair_price(prob),
                              offered=american(dec) if dec else None, dec=round(dec, 2) if dec else None,
                              ev=round(ev, 4) if ev is not None else None, stake=kelly(prob, dec) if dec else 0.0,
                              hits_every=int(round(1 / prob)) if prob > 0 else None))
        key = (lambda c: -c["prob"]) if mode == "likely" else (lambda c: -(c["ev"] if c["ev"] is not None else -9))
        out += sorted(cands, key=key)[:top_n]
    # paper-trade the top TOP_PAPER priced parlays of the mode (by the mode's own ranking, across sizes)
    ranked = sorted([c for c in out if c["dec"]], key=key)
    for c in out: c["paper"] = False
    for c in ranked[:TOP_PAPER]: c["paper"] = True
    return dict(pool=legs, parlays=out)


def build(players):
    return dict(modes={m: build_mode(players, m) for m in MODES},
                rules=dict(min_ev=MIN_EV, min_books=MIN_BOOKS, kelly=KELLY_FRACTION, max_stake=MAX_STAKE, top_paper=TOP_PAPER))


def grade(parlays, two_hit):
    """two_hit: pid -> bool (scored 2+). Returns graded parlays and leg-level tallies."""
    graded, legs_n, legs_hit, legs_p = [], 0, 0, 0.0
    for c in parlays:
        hits = [bool(two_hit.get(l["pid"], False)) for l in c["legs"]]
        won = all(hits)
        graded.append(dict(**{k: v for k, v in c.items() if k != "legs"}, legs=[dict(**l, hit=h) for l, h in zip(c["legs"], hits)],
                           won=won, profit=round((c["dec"] - 1 if won else -1.0), 3) if c.get("dec") else 0.0))
        legs_n += len(hits); legs_hit += sum(hits); legs_p += sum(l["prob"] for l in c["legs"])
    return graded, dict(legs=legs_n, legs_hit=legs_hit, legs_expected=round(legs_p, 2))
