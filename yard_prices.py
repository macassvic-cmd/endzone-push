"""Price yard ladders from the pulled events (SharpAPI DK/FD main + alternate lines, Odds API if present).

Markets: player_rush_yds / player_reception_yds / player_pass_yds, with *_alternate rows for the ladders. For each
(player, kind, line): per book, the Over price; no-vig both sides when the book quotes Over and Under at that line,
otherwise the Over divided by that book's main-line overround (its own two-sided vig). Returns the same summary shape
as odds.price_summary (best, book, median, n_books, market_p) so the Edge Board, CLV and grading reuse one path.
"""
import numpy as np
from odds import norm_name, implied, decimal, american

KIND_MARKET = {"rush": "player_rush_yds", "rec": "player_reception_yds", "pass": "player_pass_yds"}
# YARDS_SOURCE=oddsapi: the books whose verified ladders price the board and feed the pooled curve. A fixed set of regulated US books
# with their own pricing; Kambi clones (betPARX, theScore Bet, Bally Bet), the Hard Rock FL mirror, offshore and sweepstakes books
# (BetOnline, Bovada, LowVig, MyBookie, BetUS, Fliff, ReBet, Courtside) are left out so one feed never counts twice in the pool.
YARD_BOOKS = ("DraftKings", "FanDuel", "BetMGM", "Caesars", "Hard Rock Bet", "BetRivers", "Fanatics", "ESPN BET")


def only_books(events, titles):
    """Copy of `events` keeping only the prop bookmakers whose title is in `titles` (research reruns on one book's ladders,
       e.g. --books FanDuel after the DraftKings phantom-ladder finding). Game lines are untouched. None/empty -> unchanged."""
    if not titles: return events
    want = {t.lower() for t in titles}
    out = []
    for e in events:
        e2 = dict(e)
        if "props" in e2:
            e2["props"] = dict(e2["props"], bookmakers=[b for b in e2["props"].get("bookmakers", []) if (b.get("title") or b.get("key") or "").lower() in want])
        out.append(e2)
    return out


def ladder_board(events, rejected=None, source=None, books=None):
    """{(kind, norm_name, line): {book: {"over": price, "under": price, "main": bool, "ts": capture time}}}, plus each book's
       main-line hold. Only verified ladders survive (verify_ladders); `rejected`, when a dict is passed, receives the
       one-sided ladders that failed verification, keyed (kind, norm_name, book), for the research view.
       `source` ('sharpapi' | 'oddsapi', default YARDS_SOURCE) picks which feed's yardage rows are read: a saved pull can carry
       both (SharpAPI rows carry source="sharpapi"; Odds API rows "oddsapi" or none). `books` (default: YARD_BOOKS when the
       source is oddsapi, every book for sharpapi) restricts the ladders to that set; `books="all"` keeps every book."""
    import os
    source = source or ("oddsapi" if os.environ.get("YARDS_SOURCE", "sharpapi").strip().lower() == "oddsapi" else "sharpapi")
    if books is None: books = YARD_BOOKS if source == "oddsapi" else "all"
    want = None if books == "all" else {b.lower() for b in books}
    board, hold = {}, {}
    for ev in events or []:
        for b in ev.get("props", {}).get("bookmakers", []):
            if want is not None and (b.get("title") or "").lower() not in want: continue
            for m in b["markets"]:
                base = m["key"].replace("_alternate", "")
                kind = next((k for k, v in KIND_MARKET.items() if v == base), None)
                if not kind: continue
                for o in m["outcomes"]:
                    if o.get("point") is None or o.get("reference"): continue
                    if o.get("source") and o["source"] != source: continue      # the other feed's yardage rows stay in the file (research); untagged rows count for either
                    key = (kind, norm_name(o.get("description") or ""), float(o["point"]))
                    cell = board.setdefault(key, {}).setdefault(b["title"], {})
                    cell["over" if o["name"] == "Over" else "under"] = o["price"]
                    if o.get("main") or m["key"] == base: cell["main"] = True
                    if o.get("ts"): cell["ts"] = max(cell.get("ts") or "", str(o["ts"]))
    for key, books in board.items():
        for bk, c in books.items():
            if "over" in c and "under" in c and c.get("main"):
                hold.setdefault((key[0], bk), []).append(implied(c["over"]) + implied(c["under"]))
    hold = {k: float(np.median(v)) for k, v in hold.items()}
    verify_ladders(board, rejected)
    return board, hold


MAIN_TOL, STEP_TOL = 0.03, 0.02      # a one-sided rung may sit this far (no-vig P) on the wrong side of the book's two-sided line / of the rung below it
LADDER_RULE = ("a book's alternate rungs count as listed only when the whole ladder is consistent with that book's own two-sided line "
               "(every lower rung at least as likely, every higher rung at most as likely, within 3 pts) and never rises with the line; "
               "otherwise the ladder is unverified and research-only")


def verify_ladders(board, rejected=None):
    """Keep a book's one-sided alternate rungs for a player only when the whole ladder can be verified against that book's
       own two-sided line in the same pull: every rung below it at least as likely (no-vig, within MAIN_TOL), every rung
       above it at most as likely, and P(over) never rising with the line (STEP_TOL). One violation rejects every one-sided
       rung of that book for that player: the feed has carried whole ladders under the wrong market (SharpAPI's DraftKings
       'alternate' yardage rows in 2026 weeks 5-6 were a different ladder entirely: 'Emanuel Wilson Over 54.5 rush +2000'
       next to his two-sided 66.5 at -110), so the surviving rows of a broken ladder are not evidence that the book lists
       them. A ladder with no two-sided line at the book cannot be verified and is rejected too. Two-sided lines always
       stay. Rejected ladders go to `rejected[(kind, player, book)] = {reason, rungs}` when a dict is given."""
    by = {}
    for (kind, player, line), books in board.items():
        for bk, c in books.items():
            if "over" in c: by.setdefault((kind, player, bk), []).append((line, c))
    n_lad = n_bad = 0
    for (kind, player, bk), rungs in by.items():
        rungs.sort(key=lambda x: x[0])
        anchors = [(l, c) for l, c in rungs if "under" in c]
        alts = [(l, c) for l, c in rungs if "under" not in c]
        if not alts: continue
        n_lad += 1
        why = ladder_fault(anchors, alts)
        if why is None: continue
        n_bad += 1
        for l, c in alts:
            board[(kind, player, l)].pop(bk, None)
            if rejected is not None:
                rejected.setdefault((kind, player, bk), dict(reason=why, rungs=[]))["rungs"].append(dict(line=l, over=int(c["over"]), ts=c.get("ts")))
    for k in [k for k, v in board.items() if not v]: board.pop(k)
    if n_bad: print(f"yard ladders: {n_bad} of {n_lad} one-sided alternate ladders rejected as unverifiable against the book's own two-sided line")
    return board


def ladder_fault(anchors, alts):
    """None when a book's ladder verifies, else the reason. anchors = [(line, cell)] two-sided rows, alts = one-sided rows."""
    if not anchors: return "no two-sided line at this book to verify the ladder against"
    an = [(l, implied(c["over"]) / (implied(c["over"]) + implied(c["under"])), implied(c["over"]) + implied(c["under"])) for l, c in anchors]
    hold = float(np.median([h for _, _, h in an]))
    pts = [(l, min(implied(c["over"]) / hold, 0.999)) for l, c in alts]
    for ml, pm, _ in an:
        bad = [(l, p) for l, p in pts if (l < ml and p < pm - MAIN_TOL) or (l > ml and p > pm + MAIN_TOL)]
        if bad:
            return f"contradicts the book's own line (Over {ml:g} at {pm:.0%}): " + ", ".join(f"Over {l:g} at {p:.0%}" for l, p in bad[:3]) + (f" and {len(bad) - 3} more" if len(bad) > 3 else "")
    seq = sorted(pts + [(l, p) for l, p, _ in an])
    steps = [(seq[i][0], seq[i][1], seq[i - 1][0]) for i in range(1, len(seq)) if seq[i][1] > seq[i - 1][1] + STEP_TOL]
    if steps:
        return "P(over) rises with the line: " + ", ".join(f"Over {l:g} at {p:.0%} above Over {lo:g}" for l, p, lo in steps[:3])
    return None


def book_ranges(board, kind, player):
    """{book: (lowest, highest listed line)} over the verified board for this player and stat."""
    nn = norm_name(player); rng = {}
    for (k, n, line), books in board.items():
        if k != kind or n != nn: continue
        for bk, c in books.items():
            if "over" not in c: continue
            lo, hi = rng.get(bk, (line, line)); rng[bk] = (min(lo, line), max(hi, line))
    return rng


def monotone_violations(xs, ps, tol=0.0):
    """[(x, p, previous x, previous p)] wherever P(over) rises as the line rises; [] when the sequence is non-increasing."""
    seq = sorted(zip(xs, ps)); out = []
    for i in range(1, len(seq)):
        if seq[i][1] > seq[i - 1][1] + tol: out.append((seq[i][0], seq[i][1], seq[i - 1][0], seq[i - 1][1]))
    return out


DISAGREE, MODEL_BAND, LONE_BAND = 0.30, 0.15, 0.35   # disagreeing books: keep quotes within MODEL_BAND of the model; a lone quote within LONE_BAND


def price_rung(board, hold, kind, player, line, model_p=None):
    """When books disagree on a rung by more than DISAGREE in implied probability, keep only quotes within MODEL_BAND of the
       model's P(over) (SharpAPI has carried lone DraftKings alternate rows like 'Over 9.5 +2000' against FanDuel's -1450)."""
    books = board.get((kind, norm_name(player), float(line)))
    if not books: return None
    overs = [(bk, c["over"]) for bk, c in books.items() if "over" in c]
    if not overs: return None
    if model_p is not None:
        probs = [implied(pr) for _, pr in overs]
        band = MODEL_BAND if max(probs) - min(probs) > DISAGREE else LONE_BAND if len(overs) == 1 else None
        if band is not None:
            overs = [(bk, pr) for (bk, pr), pp in zip(overs, probs) if abs(pp - model_p) <= band]
            books = {bk: books[bk] for bk, _ in overs}
            if not overs: return None
    best_bk, best = max(overs, key=lambda x: decimal(x[1]))
    med = american(float(np.median([decimal(pr) for _, pr in overs])))
    fair = []
    for bk, c in books.items():
        if "over" not in c: continue
        if "under" in c:
            po, pu = implied(c["over"]), implied(c["under"]); fair.append(po / (po + pu))
        else:
            fair.append(implied(c["over"]) / hold.get((kind, bk), 1.06))
    return dict(best=int(best), book=best_bk, median=med, n_books=len(overs), market_p=float(np.median(fair)), two_sided=any("under" in c for c in books.values()),
                captured=books[best_bk].get("ts"))


MIN_CURVE_BOOKS, MIN_CURVE_RUNGS = 2, 4      # a market curve counts when 2+ books contribute, or one book posts 4+ rungs
CROSS_BAND, MODEL_BAND_CURVE = 0.30, 0.35     # a quote more than 30 pts from the other books' curve at that line (35 from the model when no other
                                              # book covers it) is a bad row: SharpAPI carries DraftKings alternates like "Over 14.5 rec yds +2200"


def book_points(board, hold, kind, player):
    """Every quoted Over for this player and stat on the verified board: [(line, book, no-vig P(over), price, capture ts)].
       Two-sided rungs are de-vigged against their own Under; one-sided alternates against the book's main-line hold."""
    nn = norm_name(player); pts = []
    for (k, n, line), books in board.items():
        if k != kind or n != nn: continue
        for bk, c in books.items():
            if "over" not in c: continue
            if "under" in c:
                po, pu = implied(c["over"]), implied(c["under"]); p = po / (po + pu)
            else:
                p = implied(c["over"]) / hold.get((kind, bk), 1.06)
            pts.append((float(line), bk, float(min(max(p, 0.001), 0.999)), int(c["over"]), c.get("ts")))
    return pts


def _pav_decreasing(xs, ys):
    """Pool-adjacent-violators: the closest non-increasing sequence to ys (in order of xs)."""
    blocks = [[y, 1] for y in ys]
    i = 0
    while i < len(blocks) - 1:
        if blocks[i][0] < blocks[i + 1][0] - 1e-12:          # a later line more likely than an earlier one: pool
            a, b = blocks[i], blocks[i + 1]
            blocks[i] = [(a[0] * a[1] + b[0] * b[1]) / (a[1] + b[1]), a[1] + b[1]]; del blocks[i + 1]
            i = max(i - 1, 0)
        else:
            i += 1
    out = []
    for v, n in blocks: out += [v] * n
    return out


def _book_curve(pts_bk):
    xs = sorted({l for l, *_ in pts_bk}); by = {}
    for l, _, p, *_ in pts_bk: by.setdefault(l, []).append(p)
    return xs, _pav_decreasing(xs, [float(np.mean(by[l])) for l in xs])


def market_curve(board, hold, kind, player, model=None):
    """Smooth monotone P(over line) from both books' main line and every alternate rung, pooled. Each quote is first
       checked against the other books' own monotone curve at that line (CROSS_BAND), or against the model knots
       `model=(lines, probs)` when no other book covers the line (MODEL_BAND_CURVE); survivors are averaged per line,
       made non-increasing (PAV) and joined linearly in log-odds. No extrapolation: curve_at() is None outside [lo, hi].
       `eligible` = 2+ books or one book with 4+ rungs. `quotes` = {line: [(book, price, p)]} of the surviving quotes,
       `captured` = {(line, book): capture ts}."""
    pts = book_points(board, hold, kind, player)
    if not pts: return None
    by_book = {}
    for q in pts: by_book.setdefault(q[1], []).append(q)
    curves = {bk: _book_curve(v) for bk, v in by_book.items()}
    kept, dropped = [], 0
    for line, bk, p, price, ts in pts:
        refs = [interp_logit(*curves[o], line) for o in curves if o != bk]
        refs = [r for r in refs if r is not None]
        if refs:
            ok = abs(p - float(np.mean(refs))) <= CROSS_BAND
        elif model:
            mp = interp_logit(model[0], model[1], line); ok = mp is None or abs(p - mp) <= MODEL_BAND_CURVE
        else:
            ok = True
        if ok: kept.append((line, bk, p, price, ts))
        else: dropped += 1
    if not kept: return None
    by_line, per_book, quotes, cap = {}, {}, {}, {}
    for line, bk, p, price, ts in kept:
        by_line.setdefault(line, []).append(p); per_book[bk] = per_book.get(bk, 0) + 1; quotes.setdefault(line, []).append((bk, price, round(p, 4)))
        if ts: cap[(line, bk)] = ts
    lines = sorted(by_line); probs = _pav_decreasing(lines, [float(np.mean(by_line[l])) for l in lines])
    return dict(lines=lines, probs=[round(p, 4) for p in probs], books=sorted(per_book), per_book=per_book, n_rungs=len(lines), lo=lines[0], hi=lines[-1],
                eligible=len(per_book) >= MIN_CURVE_BOOKS or max(per_book.values()) >= MIN_CURVE_RUNGS, quotes=quotes, captured=cap, dropped=dropped)


def interp_logit(xs, ps, x):
    """Linear interpolation in log-odds between knots; None outside the knots' range."""
    if x < xs[0] - 1e-9 or x > xs[-1] + 1e-9: return None
    lg = lambda p: np.log(min(max(p, 1e-4), 1 - 1e-4) / (1 - min(max(p, 1e-4), 1 - 1e-4)))
    for i in range(len(xs) - 1):
        if xs[i] - 1e-9 <= x <= xs[i + 1] + 1e-9:
            if xs[i + 1] == xs[i]: return float(ps[i])
            t = (x - xs[i]) / (xs[i + 1] - xs[i]); z = lg(ps[i]) + t * (lg(ps[i + 1]) - lg(ps[i]))
            return float(1 / (1 + np.exp(-z)))
    return float(ps[-1]) if abs(x - xs[-1]) <= 1e-9 else None


def curve_at(curve, line):
    if not curve: return None
    return interp_logit(curve["lines"], curve["probs"], float(line))


def offered_at(board, kind, player, line):
    """[(book, Over price)] listed at exactly this line on the verified board (a two-sided line or a verified alternate rung)."""
    books = board.get((kind, norm_name(player), float(line))) or {}
    return [(bk, int(c["over"])) for bk, c in books.items() if "over" in c]


def captured_at(board, kind, player, line, book):
    """Capture timestamp (ISO, from the feed) of this book's quote at exactly this line, or None."""
    c = (board.get((kind, norm_name(player), float(line))) or {}).get(book) or {}
    return c.get("ts")


def offered_lines(board, kind, player):
    """All lines the books offer for this player and kind."""
    nn = norm_name(player)
    return sorted({k[2] for k in board if k[0] == kind and k[1] == nn})
