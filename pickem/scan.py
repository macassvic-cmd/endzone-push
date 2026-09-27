"""Scan the Underdog NFL board against sportsbook props and find the best entries.

    python -m pickem.scan                       # live: Underdog board + Odds API (needs ODDS_API_KEY)
    UD_MOCK=ud.json ODDS_MOCK=odds.json python -m pickem.scan   # offline rerun

Writes pickem_latest.json (read by pickem.html).

Per leg:  model P(hit) from devigged book props (fantasy legs simulated from their components),
          edge vs Underdog's own per-pick price, and Underdog's payout multiplier.
Entries:  every 2-6 leg combo of the best legs (max one leg per player), P(all hit) from one
          correlated Monte Carlo (same-game legs correlated, different games independent),
          payout = base[k] x product(pick payout_multiplier), EV = P * payout - 1.
"""
from __future__ import annotations

import datetime as dt
import itertools
import json
import os
import sys

import numpy as np
from scipy import stats

from . import ud_board, props
from .engine import Player, Leg, simulate_fantasy, corr_matrix, SCORING
from .odds import american_to_decimal, american_to_implied, fmt_american
import odds as lab_odds

# Underdog standard payout by number of picks, before per-pick multipliers. The 6-pick value is
# backed out of two real LAC@BUF slips (31.45x = 29.39 x 1.07 overs, 26.2x = 29.39 x 0.89 unders).
# The rest are Underdog's long-standing standard table - verify in-app and override with
# PICKEM_BASE='{"2":3,"3":6,"4":10,"5":20,"6":29.39}' if they differ.
DEFAULT_BASE = {2: 3.0, 3: 6.0, 4: 10.0, 5: 20.0, 6: 29.39}
CORR_SCALE = float(os.environ.get("PICKEM_CORR_SCALE", 0.6))   # 0.6 = calibrated to DK SGPs, 2026 wk3
TOP_K = int(os.environ.get("PICKEM_TOP_K", 22))
SIMS = int(os.environ.get("PICKEM_SIMS", 120_000))
MIN_BOOKS = int(os.environ.get("PICKEM_MIN_BOOKS", 2))

# a fantasy leg is only priced when the components that drive it are priced
FANTASY_NEEDS = {"QB": {"pass_yds", "pass_tds"}, "RB": {"rush_yds", "anytime_td"},
                 "WR": {"rec_yds", "anytime_td"}, "TE": {"rec_yds", "anytime_td"}}


def base_table():
    raw = os.environ.get("PICKEM_BASE")
    return {int(k): float(v) for k, v in json.loads(raw).items()} if raw else DEFAULT_BASE


def build_players(ud_legs, cons, method="multiplicative"):
    players, skipped = {}, []
    for l in ud_legs:
        name = l["player"]
        if name in players:
            continue
        pp = cons.get(lab_odds.norm_name(name))
        if not pp:
            continue
        pl = Player(name, l["team"], l["pos"], {}, game=str(l["game_id"]))
        for stat, prop in pp.items():
            if prop["n_books"] < MIN_BOOKS and stat != "anytime_td":
                continue
            try:
                from .dists import fit_stat
                pl.fits[stat] = fit_stat(stat, prop, method)
                pl.props[stat] = prop
            except Exception as e:                       # unfittable market: skip, don't guess
                skipped.append(f"{name} {stat}: {e}")
        if pl.fits:
            players[name] = pl
    return players, skipped


def price_legs(ud_legs, players, rng):
    fantasy_cache, priced = {}, []
    for l in ud_legs:
        pl = players.get(l["player"])
        if not pl:
            continue
        if l["stat"] == "fantasy":
            need = FANTASY_NEEDS.get(pl.pos.upper(), {"rec_yds", "anytime_td"})
            if not need <= set(pl.fits):
                continue
            if pl.name not in fantasy_cache:
                fantasy_cache[pl.name] = simulate_fantasy(pl, SCORING["underdog"], SIMS, rng)
            samples, comps = fantasy_cache[pl.name]
            med = float(np.median(samples))
            if not (0.4 * l["line"] <= med <= 2.5 * l["line"]):     # a component fit went wrong: don't guess
                continue
            leg = Leg(pl, "fantasy", l["line"], l["side"], samples=samples, comps=comps)
        else:
            if l["stat"] not in pl.fits:
                continue
            prop = pl.props[l["stat"]]
            exact = {x: p for x, p in prop["fair_points"]}
            p_over = exact.get(l["line"], float(pl.fits[l["stat"]].sf(l["line"])))
            leg = Leg(pl, l["stat"], l["line"], l["side"], p_over=p_over)
        p = leg.p_hit
        ud_price = l["ud_price"]
        row = {**l, "p_hit": round(p, 4), "fair": fmt_american(p) if 0 < p < 1 else None,
               "ud_implied": round(american_to_implied(ud_price), 4) if ud_price else None,
               "edge_vs_ud": round(p * american_to_decimal(ud_price) - 1, 4) if ud_price else None,
               "book_line": pl.props.get(l["stat"], {}).get("line") if l["stat"] != "fantasy" else None,
               "model_median": round(float(np.median(leg.samples)), 2) if leg.samples is not None else None}
        priced.append((row, leg))
    return priced


def _popcount_table():
    return np.array([bin(i).count("1") for i in range(256)], dtype=np.uint32)


def search_entries(cands, base, rng, n=SIMS, per_size=8):
    """cands: [(row, leg)]. One correlated MC over all candidates, then exact combo counting on bitsets."""
    legs = [c[1] for c in cands]
    C = corr_matrix(legs, CORR_SCALE)
    u = stats.norm.cdf(rng.multivariate_normal(np.zeros(len(legs)), C, n))
    cols = []
    for i, l in enumerate(legs):
        if l.samples is not None:
            v = l.samples[np.minimum((u[:, i] * len(l.samples)).astype(int), len(l.samples) - 1)]
            h = v > l.line if l.side == "over" else v < l.line
        else:
            h = u[:, i] > 1 - l.p_over if l.side == "over" else u[:, i] <= 1 - l.p_over
        cols.append(np.packbits(h))
    cols = np.array(cols)
    pc = _popcount_table()
    out = {}
    for k in sorted(base):
        best = []
        for combo in itertools.combinations(range(len(legs)), k):
            names = [legs[i].player.name for i in combo]
            if len(set(names)) < k:
                continue
            bits = np.bitwise_and.reduce(cols[list(combo)], axis=0)
            p = pc[bits].sum() / n
            mult = base[k] * float(np.prod([cands[i][0]["ud_mult"] for i in combo]))
            ev = p * mult - 1
            best.append((ev, p, mult, combo))
        best.sort(key=lambda x: -x[0])
        out[k] = []
        for ev, p, mult, combo in best[:per_size]:
            rows = [cands[i][0] for i in combo]
            indep = float(np.prod([r["p_hit"] for r in rows]))
            out[k].append({
                "legs": [f"{r['player']} {'Higher' if r['side']=='over' else 'Lower'} {r['line']} {r['ud_stat']}" for r in rows],
                "games": sorted({r["game"] for r in rows}),
                "same_game": len({r["game_id"] for r in rows}) < k,
                "p_all": round(p, 4), "p_all_independent": round(indep, 4),
                "payout": round(mult, 2), "ev": round(ev, 4),
                "breakeven_payout": round(1 / p, 2) if p else None,
            })
    return out


def run(now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    rng = np.random.default_rng(int(os.environ.get("PICKEM_SEED", 11)))
    raw = ud_board.fetch()
    ud_legs = [l for l in ud_board.nfl_legs(raw)
               if l["game_status"] in (None, "scheduled") and (l["kickoff"] or "9") > now.strftime("%Y-%m-%dT%H:%M")]
    events = props.fetch_events()
    if not events:
        raise SystemExit("no odds (set ODDS_API_KEY or ODDS_MOCK)")
    cons = props.consensus(events)
    players, skipped = build_players(ud_legs, cons)
    priced = price_legs(ud_legs, players, rng)

    base = base_table()
    pos = [x for x in priced if (x[0]["edge_vs_ud"] or -1) > 0]
    pos.sort(key=lambda x: -x[0]["edge_vs_ud"])
    # best TOP_K legs by edge (one side per line; a slightly negative leg can still complete a good entry)
    ranked = sorted([x for x in priced if x[0]["edge_vs_ud"] is not None], key=lambda x: -x[0]["edge_vs_ud"])
    seen, cands = set(), []
    for x in ranked:
        if x[0]["line_id"] in seen:
            continue
        seen.add(x[0]["line_id"]); cands.append(x)
        if len(cands) == TOP_K:
            break
    entries = search_entries(cands, base, rng) if len(cands) >= 2 else {}

    legs_out = sorted((r for r, _ in priced), key=lambda r: -(r["edge_vs_ud"] or -9))
    result = {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "config": {"corr_scale": CORR_SCALE, "base_payouts": base, "top_k": TOP_K, "sims": SIMS,
                   "min_books": MIN_BOOKS, "alts": os.environ.get("PICKEM_ALTS") == "1"},
        "counts": {"ud_legs": len(ud_legs), "players_priced": len(players), "legs_priced": len(priced),
                   "positive_legs": len(pos), "skipped_markets": len(skipped)},
        "legs": legs_out,
        "entries": {str(k): v for k, v in entries.items()},
        "skipped": skipped[:200],
    }
    out = os.environ.get("PICKEM_OUT", "pickem_latest.json")
    with open(out + ".tmp", "w") as f:
        json.dump(result, f, indent=1, default=float)
    os.replace(out + ".tmp", out)
    return result


def summary(res, n_legs=15):
    lines = [f"Pickem scan {res['generated_at']}  {res['counts']}"]
    lines.append("\nTop legs (model vs Underdog price):")
    for r in res["legs"][:n_legs]:
        lines.append(f"  {r['edge_vs_ud']:+.1%}  {r['player']:<22} {r['side']:<5} {r['line']:>6} {r['ud_stat']:<16}"
                     f" model {r['p_hit']:.1%}  UD {r['ud_price']:+.0f} x{r['ud_mult']}  [{r['game']}]")
    for k, ents in res["entries"].items():
        if not ents:
            continue
        e = ents[0]
        lines.append(f"\nBest {k}-pick: EV {e['ev']:+.1%}  hit {e['p_all']:.1%}  pays {e['payout']}x"
                     f"{'  (same game - confirm payout in app)' if e['same_game'] else ''}")
        lines += [f"    {x}" for x in e["legs"]]
    return "\n".join(lines)


if __name__ == "__main__":
    print(summary(run()))
