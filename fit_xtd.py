"""Fit the expected-TD (xTD) models used by model.add_xtd and save their coefficients to xtd_model.json.

    python fit_xtd.py            # trains on 2022-2024 regular season, reports 2025 out-of-sample calibration, writes xtd_model.json

Rush xTD: logistic regression on yardline_100 (plus log and goal-line indicators), down, ydstogo, goal_to_go,
shotgun and the QB-carry flag. Target xTD: same idea on yardline_100, air_yards, end-zone-target flag, pass_location,
down, ydstogo, goal_to_go. Coefficients are stored so the weekly run only needs numpy (model.fitted_xtd).
A HistGradientBoosting model is fitted alongside for comparison only.
Needs data/pbp_2022.parquet (nflreadpy load_pbp([2022])) next to the usual data/pbp.parquet (2023-).
"""
import json, numpy as np, pandas as pd, model as M
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import log_loss

TRAIN, TEST = [2022, 2023, 2024], 2025


def load_all():
    p = pd.concat([pd.read_parquet("data/pbp_2022.parquet"), pd.read_parquet("data/pbp.parquet")], ignore_index=True)
    p = p[p.season_type == "REG"]
    return M.tag_qb_rush(p)


def rush_frame(p):
    d = p[p.is_rush & p.yardline_100.notna() & p.rusher_player_id.notna()].copy()
    return d, M.rush_features(d), d.rush_touchdown.astype(int).values


def rec_frame(p):
    d = p[(p.pass_attempt == 1) & (p.sack == 0) & p.receiver_player_id.notna() & p.yardline_100.notna()].copy()
    return d, M.rec_features(d), d.pass_touchdown.astype(int).values


def calib(pred, y, edges=(0, .02, .05, .1, .2, .3, .5, .7, 1.01)):
    b = pd.cut(pred, edges, right=False)
    t = pd.DataFrame(dict(p=pred, y=y, b=b)).groupby("b", observed=True).agg(n=("y", "size"), pred=("p", "mean"), act=("y", "mean"))
    return t.round(4)


def main():
    p = load_all()
    tr, te = p[p.season.isin(TRAIN)], p[p.season == TEST]
    out = {"train_seasons": TRAIN, "test_season": TEST}
    ru_b, tg_b, _ = M.xtd_tables(tr)                       # old bucket tables from the same training data, for comparison
    for name, fn, old in [("rush", rush_frame, "rush"), ("rec", rec_frame, "rec")]:
        dtr, Xtr, ytr = fn(tr); dte, Xte, yte = fn(te)
        lr = LogisticRegression(C=10.0, max_iter=2000).fit(Xtr, ytr)
        hgb = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=200).fit(Xtr, ytr)
        p_lr, p_hgb = lr.predict_proba(Xte)[:, 1], hgb.predict_proba(Xte)[:, 1]
        oldp = M.add_xtd(dte, ru_b, tg_b, None)[f"{old}_xtd"].values
        print(f"\n== {name}: train {len(ytr)} plays, test {len(yte)} (2025) | base rate {yte.mean():.4f}")
        print(f"   log-loss  old tables {log_loss(yte, np.clip(oldp, 1e-6, 1-1e-6)):.5f} | logistic {log_loss(yte, p_lr):.5f} | HGB {log_loss(yte, p_hgb):.5f}")
        print(f"   mean pred old {oldp.mean():.4f} | logistic {p_lr.mean():.4f} | HGB {p_hgb.mean():.4f}")
        print("   logistic calibration on 2025:"); print(calib(p_lr, yte).to_string())
        print("   old-table calibration on 2025:"); print(calib(oldp, yte).to_string())
        out[name] = dict(features=list(Xtr.columns), coef=[round(float(c), 6) for c in lr.coef_[0]], intercept=round(float(lr.intercept_[0]), 6),
                         logloss_2025=dict(old=round(log_loss(yte, np.clip(oldp, 1e-6, 1-1e-6)), 5), logistic=round(log_loss(yte, p_lr), 5), hgb=round(log_loss(yte, p_hgb), 5)))
    json.dump(out, open("xtd_model.json", "w"), indent=1)
    print("\nwrote xtd_model.json")


if __name__ == "__main__":
    main()
