"""YARDS_SOURCE plumbing: both feeds' yardage rows survive the merge, ladder_board reads one feed and (for the Odds API) the fixed
book set, the usage log sums NFL credits month to date and trips the guard at the stated limits.

    python test_yards_source.py        # CI runs it in the "Tests and data" steps
"""
import json, os, tempfile
import odds as O
import yard_prices as YP


def _ev(book, key, rows, source=None):
    outs = [dict(name=n, description="Test Back", point=pt, price=pr, **({"source": source} if source else {})) for pt, n, pr in rows]
    return dict(away_team="Houston Texans", home_team="Jacksonville Jaguars", commence_time="2026-10-18T13:30:00Z", bookmakers=[],
                props=dict(bookmakers=[dict(key=book.lower(), title=book, markets=[dict(key=key, outcomes=outs)])]))


def test_ladder_board_reads_one_feed_and_the_fixed_books():
    """A pull carrying DraftKings rush rows from both feeds: ladder_board(source='sharpapi') sees only SharpAPI's, 'oddsapi' only the
       Odds API's; an off-list book's rows are ignored for the Odds API source and kept with books='all'."""
    main = [(63.5, "Over", -114), (63.5, "Under", -110)]
    alt_oa = [(49.5, "Over", -238), (79.5, "Over", 180)]
    alt_sa = [(29.5, "Over", -1020), (99.5, "Over", 413)]
    ev = _ev("DraftKings", "player_rush_yds", main)
    bk = ev["props"]["bookmakers"][0]
    bk["markets"].append(dict(key="player_rush_yds_alternate", outcomes=[dict(name=n, description="Test Back", point=p, price=pr, source="oddsapi") for p, n, pr in alt_oa]))
    bk["markets"].append(dict(key="player_rush_yds", outcomes=[dict(name=n, description="Test Back", point=p, price=pr, source="sharpapi") for p, n, pr in main]))
    bk["markets"].append(dict(key="player_rush_yds_alternate", outcomes=[dict(name=n, description="Test Back", point=p, price=pr, source="sharpapi") for p, n, pr in alt_sa]))
    ev["props"]["bookmakers"].append(dict(key="betonlineag", title="BetOnline.ag", markets=[dict(key="player_rush_yds", outcomes=[dict(name=n, description="Test Back", point=p, price=pr, source="oddsapi") for p, n, pr in main]),
                                                                                       dict(key="player_rush_yds_alternate", outcomes=[dict(name="Over", description="Test Back", point=39.5, price=-400, source="oddsapi")])]))
    os.environ.pop("YARD_BOOKS", None)                      # the research override must not leak into the test
    b_sa, _ = YP.ladder_board([ev], source="sharpapi")
    b_oa, _ = YP.ladder_board([ev], source="oddsapi")
    b_all, _ = YP.ladder_board([ev], source="oddsapi", books="all")
    lines = lambda b, book="DraftKings": sorted(l for (k, n, l), bks in b.items() if book in bks)
    assert lines(b_sa) == [29.5, 63.5, 99.5], lines(b_sa)
    assert lines(b_oa) == [49.5, 63.5, 79.5], lines(b_oa)
    assert lines(b_oa, "BetOnline.ag") == [], "off-list book ignored for the Odds API source"
    assert lines(b_all, "BetOnline.ag") == [39.5, 63.5], "books='all' keeps every book"
    assert "BetOnline.ag" not in YP.YARD_BOOKS and {"DraftKings", "FanDuel", "BetMGM"} <= set(YP.YARD_BOOKS)
    print(f"  sharpapi {lines(b_sa)} | oddsapi {lines(b_oa)} | fixed books {len(YP.YARD_BOOKS)}")


def test_fetch_markets_follow_the_flag():
    old = os.environ.get("YARDS_SOURCE")
    try:
        os.environ["YARDS_SOURCE"] = "oddsapi"; assert O.yards_source() == "oddsapi"
        os.environ["YARDS_SOURCE"] = "sharpapi"; assert O.yards_source() == "sharpapi"
        os.environ.pop("YARDS_SOURCE"); assert O.yards_source() == "sharpapi", "default is SharpAPI (not live)"
        assert set(O.YARD_MARKETS) == {"player_rush_yds", "player_reception_yds", "player_rush_yds_alternate", "player_reception_yds_alternate"}
    finally:
        if old is None: os.environ.pop("YARDS_SOURCE", None)
        else: os.environ["YARDS_SOURCE"] = old
    print("  flag default sharpapi; 4 yardage markets under oddsapi")


def test_go_live_week_gate():
    """YARDS_SOURCE=oddsapi with YARDS_FROM_WEEK=7: weeks before 7 price with SharpAPI, week 7 on with the Odds API; no gate when unset."""
    saved = {k: os.environ.get(k) for k in ("YARDS_SOURCE", "YARDS_FROM_WEEK")}
    try:
        os.environ["YARDS_SOURCE"] = "oddsapi"; os.environ["YARDS_FROM_WEEK"] = "7"
        assert O.yards_from_week() == 7
        assert O.yards_source(6) == "sharpapi" and O.yards_source(7) == "oddsapi" and O.yards_source(8) == "oddsapi"
        assert O.yards_source() == "oddsapi", "without a week the flag reads as set (run_week pins the env once the week is known)"
        os.environ.pop("YARDS_FROM_WEEK"); assert O.yards_source(1) == "oddsapi", "no gate when YARDS_FROM_WEEK is unset"
        os.environ["YARDS_FROM_WEEK"] = ""; assert O.yards_from_week() == 0
        os.environ["YARDS_SOURCE"] = "sharpapi"; os.environ["YARDS_FROM_WEEK"] = "7"; assert O.yards_source(9) == "sharpapi"
    finally:
        for k, v in saved.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
    print("  gate: week < YARDS_FROM_WEEK stays on SharpAPI")


def test_usage_summary_and_guard():
    import datetime as dt
    fd, fn = tempfile.mkstemp(suffix=".jsonl"); os.close(fd)
    now = dt.datetime(2026, 10, 25, 15, 0, tzinfo=dt.timezone.utc)
    rows = [dict(ts="2026-09-30T23:00:00Z", tag="nfl", path="/odds", cost=5000, used=5000, remaining=95000),        # last month: not counted
            dict(ts="2026-10-01T15:00:00Z", tag="nfl", path="/odds", cost=30000, used=35000, remaining=65000),
            dict(ts="2026-10-24T15:00:00Z", tag="pickem", path="/odds", cost=9000, used=44000, remaining=56000),     # other tag: not NFL
            dict(ts="2026-10-25T14:50:00Z", tag="nfl", path="/events/x/odds", cost=86, used=44086, remaining=55914)]
    with open(fn, "w") as f:
        for r in rows: f.write(json.dumps(r) + "\n")
    u = O.usage_summary(fn, now=now)
    assert u["nfl_mtd"] == 30086 and u["remaining"] == 55914 and not u["guard"]["tripped"], u
    with open(fn, "a") as f: f.write(json.dumps(dict(ts="2026-10-25T14:55:00Z", tag="nfl", path="/odds", cost=10000, used=54086, remaining=45914)) + "\n")
    u = O.usage_summary(fn, now=now)
    assert u["nfl_mtd"] == 40086 and u["guard"]["tripped"] and "40,086 > 40,000" in u["guard"]["reasons"][0], u
    with open(fn, "a") as f: f.write(json.dumps(dict(ts="2026-10-25T14:56:00Z", tag="pickem", path="/odds", cost=30000, used=84086, remaining=15914)) + "\n")
    u = O.usage_summary(fn, now=now)
    assert u["guard"]["tripped"] and any("15,914 < 20,000" in r for r in u["guard"]["reasons"]), u
    assert O.usage_summary(fn + ".missing", now=now)["nfl_mtd"] == 0
    os.remove(fn)
    print(f"  guard: limits {O.NFL_MTD_LIMIT:,} NFL month-to-date / {O.REMAINING_FLOOR:,} remaining")


def test_props_source_primary_markets_fallback_and_books():
    """PROPS_SOURCE=oddsapi: fetch_all asks for all 4 TD + 4 yardage markets (8 credits per event), counts per-event errors, and
       fallback_reason() fires on guard / no key / lines failure / props errors / no prop prices but not on a clean pull or an empty
       slate; only_books keeps the fixed set plus Kalshi; PROPS_FROM_WEEK gates like YARDS_FROM_WEEK."""
    saved = {k: os.environ.get(k) for k in ("PROPS_SOURCE", "PROPS_FROM_WEEK", "YARDS_SOURCE", "ODDS_GUARD_TRIPPED", "SHARPAPI_KEY")}
    try:
        for k in saved: os.environ.pop(k, None)
        assert O.props_source() == "sharpapi", "default: SharpAPI primary"
        os.environ["PROPS_SOURCE"] = "oddsapi"; os.environ["PROPS_FROM_WEEK"] = "6"
        assert O.props_source(5) == "sharpapi" and O.props_source(6) == "oddsapi" and O.props_source() == "oddsapi"
        import datetime as dt
        soon = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
        calls = []
        def fake(url, params):
            calls.append((url, params))
            if "/events/" not in url: return [dict(id="e1", commence_time=soon, home_team="Jacksonville Jaguars", away_team="Houston Texans", bookmakers=[]),
                                               dict(id="e2", commence_time=soon, home_team="Denver Broncos", away_team="Seattle Seahawks", bookmakers=[])]
            if "/events/e2/" in url: raise RuntimeError("boom")
            return dict(bookmakers=[dict(key="draftkings", title="DraftKings", markets=[])])
        ev = O.fetch_all(fetch=fake)
        assert set(calls[1][1]["markets"].split(",")) == set(O.PROP_MARKETS) | set(O.YARD_MARKETS) and len(set(O.PROP_MARKETS) | set(O.YARD_MARKETS)) == 8, calls[1]
        assert O.FETCH["events"] == 2 and O.FETCH["props_errors"] == 1 and O.FETCH["with_props"] == 1, O.FETCH
        assert O.fallback_reason(ev) == "1 of 2 Odds API props calls errored"
        O.FETCH["props_errors"] = 0
        assert O.fallback_reason(ev) is None, "clean pull: no fallback"
        for e in ev: e["props"] = {"bookmakers": []}
        assert O.fallback_reason(ev) == "Odds API returned no prop prices"
        assert O.fallback_reason([]) is None, "empty slate is not a failure"
        assert O.fallback_reason(None) == "no Odds API key"
        O.FETCH["lines_error"] = "HTTPError: 401"; assert "lines call failed" in O.fallback_reason(None)
        os.environ["ODDS_GUARD_TRIPPED"] = "1"; assert O.fallback_reason(ev) == "credit guard tripped"; os.environ.pop("ODDS_GUARD_TRIPPED")
        evs = [dict(props=dict(bookmakers=[dict(key="draftkings", title="DraftKings", markets=[]), dict(key="bovada", title="Bovada", markets=[]),
                                           dict(key="kalshi", title="Kalshi", markets=[]), dict(key="espnbet", title="ESPN BET", markets=[])]), bookmakers=[dict(key="bovada")])]
        kept = [b["title"] for b in O.only_books(evs, O.PRIMARY_BOOKS)[0]["props"]["bookmakers"]]
        assert kept == ["DraftKings", "Kalshi", "ESPN BET"], kept
        assert O.only_books(evs, O.PRIMARY_BOOKS)[0]["bookmakers"] == [dict(key="bovada")], "game lines are never filtered"
        assert tuple(O.PRIMARY_BOOKS) == tuple(YP.YARD_BOOKS) and len(O.PRIMARY_BOOKS) == 8
    finally:
        for k, v in saved.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
    print("  primary: 8 markets per event, fallback reasons, fixed books + Kalshi")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try: t(); print("PASS", t.__name__)
        except Exception as e: failed += 1; print("FAIL", t.__name__, "->", repr(e))
    print(f"{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
