"""The Edge Board rule for TD singles, shared by run_week.py (live board) and the tests.

An edge needs EV >= MIN_EV at the median book and at the best book, MIN_BOOKS or more books, and no weak-spot tag.
Every field is read NaN-safely (nansafe): a missing or NaN column never drops or admits a row by accident.
"""
import os
import pandas as pd
from nansafe import val, flag, text, num, isnan

MIN_BOOKS, MIN_EV = 2, 0.05
MARKETS = [("any_", "Anytime TD", "p_any"), ("first_", "First TD", "p_first"), ("two_", "2+ TD", "p_2plus")]
# CEO ruling 2026-10-31: 2+ TD produces NO actionable edges (not on the card, not in Discord, not in the decision-pull record, not graded,
# never logged): the EV-flag version surfaced longshot backups (104 flags W4-W7, 1 cashed, 0-2 in the record) and never matched the
# original ask, a likelihood list of low-odds 2-TD scorers for 2-3 player parlays. Player rows keep p_2plus and the 2+ pricing fields
# (the 2+ TD tab and the parlay builder read them); only the edge creation is gated here, at the source. Frozen weeks are never rewritten.
DEAD_MARKETS = ("two",)

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
# Consolidated build (CEO rulings 2026-10-29, live from week 8): alt rungs are CREATED at ALT_STAKE for every role; a first-TD edge priced
# LONGSHOT_FIRST_PRICE or longer whose model P is more than LONGSHOT_FIRST_RATIO x the no-vig market routes to the Bench / longshot section at
# BENCH_STAKE whatever the role (group "bench", role unchanged); the actionable card is capped at CARD_CAP bets per kickoff window, ranked by EV
# at the median book, with one alt rung per player-stat per window (apply_card_cap); the rest display greyed "over card cap", are logged as
# research and never enter the primary record. The flag rule itself (MIN_EV at the median and best book, MIN_BOOKS) is unchanged.
ALT_STAKE, LONGSHOT_FIRST_PRICE, LONGSHOT_FIRST_RATIO, CARD_CAP = 0.25, 2000, 2.0, 25
# Card cap quota (CEO ruling 2026-10-30, live from the week 8 Sunday decision pull): per kickoff window, main-line yard edges (market "yds", the
# Results by_market bucket; alt rungs do not count) get first claim on up to CARD_YARD_FLOOR of the CARD_CAP slots, ranked by EV at the median
# book; everything else fills the rest by EV at the median; unused floor slots flow back to the pool. A floor for yards, not a cap on TDs:
# ranking only, nothing about which edges are flagged, their EV or their stake changes.
CARD_YARD_FLOOR = 10
DECISION_PT = (9, 23)      # ruling 7: Sunday early / late games are graded at the first pull at or after 9:23 AM Pacific; other games at the last pre-cutoff pull
# Timing rules (2026-10-15): an edge is actionable only while its game is more than KICKOFF_CUTOFF_MIN minutes from kickoff
# and, once MAX_PRICE_AGE_MIN is set, only while the price it was flagged at is younger than that. Edges that fail are held
# off the board (shown with the reason, never logged, never graded). MAX_PRICE_AGE_MIN = None shows the price age on every
# row without enforcing a limit; the user sets the number.
KICKOFF_CUTOFF_MIN, MAX_PRICE_AGE_MIN = 30, 15
# Staleness (2026-10-18): the price-age limit applies only inside kickoff loops (pull_loop sets IN_LOOP=1; pulls every 10 min). Outside
# them (scheduled and early-week pulls) an edge stays on the board until the next pull, labelled with its price age, and the viewer
# bets only at or better than BET DOWN TO. The kickoff cutoff applies everywhere.
BET_DOWN_TO_RULE = "bet only if your book's price is at or better than BET DOWN TO"
# Listing rule (2026-10-15): an alternate yard rung is actionable only when the named book lists that exact rung at that
# price on the verified board (yard_prices.verify_ladders); curve values never stand in for a price.
LISTED_RULE = "actionable alt rungs are listed at the named book at the named price; unlisted or unverified rungs are research-only"


def stake_for(role, market=None):
    """Stake by role and market: Bench plays BENCH_STAKE, alt rungs ALT_STAKE for every role (ruling 5), else 1u."""
    return BENCH_STAKE if role == "Bench" else ALT_STAKE if market == "yds_alt" else 1.0


def first_td_longshot(e):
    """Ruling 6: a first-TD edge priced LONGSHOT_FIRST_PRICE or longer with model P more than LONGSHOT_FIRST_RATIO x the market P."""
    try:
        return (e.get("market") == "first" and e.get("best") is not None and int(e["best"]) >= LONGSHOT_FIRST_PRICE
                and e.get("model_p") is not None and e.get("mkt_p") is not None and float(e["mkt_p"]) > 0 and float(e["model_p"]) > LONGSHOT_FIRST_RATIO * float(e["mkt_p"]))
    except (TypeError, ValueError):
        return False


def group_of(e):
    """Board section: "bench" for Bench players and routed first-TD longshots, else "main"."""
    role = e.get("role") if isinstance(e.get("role"), str) else "Unknown"
    return "bench" if role == "Bench" or first_td_longshot(e) else "main"


def stake_of(e):
    """The stake an edge is created at: BENCH_STAKE in the bench section (Bench role or a routed first-TD longshot), ALT_STAKE for alt rungs, else 1u."""
    role = e.get("role") if isinstance(e.get("role"), str) else "Unknown"
    return BENCH_STAKE if group_of(e) == "bench" else stake_for(role, e.get("market"))


def bet_down_to(blend_p, min_ev=MIN_EV):
    """Worst American price at which EV at the model-market blend is still >= min_ev: decimal 1 + min_ev over blend_p, rounded to the
       nearest American price that keeps EV >= min_ev (never rounded past the threshold). None without a usable probability."""
    try:
        p = float(blend_p)
    except (TypeError, ValueError):
        return None
    if not (0 < p < 1): return None
    dec = (1 + min_ev) / p
    am = int(round((dec - 1) * 100)) if dec >= 2 else int(round(-100 / (dec - 1)))
    # rounding to a whole American price can land a hair short of the threshold: step one tick toward the bettor until EV >= min_ev
    def ev(a): return p * (1 + (a / 100 if a > 0 else 100 / -a)) - 1
    for _ in range(3):
        if ev(am) >= min_ev - 1e-9: break
        am = am + 1 if am > 0 else am + 1 if am < -100 else -100            # +100 and -100 are the same price; move toward longer odds
        if am == -99 or am == 0: am = 100
    return am


def price_age_limit():
    """MAX_PRICE_AGE_MIN inside a kickoff loop (IN_LOOP=1), no limit otherwise (price age is shown instead)."""
    return MAX_PRICE_AGE_MIN if os.environ.get("IN_LOOP") else None


def ratio_of(model_p, mkt_p):
    try:
        return round(float(model_p) / float(mkt_p), 2) if model_p is not None and mkt_p is not None and float(mkt_p) > 0 else None
    except (TypeError, ValueError):
        return None


def ratio_blocked(role, model_p, mkt_p):
    r = ratio_of(model_p, mkt_p)
    return role == "Rotational" and r is not None and r > MAX_RATIO_ROT


def minutes_to_kick(kick_utc, now):
    """Minutes from `now` (tz-aware pandas Timestamp) to an ISO kickoff like '2026-10-16T00:15Z'; None when unknown."""
    if not kick_utc: return None
    try:
        return float((pd.to_datetime(kick_utc, utc=True) - now) / pd.Timedelta(minutes=1))
    except (TypeError, ValueError):
        return None


def price_age_min(captured, now):
    """Minutes between a quote's capture timestamp (ISO, from the feed) and `now`; None when the feed gave no timestamp."""
    if not captured: return None
    try:
        return float((now - pd.to_datetime(captured, utc=True)) / pd.Timedelta(minutes=1))
    except (TypeError, ValueError):
        return None


_DEFAULT = object()


def not_actionable(e, now, kickoff_cutoff=None, max_age=_DEFAULT):
    """Reasons an edge cannot be acted on at `now`: kickoff within the cutoff (or underway), or a price older than the age limit.
       [] when it is actionable. Sets e['kick_min'] and e['age_min'] as a side effect so the page can show them.
       max_age: omitted -> MAX_PRICE_AGE_MIN; None -> no age limit (outside kickoff loops, see price_age_limit)."""
    cut = KICKOFF_CUTOFF_MIN if kickoff_cutoff is None else kickoff_cutoff
    lim = MAX_PRICE_AGE_MIN if max_age is _DEFAULT else max_age
    why = []
    km = minutes_to_kick(e.get("kick_utc"), now); e["kick_min"] = None if km is None else round(km, 1)
    if km is None: why.append("kickoff unknown")
    elif km <= 0: why.append("game underway")
    elif km < cut: why.append(f"kickoff in {km:.0f} min (under {cut})")
    age = price_age_min(e.get("captured"), now); e["age_min"] = None if age is None else round(age, 1)
    if lim is not None:
        if age is None: why.append("price age unknown")
        elif age > lim: why.append(f"price captured {age:.0f} min ago (over {lim})")
    return why


def split_board(edges):
    """Edge dicts -> dict(main, bench, blocked, hidden). Every edge gets stake and ratio. hidden = passing-yard rungs;
       blocked = Rotational players over MAX_RATIO_ROT; bench = role Bench; main = everything else (the headline board)."""
    out = dict(main=[], bench=[], blocked=[], hidden=[])
    for e in edges:
        role = e.get("role") if isinstance(e.get("role"), str) else "Unknown"
        e["group"] = group_of(e); e["routed"] = "first-TD longshot" if e["group"] == "bench" and role != "Bench" else None
        e["stake"] = stake_of(e); e["ratio"] = ratio_of(e.get("model_p"), e.get("mkt_p")); e["bet_down_to"] = bet_down_to(e.get("blend_p"))
        if e.get("kind") in HIDDEN_KINDS:
            out["hidden"].append(dict(e, reasons=["pass-yds hidden"]))
        elif ratio_blocked(role, e.get("model_p"), e.get("mkt_p")):
            out["blocked"].append(dict(e, reasons=[f"rotational > {MAX_RATIO_ROT}x market"]))
        elif e["group"] == "bench":
            out["bench"].append(e)
        else:
            out["main"].append(e)
    return out


def window_key(e, clusters=None):
    """The kickoff window an edge belongs to: the pull_windows.json cluster whose [start, end] holds its kickoff, else the kickoff itself."""
    k = e.get("kick_utc")
    if not k: return "unknown"
    try:
        kt = pd.to_datetime(k, utc=True)
        for c in clusters or []:
            if c.get("start") and c.get("end") and pd.to_datetime(c["start"], utc=True) <= kt <= pd.to_datetime(c["end"], utc=True):
                return f"{c['start']}..{c['end']}"
    except (TypeError, ValueError):
        pass
    return k


def apply_card_cap(edges, clusters=None, cap=CARD_CAP, yard_floor=CARD_YARD_FLOOR):
    """Rulings 10-11 and the card-cap quota (2026-10-30): actionable edges -> (card, dropped). Per kickoff window:
       1. one alt rung per player-stat (the highest EV at the median book; ties by EV at the best book, then the longer line);
       2. main-line yard edges (market "yds") get first claim on up to `yard_floor` of the `cap` slots, highest EV at the median first;
       3. every remaining edge (TD markets, alt rungs, the yard mains past the floor) fills the remaining slots by EV at the median;
          floor slots a window cannot use flow back here, so the card reaches `cap` whenever enough edges exist.
       Card rows carry cap_rank (EV-at-median order within the card), window and quota ("yard_floor" when the row is on the card only
       because of the floor); dropped rows carry cap ("alt" | "card"), a rank after the card and reasons (research only, greyed on the board,
       never in the primary record). A pure partition of the input: no flag, price, EV or stake of any edge changes."""
    card, dropped, by_w = [], [], {}
    key = lambda e: (-(e.get("ev_med") or 0), -(e.get("ev") or 0), -(e.get("line") or 0), e.get("bet") or "")
    for e in edges:
        by_w.setdefault(window_key(e, clusters), []).append(e)
    for w, rows in by_w.items():
        rows = sorted(rows, key=key)
        seen, kept = {}, []
        for e in rows:
            e["window"] = w
            if e.get("market") == "yds_alt":
                k = (e.get("pid"), e.get("kind"))
                if k in seen:
                    dropped.append(dict(e, cap="alt", cap_rank=None, quota=None, reasons=[f"alt-rung cap: one rung per player-stat per window (Over {seen[k]:g} kept)"])); continue
                seen[k] = float(e.get("line") or 0)
            kept.append(e)
        ev_rank = {id(e): i for i, e in enumerate(kept, 1)}                                   # rank by EV alone (what the card was before the quota)
        floor = [e for e in kept if e.get("market") == "yds"][:max(min(yard_floor, cap), 0)]  # main-line yard edges: first claim on up to yard_floor slots
        fid = {id(e) for e in floor}
        pool = [e for e in kept if id(e) not in fid]
        n_pool = max(cap - len(floor), 0)                                                      # unused floor slots flow back to the pool
        on = sorted(floor + pool[:n_pool], key=key)
        for i, e in enumerate(on, 1):
            e["cap_rank"], e["quota"] = i, ("yard_floor" if id(e) in fid and ev_rank[id(e)] > cap else None)
            card.append(e)
        for j, e in enumerate(pool[n_pool:], len(on) + 1):
            e["cap_rank"], e["quota"] = j, None
            dropped.append(dict(e, cap="card", reasons=[f"over card cap (rank {j} of {len(kept)}, cap {cap}" + (f"; {len(floor)} slot(s) held by main-line yard edges under the yard floor of {yard_floor}" if floor else "") + ")"]))
    return card, dropped


def decision_rule(kick_utc):
    """Ruling 7. ('sunday_0923', <UTC ISO of 9:23 AM Pacific that day>) for a Sunday early / late game (kickoff 12:00-17:59 Eastern on a Sunday);
       ('final', None) for everything else (TNF, SNF, MNF, London, Saturday): the last pull before the kickoff cutoff. DST-aware through the zones."""
    if not kick_utc: return ("final", None)
    try:
        et = pd.to_datetime(kick_utc, utc=True).tz_convert("America/New_York")
    except (TypeError, ValueError):
        return ("final", None)
    if et.weekday() == 6 and 12 <= et.hour < 18:
        pt = et.tz_convert("America/Los_Angeles").replace(hour=DECISION_PT[0], minute=DECISION_PT[1], second=0, microsecond=0)
        return ("sunday_0923", pt.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ"))
    return ("final", None)


def td_edges(df, min_books=MIN_BOOKS, min_ev=MIN_EV):
    """Rows of df (one per player, priced columns <prefix>best/med/ev/ev_med/nbooks/...) -> list of edge dicts."""
    edges = []
    for _, r in df.iterrows():
        if text(r, "weak_spot"):
            continue
        for m, lbl, pcol in MARKETS:
            if m[:-1] in DEAD_MARKETS: continue                     # 2+ TD: no edge is ever created (ruling 2026-10-31)
            ev, ev_med = val(r, m + "ev"), val(r, m + "ev_med")
            if ev is None or ev_med is None or isnan(val(r, m + "best")) or isnan(val(r, m + "med")):
                continue
            if ev_med >= min_ev and ev >= min_ev and num(r, m + "nbooks", 0) >= min_books:
                kal = val(r, m + "kalshi")
                edges.append(dict(bet=f"{r['name']} {lbl}", pid=r["pid"], market=m[:-1], team=r["team"], model_p=val(r, pcol), mkt_p=val(r, m + "mkt_p"),
                                  blend_p=val(r, m + "blend_p"), best=int(r[m + "best"]), book=val(r, m + "book"), ev=ev, med=int(r[m + "med"]), ev_med=ev_med,
                                  nbooks=int(num(r, m + "nbooks", 0)), games=int(num(r, "games", 0)), w=val(r, m + "w", 0.5), role=val(r, "role", "Unknown"),
                                  kalshi=None if kal is None else int(kal), kalshi_liquid=flag(r, m + "kalshi_liquid"), captured=val(r, m + "captured"),
                                  stake=stake_for(val(r, "role", "Unknown"), m[:-1])))      # created at its stake (split_board re-derives it with the routing rule)
    return edges


def qualifying_rows(df, min_books=MIN_BOOKS, min_ev=MIN_EV):
    """Independent count of (row, market) pairs that satisfy the rule, for tests: computed with plain pandas masks."""
    n = 0
    for m, _, _ in MARKETS:
        if m[:-1] in DEAD_MARKETS or m + "ev" not in df or m + "ev_med" not in df:
            continue
        ok = df[m + "ev"].notna() & df[m + "ev_med"].notna() & (df[m + "ev"] >= min_ev) & (df[m + "ev_med"] >= min_ev)
        ok &= df[m + "nbooks"].fillna(0) >= min_books if m + "nbooks" in df else False
        ok &= df[m + "best"].notna() & df[m + "med"].notna() if m + "best" in df and m + "med" in df else False
        if "weak_spot" in df:
            ok &= ~df["weak_spot"].apply(lambda v: isinstance(v, str) and bool(v.strip()))
        n += int(ok.sum())
    return n
