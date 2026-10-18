"""Cross-venue line shop (2026-10-18): name matching across venues incl. same-name players on different teams, fee math per venue, the exchange
ladder gate + monotonicity check, the outlier filter, and the invariant that the line shop never changes which bets are flagged.

    python test_lineshop.py        # CI runs it in the "Tests and data" steps
"""
import copy, json, os, datetime as dt
import odds as O, venues as V, lineshop as LS, pickem_ud as PU, yard_prices as YP, rules as RU, alerts as AL
import pandas as pd


def _ev(books, home="Las Vegas Raiders", away="Buffalo Bills", ts="2026-10-18T08:09:00Z"):
    """books = {title: {market_key: [(name, description, point, price)]}} -> one Odds API-shaped event with props."""
    bl = []
    for title, mk in books.items():
        bl.append(dict(key=title.lower(), title=title, markets=[dict(key=k, last_update=ts, outcomes=[dict(name=n, description=d, **({"point": p} if p is not None else {}), price=pr, ts=ts) for n, d, p, pr in rows]) for k, rows in mk.items()]))
    return dict(id="e1", home_team=home, away_team=away, commence_time="2026-10-18T20:25:00Z", bookmakers=[], props=dict(bookmakers=bl))


def test_fee_math_per_venue():
    """After-fee American prices: books unchanged; Kalshi = the board's fee-adjusted convention (kalshi.py); Polymarket pays 0.05·p(1−p) per share on
       top of the price; ProphetX keeps 98% of net winnings; Novig pregame straight trades are fee-free."""
    assert V.net_american("DraftKings", -150) == -150 and V.net_american("FanDuel", 300) == 300
    import kalshi as K
    assert V.net_american("Kalshi", -150) == K.fee_adjusted_american(0.6) == -157
    assert V.net_american("Kalshi", 300) == K.fee_adjusted_american(0.25) == 295
    # Polymarket at $0.50: fee 0.05 * .5 * .5 = 0.0125 per share -> cost 0.5125 -> decimal 1.951 -> -105
    assert V.net_american("Polymarket", 100) == -105, V.net_american("Polymarket", 100)
    assert V.net_american("Polymarket", 300) == 286 and V.net_american("Polymarket", -150) == -158
    # ProphetX: +300 -> win 3.00 * 0.98 = 2.94 -> +294; -150 -> win 0.6667 * .98 = .6533 -> -153
    assert V.net_american("ProphetX", 300) == 294 and V.net_american("ProphetX", -150) == -153
    assert V.net_american("Novig", 300) == 300 and V.net_american("Novig", -150) == -150
    assert V.size_label("Novig") == V.SIZE_UNKNOWN == V.size_label("ProphetX") == V.size_label("Polymarket") and V.size_label("Kalshi", 123.4) == "$123 at the ask" and V.size_label("BetMGM") == ""
    for v in ("Novig", "ProphetX", "Polymarket", "Kalshi"): assert V.is_exchange(v)
    assert not V.is_exchange("DraftKings")
    print("  fees: Kalshi -150 -> -157, Polymarket +300 -> +286, ProphetX +300 -> +294, Novig unchanged, books unchanged")


def test_outlier_filter_greys_a_too_good_price():
    """An exchange price more than 15% better (relative implied probability) than the regular-book median is an outlier and can never be best."""
    books = {"DraftKings": {"player_anytime_td": [("Yes", "James Cook", None, 300)]}, "FanDuel": {"player_anytime_td": [("Yes", "James Cook", None, 310)]},
             "BetMGM": {"player_anytime_td": [("Yes", "James Cook", None, 290)]}}
    ev = _ev(books)
    board = O.prop_board([ev])
    ex = [dict(id="e1", home_team=ev["home_team"], away_team=ev["away_team"], bookmakers=[
        dict(key="novig", title="Novig", markets=[dict(key="player_anytime_td", last_update="2026-10-18T08:09:00Z", outcomes=[dict(name="Yes", description="James Cook", price=500)])]),      # implied .167 vs median .25: 33% better -> outlier
        dict(key="prophetx", title="ProphetX", markets=[dict(key="player_anytime_td", last_update="2026-10-18T08:09:00Z", outcomes=[dict(name="Yes", description="James Cook", price=340), dict(name="Yes", description="James Cook", price=330)])])])]   # .227 vs .25: 9% better -> fine; top of book 340
    e = dict(bet="James Cook Anytime TD", pid="p1", market="any", team="BUF", best=310, book="FanDuel", ev=0.1, med=300, ev_med=0.07, nbooks=3, blend_p=0.3, model_p=0.3, mkt_p=0.25, role="Starter")
    LS.attach([e], [ev], board, {}, ex)
    by = {r["venue"]: r for r in e["venues"]}
    assert by["Novig"]["outlier"] and not by["Novig"]["eligible"] and not by["Novig"]["best"], by["Novig"]
    assert by["ProphetX"]["price"] == 340 and by["ProphetX"]["net"] == 333 and by["ProphetX"]["best"] and by["ProphetX"]["size_label"] == V.SIZE_UNKNOWN, by["ProphetX"]
    assert e["best_venue"]["venue"] == "ProphetX" and e["venues"][-1]["venue"] == "Novig", [r["venue"] for r in e["venues"]]
    assert e["best"] == 310 and e["book"] == "FanDuel", "the flagging fields never move"
    assert V.is_outlier(500, [300, 310, 290]) and not V.is_outlier(340, [300, 310, 290]) and not V.is_outlier(400, [])
    print("  outlier: Novig +500 greyed against a +300 median; ProphetX +340 (top of book, +333 after 2%) is the best place to bet")


def test_exchange_ladder_gate_and_monotonicity():
    """A us_ex ladder that contradicts its own two-sided line (or rises with the line) fails yard_prices.verify_ladders like a sportsbook ladder and is
       research-only: shown greyed 'unverified', never eligible; a consistent ladder verifies and can be best. Placeholder sides (-9900) are not quotes."""
    main = [("Over", "James Cook", 79.5, -110), ("Under", "James Cook", 79.5, -110)]
    fd_alt = [("Over", "James Cook", pt, pr) for pt, pr in [(49.5, -600), (59.5, -300), (69.5, -170), (89.5, 150), (99.5, 240)]]
    ev = _ev({"FanDuel": {"player_rush_yds": main, "player_rush_yds_alternate": fd_alt}, "DraftKings": {"player_rush_yds": main, "player_rush_yds_alternate": [("Over", "James Cook", 89.5, 160)]}})
    yb, _ = YP.ladder_board([ev], source="oddsapi")
    assert YP.offered_at(yb, "rush", "James Cook", 89.5) and len(YP.offered_at(yb, "rush", "James Cook", 89.5)) == 2
    good = dict(key="novig", title="Novig", markets=[dict(key="player_rush_yds", last_update="2026-10-18T08:09:00Z", outcomes=[
        dict(name="Over", description="James Cook", point=79.5, price=-105), dict(name="Under", description="James Cook", point=79.5, price=-112),
        dict(name="Over", description="James Cook", point=89.5, price=170), dict(name="Over", description="James Cook", point=99.5, price=260)])])
    bad = dict(key="prophetx", title="ProphetX", markets=[dict(key="player_rush_yds", last_update="2026-10-18T08:09:00Z", outcomes=[
        dict(name="Over", description="James Cook", point=79.5, price=-110), dict(name="Under", description="James Cook", point=79.5, price=-110),
        dict(name="Over", description="James Cook", point=89.5, price=-400),      # Over 89.5 more likely than the book's own Over 79.5: contradicts the line
        dict(name="Over", description="James Cook", point=99.5, price=250)])])
    poly = dict(key="polymarket", title="Polymarket", markets=[dict(key="player_rush_yds_alternate", last_update="2026-10-18T08:09:00Z", outcomes=[
        dict(name="Over", description="James Cook", point=89.5, price=165), dict(name="Under", description="James Cook", point=89.5, price=-9900)])])   # -9900 = no offer, dropped; no anchor -> unverifiable
    ex = [dict(id="e1", home_team=ev["home_team"], away_team=ev["away_team"], bookmakers=[good, bad, poly])]
    e = dict(bet="James Cook Over 89.5 rush yds (alt)", pid="p1", market="yds_alt", kind="rush", line=89.5, team="BUF", best=160, book="DraftKings", ev=0.08, med=160, ev_med=0.08, nbooks=2, blend_p=0.42, model_p=0.45, mkt_p=0.39, role="Starter", alt=True)
    s = LS.attach([e], [ev], O.prop_board([ev]), yb, ex)
    by = {r["venue"]: r for r in e["venues"]}
    assert by["Novig"]["verified"] and by["Novig"]["eligible"] and by["Novig"]["best"] and by["Novig"]["price"] == 170, by["Novig"]
    assert "ProphetX" in by and not by["ProphetX"]["verified"] and not by["ProphetX"]["eligible"] and "contradicts" in by["ProphetX"]["reason"], by.get("ProphetX")
    assert "Polymarket" in by and not by["Polymarket"]["verified"] and "no two-sided line" in by["Polymarket"]["reason"], by.get("Polymarket")
    assert s["ladders_rejected"] == 2 and e["best_venue"]["venue"] == "Novig" and e["best"] == 160 and e["book"] == "DraftKings"
    # monotonicity: a rising ladder fails too
    rising = dict(key="novig", title="Novig", markets=[dict(key="player_rush_yds", last_update="2026-10-18T08:09:00Z", outcomes=[
        dict(name="Over", description="James Cook", point=79.5, price=-110), dict(name="Under", description="James Cook", point=79.5, price=-110),
        dict(name="Over", description="James Cook", point=89.5, price=200), dict(name="Over", description="James Cook", point=99.5, price=120)])])   # 99.5 more likely than 89.5
    e2 = dict(e); LS.attach([e2], [ev], O.prop_board([ev]), yb, [dict(id="e1", home_team=ev["home_team"], away_team=ev["away_team"], bookmakers=[rising])])
    nv = next(r for r in e2["venues"] if r["venue"] == "Novig")
    assert not nv["verified"] and "rises with the line" in nv["reason"], nv
    assert V.collapse_depth(ex)[0]["props"]["bookmakers"][2]["markets"][0]["outcomes"] == [dict(name="Over", description="James Cook", point=89.5, price=165, source="us_ex", ts="2026-10-18T08:09:00Z")]
    print("  exchange ladders: consistent Novig ladder verified and best; contradicting ProphetX, anchor-less Polymarket and a rising ladder are research-only")


def test_name_matching_across_venues_same_name_different_team():
    """Underdog legs attach by name + team + game: the Jaguars' Josh Allen never attaches to the Bills' Josh Allen, and a name on the wrong team is dropped.
       Exchange quotes key on the same normalised name as the board (norm_name), so 'Kenneth Walker III' and 'Marvin Harrison Jr.' line up."""
    slate = [("00-1", "Josh Allen", "BUF"), ("00-2", "Josh Allen", "JAX"), ("00-3", "Kenneth Walker III", "LAC"), ("00-4", "Marvin Harrison Jr.", "HOU")]
    ud = [dict(player="Josh Allen", team="BUF", game_teams=["BUF", "LV"], market="yds", kind="rush", line=39.5, ud_stat="Rush Yards", higher=dict(price=-120, mult=0.93), lower=dict(price=100, mult=1.0)),
          dict(player="Josh Allen", team="JAX", game_teams=["HOU", "JAX"], market="td", kind="any", line=0.5, ud_stat="Anytime TD", higher=dict(price=400, mult=1.2)),
          dict(player="Josh Allen", team="NYG", game_teams=["NO", "NYG"], market="td", kind="any", line=0.5, ud_stat="Anytime TD", higher=dict(price=400, mult=1.2)),
          dict(player="Kenneth Walker", team="LAC", game_teams=["LAC", "KC"], market="yds", kind="rush", line=59.5, ud_stat="Rush Yards", higher=dict(price=-110, mult=1.0), lower=dict(price=-110, mult=1.0)),
          dict(player="Marvin Harrison", team="HOU", game_teams=["HOU", "JAX"], market="yds", kind="rec", line=24.5, ud_stat="Receiving Yards", higher=dict(price=103, mult=1.05), lower=dict(price=-125, mult=0.91))]
    hits, dropped = PU.match_players(ud, slate)
    assert [(l["team"], pid) for l, pid in hits] == [("BUF", "00-1"), ("JAX", "00-2"), ("LAC", "00-3"), ("HOU", "00-4")], hits
    assert dropped == [dict(player="Josh Allen", ud_team="NYG", slate_teams=["BUF", "JAX"])], dropped
    yards = [dict(pid="00-1", name="Josh Allen", team="BUF", kind="rush", rungs=[dict(line=29.5, p=0.7, blend=0.66), dict(line=39.5, p=0.52, curve_blend=0.61)]),
             dict(pid="00-3", name="Kenneth Walker III", team="LAC", kind="rush", rungs=[dict(line=59.5, p=0.5, curve_blend=0.56)])]
    summ = PU.flag(dict(lines=ud, fetched="x"), slate, yards, {("00-2", "any"): 0.22, ("00-1", "any"): 0.6})
    legs = {(l["pid"], l["market"], l.get("kind"), l["line"]): l for l in summ["legs"]}
    allen = legs[("00-1", "yds", "rush", 39.5)]
    assert allen["side"] == "higher" and allen["flag"] and abs(allen["margin"] - (0.61 - PU.breakeven(PU.DEFAULT_PAYOUTS["3-pick"], 0.93))) < 1e-6, allen
    assert yards[0]["rungs"][1]["ud"]["flag"] and "ud" not in yards[0]["rungs"][0]
    assert not legs[("00-3", "yds", "rush", 59.5)]["flag"], "0.56 vs 0.5503 break-even: under the 3-pt margin"
    assert legs[("00-2", "any", None, 0.5)]["blend_higher"] == 0.22 and legs[("00-2", "any", None, 0.5)]["side"] == "higher" and not legs[("00-2", "any", None, 0.5)]["flag"], "only a higher option: priced on the Jaguars' own blend, not flagged"
    assert allen["flag_higher"] and not allen["flag_lower"] and allen["margins"]["higher"] == allen["margin"]
    low = PU.evaluate(dict(ud_stat="Rush Yards", line=39.5, higher=dict(price=-110, mult=1.0), lower=dict(price=-110, mult=1.0)), 0.40)
    assert low["side"] == "lower" and low["flag"] and low["flag_lower"] and not low["flag_higher"], "a lower leg flags on the Yards tab but would never tag an (over) edge"
    assert summ["flagged"] == 1 and summ["matched"] == 3 and len(summ["dropped_other_team"]) == 1
    # exchange name keys: the board and the exchange reader normalise the same way
    assert O.norm_name("Kenneth Walker III") == O.norm_name("Kenneth Walker") and O.norm_name("Marvin Harrison Jr.") == O.norm_name("Marvin Harrison")
    print("  names: Bills' Josh Allen 39.5 rush flagged, Jaguars' Josh Allen priced on his own team, Giants' 'Josh Allen' dropped; suffixes normalised")


def test_breakeven_table():
    be = {k: PU.breakeven(v) for k, v in PU.DEFAULT_PAYOUTS.items()}
    assert abs(be["2-pick"] - 3 ** -0.5) < 1e-3 and abs(be["3-pick"] - 6 ** (-1 / 3)) < 1e-3 and 0.52 < be["5-pick flex"] < 0.535, be
    assert PU.breakeven(PU.DEFAULT_PAYOUTS["2-pick"], 0.86) > be["2-pick"] > PU.breakeven(PU.DEFAULT_PAYOUTS["2-pick"], 1.11)
    print(f"  break-even per leg: 2-pick {be['2-pick']:.1%}, 3-pick {be['3-pick']:.1%}, 5-pick flex {be['5-pick flex']:.1%}")


def test_flag_set_identical_with_and_without_line_shop():
    """The best venue never changes whether an edge is flagged: on the saved week-6 pull the edge list (keys and every flag field) is identical before and
       after lineshop.attach, with the probed us_ex rows attached. Exchange prices never enter the board or the EV."""
    fn = "odds_history/2026_w6_20261018T0346Z.json"
    if not os.path.exists(fn): print("  (saved pull not present, skipped)"); return
    events = json.load(open(fn))
    rej = {}
    board = O.prop_board(events); hold = O.measure_hold(events)
    yb, _ = YP.ladder_board(events, rejected=rej, source="oddsapi")
    import pandas as pd
    # a board-shaped frame of priced players from the pull, then the shared edge rule
    rows = []
    for (mk, nn, pt), books in board.items():
        if mk != "player_anytime_td": continue
        ps = O.price_summary(board, mk, nn, pt, hold)
        if not ps: continue
        p = min(max(ps["market_p"] * 1.7, 0.01), 0.95)                       # a model that likes everyone 70% more than the market: plenty of edges
        blend = (p + ps["market_p"]) / 2
        rows.append(dict(name=nn, pid=nn, team="X", role="Starter", weak_spot=None, games=5, p_any=p, any_best=ps["best"], any_book=ps["book"], any_med=ps["median"], any_mkt_p=ps["market_p"],
                         any_blend_p=blend, any_ev=blend * O.decimal(ps["best"]) - 1, any_ev_med=blend * O.decimal(ps["median"]) - 1, any_nbooks=ps["n_books"], any_captured=ps.get("captured")))
    df = pd.DataFrame(rows)
    edges = RU.td_edges(df); assert len(edges) >= 5, len(edges)
    before = copy.deepcopy(edges)
    ex = [e["exchange"] for e in events if e.get("exchange")]
    if not ex:   # the probed rows live in the session fixture; synthesise one exchange quote per edge so the venues list is non-trivial
        ex = [dict(id=events[0]["id"], home_team=events[0]["home_team"], away_team=events[0]["away_team"], bookmakers=[dict(key="novig", title="Novig", markets=[dict(key="player_anytime_td", last_update="2026-10-18T03:46:00Z",
                   outcomes=[dict(name="Yes", description=e["bet"].replace(" Anytime TD", ""), price=int(e["best"]) + 40 if e["best"] > 0 else e["best"] + 10) for e in edges])])])]
    LS.attach(edges, events, board, yb, ex)
    keys = ("bet", "pid", "market", "best", "book", "ev", "med", "ev_med", "nbooks", "blend_p", "mkt_p", "model_p")
    assert [tuple(e[k] for k in keys) for e in edges] == [tuple(e[k] for k in keys) for e in before], "flag fields changed"
    assert RU.td_edges(df) == before, "the rule itself is deterministic and untouched"
    assert all("venues" in e for e in edges) and any(e.get("best_venue") and e["best_venue"]["venue"] != e["book"] for e in edges)
    assert all((r["best"] is False) or r["eligible"] for e in edges for r in e["venues"])
    print(f"  flag set identical with and without the line shop on {len(edges)} edges; best place differs from the flagging book on {sum(1 for e in edges if e['best_venue'] and e['best_venue']['venue'] != e['book'])}")


def test_exchange_pull_gating_and_budget_guard():
    """us_ex pulls: only games with an edge; 'final' mode only the last actionable loop pull (kickoff in [30, 40) min at a 10-min step); the projection
       guard stages at the 10,000 floor (stage 1 drops us_ex, stage 2 halves the loop cadence) and the Discord alert formats."""
    now = pd.Timestamp("2026-10-18T16:35:00Z")
    games = [dict(home="ATL", away="CHI", kick_utc="2026-10-18T17:00Z"), dict(home="NE", away="NYJ", kick_utc="2026-10-18T17:00Z"), dict(home="KC", away="LAC", kick_utc="2026-10-18T20:25Z")]
    assert [g["home"] for g in O.exchange_games({"ATL", "KC"}, games, now, "all")] == ["ATL", "KC"]
    assert [g["home"] for g in O.exchange_games({"ATL", "KC"}, games, now, "final")] == [], "kickoff in 25 min: inside the cutoff, no pull"
    assert [g["home"] for g in O.exchange_games({"ATL", "KC"}, games, pd.Timestamp("2026-10-18T16:25:00Z"), "final")] == ["ATL"], "35 min out: the final actionable pull"
    assert O.exchange_games({"ATL"}, games, now, "off") == []
    import tempfile
    fd, fn = tempfile.mkstemp(suffix=".jsonl"); os.close(fd)
    t0 = dt.datetime(2026, 10, 25, 12, 0, tzinfo=dt.timezone.utc)
    with open(fn, "w") as f:
        for i, rem in enumerate((90000, 80000, 70000)):            # 10,000 a day observed
            f.write(json.dumps(dict(ts=(t0 - dt.timedelta(days=2 - i)).strftime("%Y-%m-%dT%H:%M:%SZ"), tag="nfl", path="/odds", cost=2, used=1, remaining=rem)) + "\n")
    w = dict(clusters=[dict(games=["A@B"] * 14)], odds_api_credits=7300, early=dict(credits=3190))
    b = O.budget_projection(now=t0, fn=fn, windows=w)
    assert b["observed_per_day"] == 10000 and b["burn_per_day"] == 10000 and b["reset_at"] == "2026-11-17T00:00:00Z" and b["stage"] == 2, b
    b1 = O.budget_projection(now=t0, remaining=70000 + 10000 * b["days_to_reset"] + 10000 + 1000, fn=fn, windows=w)   # enough for stage 0
    assert b1["stage"] == 0 and b1["projected_at_reset"] >= 10000, b1
    b2 = O.budget_projection(now=t0, remaining=int(10000 * b["days_to_reset"]) + 10000 - 500, fn=fn, windows=w)     # under the floor by less than the us_ex share
    assert b2["stage"] == 1 and "us_ex" in b2["actions"][0], b2
    assert O.planned_weekly(w)["exchange"] == 6 * 14 * 5 and O.planned_weekly(w)["total"] == 7300 + 3190 + 4 * (2 + 8 * 14) + 7 * 14 + 420
    assert O.next_reset(dt.datetime(2026, 11, 17, 0, 0, tzinfo=dt.timezone.utc)) == dt.datetime(2026, 12, 17, tzinfo=dt.timezone.utc)
    m = AL.guard_message(b); assert "stage 2" in m and "every 20 min" in m and "10,000" in m, m
    st = fn + ".state"
    assert AL.notify_guard(b, webhook="", state=st).startswith("no DISCORD_WEBHOOK_URL") and AL.notify_guard(dict(b, stage=0), state=st) == "guard not tripped"
    os.remove(fn)
    print("  us_ex gating: games with edges, final actionable loop pull only; projection guard stages 0/1/2 at the 10,000 floor")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try: t(); print("PASS", t.__name__)
        except Exception as e:
            failed += 1; print("FAIL", t.__name__, "->", repr(e))
    print(f"{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
