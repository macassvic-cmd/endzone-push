"""Closing line value for flagged edges.

Flag time: run_week.py calls log_edges() every run; an edge (pid, market) is recorded the first time it appears with
the pull timestamp, best price/book, median price and no-vig median-book probability at that moment.
Closing: the last odds pull in odds_history/ before the game's kickoff (Sunday 9:45 PT run for most games, or a
later pull for SNF/MNF). closing_summary() re-prices every logged edge from that pull with the same no-vig method
(odds.measure_hold + price_summary). CLV = closing no-vig prob - flag-time no-vig prob; "beat the close" = CLV > 0.
results.py adds the aggregates (overall, by role, by market, % beating the close) to results.json.
"""
import glob, json, os, re
import odds as O

EDGE_DIR, ODDS_DIR = "edge_log", "odds_history"


def log_edges(edges, season, week, stamp):
    """Append first-seen edges to edge_log/<season>_w<week>.json. Returns the number newly logged."""
    os.makedirs(EDGE_DIR, exist_ok=True)
    fn = f"{EDGE_DIR}/{season}_w{week}.json"
    log = json.load(open(fn)) if os.path.exists(fn) else {}
    n = 0
    for e in edges:
        key = f"{e['pid']}|{e['market']}"
        if key in log:
            continue
        if e["market"] == "yds": key = f"{e['pid']}|yds|{e.get('kind')}|{e.get('line')}"
        if key in log:
            continue
        log[key] = dict(pid=e["pid"], bet=e["bet"], market=e["market"], team=e["team"], role=e.get("role"), kind=e.get("kind"), line=e.get("line"),
                        flagged=stamp, best=e["best"], book=e["book"], med=e["med"], mkt_p=e["mkt_p"],
                        blend_p=e["blend_p"], model_p=e["model_p"], ev=e["ev"], ev_med=e["ev_med"])
        n += 1
    json.dump(log, open(fn, "w"), indent=0)
    return n


def pulls(season, week):
    """[(stamp, path)] of saved odds pulls for the week, oldest first."""
    out = []
    for fn in glob.glob(f"{ODDS_DIR}/{season}_w{week}_*.json"):
        m = re.search(r"_(\d{8}T\d{4})Z\.json$", fn)
        if m: out.append((m.group(1), fn))
    return sorted(out)


MIN_BOARD = 8      # a game's board counts as complete when some book prices at least this many players in the market


def board_complete(events, team, market_key):
    """Does this pull hold a complete (pre-kickoff) board for the game with this team, in this market?"""
    for ev in events:
        if team in (O.TEAM_ABBR.get(ev["home_team"]), O.TEAM_ABBR.get(ev["away_team"])):
            for b in ev.get("props", {}).get("bookmakers", []):
                for m in b["markets"]:
                    if O.MARKET_ALIAS.get(m["key"], m["key"]) == market_key:
                        if sum(1 for o in m["outcomes"] if o["name"].lower() in ("yes", "over") and not o.get("reference")) >= MIN_BOARD:
                            return True
    return False


def closing_pull(season, week, commence_iso, team=None, market_key=None, cache=None):
    """Path of the last pull strictly before kickoff (ISO 8601 Z) whose board for this game is complete, or None.
       A pull taken after kickoff, or one where the books had already stripped the board, is never the close."""
    kick = commence_iso.replace("-", "").replace(":", "")[:13]      # YYYYMMDDTHHMM
    before = [(st, fn) for st, fn in pulls(season, week) if st < kick]
    for st, fn in reversed(before):
        if team is None or board_complete(_load(fn, cache if cache is not None else {}), team, market_key):
            return fn
    return None


def closing_summary(season, week, log=None):
    """Attach closing price and CLV to each logged edge. Uses the game's commence_time from the pull itself."""
    fn = f"{EDGE_DIR}/{season}_w{week}.json"
    log = log if log is not None else (json.load(open(fn)) if os.path.exists(fn) else {})
    if not log:
        return {}
    cache = {}
    for key, e in log.items():
        # find the event for this team in the latest pull to learn kickoff, then the last pull before it
        latest = pulls(season, week)
        if not latest:
            break
        events = _load(latest[-1][1], cache)
        ev = next((x for x in events if e["team"] in (O.TEAM_ABBR.get(x["home_team"]), O.TEAM_ABBR.get(x["away_team"]))), None)
        if not ev:
            continue
        if e["market"] == "yds":
            import yard_prices as YP
            before = [(st, fn) for st, fn in pulls(season, week) if st < ev["commence_time"].replace("-", "").replace(":", "")[:13]]
            if not before: continue
            cp = before[-1][1]; cev = _load(cp, cache)
            yb, yh = cache.setdefault(("yboard", cp), YP.ladder_board(cev))
            ps = YP.price_rung(yb, yh, e["kind"], e["bet"].split(" Over ")[0], float(e["line"]))
            if not ps: continue
            e["close_pull"] = os.path.basename(cp); e["close_best"] = ps["best"]; e["close_book"] = ps["book"]; e["close_med"] = ps["median"]
            e["close_mkt_p"] = round(ps["market_p"], 4); e["clv"] = round(ps["market_p"] - e["mkt_p"], 4); e["beat_close"] = ps["market_p"] > e["mkt_p"]
            continue
        market = {"any": "player_anytime_td", "first": "player_first_td", "two": "player_tds_over"}.get(e["market"], "player_anytime_td")
        point = 1.5 if e["market"] == "two" else None
        cp = closing_pull(season, week, ev["commence_time"], e["team"], market, cache)
        if not cp:
            continue
        cev = _load(cp, cache)
        board = cache.setdefault(("board", cp), O.prop_board(cev)); hold = cache.setdefault(("hold", cp), O.measure_hold(cev))
        ps = O.price_summary(board, market, e["bet"].rsplit(" Anytime TD", 1)[0].rsplit(" First TD", 1)[0].rsplit(" 2+ TD", 1)[0], point, hold)
        if not ps:
            continue
        e["close_pull"] = os.path.basename(cp); e["close_best"] = ps["best"]; e["close_book"] = ps["book"]
        e["close_med"] = ps["median"]; e["close_mkt_p"] = round(ps["market_p"], 4)
        e["clv"] = round(ps["market_p"] - e["mkt_p"], 4)
        e["beat_close"] = ps["market_p"] > e["mkt_p"]
    return log


def _load(fn, cache):
    if fn not in cache:
        cache[fn] = json.load(open(fn))
    return cache[fn]


def aggregate(rows):
    """rows: logged edges with clv. -> dict(n, avg_clv, beat_close_pct, by_role, by_market)."""
    rows = [r for r in rows if "clv" in r]
    def agg(g):
        return dict(n=len(g), avg_clv=round(sum(r["clv"] for r in g) / len(g), 4) if g else None,
                    beat_close=round(sum(r["beat_close"] for r in g) / len(g), 3) if g else None)
    out = agg(rows)
    out["by_role"] = {k: agg([r for r in rows if r.get("role") == k]) for k in sorted({r.get("role") or "Unknown" for r in rows})}
    out["by_market"] = {k: agg([r for r in rows if r["market"] == k]) for k in sorted({r["market"] for r in rows})}
    return out
