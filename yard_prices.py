"""Price yard ladders from the pulled events (SharpAPI DK/FD main + alternate lines, Odds API if present).

Markets: player_rush_yds / player_reception_yds / player_pass_yds, with *_alternate rows for the ladders. For each
(player, kind, line): per book, the Over price; no-vig both sides when the book quotes Over and Under at that line,
otherwise the Over divided by that book's main-line overround (its own two-sided vig). Returns the same summary shape
as odds.price_summary (best, book, median, n_books, market_p) so the Edge Board, CLV and grading reuse one path.
"""
import numpy as np
from odds import norm_name, implied, decimal, american

KIND_MARKET = {"rush": "player_rush_yds", "rec": "player_reception_yds", "pass": "player_pass_yds"}


def ladder_board(events):
    """{(kind, norm_name, line): {book: {"over": price, "under": price, "main": bool}}}, plus each book's main-line hold."""
    board, hold = {}, {}
    for ev in events or []:
        for b in ev.get("props", {}).get("bookmakers", []):
            for m in b["markets"]:
                base = m["key"].replace("_alternate", "")
                kind = next((k for k, v in KIND_MARKET.items() if v == base), None)
                if not kind: continue
                for o in m["outcomes"]:
                    if o.get("point") is None or o.get("reference"): continue
                    key = (kind, norm_name(o.get("description") or ""), float(o["point"]))
                    cell = board.setdefault(key, {}).setdefault(b["title"], {})
                    cell["over" if o["name"] == "Over" else "under"] = o["price"]
                    if o.get("main") or m["key"] == base: cell["main"] = True
    for key, books in board.items():
        for bk, c in books.items():
            if "over" in c and "under" in c and c.get("main"):
                hold.setdefault((key[0], bk), []).append(implied(c["over"]) + implied(c["under"]))
    hold = {k: float(np.median(v)) for k, v in hold.items()}
    _sanity(board)
    return board, hold


def _sanity(board):
    """Drop alternate rungs that contradict the ladder: within one book and player, P(over) must fall as the line rises.
       Anchored on the book's main line when it has one (SharpAPI has shown DraftKings alternate rows like 'Over 9.5
       +2000' next to a main 25.5 at -113). Rows that violate the order are removed from that book."""
    by = {}
    for (kind, player, line), books in board.items():
        for bk, c in books.items():
            if "over" in c: by.setdefault((kind, player, bk), []).append((line, c))
    dropped = 0
    for (kind, player, bk), rungs in by.items():
        rungs.sort(key=lambda x: x[0])
        probs = [implied(c["over"]) for _, c in rungs]
        main = next((i for i, (_, c) in enumerate(rungs) if c.get("main") and "under" in c), None)
        bad = set()
        for i, (line, c) in enumerate(rungs):
            if main is not None and i != main:
                if (i < main and probs[i] < probs[main]) or (i > main and probs[i] > probs[main]): bad.add(i)
            for j in range(i):
                if j not in bad and probs[i] > probs[j] + 0.02: bad.add(i); break      # a higher line can't be likelier than a lower one
        for i in bad:
            board[(kind, player, rungs[i][0])].pop(bk, None); dropped += 1
    for k in [k for k, v in board.items() if not v]: board.pop(k)
    if dropped: print(f"yard ladders: dropped {dropped} inconsistent alternate rungs")


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
    return dict(best=int(best), book=best_bk, median=med, n_books=len(overs), market_p=float(np.median(fair)), two_sided=any("under" in c for c in books.values()))


MIN_CURVE_BOOKS, MIN_CURVE_RUNGS = 2, 4      # a market curve counts when 2+ books contribute, or one book posts 4+ rungs
CROSS_BAND, MODEL_BAND_CURVE = 0.30, 0.35     # a quote more than 30 pts from the other books' curve at that line (35 from the model when no other
                                              # book covers it) is a bad row: SharpAPI carries DraftKings alternates like "Over 14.5 rec yds +2200"


def book_points(board, hold, kind, player):
    """Every quoted Over for this player and stat after the sanity filters: [(line, book, no-vig P(over), price)].
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
            pts.append((float(line), bk, float(min(max(p, 0.001), 0.999)), int(c["over"])))
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
    for l, _, p, _ in pts_bk: by.setdefault(l, []).append(p)
    return xs, _pav_decreasing(xs, [float(np.mean(by[l])) for l in xs])


def market_curve(board, hold, kind, player, model=None):
    """Smooth monotone P(over line) from both books' main line and every alternate rung, pooled. Each quote is first
       checked against the other books' own monotone curve at that line (CROSS_BAND), or against the model knots
       `model=(lines, probs)` when no other book covers the line (MODEL_BAND_CURVE); survivors are averaged per line,
       made non-increasing (PAV) and joined linearly in log-odds. No extrapolation: curve_at() is None outside [lo, hi].
       `eligible` = 2+ books or one book with 4+ rungs. `quotes` = {line: [(book, price, p)]} of the surviving quotes."""
    pts = book_points(board, hold, kind, player)
    if not pts: return None
    by_book = {}
    for q in pts: by_book.setdefault(q[1], []).append(q)
    curves = {bk: _book_curve(v) for bk, v in by_book.items()}
    kept, dropped = [], 0
    for line, bk, p, price in pts:
        refs = [interp_logit(*curves[o], line) for o in curves if o != bk]
        refs = [r for r in refs if r is not None]
        if refs:
            ok = abs(p - float(np.mean(refs))) <= CROSS_BAND
        elif model:
            mp = interp_logit(model[0], model[1], line); ok = mp is None or abs(p - mp) <= MODEL_BAND_CURVE
        else:
            ok = True
        if ok: kept.append((line, bk, p, price))
        else: dropped += 1
    if not kept: return None
    by_line, per_book, quotes = {}, {}, {}
    for line, bk, p, price in kept:
        by_line.setdefault(line, []).append(p); per_book[bk] = per_book.get(bk, 0) + 1; quotes.setdefault(line, []).append((bk, price, round(p, 4)))
    lines = sorted(by_line); probs = _pav_decreasing(lines, [float(np.mean(by_line[l])) for l in lines])
    return dict(lines=lines, probs=[round(p, 4) for p in probs], books=sorted(per_book), per_book=per_book, n_rungs=len(lines), lo=lines[0], hi=lines[-1],
                eligible=len(per_book) >= MIN_CURVE_BOOKS or max(per_book.values()) >= MIN_CURVE_RUNGS, quotes=quotes, dropped=dropped)


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
    """[(book, Over price)] quoted at exactly this line (after the sanity filters)."""
    books = board.get((kind, norm_name(player), float(line))) or {}
    return [(bk, int(c["over"])) for bk, c in books.items() if "over" in c]


def offered_lines(board, kind, player):
    """All lines the books offer for this player and kind."""
    nn = norm_name(player)
    return sorted({k[2] for k in board if k[0] == kind and k[1] == nn})
