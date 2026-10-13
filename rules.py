"""The Edge Board rule for TD singles, shared by run_week.py (live board) and the tests.

An edge needs EV >= MIN_EV at the median book and at the best book, MIN_BOOKS or more books, and no weak-spot tag.
Every field is read NaN-safely (nansafe): a missing or NaN column never drops or admits a row by accident.
"""
import pandas as pd
from nansafe import val, flag, text, num, isnan

MIN_BOOKS, MIN_EV = 2, 0.05
MARKETS = [("any_", "Anytime TD", "p_any"), ("first_", "First TD", "p_first"), ("two_", "2+ TD", "p_2plus")]

# Board rules in force from BOARD_RULES_WEEK (2026 week 6; weeks 3-5 were played at 1u for every role with no ratio block,
# and their frozen record is never rewritten):
#   - Bench plays stay on the board in their own "Bench / longshot" section at BENCH_STAKE units; the headline record is
#     Starter + Rotational singles only.
#   - A Rotational player whose model probability is more than MAX_RATIO_ROT times the no-vig market probability is
#     blocked (logged to research/ratio_block_<season>_w<week>.json). Bench plays that trip it simply sit in the bench
#     section. Starters are not blocked.
#   - Passing-yard rungs (main line and alt) are hidden from the board and the record and logged to
#     research/pass_yds_<season>_w<week>.json until the passing model beats the books on Brier over 3+ graded weeks.
BOARD_RULES_WEEK, BENCH_STAKE, MAX_RATIO_ROT, HIDDEN_KINDS = 6, 0.25, 2.5, ("pass",)


def stake_for(role):
    return BENCH_STAKE if role == "Bench" else 1.0


def ratio_of(model_p, mkt_p):
    try:
        return round(float(model_p) / float(mkt_p), 2) if model_p is not None and mkt_p is not None and float(mkt_p) > 0 else None
    except (TypeError, ValueError):
        return None


def ratio_blocked(role, model_p, mkt_p):
    r = ratio_of(model_p, mkt_p)
    return role == "Rotational" and r is not None and r > MAX_RATIO_ROT


def split_board(edges):
    """Edge dicts -> dict(main, bench, blocked, hidden). Every edge gets stake and ratio. hidden = passing-yard rungs;
       blocked = Rotational players over MAX_RATIO_ROT; bench = role Bench; main = everything else (the headline board)."""
    out = dict(main=[], bench=[], blocked=[], hidden=[])
    for e in edges:
        role = e.get("role") if isinstance(e.get("role"), str) else "Unknown"
        e["stake"] = stake_for(role); e["ratio"] = ratio_of(e.get("model_p"), e.get("mkt_p"))
        if e.get("kind") in HIDDEN_KINDS:
            out["hidden"].append(dict(e, reasons=["pass-yds hidden"]))
        elif ratio_blocked(role, e.get("model_p"), e.get("mkt_p")):
            out["blocked"].append(dict(e, reasons=[f"rotational > {MAX_RATIO_ROT}x market"]))
        elif role == "Bench":
            out["bench"].append(e)
        else:
            out["main"].append(e)
    return out


def td_edges(df, min_books=MIN_BOOKS, min_ev=MIN_EV):
    """Rows of df (one per player, priced columns <prefix>best/med/ev/ev_med/nbooks/...) -> list of edge dicts."""
    edges = []
    for _, r in df.iterrows():
        if text(r, "weak_spot"):
            continue
        for m, lbl, pcol in MARKETS:
            ev, ev_med = val(r, m + "ev"), val(r, m + "ev_med")
            if ev is None or ev_med is None or isnan(val(r, m + "best")) or isnan(val(r, m + "med")):
                continue
            if ev_med >= min_ev and ev >= min_ev and num(r, m + "nbooks", 0) >= min_books:
                kal = val(r, m + "kalshi")
                edges.append(dict(bet=f"{r['name']} {lbl}", pid=r["pid"], market=m[:-1], team=r["team"], model_p=val(r, pcol), mkt_p=val(r, m + "mkt_p"),
                                  blend_p=val(r, m + "blend_p"), best=int(r[m + "best"]), book=val(r, m + "book"), ev=ev, med=int(r[m + "med"]), ev_med=ev_med,
                                  nbooks=int(num(r, m + "nbooks", 0)), games=int(num(r, "games", 0)), w=val(r, m + "w", 0.5), role=val(r, "role", "Unknown"),
                                  kalshi=None if kal is None else int(kal), kalshi_liquid=flag(r, m + "kalshi_liquid")))
    return edges


def qualifying_rows(df, min_books=MIN_BOOKS, min_ev=MIN_EV):
    """Independent count of (row, market) pairs that satisfy the rule, for tests: computed with plain pandas masks."""
    n = 0
    for m, _, _ in MARKETS:
        if m + "ev" not in df or m + "ev_med" not in df:
            continue
        ok = df[m + "ev"].notna() & df[m + "ev_med"].notna() & (df[m + "ev"] >= min_ev) & (df[m + "ev_med"] >= min_ev)
        ok &= df[m + "nbooks"].fillna(0) >= min_books if m + "nbooks" in df else False
        ok &= df[m + "best"].notna() & df[m + "med"].notna() if m + "best" in df and m + "med" in df else False
        if "weak_spot" in df:
            ok &= ~df["weak_spot"].apply(lambda v: isinstance(v, str) and bool(v.strip()))
        n += int(ok.sum())
    return n
