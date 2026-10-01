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


def offered_lines(board, kind, player):
    """All lines the books offer for this player and kind."""
    nn = norm_name(player)
    return sorted({k[2] for k in board if k[0] == kind and k[1] == nn})
