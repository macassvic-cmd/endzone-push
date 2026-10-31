"""Fitted model+market blend: P(hit) = sigmoid(a + b*logit(model) + c*logit(no-vig market)), one fit per market.

results.py refits on every graded, priced row each run (anytime and first TD separately; 2+ TD once it has rows)
and writes blend_model.json; run_week.py reads it for every EV on the Edge Board and in the parlay builder. Below
MIN_ROWS rows, or without a file, the blend falls back to 50/50 in probability space. Since 2026-10-17 a fitted blend goes
live only for markets in APPROVED (user sign-off) and only after passing every gate in gates(): coefficients non-negative,
blend between the model and market probability on every row, first-TD per-game sums sane. The fit itself always runs and
is reported (results.json `blend`). Fitting is plain numpy IRLS
with a light ridge so the weekly CI run needs no extra dependency. Leave-one-week-out Brier guards against overfit.
"""
import json, os, numpy as np

FILE, MIN_ROWS, RIDGE = "blend_model.json", 300, 1e-3
APPROVED = frozenset({"first"})  # markets whose fitted blend the CEO has approved for live use. 2026-10-17: none. 2026-10-31 (CEO ruling): first TD, the
                                 # stored weeks 1-6 fit, frozen as stored (freeze_approved); anytime and 2+ TD stay 50/50 everywhere
GATE_WAIVED = frozenset({"first"})   # CEO ruling 2026-10-31: the first-TD fit fails the between-inputs and game-sum gates (sits above both inputs on 70% of
                                 # rows, max +0.11; 14 of 77 games over 1) and is restored anyway as a results call over the gate (W4/W5 record 7-33, +21.9u).
                                 # Known and accepted; recorded in CHANGELOG 2026-10-31. The monotone gate is never waived.
FROZEN_NOTE = ("2026-10-31 CEO ruling: first-TD coefficients frozen as stored (the weeks 1-6 fit) and restored over the between-inputs and game-sum gates; "
               "results.py refits every run for the report only (refit)")
BETWEEN_TOL = 1e-6              # gate 1: the blend must lie between the model and the market probability on every fitted row
GAME_SUM_TOL = 0.02             # gate 2 (first TD): per game, sum of blend P(first) <= 1 + tol and >= min(model sum, market sum) - tol
GAME_SUM_MARKETS = ("first",)   # first TD is one-of-N: the candidates' probabilities sum to 1 - P(no TD in the game), 1-3% in NFL games
MARKETS = {"any": ("p_any", "any_mkt_p", "hit"), "first": ("p_first", "first_mkt_p", "first_hit"), "two": ("p_2plus", "two_mkt_p", "two_hit"),
           "yds": ("p_yds", "yds_mkt_p", "yds_hit")}     # yards: pooled over rush / rec / pass at the books' main lines (results.py, 3+ graded weeks)


def logit(p):
    p = np.clip(np.asarray(p, float), 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def sigmoid(z):
    return 1 / (1 + np.exp(-z))


def fit(pm, pk, y, iters=50):
    """IRLS logistic regression on [1, logit(pm), logit(pk)] -> (a, b, c)."""
    X = np.column_stack([np.ones(len(pm)), logit(pm), logit(pk)]); y = np.asarray(y, float)
    w = np.zeros(3)
    for _ in range(iters):
        p = sigmoid(X @ w); W = p * (1 - p)
        H = X.T @ (X * W[:, None]) + RIDGE * np.eye(3); g = X.T @ (y - p) - RIDGE * w
        step = np.linalg.solve(H, g); w = w + step
        if np.abs(step).max() < 1e-8: break
    return [round(float(v), 5) for v in w]


def blend(pm, pk, coef=None):
    """THE blend (one ruler, 2026-10-20 / ratified 2026-10-29): P(hit) from the model probability pm and the no-vig market probability pk.
       coef None (anytime TD, 2+ TD, yards) = 50/50 in probability space, which always lies between its two inputs. A fitted logistic
       blend is used only through coef_for() (approval + gates, or the CEO waiver of 2026-10-31 for first TD). Every EV on the board, in grading and in the research scripts
       goes through this function; nothing else may average model and market."""
    pm, pk = np.asarray(pm, float), np.asarray(pk, float)
    if coef is None:
        return 0.5 * pm + 0.5 * pk
    a, b, c = coef
    return sigmoid(a + b * logit(pm) + c * logit(pk))


def predict(coef, pm, pk):
    """Kept for the fit / gate code: predict(coef, pm, pk) == blend(pm, pk, coef)."""
    return blend(pm, pk, coef)


def brier(p, y):
    return float(np.mean((np.asarray(p, float) - np.asarray(y, float)) ** 2))


def fit_market(df, mk):
    """df: graded rows with model prob, market prob, outcome and week. Returns the fit record or None."""
    pcol, kcol, ycol = MARKETS[mk]
    if kcol not in df:
        return None
    d = df[df[kcol].notna() & df[pcol].notna()]
    if len(d) < MIN_ROWS:
        return None
    pm, pk, y = d[pcol].values, d[kcol].values, d[ycol].astype(float).values
    coef = fit(pm, pk, y)
    rec = dict(coef=coef, n=int(len(d)), weeks=sorted(int(w) for w in d.week.unique()),
               brier=dict(model=round(brier(pm, y), 5), market=round(brier(pk, y), 5), half=round(brier(blend(pm, pk), y), 5),
                          fitted=round(brier(predict(coef, pm, pk), y), 5)))
    # leave-one-week-out: fit on the other weeks, score the held-out week; pooled Brier of the held-out predictions
    oos, oos_half, oos_mkt, oos_mod = [], [], [], []
    keys = list(zip(d.season, d.week)) if "season" in d else [(0, w) for w in d.week]
    d = d.assign(_k=[f"{s}-{w}" for s, w in keys])
    for k in sorted(d._k.unique()):
        tr, te = d[d._k != k], d[d._k == k]
        if len(tr) < MIN_ROWS // 2 or len(te) == 0: continue
        c = fit(tr[pcol].values, tr[kcol].values, tr[ycol].astype(float).values)
        oos += list((predict(c, te[pcol].values, te[kcol].values) - te[ycol].astype(float).values) ** 2)
        oos_half += list((blend(te[pcol].values, te[kcol].values) - te[ycol].astype(float).values) ** 2)
        oos_mkt += list((te[kcol].values - te[ycol].astype(float).values) ** 2); oos_mod += list((te[pcol].values - te[ycol].astype(float).values) ** 2)
    if oos:
        rec["loo"] = dict(fitted=round(float(np.mean(oos)), 5), half=round(float(np.mean(oos_half)), 5),
                          market=round(float(np.mean(oos_mkt)), 5), model=round(float(np.mean(oos_mod)), 5), n=len(oos))
    rec["gates"] = gates(coef, pm, pk, d, mk)
    rec["approved"] = mk in APPROVED
    rec["in_use"], rec["reason"] = status(rec, mk)
    return rec


def status(rec, mk):
    """(in_use, reason) of a fit record under today's approval / gate / waiver state; in_use is exactly coef_for() returning its coefficients."""
    loo_ok = bool(rec.get("loo") and rec["loo"]["fitted"] < rec["loo"]["half"])
    bad = [k for k, v in (rec.get("gates") or {}).items() if isinstance(v, dict) and not v.get("ok", True)]
    in_use = coef_for({mk: rec}, mk) is not None
    if in_use: reason = "in use" + (f" (CEO ruling 2026-10-31: gate waived, fails {', '.join(bad)})" if bad and mk in GATE_WAIVED else "")
    elif not loo_ok: reason = "not better than 50/50 held out"
    elif bad: reason = "fails a gate: " + ", ".join(bad)
    else: reason = "not approved for live use (blend.APPROVED)"
    return in_use, reason


def freeze_approved(models, prev):
    """The live coefficients of an APPROVED market never move with the weekly refit (model frozen): the record already stored in
       blend_model.json (prev) stays the ruler for its era and the fresh fit is kept beside it under "refit" for the report."""
    out = dict(models or {})
    for mk in APPROVED:
        p = (prev or {}).get(mk)
        if not p or not p.get("coef"): continue
        new = out.get(mk)
        rec = {k: v for k, v in p.items() if k not in ("refit", "in_use", "reason", "approved")}
        rec["frozen"] = p.get("frozen") or FROZEN_NOTE
        rec["refit"] = {k: new.get(k) for k in ("coef", "n", "weeks", "brier", "loo", "gates")} if new else p.get("refit")
        rec["approved"] = True
        rec["in_use"], rec["reason"] = status(rec, mk)
        out[mk] = rec
    return out


def between_inputs(coef, pm, pk, tol=BETWEEN_TOL):
    """Gate 1: a blend of two probabilities must not leave the interval they span, on any row. A logistic fit with an intercept or
       with b + c != 1 can (the 2026-10-17 first-TD fit sat above both inputs on 72% of rows, by up to 0.12)."""
    pm, pk = np.asarray(pm, float), np.asarray(pk, float); b = predict(coef, pm, pk)
    out = np.maximum(np.minimum(pm, pk) - b, b - np.maximum(pm, pk))
    return dict(ok=bool(np.all(out <= tol)), rows_outside=int((out > tol).sum()), n=int(len(b)), max_excursion=round(float(out.max()) if len(b) else 0.0, 4))


def game_sums(coef, pm, pk, game_ids, tol=GAME_SUM_TOL):
    """Gate 2 (first TD): per game, the blend's P(first TD) over the priced players may not exceed 1 + tol (the candidates' true
       sum is 1 - P(no TD) and the priced set is a subset of them), and may not fall below min(model sum, market sum) - tol.
       tol = 0.02: no-vig rounding plus the 1-3% of NFL games with no touchdown."""
    pm, pk = np.asarray(pm, float), np.asarray(pk, float); b = predict(coef, pm, pk); g = np.asarray(game_ids)
    worst_hi, worst_lo, bad = 0.0, 0.0, 0
    for gid in np.unique(g):
        m = g == gid; sb, sm, sk = float(b[m].sum()), float(pm[m].sum()), float(pk[m].sum())
        hi = sb - 1.0; lo = min(sm, sk) - sb
        worst_hi, worst_lo = max(worst_hi, hi), max(worst_lo, lo)
        if hi > tol or lo > tol: bad += 1
    return dict(ok=bad == 0, games_outside=bad, games=int(len(np.unique(g))), max_over_one=round(worst_hi, 4), max_under_inputs=round(worst_lo, 4), tol=tol)


def gates(coef, pm, pk, d=None, mk=None):
    """Every gate a fitted blend must pass before it can go live (and then only when the market is in APPROVED)."""
    g = dict(monotone=dict(ok=monotone(coef)), between=between_inputs(coef, pm, pk))
    if mk in GAME_SUM_MARKETS and d is not None and "game_id" in d:
        g["game_sum"] = game_sums(coef, pm, pk, d["game_id"].values)
    g["ok"] = all(v["ok"] for k, v in g.items() if isinstance(v, dict))
    return g


def monotone(coef):
    """A blend is usable only if it rises with both inputs (b, c >= 0). A fit on two-sided main lines alone (market P ~ 0.5 everywhere)
       can return a negative market coefficient that passes the leave-one-week-out gate yet inverts P(over) across a ladder:
       the 2026-10-17 week-6 yds fit [0.109, 0.333, -2.70] failed the yard-curve monotonicity gate on every CI run."""
    return coef is None or (coef[1] >= 0 and coef[2] >= 0)


def save(models, fn=FILE):
    json.dump(models, open(fn, "w"), indent=1)


def load(fn=FILE):
    return json.load(open(fn)) if os.path.exists(fn) else {}


def coef_for(models, mk):
    """Fitted coefficients only when the market is in APPROVED (CEO sign-off), the fit has enough rows, beat 50/50 out of sample
       (leave-one-week-out), rises with both inputs (monotone, never waived) and passed every other gate (between the inputs, first-TD
       game sums) unless the market is in GATE_WAIVED (2026-10-31: first TD); otherwise None, which blend() treats as 50/50.
       An approved market's record is frozen as stored (freeze_approved): the weekly refit never moves the live coefficients."""
    m = (models or {}).get(mk)
    if not m or m.get("n", 0) < MIN_ROWS or not m.get("loo") or mk not in APPROVED:
        return None                                      # not approved -> 50/50, whatever the fit says
    ok = m["loo"]["fitted"] < m["loo"]["half"] and monotone(m["coef"]) and ((m.get("gates") or {}).get("ok", False) or mk in GATE_WAIVED)
    return m["coef"] if ok else None
