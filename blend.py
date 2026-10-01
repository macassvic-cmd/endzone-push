"""Fitted model+market blend: P(hit) = sigmoid(a + b*logit(model) + c*logit(no-vig market)), one fit per market.

results.py refits on every graded, priced row each run (anytime and first TD separately; 2+ TD once it has rows)
and writes blend_model.json; run_week.py reads it for every EV on the Edge Board and in the parlay builder. Below
MIN_ROWS rows, or without a file, the blend falls back to 50/50 in probability space. Fitting is plain numpy IRLS
with a light ridge so the weekly CI run needs no extra dependency. Leave-one-week-out Brier guards against overfit.
"""
import json, os, numpy as np

FILE, MIN_ROWS, RIDGE = "blend_model.json", 300, 1e-3
MARKETS = {"any": ("p_any", "any_mkt_p", "hit"), "first": ("p_first", "first_mkt_p", "first_hit"), "two": ("p_2plus", "two_mkt_p", "two_hit")}


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


def predict(coef, pm, pk):
    if coef is None:
        return 0.5 * np.asarray(pm, float) + 0.5 * np.asarray(pk, float)
    a, b, c = coef
    return sigmoid(a + b * logit(pm) + c * logit(pk))


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
               brier=dict(model=round(brier(pm, y), 5), market=round(brier(pk, y), 5), half=round(brier(0.5 * pm + 0.5 * pk, y), 5),
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
        oos_half += list((0.5 * te[pcol].values + 0.5 * te[kcol].values - te[ycol].astype(float).values) ** 2)
        oos_mkt += list((te[kcol].values - te[ycol].astype(float).values) ** 2); oos_mod += list((te[pcol].values - te[ycol].astype(float).values) ** 2)
    if oos:
        rec["loo"] = dict(fitted=round(float(np.mean(oos)), 5), half=round(float(np.mean(oos_half)), 5),
                          market=round(float(np.mean(oos_mkt)), 5), model=round(float(np.mean(oos_mod)), 5), n=len(oos))
    rec["in_use"] = bool(rec.get("loo") and rec["loo"]["fitted"] < rec["loo"]["half"])
    return rec


def save(models, fn=FILE):
    json.dump(models, open(fn, "w"), indent=1)


def load(fn=FILE):
    return json.load(open(fn)) if os.path.exists(fn) else {}


def coef_for(models, mk):
    """Fitted coefficients only when the fit has enough rows AND beat 50/50 out of sample (leave-one-week-out);
       otherwise None, which predict() treats as the 50/50 blend. Refit and re-gated every run."""
    m = (models or {}).get(mk)
    if not m or m.get("n", 0) < MIN_ROWS or not m.get("loo"):
        return None
    return m["coef"] if m["loo"]["fitted"] < m["loo"]["half"] else None
