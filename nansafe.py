"""NaN-safe field access for rows that come from DataFrames or JSON.

pandas fills a column that is missing from one of the frames in a concat with NaN, and NaN is truthy in Python, so
`if r.get("weak_spot")` or `r.get("nbooks") or 0` quietly misbehaves (the first skipped every graded TD row twice).
Use these instead of raw .get() / truthiness on any field that can be NaN or None:

    val(row, "games", 0)        -> the value, or the default when the field is missing, None or NaN
    flag(row, "weak_spot")      -> False for missing / None / NaN / "" ; bool(value) otherwise
    text(row, "weak_spot")      -> the string, or None when missing / None / NaN / ""
    num(row, "nbooks", 0)       -> float(value), or the default when missing / None / NaN / not a number
    isnan(x)                    -> True for None, float NaN, numpy NaN, pd.NA, pd.NaT
Works on dicts, pandas Series rows (iterrows) and namedtuples (itertuples).
"""
import pandas as pd


def isnan(x):
    if x is None:
        return True
    if isinstance(x, (str, bytes, bool, int, list, dict, tuple, set)):
        return False
    try:
        return bool(pd.isna(x))
    except (TypeError, ValueError):          # arrays and other non-scalars are values, not missing
        return False


def val(row, key, default=None):
    if row is None:
        return default
    try:
        v = row.get(key, default) if hasattr(row, "get") else getattr(row, key, default)
    except Exception:
        v = default
    return default if isnan(v) else v


def flag(row, key):
    v = val(row, key)
    if v is None:
        return False
    return bool(v.strip()) if isinstance(v, str) else bool(v)


def text(row, key):
    v = val(row, key)
    return v if isinstance(v, str) and v.strip() else None


def num(row, key, default=0.0):
    v = val(row, key, default)
    try:
        return float(v)
    except (TypeError, ValueError):
        return default
