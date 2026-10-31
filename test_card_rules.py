"""CI checks for the 2026-10-29 consolidated build (fail on any violation; CI "Tests and data" step and the pre-push hook run this):

  1. an alt-rung edge is created at 0.25u (rules.ALT_STAKE), every role, at the source (run_week creation dict + rules.split_board);
  2. a first-TD edge priced +2000 or longer with model P > 2x the market routes to the bench section at 0.25u, every role;
  3. the card cap never exceeds 25 actionable bets per kickoff window and never allows two alt rungs of the same player-stat;
  4. no stored blend_p falls outside [min(model, market), max(model, market)]: the one blend function, every slate / latest.json /
     edge-log / decision-log row on disk;
  5. the decision rule (Sunday 9:23 AM PT for early / late games, DST-aware; final pre-cutoff pull otherwise) and the decision log's
     pending -> decided / missed transitions;
  6. the card-cap quota (CEO ruling 2026-10-30): in any window with 10+ actionable main-line yard edges the 10 highest-EV@median yard mains
     are all on the card; with fewer, every yard main is on the card and the card still reaches 25 when enough edges exist; alt rungs never
     claim floor slots; the cap stays <= 25 per window;
  7. flag invariance: the cap step is a pure partition. Re-running it on the pre-cap edge sets of the week 8 replay and the live board built by
     the pre-quota code (research/card_cap_quota_invariance_2026_w8.json) gives back the same rows byte for byte (sha256), only cap_rank /
     over_cap / quota may differ.

    python test_card_rules.py
"""
import glob, json, os, re
import pandas as pd
import rules as RU, blend as BL, decision as DEC

TOL = 1e-6
ONE_RULER_FROM_WEEK = 7   # the 50/50 pin landed 2026-10-17, mid-week 6: weeks 4-6 keep fitted-era blends in their frozen slates / edge logs (reported, never rewritten)


def _edge(**kw):
    e = dict(bet="X Y Anytime TD", pid="p", market="any", team="KC", model_p=0.2, mkt_p=0.1, blend_p=0.15, best=800, book="DK", ev=0.2, med=750, ev_med=0.15, nbooks=4,
             role="Starter", kick_utc="2026-11-01T18:00Z")
    e.update(kw); return e


def test_alt_rungs_created_at_quarter_unit_every_role():
    for role in ("Starter", "Rotational", "Bench", "Unknown"):
        assert RU.stake_for(role, "yds_alt") == RU.ALT_STAKE == 0.25, (role, RU.stake_for(role, "yds_alt"))
        parts = RU.split_board([_edge(market="yds_alt", kind="rush", line=49.5, role=role, alt=True)])
        e = (parts["main"] + parts["bench"])[0]
        assert e["stake"] == 0.25, (role, e["stake"])
    assert RU.stake_for("Starter", "yds") == 1.0 and RU.stake_for("Rotational", "any") == 1.0 and RU.stake_for("Bench", "any") == RU.BENCH_STAKE
    # the creation code in run_week.py stamps the stake itself (ruling 5: at the source, not re-labelled later)
    src = open("run_week.py", encoding="utf-8").read()
    assert "stake=RU.ALT_STAKE" in src, "run_week.py must create alt-rung edges with stake=RU.ALT_STAKE"
    # on-disk builds made under the rule carry it on every alt rung
    for fn in ["latest.json"] + sorted(glob.glob("slate_*_w*.json")):
        if not os.path.exists(fn): continue
        d = json.load(open(fn))
        if (d.get("rules") or {}).get("alt_stake") is None: continue           # built before the rule
        bad = [e["bet"] for e in d.get("edges", []) if e.get("market") == "yds_alt" and float(e.get("stake") or 1) != 0.25]
        assert not bad, (fn, bad[:5])
    print("  alt rungs: 0.25u at creation for every role")


def test_first_td_longshots_route_to_bench_at_quarter_unit():
    for role in ("Starter", "Rotational", "Bench"):
        e = _edge(bet="X Y First TD", market="first", best=2000, model_p=0.06, mkt_p=0.025, role=role)
        parts = RU.split_board([e])
        assert parts["bench"] and not parts["main"], (role, parts)
        b = parts["bench"][0]
        assert b["stake"] == 0.25 and b["group"] == "bench" and b["role"] == role, b
        assert (b["routed"] == "first-TD longshot") == (role != "Bench"), b
    # not routed: shorter than +2000, or model <= 2x market, or another market
    for kw in (dict(best=1900, model_p=0.06, mkt_p=0.025), dict(best=2500, model_p=0.05, mkt_p=0.025), dict(best=2500, model_p=0.06, mkt_p=0.025, market="any")):
        e = _edge(market="first", role="Starter"); e.update(kw)
        parts = RU.split_board([e])
        assert parts["main"] and parts["main"][0]["stake"] == 1.0 and parts["main"][0]["routed"] is None, (kw, parts)
    print("  first-TD +2000 with model > 2x market -> bench at 0.25u, every role")


def test_card_cap_per_window_and_one_alt_rung_per_player_stat():
    rows = []
    for i in range(40):                                                         # 40 TD edges in the Sunday window
        rows.append(_edge(bet=f"P{i} Anytime TD", pid=f"p{i}", ev_med=0.30 - i * 0.005, ev=0.35 - i * 0.005))
    for line in (39.5, 49.5, 59.5):                                             # three alt rungs on the same player-stat
        rows.append(_edge(bet=f"Q Over {line} rush yds (alt)", pid="q", market="yds_alt", kind="rush", line=line, ev_med=0.40 + line / 1000, ev=0.41, alt=True, stake=0.25))
    rows.append(_edge(bet="Q Over 29.5 rec yds (alt)", pid="q", market="yds_alt", kind="rec", line=29.5, ev_med=0.39, ev=0.40, alt=True, stake=0.25))   # other stat: allowed
    for i in range(30):                                                         # 30 edges in the SNF window
        rows.append(_edge(bet=f"S{i} Anytime TD", pid=f"s{i}", kick_utc="2026-11-02T01:20Z", ev_med=0.20 - i * 0.004, ev=0.22 - i * 0.004))
    clusters = [dict(start="2026-11-01T15:00:00Z", end="2026-11-01T21:25:00Z"), dict(start="2026-11-01T22:20:00Z", end="2026-11-02T01:20:00Z")]
    card, dropped = RU.apply_card_cap([dict(r) for r in rows], clusters)
    by_w = {}
    for e in card: by_w.setdefault(e["window"], []).append(e)
    assert set(by_w) == {"2026-11-01T15:00:00Z..2026-11-01T21:25:00Z", "2026-11-01T22:20:00Z..2026-11-02T01:20:00Z"}, set(by_w)
    for w, g in by_w.items():
        assert len(g) <= RU.CARD_CAP == 25, (w, len(g))
        alts = [(e["pid"], e["kind"]) for e in g if e["market"] == "yds_alt"]
        assert len(alts) == len(set(alts)), ("two rungs of one player-stat on the card", alts)
        assert [e["cap_rank"] for e in g] == list(range(1, len(g) + 1))
        assert all(g[i]["ev_med"] >= g[i + 1]["ev_med"] for i in range(len(g) - 1)), "ranked by EV at the median book"
    sun = by_w["2026-11-01T15:00:00Z..2026-11-01T21:25:00Z"]
    assert len(sun) == 25 and sun[0]["bet"] == "Q Over 59.5 rush yds (alt)", [e["bet"] for e in sun[:3]]       # the highest-EV rung of the player-stat kept
    assert len(by_w["2026-11-01T22:20:00Z..2026-11-02T01:20:00Z"]) == 25
    kinds = {e["cap"] for e in dropped}
    assert kinds == {"alt", "card"} and sum(1 for e in dropped if e["cap"] == "alt") == 2 and sum(1 for e in dropped if e["cap"] == "card") == 17 + 5, kinds
    assert all(e["reasons"] for e in dropped)
    assert len(card) + len(dropped) == len(rows), "every edge is either on the card or dropped with a reason"
    # the live board file, when built under the rule, respects both limits
    if os.path.exists("latest.json"):
        d = json.load(open("latest.json"))
        if (d.get("rules") or {}).get("card_cap"):
            per = {}
            for e in d.get("edges", []):
                if e.get("window"): per.setdefault(e["window"], []).append(e)
            for w, g in per.items():
                assert len(g) <= d["rules"]["card_cap"], (w, len(g))
                alts = [(e["pid"], e.get("kind")) for e in g if e.get("market") == "yds_alt"]
                assert len(alts) == len(set(alts)), (w, alts)
    print("  card cap: <= 25 per kickoff window, one alt rung per player-stat, EV at the median within the card")


def _check_bounds(label, m, k, b):
    lo, hi = min(m, k), max(m, k)
    assert lo - TOL <= b <= hi + TOL, (label, m, k, b)


def test_blend_is_between_model_and_market_everywhere():
    import numpy as np
    rng = np.random.default_rng(0)
    pm, pk = rng.uniform(0.001, 0.999, 500), rng.uniform(0.001, 0.999, 500)
    b = BL.blend(pm, pk)
    assert np.all(b >= np.minimum(pm, pk) - TOL) and np.all(b <= np.maximum(pm, pk) + TOL)
    assert np.allclose(BL.predict(None, pm, pk), b), "predict(None, ...) is the same function"
    assert BL.coef_for(BL.load(), "any") is None and BL.coef_for(BL.load(), "first") is None and BL.coef_for(BL.load(), "two") is None, "fitted TD blends pinned to 50/50 (ratified 2026-10-29)"
    n, legacy = 0, 0
    def week_of(fn, d=None):
        m = re.search(r"_w(\d+)\.json$", fn)
        return int(m.group(1)) if m else int((d or {}).get("week") or 99)
    for fn in ["latest.json"] + sorted(glob.glob("slate_*_w*.json")):
        if not os.path.exists(fn): continue
        d = json.load(open(fn))
        if week_of(fn, d) < ONE_RULER_FROM_WEEK:
            legacy += sum(1 for e in d.get("edges", []) if e.get("blend_p") is not None and e.get("model_p") is not None and e.get("mkt_p") is not None
                          and not (min(e["model_p"], e["mkt_p"]) - TOL <= e["blend_p"] <= max(e["model_p"], e["mkt_p"]) + TOL))
            continue
        for e in d.get("edges", []) + d.get("held", []) + d.get("over_cap", []):
            if e.get("blend_p") is not None and e.get("model_p") is not None and e.get("mkt_p") is not None:
                _check_bounds((fn, e.get("bet")), float(e["model_p"]), float(e["mkt_p"]), float(e["blend_p"])); n += 1
        for p in d.get("players", []):
            for pre, pcol in (("any_", "p_any"), ("first_", "p_first"), ("two_", "p_2plus")):
                b_, m_, k_ = p.get(pre + "blend_p"), p.get(pcol), p.get(pre + "mkt_p")
                if b_ is not None and m_ is not None and k_ is not None:
                    _check_bounds((fn, p.get("name"), pre), float(m_), float(k_), float(b_)); n += 1
        for y in d.get("yards", []):
            for g in y.get("rungs", []):
                if g.get("blend") is not None and g.get("mkt_p") is not None: _check_bounds((fn, y.get("name"), g.get("line")), float(g["p"]), float(g["mkt_p"]), float(g["blend"])); n += 1
                if g.get("curve_blend") is not None and g.get("curve_p") is not None: _check_bounds((fn, y.get("name"), g.get("line"), "curve"), float(g["p"]), float(g["curve_p"]), float(g["curve_blend"])); n += 1
    for fn in sorted(glob.glob("edge_log/*_w*.json")) + sorted(glob.glob("decision_log/*_w*.json")):
        if week_of(fn) < ONE_RULER_FROM_WEEK: continue
        d = json.load(open(fn))
        rows = d.values() if "edge_log" in fn else [e for r in d.values() for e in (r.get("edges") or [])]
        for e in rows:
            if e.get("blend_p") is not None and e.get("model_p") is not None and e.get("mkt_p") is not None:
                _check_bounds((fn, e.get("bet")), float(e["model_p"]), float(e["mkt_p"]), float(e["blend_p"])); n += 1
    print(f"  blend bounds: {n} stored blends on disk (week {ONE_RULER_FROM_WEEK}+) inside [min(model, market), max(model, market)]; {legacy} fitted-era edge rows before that, untouched")


def test_decision_rule_and_log():
    assert RU.decision_rule("2026-11-01T18:00Z") == ("sunday_0923", "2026-11-01T17:23:00Z"), RU.decision_rule("2026-11-01T18:00Z")       # 1 PM ET, PST day
    assert RU.decision_rule("2026-11-01T21:25Z") == ("sunday_0923", "2026-11-01T17:23:00Z")                                                 # 4:25 PM ET
    assert RU.decision_rule("2026-10-25T17:00Z") == ("sunday_0923", "2026-10-25T16:23:00Z")                                                 # 1 PM EDT, PDT day
    for k in ("2026-10-30T00:15Z", "2026-11-02T01:20Z", "2026-11-03T01:15Z", "2026-10-25T13:30Z", "2026-12-19T21:30Z"):                     # TNF, SNF, MNF, London, Saturday
        assert RU.decision_rule(k) == ("final", None), (k, RU.decision_rule(k))
    assert RU.decision_rule(None) == ("final", None)
    games = [dict(game_id="2026_08_BAL_BUF", away="BAL", home="BUF", kick_utc="2026-11-01T18:00Z"), dict(game_id="2026_08_PHI_WAS", away="PHI", home="WAS", kick_utc="2026-11-02T01:20Z")]
    sun = _edge(bet="A Anytime TD", pid="a", team="BUF", game_id="2026_08_BAL_BUF", cap_rank=1, stake=1.0)
    snf = _edge(bet="B Anytime TD", pid="b", team="WAS", game_id="2026_08_PHI_WAS", kick_utc="2026-11-02T01:20Z", cap_rank=1, stake=1.0)
    log = {}
    t = pd.Timestamp("2026-11-01T17:13:00Z")                                                                        # before 9:23 PT: pending, fallback snapshot kept
    assert DEC.update(log, games, [sun, snf], t, "20261101T1713Z") == [] and log["2026_08_BAL_BUF"]["status"] == "pending" and log["2026_08_BAL_BUF"]["as_of"] == "20261101T1713Z"
    assert log["2026_08_PHI_WAS"]["as_of"] == "20261101T1713Z" and log["2026_08_PHI_WAS"]["status"] == "pending"   # final-rule game: pending snapshot
    t = pd.Timestamp("2026-11-01T17:25:00Z")                                                                        # first build after 9:23 PT, 35 min out -> decided, 2 min late
    ch = DEC.update(log, games, [dict(sun, best=820), snf], t, "20261101T1725Z")
    assert ch == [("2026_08_BAL_BUF", "decided")] and log["2026_08_BAL_BUF"]["as_of"] == "20261101T1725Z" and log["2026_08_BAL_BUF"]["late_min"] == 2.0
    assert log["2026_08_BAL_BUF"]["edges"][0]["best"] == 820, "at that pull's prices"
    ch = DEC.update(log, games, [dict(sun, best=900), snf], pd.Timestamp("2026-11-01T17:43:00Z"), "20261101T1743Z")   # later builds never rewrite it
    assert ch == [] and log["2026_08_BAL_BUF"]["edges"][0]["best"] == 820
    ch = DEC.update(log, games, [], pd.Timestamp("2026-11-02T00:55:00Z"), "20261102T0055Z")                         # SNF inside the cutoff: the last pre-cutoff snapshot freezes
    assert ch == [("2026_08_PHI_WAS", "decided")] and log["2026_08_PHI_WAS"]["as_of"] == "20261101T1743Z"
    log2 = {}                                                                                                       # the post-9:23 build lands inside the 30-min cutoff (17:33Z, 27 min out):
    DEC.update(log2, games[:1], [sun], pd.Timestamp("2026-11-01T17:13:00Z"), "20261101T1713Z")                     # the last actionable build before 9:23 stands, 10 min early
    ch = DEC.update(log2, games[:1], [dict(sun, best=820)], pd.Timestamp("2026-11-01T17:33:00Z"), "20261101T1733Z")
    assert ch == [("2026_08_BAL_BUF", "decided")] and log2["2026_08_BAL_BUF"]["as_of"] == "20261101T1713Z" and log2["2026_08_BAL_BUF"]["late_min"] == -10.0
    assert log2["2026_08_BAL_BUF"]["edges"][0]["best"] == 800 and log2["2026_08_BAL_BUF"]["reason"]
    log3 = {}
    ch = DEC.update(log3, games[:1], [sun], pd.Timestamp("2026-11-01T17:45:00Z"), "x")                                # no build at all before the cutoff = missed
    assert ch == [("2026_08_BAL_BUF", "missed")] and log3["2026_08_BAL_BUF"]["n"] == 0
    tdhit = {(8, "a"): (True, False, False), (8, "b"): (False, False, False)}
    bets = DEC.grade(log, 2026, 8, tdhit, {}, final_games={"2026_08_BAL_BUF", "2026_08_PHI_WAS"})
    assert {(b["bet"], b["won"], b["price"]) for b in bets} == {("A Anytime TD", True, 820), ("B Anytime TD", False, 800)}, bets
    assert all(b["decision_pull"] for b in bets)
    s = DEC.summary(log)
    assert s["decided"] == 2 and s["bets"] == 2 and s["missed"] == 0
    print("  decision rule: Sunday 9:23 PT (DST-aware) / final pre-cutoff pull; log pending -> decided | missed, prices frozen at the decision pull")


def _yard(i, ev_med, kind="rush", **kw):
    e = _edge(bet=f"Y{i} Over {50.5 + i} {kind} yds", pid=f"y{i}", market="yds", kind=kind, line=50.5 + i, best=-110, med=-115, ev=ev_med + 0.01, ev_med=ev_med, nbooks=3, stake=1.0, blend_p=0.55)
    e.update(kw); return e


def _by_window(card):
    by_w = {}
    for e in card: by_w.setdefault(e["window"], []).append(e)
    return by_w


SUN = "2026-11-01T15:00:00Z..2026-11-01T21:25:00Z"
CLUSTERS = [dict(start="2026-11-01T15:00:00Z", end="2026-11-01T21:25:00Z"), dict(start="2026-11-01T22:20:00Z", end="2026-11-02T01:20:00Z")]


def test_yard_floor_honored_and_alts_do_not_count():
    assert RU.CARD_YARD_FLOOR == 10 and RU.CARD_CAP == 25
    rows = [_edge(bet=f"P{i} Anytime TD", pid=f"p{i}", ev_med=0.30 - i * 0.005, ev=0.35 - i * 0.005) for i in range(40)]          # 40 TD edges, EV 0.105-0.30
    rows += [_yard(i, 0.05 + i * 0.002) for i in range(15)]                                                                         # 15 yard mains, EV 0.050-0.078: all below every TD edge
    rows += [_edge(bet=f"A{i} Over 29.5 rec yds (alt)", pid=f"a{i}", market="yds_alt", kind="rec", line=29.5, ev_med=0.09 + i * 0.001, ev=0.1, alt=True, stake=0.25) for i in range(12)]   # 12 alt rungs, EV 0.090-0.101
    card, dropped = RU.apply_card_cap([dict(r) for r in rows], CLUSTERS)
    sun = _by_window(card)[SUN]
    assert len(sun) == 25 and len(card) + len(dropped) == len(rows)
    mains_on = sorted(e["pid"] for e in sun if e["market"] == "yds")
    top10 = sorted(e["pid"] for e in sorted((r for r in rows if r["market"] == "yds"), key=lambda r: -r["ev_med"])[:10])
    assert mains_on == top10, ("the 10 highest-EV@median yard mains are all on the card", mains_on, top10)
    assert all(e["quota"] == "yard_floor" for e in sun if e["market"] == "yds"), "on the card only by the floor (every one ranks past 25 by EV alone)"
    assert sum(1 for e in sun if e["market"] == "any") == 15 and not any(e["market"] == "yds_alt" for e in sun), "alt rungs claim no floor slot: the 15 free slots go by EV (all TD edges here)"
    assert [e["cap_rank"] for e in sun] == list(range(1, 26)) and all(sun[i]["ev_med"] >= sun[i + 1]["ev_med"] for i in range(24)), "cap_rank = EV@median order within the card"
    over = [e for e in dropped if e["window"] == SUN]
    assert sorted(e["cap_rank"] for e in over) == list(range(26, 26 + len(over))) and all(e["cap"] == "card" for e in over)
    assert sum(1 for e in over if e["market"] == "yds") == 5 and all("yard floor" in e["reasons"][0] for e in over), over[0]["reasons"]
    # a yard main that makes the card by EV alone is not a floor row
    card2, _ = RU.apply_card_cap([_yard(99, 0.50)] + [dict(r) for r in rows], CLUSTERS)
    y = [e for e in card2 if e["pid"] == "y99"][0]
    assert y["cap_rank"] == 1 and y["quota"] is None
    print("  yard floor: the 10 highest-EV@median main-line yard edges hold their slots; alt rungs never claim one; ranks by EV within the card")


def test_yard_floor_backfill_fills_the_card():
    rows = [_edge(bet=f"P{i} Anytime TD", pid=f"p{i}", ev_med=0.30 - i * 0.005, ev=0.35 - i * 0.005) for i in range(40)]
    rows += [_yard(i, 0.05 + i * 0.002) for i in range(4)]                                                                          # 4 yard mains < the floor of 10
    card, dropped = RU.apply_card_cap([dict(r) for r in rows], CLUSTERS)
    sun = _by_window(card)[SUN]
    assert len(sun) == 25, "unused floor slots flow back: the card still fills to 25"
    assert sorted(e["pid"] for e in sun if e["market"] == "yds") == ["y0", "y1", "y2", "y3"], "every yard main is on the card"
    assert sorted(int(e["pid"][1:]) for e in sun if e["market"] == "any") == list(range(21)), "the 21 free slots go to the 21 highest-EV TD edges"
    assert len([e for e in dropped if e["window"] == SUN]) == 19
    # a thin window: everything fits, nothing dropped, no quota tag
    small = [_edge(bet=f"S{i} Anytime TD", pid=f"s{i}", kick_utc="2026-11-02T01:20Z", ev_med=0.2 - i * 0.01) for i in range(3)] + [_yard(i, 0.06, kick_utc="2026-11-02T01:20Z") for i in range(2)]
    card, dropped = RU.apply_card_cap([dict(r) for r in small], CLUSTERS)
    assert len(card) == 5 and not dropped and all(e["quota"] is None for e in card) and [e["cap_rank"] for e in card] == [1, 2, 3, 4, 5]
    # the cap and the alt-rung rule still bind with the floor in play: 12 yard mains + 3 rungs of one player-stat + 30 TD edges
    rows = [_yard(i, 0.20 - i * 0.005) for i in range(12)] + [_edge(bet=f"P{i} Anytime TD", pid=f"p{i}", ev_med=0.15 - i * 0.003) for i in range(30)]
    rows += [_edge(bet=f"Q Over {ln} rush yds (alt)", pid="q", market="yds_alt", kind="rush", line=ln, ev_med=0.4 + ln / 1000, alt=True, stake=0.25) for ln in (39.5, 49.5, 59.5)]
    card, dropped = RU.apply_card_cap([dict(r) for r in rows], CLUSTERS)
    sun = _by_window(card)[SUN]
    assert len(sun) == 25 and sum(1 for e in sun if e["market"] == "yds") == 12, "yard mains past the floor compete in the pool on EV (here they win it)"
    assert [e["pid"] for e in sun if e["market"] == "yds_alt"] == ["q"] and sun[0]["line"] == 59.5 and sum(1 for e in dropped if e["cap"] == "alt") == 2
    print("  yard floor backfill: fewer than 10 yard mains -> all on the card and the card still reaches 25; cap and alt-rung rule unchanged")


CAP_FIELDS = ("cap_rank", "window", "cap", "reasons", "quota")


def _strip(rows):
    return sorted(({k: v for k, v in r.items() if k not in CAP_FIELDS} for r in rows), key=lambda r: (r.get("bet") or "", r.get("market") or "", r.get("book") or "", r.get("best") or 0, r.get("line") or 0))


def _sha(rows):
    import hashlib
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def test_cap_is_a_pure_partition_flag_invariant():
    import copy
    # synthetic: whatever goes in comes out, byte for byte, once the cap fields are stripped
    rows = [_edge(bet=f"P{i} Anytime TD", pid=f"p{i}", ev_med=0.30 - i * 0.005) for i in range(40)] + [_yard(i, 0.05 + i * 0.002) for i in range(15)]
    card, dropped = RU.apply_card_cap(copy.deepcopy(rows), CLUSTERS)
    assert _sha(_strip(card + dropped)) == _sha(_strip(rows))
    # the week 8 pre-cap sets built by the pre-quota code: the replay of the saved 15:33Z pull and the live 06:27Z board
    fx = json.load(open("research/card_cap_quota_invariance_2026_w8.json", encoding="utf-8"))
    for name, st in fx["sets"].items():
        before = _strip(st["rows"])
        assert _sha(before) == st["sha256_before"], (name, "fixture rows do not match their recorded sha")
        card, dropped = RU.apply_card_cap(copy.deepcopy(st["rows"]), fx["clusters"])
        after = _strip(card + dropped)
        assert json.dumps(after, sort_keys=True, ensure_ascii=False) == json.dumps(before, sort_keys=True, ensure_ascii=False), (name, "the flagged edge set changed through the cap step")
        assert _sha(after) == st["sha256_before"] and len(after) == st["n"]
        for w, g in _by_window(card).items():
            assert len(g) <= RU.CARD_CAP, (name, w, len(g))
            mains = sorted((e for e in dropped + card if e["window"] == w and e["market"] == "yds"), key=lambda e: -e["ev_med"])
            on = {e["bet"] for e in g if e["market"] == "yds"}
            assert all(e["bet"] in on for e in mains[:RU.CARD_YARD_FLOOR]), (name, w, "a top-10 yard main is over the cap")
        print(f"  flag invariance ({name}): {len(after)} pre-cap edges byte-identical through the cap step (sha256 {st['sha256_before'][:12]}...), card {len(card)} / dropped {len(dropped)}")
    # the live board file: re-running the cap on its own pre-cap set reproduces the published card exactly
    if os.path.exists("latest.json"):
        d = json.load(open("latest.json", encoding="utf-8"))
        if (d.get("rules") or {}).get("card_yard_floor"):
            pre = d.get("edges", []) + d.get("over_cap", [])
            card, dropped = RU.apply_card_cap(copy.deepcopy(pre), json.load(open("pull_windows.json")).get("clusters") if os.path.exists("pull_windows.json") else None)
            assert _sha(_strip(card + dropped)) == _sha(_strip(pre))
            assert {(e["bet"], e["book"], e["best"]) for e in card} == {(e["bet"], e["book"], e["best"]) for e in d.get("edges", [])}, "latest.json card is what the rule gives"
    assert "quota" in DEC.FIELDS, "the decision snapshot keeps the floor tag"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests: t()
    print(f"PASS test_card_rules: {len(tests)}/{len(tests)}")
