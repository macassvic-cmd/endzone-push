"""Venue registry for the cross-venue line shop: venue kind, fee rule, size rule, after-fee price, outlier filter.

Display + data only. A venue price changes WHERE to bet, never WHETHER a bet is flagged: the Edge Board rule (EV >= 5% at the
median and at the best regular book, 2+ books) runs on the sportsbook board before this module sees anything.

Fees (every rule documented with its source in ODDS_SOURCES.md, "Venue fees"):
  sportsbooks   none: the margin is in the price.
  Kalshi        taker fee 0.07 x C x P x (1 - P) per contract, rounded up to the cent per order; the NFL player series run the default
                multiplier 1 (fee_type quadratic / quadratic_with_maker_fees, Kalshi series API). kalshi.py already puts the fee-adjusted
                price on the board; the raw ask is kept for display. Liquidity = dollars at the ask (>= $100 floor, kalshi.is_liquid).
  Polymarket    taker fee = C x 0.05 x p x (1 - p) for sports markets (docs.polymarket.com, Fee Structure V2); makers pay nothing.
  ProphetX      2% commission on net winnings on winning trades, nothing on losers (secondary sources; no official fee page reachable).
  Novig         pregame straight trades carry no fee (live trades: taker 0.03 x p x (1 - p); parlays: 0.10 built in) per the secondary
                schedule; treat as 0 pregame and confirm in app.
The Odds API's us_ex feed carries no size for Novig, ProphetX or Polymarket: those prices are labelled "size unknown, confirm in app".
"""
from odds import implied, decimal, american

SPORTSBOOK, EXCHANGE, PREDICTION = "sportsbook", "exchange", "prediction market"
SIZE_UNKNOWN = "size unknown, confirm in app"
OUTLIER_REL = 0.15          # an exchange price more than 15% better (relative implied probability) than the regular-book median is greyed out
PLACEHOLDER_IMPLIED = 0.98  # an exchange side priced at -4900 or shorter is "no offer" (Polymarket / Kalshi -9900 placeholders), not a quote

# confirmed = the fee rule was read from the venue's own documentation (ODDS_SOURCES.md "Venue fees"); False = secondary sources only, and the page
# shows "unconfirmed fee" on that venue's prices until it is verified in the app
VENUES = {
    "Kalshi":     dict(kind=PREDICTION, fee="taker 0.07·P·(1−P) per contract",           model="kalshi_quadratic", rate=0.07, size="dollars at the ask", confirmed=True,
                       source="Kalshi fee schedule (July 2026) + series API fee_type/fee_multiplier"),
    "Polymarket": dict(kind=PREDICTION, fee="taker 0.05·P·(1−P) per share (sports)",      model="quadratic",        rate=0.05, size=None, confirmed=True,
                       source="docs.polymarket.com/polymarket-learn/trading/fees"),
    "ProphetX":   dict(kind=EXCHANGE,   fee="2% of net winnings",                          model="commission",       rate=0.02, size=None, confirmed=False,
                       source="secondary (nextpredict.io, oddsassist.com, kairos.trade); no official fee page reachable 2026-10-18"),
    "Novig":      dict(kind=EXCHANGE,   fee="none on pregame straight trades",             model="none",             rate=0.0,  size=None, confirmed=False,
                       source="secondary (bettorsinsider.com, oddsassist.com); novig.com/fees is a JavaScript app"),
}
UD_PAYOUTS_CONFIRMED = False      # Underdog's payout table (pickem_ud.DEFAULT_PAYOUTS) comes from secondary sources; the rules page is a JavaScript app


def fee_confirmed(venue):
    return bool(info(venue).get("confirmed", True))      # sportsbooks: no fee rule to confirm


def fee_sources():
    """[{venue, fee, confirmed, source}] for the site and the report."""
    return [dict(venue=v, fee=d["fee"], confirmed=d.get("confirmed", False), source=d.get("source", "")) for v, d in VENUES.items()]
EXCHANGE_KEYS = {"novig": "Novig", "prophetx": "ProphetX", "polymarket": "Polymarket", "kalshi": "Kalshi"}   # Odds API us_ex bookmaker keys


def info(venue):
    return VENUES.get(venue) or dict(kind=SPORTSBOOK, fee="none (vig in the price)", model="none", rate=0.0, size=None, confirmed=True, source="")


def kind(venue):
    return info(venue)["kind"]


def is_exchange(venue):
    """Not a sportsbook: exchanges and prediction markets (size rules and the outlier filter apply)."""
    return venue in VENUES


def net_decimal(venue, price):
    """Decimal payout per 1 staked after the venue's fee, from the American price shown at the venue."""
    v = info(venue); d = decimal(int(price))
    if v["model"] == "none": return d
    if v["model"] == "commission": return 1 + (d - 1) * (1 - v["rate"])                 # commission on net winnings
    p = 1 / d                                                                             # contract price in dollars (no vig on a single price)
    if v["model"] == "kalshi_quadratic":                                                  # same convention as the board price (kalshi.fee_adjusted_american)
        import kalshi as K
        return decimal(K.fee_adjusted_american(p))
    if v["model"] == "quadratic":                                                         # fee paid on top of the price, per share
        cost = p + v["rate"] * p * (1 - p)
        return 1 / cost if cost < 1 else 1.0
    return d


def net_american(venue, price):
    """After-fee American price (what the bettor's payout is worth at the venue), from the price shown at the venue."""
    d = net_decimal(venue, price)
    return american(d) if d > 1 else -100000


def fee_note(venue):
    return info(venue)["fee"]


def size_label(venue, dollars=None):
    """Liquidity text for a venue quote: dollars at the ask for Kalshi, 'size unknown, confirm in app' for feeds without size, '' for books."""
    if venue == "Kalshi": return f"${dollars:,.0f} at the ask" if dollars is not None else "size unknown"
    if is_exchange(venue): return SIZE_UNKNOWN
    return ""


def is_outlier(price, regular_prices, rel=OUTLIER_REL):
    """True when an exchange price is more than `rel` (relative) better in implied probability than the median of the regular books'
       prices for the same bet: implied(ex) < median_implied x (1 - rel). Such a quote can never be the best place to bet."""
    reg = [implied(int(p)) for p in regular_prices if p is not None]
    if not reg: return False
    reg.sort(); med = reg[len(reg) // 2] if len(reg) % 2 else (reg[len(reg) // 2 - 1] + reg[len(reg) // 2]) / 2
    return implied(int(price)) < med * (1 - rel) - 1e-12


def is_placeholder(price):
    return implied(int(price)) >= PLACEHOLDER_IMPLIED


def collapse_depth(events, source="us_ex"):
    """Normalise an Odds API us_ex response for the ladder and TD readers: per (bookmaker, market, player, side, point) keep the single best
       price for the bettor (ProphetX lists several order-book levels per outcome), drop placeholder sides (-9900 'no offer'), tag every
       outcome source=`source` and ts = the market's last_update. Kalshi rows are dropped (the direct Kalshi feed carries size; the us_ex
       copy does not). Returns new event dicts with props.bookmakers in the regular shape."""
    out = []
    for ev in events or []:
        books = []
        for b in (ev.get("bookmakers") or ev.get("props", {}).get("bookmakers") or []):
            venue = EXCHANGE_KEYS.get((b.get("key") or "").lower(), b.get("title"))
            if venue == "Kalshi": continue
            mk = []
            for m in b.get("markets", []):
                best = {}
                for o in m.get("outcomes", []):
                    if o.get("price") is None or is_placeholder(o["price"]): continue
                    k = (o.get("description") or o.get("name"), o["name"], o.get("point"))
                    if k not in best or decimal(o["price"]) > decimal(best[k]["price"]):
                        best[k] = dict(o, source=source, ts=o.get("ts") or m.get("last_update") or b.get("last_update"))
                if best: mk.append(dict(m, outcomes=list(best.values()), depth=len(m.get("outcomes", []))))
            if mk: books.append(dict(key=b.get("key"), title=venue, markets=mk))
        if books: out.append(dict({k: v for k, v in ev.items() if k not in ("bookmakers", "props", "exchange")}, props=dict(bookmakers=books)))
    return out
