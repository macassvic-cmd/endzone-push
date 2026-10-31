"""Pull-loop plumbing: kickoff clustering (pull_windows.py), first-seen log merge and the kept-pull rule (pull_loop.py).

    python test_pull_loop.py        # CI runs it in the "Tests and data" steps
"""
import pandas as pd
import pull_windows as PW
import pull_loop as PL

T = lambda s: pd.Timestamp(s, tz="UTC")


def test_clusters_merge_rule():
    """1 pm + 4:05 + 4:25 form one loop (the 4:05 lead-in starts 5 min after the 1 pm kickoff, within one step); London 9:30 stays
       separate (its last kickoff is 30 min before the 1 pm lead-in starts); SNF / MNF are their own loops."""
    kicks = [T("2026-10-25 13:30"), T("2026-10-25 17:00")] * 3 + [T("2026-10-25 20:05"), T("2026-10-25 20:25"), T("2026-10-26 00:20"), T("2026-10-27 00:15")]
    cl = PW.clusters(kicks, lead_h=3, step_min=10)
    spans = [(c["start"].strftime("%m-%d %H:%M"), c["end"].strftime("%m-%d %H:%M"), len(c["kicks"])) for c in cl]
    assert spans == [("10-25 10:30", "10-25 13:30", 1), ("10-25 14:00", "10-25 20:25", 3), ("10-25 21:20", "10-26 00:20", 1), ("10-26 21:15", "10-27 00:15", 1)], spans
    # a kickoff whose lead-in starts more than one step after the previous last kickoff does not merge
    cl2 = PW.clusters([T("2026-10-25 17:00"), T("2026-10-25 20:15")], lead_h=3, step_min=10)
    assert len(cl2) == 2, "a 15-min gap (one step is 10) keeps two loops"
    cl3 = PW.clusters([T("2026-10-25 17:00"), T("2026-10-25 20:10")], lead_h=3, step_min=10)
    assert len(cl3) == 1, "a 10-min gap (exactly one step) merges"
    print("  clusters:", spans)


def test_week_windows_chained_flag_and_dst():
    """A cluster over the job cap is flagged chained; Eastern -> UTC follows DST (Nov 8 1 pm ET = 18:00Z, Oct 25 1 pm ET = 17:00Z)."""
    rows = [dict(season=2026, week=7, game_type="REG", gameday="2026-10-25", gametime=t, away_team=a, home_team=h)
            for t, a, h in (("13:00", "SF", "ATL"), ("13:00", "CIN", "BAL"), ("16:05", "DEN", "ARI"), ("16:25", "GB", "DET"), ("20:20", "KC", "SEA"))]
    rows += [dict(season=2026, week=9, game_type="REG", gameday="2026-11-08", gametime="13:00", away_team="A", home_team="B")]
    s = pd.DataFrame(rows)
    w7 = PW.week_windows(s, 2026, 7)
    assert [c["chained"] for c in w7["clusters"]] == [True, False], w7["clusters"]
    assert w7["clusters"][0]["start"] == "2026-10-25T14:00:00Z" and w7["clusters"][0]["end"] == "2026-10-25T20:25:00Z"
    assert w7["clusters"][0]["pulls"] == 39 and w7["clusters"][1]["games"] == ["KC@SEA"]
    w9 = PW.week_windows(s, 2026, 9)
    assert w9["clusters"][0]["end"] == "2026-11-08T18:00:00Z", "after DST ends, 1 pm ET is 18:00Z"
    print("  chained flag and DST ok:", [(c["start"], c["end"], c["chained"]) for c in w7["clusters"]])


def test_merge_first_seen_and_kept_pulls():
    theirs = {"a": {"flagged": "1"}, "b": {"flagged": "1"}}
    ours = {"b": {"flagged": "2"}, "c": {"flagged": "2"}}
    m = PL.merge_first_seen(theirs, ours)
    assert m == {"a": {"flagged": "1"}, "b": {"flagged": "1"}, "c": {"flagged": "2"}}, "upstream wins on shared keys, our new keys are added"
    assert PL.merge_first_seen({}, ours) == ours and PL.merge_first_seen(theirs, {}) == theirs
    pulls = [f"odds_history/2026_w7_20261025T{h:02d}{m:02d}Z.json" for h in (14, 15) for m in (3, 13, 23, 33, 43, 53)] + ["odds_history/2026_w7_20261025T1603Z.json", "odds_history/2026_w7_20261025T1613Z.json"]
    keep = PL.select_pulls(pulls, 10)
    assert [k[-10:-5] for k in keep] == ["1403Z", "1503Z", "1603Z", "1613Z"], keep   # first of each hour + the newest
    assert PL.select_pulls([], 10) == []
    print("  merge and kept-pull rule ok")


def test_early_week_slots_and_cluster_skip():
    """Dispatcher early-week pulls: 8 AM-11 PM PT every 3 h on Tue-Sat only, live for 45 min after the slot, DST-aware; a slot inside a
       kickoff-loop window is skipped."""
    import datetime as dt
    import pull_dispatch as PD
    U = lambda s: dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    assert PD.early_slots(U("2026-10-20T15:10:00Z")) == [U("2026-10-20T15:00:00Z")], "Tue 8:10 AM PDT -> the 8 AM slot"
    assert PD.early_slots(U("2026-10-20T15:50:00Z")) == [], "45 min grace passed"
    assert PD.early_slots(U("2026-10-21T06:20:00Z")) == [U("2026-10-21T06:00:00Z")], "Tue 11 PM PDT = Wed 06:00Z"
    assert PD.early_slots(U("2026-10-19T15:10:00Z")) == [], "Monday: no early pulls"
    assert PD.early_slots(U("2026-10-25T15:10:00Z")) == [], "Sunday: kickoff loops only"
    assert PD.early_slots(U("2026-10-24T21:05:00Z")) == [U("2026-10-24T21:00:00Z")], "Sat 2 PM PDT"
    assert PD.early_slots(U("2026-11-03T16:10:00Z")) == [U("2026-11-03T16:00:00Z")], "after the DST change 8 AM PST = 16:00Z"
    clusters = [dict(start="2026-10-22T21:15:00Z", end="2026-10-23T00:15:00Z")]
    assert PD.in_cluster(U("2026-10-22T21:00:00Z"), clusters) and PD.in_cluster(U("2026-10-23T00:00:00Z"), clusters) and not PD.in_cluster(U("2026-10-22T18:00:00Z"), clusters)
    import pull_windows as PW
    e = PW.early_pull_credits(clusters + [dict(start="2026-10-25T14:00:00Z", end="2026-10-25T20:25:00Z")], 14, markets=8)
    assert e["slots"] == 28 and e["credits"] == 28 * (2 + 8 * 14), e      # 30 Tue-Sat slots minus the two inside Thursday's loop
    print(f"  early slots: Tue-Sat PT, 45-min grace, DST ok; {e['slots']} slots/week ~{e['credits']} credits at 14 games")


def test_changed_sees_files_in_a_new_log_directory():
    """2026-10-30/31: decision_log/ was a new directory, so `git status --porcelain` listed it as one "?? decision_log/" line, finalize() kept
       only paths ending in .json and the decision log was never committed to main (two once-runs, both "logs merged"). --untracked-files=all."""
    import subprocess, tempfile, os, json
    import pull_loop as PLP
    d = tempfile.mkdtemp(prefix="plp_"); cwd = os.getcwd()
    try:
        os.chdir(d)
        subprocess.run(["git", "init", "-q"], check=True)
        open("a.txt", "w").write("x"); subprocess.run(["git", "add", "a.txt"], check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"], check=True)
        os.makedirs("decision_log"); json.dump({}, open("decision_log/2026_w8.json", "w"))
        os.makedirs("edge_log"); json.dump({}, open("edge_log/2026_w8.json", "w"))
        got = PLP.changed(["edge_log", "research", "decision_log"])
        assert got == ["decision_log/2026_w8.json", "edge_log/2026_w8.json"], got
    finally:
        os.chdir(cwd)
    print("  changed(): files inside a new log directory are listed one by one")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try: t(); print("PASS", t.__name__)
        except Exception as e: failed += 1; print("FAIL", t.__name__, "->", repr(e))
    print(f"{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
