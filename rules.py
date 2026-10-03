"""The Edge Board rule for TD singles, shared by run_week.py (live board) and the tests.

An edge needs EV >= MIN_EV at the median book and at the best book, MIN_BOOKS or more books, and no weak-spot tag.
Every field is read NaN-safely (nansafe): a missing or NaN column never drops or admits a row by accident.
"""
import pandas as pd
from nansafe import val, flag, text, num, isnan

MIN_BOOKS, MIN_EV = 2, 0.05
MARKETS = [("any_", "Anytime TD", "p_any"), ("first_", "First TD", "p_first"), ("two_", "2+ TD", "p_2plus")]


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
