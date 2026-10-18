"""Cross-venue line shop: for every edge on the board, every venue's price for that exact bet, after fees, with size and capture time, and
the best place to bet. Display + data only: the edge list, its best/book/EV fields and the flag rule are inputs here and are never changed
(test_lineshop.py::test_flag_set_identical_with_and_without_line_shop).

Venues:
  regular books   the sportsbook quotes that priced the bet (odds.prop_board for TD markets, the VERIFIED yard board for yard rungs);
  Kalshi          the direct feed (kalshi.attach): fee-adjusted price, dollars at the ask, $100-at-the-ask liquidity floor (reference only below it);
  us_ex           Novig, ProphetX, Polymarket from The Odds API's us_ex region (odds.fetch_exchange), top of book per outcome, "size unknown,
                  confirm in app"; alt ladders go through yard_prices.verify_ladders + the monotonicity check exactly like sportsbook ladders
                  (a failed ladder is research-only, shown greyed "unverified", never bettable); a price more than venues.OUTLIER_REL better
                  than the regular-book median is greyed "outlier" and can never be the best place to bet.
"""
import numpy as np
import odds as O, yard_prices as YP, venues as V, kalshi as K

TD_KEY = {"any": ("player_anytime_td", None), "first": ("player_first_td", None), "two": ("player_tds_over", 1.5)}
STALE_MIN = 15          # inside kickoff windows the page hides venue prices older than this (rules.MAX_PRICE_AGE_MIN); recorded here for the data
KICKOFF_WINDOW_MIN = 180


def _norm_bet(e):
    """(market key or yard kind, norm player, point) for an edge."""
    name = e["bet"].split(" Anytime TD")[0].split(" First TD")[0].split(" 2+ TD")[0].split(" Over ")[0]
    if e["market"] in ("yds", "yds_alt"): return e["kind"], O.norm_name(name), float(e["line"])
    mk, pt = TD_KEY[e["market"]]
    return mk, O.norm_name(name), pt


def kalshi_index(events):
    """{(market, norm player, point): dict(price, ask, dollars, ts, liquid)} from the direct Kalshi bookmaker on each event."""
    out = {}
    for ev in events or []:
        for b in ev.get("props", {}).get("bookmakers", []):
            if b.get("title") != K.BOOK: continue
            for m in b["markets"]:
                mk = O.MARKET_ALIAS.get(m["key"], m["key"])
                for o in m["outcomes"]:
                    out[(mk, O.norm_name(o.get("description") or ""), o.get("point"))] = dict(
                        price=o["price"], ask=o.get("kalshi_yes_ask"), dollars=o.get("kalshi_dollars_at_ask"), ts=o.get("ts"), liquid=not o.get("reference"))
    return out


def exchange_boards(ex_events, rejected=None):
    """TD quotes and the VERIFIED yard ladder board from collapsed us_ex events. td: {(market, norm, point): {venue: {"yes": price, "ts": ts}}};
       yards: yard_prices.ladder_board on source 'us_ex' with every venue, ladders that fail verification in `rejected`."""
    td = {}
    for ev in ex_events or []:
        for b in ev.get("props", {}).get("bookmakers", []):
            for m in b["markets"]:
                mk = O.MARKET_ALIAS.get(m["key"], m["key"])
                if "yds" in mk: continue
                for o in m["outcomes"]:
                    if o["name"] not in ("Yes", "Over"): continue
                    key = (mk, O.norm_name(o.get("description") or ""), o.get("point"))
                    td.setdefault(key, {})[b["title"]] = dict(yes=o["price"], ts=o.get("ts"))
    yards, _ = YP.ladder_board(ex_events, rejected=rejected, source="us_ex", books="all") if ex_events else ({}, {})
    return td, yards


def _row(venue, price, ts, size=None, liquid=True, verified=True, regular=False):
    return dict(venue=venue, kind=V.kind(venue), price=int(price), net=V.net_american(venue, int(price)), fee=V.fee_note(venue), fee_confirmed=V.fee_confirmed(venue),
                size=size, size_label=V.size_label(venue, size), captured=ts, liquid=bool(liquid), verified=bool(verified), outlier=False,
                regular=bool(regular), eligible=False, best=False)


def venues_for(e, board, yard_board, kidx, ex_td, ex_yards, ex_rejected):
    """Every venue's price for edge `e`, best to worst by after-fee payout; ineligible rows (outlier, unverified ladder, illiquid Kalshi) last."""
    k, nn, pt = _norm_bet(e)
    rows = []
    if e["market"] in ("yds", "yds_alt"):
        for bk, c in (yard_board.get((k, nn, pt)) or {}).items():
            if "over" in c and bk != K.BOOK: rows.append(_row(bk, c["over"], c.get("ts"), regular=True))
        for bk, c in (ex_yards.get((k, nn, pt)) or {}).items():
            if "over" in c: rows.append(_row(bk, c["over"], c.get("ts"), verified=True))
        for (kk, n2, bk), rej in (ex_rejected or {}).items():                                 # ladders that failed the gate: shown greyed, never bettable
            if kk != k or n2 != nn: continue
            for r in rej["rungs"]:
                if float(r["line"]) == pt: rows.append(dict(_row(bk, r["over"], r.get("ts"), verified=False), reason=rej["reason"]))
    else:
        for bk, c in (board.get((k, nn, pt)) or {}).items():
            if "yes" in c and bk != K.BOOK: rows.append(_row(bk, c["yes"], c.get("ts"), regular=True))
        kq = kidx.get((k, nn, pt))
        if kq: rows.append(_row(K.BOOK, kq["price"], kq["ts"], size=kq["dollars"], liquid=kq["liquid"]))
        for bk, c in (ex_td.get((k, nn, pt)) or {}).items():
            rows.append(_row(bk, c["yes"], c["ts"]))
    reg = [r["price"] for r in rows if r["regular"]]
    for r in rows:
        if r["venue"] == K.BOOK: r["net"] = r["price"]; r["price"] = r.get("raw", r["price"])    # the board's Kalshi price is already fee-adjusted
        if V.is_exchange(r["venue"]) and not (r["venue"] == K.BOOK and r["liquid"]) and V.is_outlier(r["price"], reg): r["outlier"] = True   # size-unknown feeds only; a Kalshi quote with $100+ at the ask is real
        r["eligible"] = r["verified"] and not r["outlier"] and (r["liquid"] or r["venue"] != K.BOOK)
    rows.sort(key=lambda r: (not r["eligible"], -O.decimal(r["net"])))
    if rows and rows[0]["eligible"]: rows[0]["best"] = True
    return rows


def american_points(a):
    """American price on one continuous scale (-100 and +100 meet): +240 -> 240, -150 -> 50, so 'cents' = the difference between two prices."""
    a = int(a); return a if a > 0 else a + 200


def cents(better, worse):
    return american_points(better) - american_points(worse)


def log_improvements(edges, season, week, stamp, d="research"):
    """First sighting of every edge whose best place to bet (after fees) is not the flagging book -> research/lineshop_<season>_w<week>.json keyed
       pid|market|kind|line: book + price, venue + after-fee price, improvement in cents, stake, flag time. results.py grades it: units the venue
       would have added = stake x (venue decimal - book decimal) on winners, 0 on losers. Returns the number newly logged."""
    import json, os
    os.makedirs(d, exist_ok=True)
    fn = f"{d}/lineshop_{season}_w{week}.json"
    log = json.load(open(fn)) if os.path.exists(fn) else {}
    n = 0
    for e in edges:
        bv = e.get("best_venue")
        if not bv or bv["venue"] == e.get("book"): continue
        key = f"{e['pid']}|{e['market']}|{e.get('kind')}|{e.get('line')}"
        if key in log: continue
        log[key] = dict(pid=e["pid"], bet=e["bet"], market=e["market"], kind=e.get("kind"), line=e.get("line"), team=e.get("team"), role=e.get("role"), stake=e.get("stake", 1.0),
                        book=e.get("book"), best=e.get("best"), bet_down_to=e.get("bet_down_to"), venue=bv["venue"], venue_price=bv["price"], venue_net=bv["net"],
                        cents=cents(bv["net"], e["best"]), size_label=bv.get("size_label"), fee_confirmed=bv.get("fee_confirmed", True), ev=e.get("ev"), flagged=stamp, kick_utc=e.get("kick_utc"))
        n += 1
    if n: json.dump(log, open(fn, "w"), indent=0)
    return n


def attach(edges, events, board, yard_board, ex_events=None, names=None):
    """Set e['venues'] (sorted list) and e['best_venue'] on every edge. The exchange events are first collapsed (venues.collapse_depth) and
       their ladders verified; `summary` returns counts for the run log and the site notice. Never touches best/book/ev/med/flag fields."""
    ex = V.collapse_depth(ex_events) if ex_events else []
    rej = {}
    ex_td, ex_yards = exchange_boards(ex, rejected=rej)
    kidx = kalshi_index(events)
    for e in edges:
        rows = venues_for(e, board, yard_board, kidx, ex_td, ex_yards, rej)
        e["venues"] = rows
        best = next((r for r in rows if r["best"]), None)
        e["best_venue"] = dict(venue=best["venue"], price=best["price"], net=best["net"], size_label=best["size_label"], captured=best["captured"], fee=best["fee"], fee_confirmed=best["fee_confirmed"]) if best else None
    venues_live = sorted({r["venue"] for e in edges for r in e.get("venues", []) if V.is_exchange(r["venue"])})
    return dict(exchange_events=len(ex), venues=venues_live, ladders_rejected=len(rej), outliers=sum(1 for e in edges for r in e["venues"] if r["outlier"]), fee_sources=V.fee_sources(),
                edges_with_exchange_quote=sum(1 for e in edges if any(V.is_exchange(r["venue"]) and r["venue"] != K.BOOK for r in e["venues"])),
                best_off_book=sum(1 for e in edges if e.get("best_venue") and e["best_venue"]["venue"] != e.get("book")))
