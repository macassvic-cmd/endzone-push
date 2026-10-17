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


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try: t(); print("PASS", t.__name__)
        except Exception as e: failed += 1; print("FAIL", t.__name__, "->", repr(e))
    print(f"{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
