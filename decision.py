"""Decision-pull record (CEO ruling 7, 2026-10-29): the primary record grades only the edges on the capped card at a game's DECISION PULL,
at that pull's prices. decision_log/<season>_w<week>.json holds one record per game:

    rule "sunday_0923": Sunday early / late games (kickoff 12:00-17:59 Eastern on a Sunday). The decision pull is the first build at or
                        after 9:23 AM Pacific that day (rules.decision_rule) that is still outside the kickoff cutoff; when that first build
                        lands inside the cutoff (9:23 PT is only 37 min before a 1 PM ET kickoff) the last actionable build before 9:23
                        stands. `late_min` is the signed distance of the pull used from 9:23 PT.
    rule "final":       TNF / SNF / MNF / London / Saturday games. The decision pull is the last build before the kickoff cutoff
                        (rules.KICKOFF_CUTOFF_MIN): every pre-cutoff build overwrites the pending snapshot, the first build inside the
                        cutoff (or once the game is locked) freezes it.
    status: pending -> decided | missed (no build at the decision time before the cutoff: that game has no primary-record bets).

run_week.py calls update() on every build after the timing rules and the card cap (not under ODDS_MOCK / DRY_RUN); results.py grades the
decided games with grade(). The first-seen edge log (clv.log_edges) stays as the secondary research record. Slate-snapshot re-scoring
(results.rescore_edges on the slate's player rows) is no longer the primary record for weeks that have a decision log.
"""
import json, os
import pandas as pd
import rules as RU

DIR = "decision_log"      # run_week.py points this under OUT_DIR for replays
FIELDS = ("bet", "pid", "market", "kind", "line", "team", "game_id", "role", "group", "routed", "stake", "best", "book", "med", "nbooks", "mkt_p", "model_p",
          "blend_p", "ev", "ev_med", "rule", "bet_down_to", "captured", "cap_rank", "window", "alt", "listed", "ratio", "best_venue")
TD = ("any", "first", "two")


def path(season, week, d=None):
    return f"{d or DIR}/{season}_w{week}.json"


def load(season, week, d=None):
    fn = path(season, week, d)
    return json.load(open(fn)) if os.path.exists(fn) else {}


def save(log, season, week, d=None):
    fn = path(season, week, d)
    os.makedirs(os.path.dirname(fn), exist_ok=True)
    json.dump(log, open(fn, "w"), indent=0)
    return fn


def snapshot(e):
    return {k: e.get(k) for k in FIELDS}


def _iso(t):
    return pd.Timestamp(t).strftime("%Y-%m-%dT%H:%M:%SZ")


def update(log, games, card, now, stamp, cutoff_min=RU.KICKOFF_CUTOFF_MIN):
    """Advance the week's log one build. games: slate games (game_id, away, home, kick_utc, locked); card: the capped actionable edges
       (each with game_id); now: tz-aware UTC Timestamp of the build; stamp: the pull stamp the prices came from.
       Returns [(game_id, 'decided' | 'missed')] for the games that changed state this build."""
    changes = []
    by_game = {}
    for e in card:
        by_game.setdefault(e.get("game_id"), []).append(e)
    for g in games:
        gid = g["game_id"]
        rule, at = RU.decision_rule(g.get("kick_utc"))
        rec = log.setdefault(gid, dict(game_id=gid, away=g.get("away"), home=g.get("home"), kick_utc=g.get("kick_utc"), rule=rule, decision_at=at,
                                       status="pending", as_of=None, edges=[], n=0))
        if rec["status"] != "pending":
            continue
        km = RU.minutes_to_kick(g.get("kick_utc"), now)
        actionable = km is not None and km >= cutoff_min and not g.get("locked")
        rows = [snapshot(e) for e in by_game.get(gid, [])]
        if rule == "sunday_0923":
            if at is None or now < pd.Timestamp(at):
                if actionable:                                             # before 9:23 PT: keep the latest actionable build as the fallback snapshot
                    rec.update(as_of=stamp, seen_at=_iso(now), kick_min=round(km, 1), edges=rows, n=len(rows))
                continue
            late = float((now - pd.Timestamp(at)) / pd.Timedelta(minutes=1))
            if actionable:                                                 # the first build at or after 9:23 PT, still outside the kickoff cutoff
                rec.update(status="decided", as_of=stamp, decided_at=_iso(now), late_min=round(late, 1), kick_min=round(km, 1), edges=rows, n=len(rows))
                changes.append((gid, "decided"))
            elif rec.get("as_of"):                                         # 9:23 PT sits 37 min before a 1 PM ET kickoff: a 10-min loop can land inside the 30-min cutoff,
                early = float((pd.Timestamp(at) - pd.Timestamp(rec["seen_at"])) / pd.Timedelta(minutes=1))   # so the last actionable build before 9:23 stands
                rec.update(status="decided", decided_at=_iso(now), late_min=round(-early, 1),
                           reason=f"first build after {at[11:16]}Z was inside the kickoff cutoff ({cutoff_min} min): the last actionable build before it ({rec['seen_at'][11:16]}Z) stands")
                changes.append((gid, "decided"))
            else:
                rec.update(status="missed", decided_at=_iso(now), late_min=round(late, 1), edges=[], n=0,
                           reason=f"no actionable build at the {at[11:16]}Z decision time: the first build after it came inside the kickoff cutoff ({cutoff_min} min) or after kickoff")
                changes.append((gid, "missed"))
        else:
            if actionable:
                rec.update(as_of=stamp, seen_at=_iso(now), kick_min=round(km, 1), edges=rows, n=len(rows))      # the latest pre-cutoff build so far
            elif rec.get("as_of"):
                rec.update(status="decided", decided_at=_iso(now)); changes.append((gid, "decided"))
            else:
                rec.update(status="missed", decided_at=_iso(now), reason=f"no build before the kickoff cutoff ({cutoff_min} min)"); changes.append((gid, "missed"))
    return changes


def summary(log):
    """Per-game status for the page and the run log."""
    rows = []
    for gid, r in sorted(log.items(), key=lambda kv: (kv[1].get("kick_utc") or "", kv[0])):
        rows.append(dict(game_id=gid, away=r.get("away"), home=r.get("home"), kick_utc=r.get("kick_utc"), rule=r.get("rule"), decision_at=r.get("decision_at"),
                         status=r.get("status"), as_of=r.get("as_of"), late_min=r.get("late_min"), kick_min=r.get("kick_min"), n=r.get("n", len(r.get("edges") or [])),
                         reason=r.get("reason")))
    return dict(games=rows, decided=sum(1 for r in rows if r["status"] == "decided"), pending=sum(1 for r in rows if r["status"] == "pending"),
                missed=sum(1 for r in rows if r["status"] == "missed"), bets=sum(r["n"] for r in rows if r["status"] == "decided"))


def grade(log, season, week, tdhit, ymap, final_games=None):
    """Bet records (results.py shape) for the decided games. A game still 'pending' whose result is final counts as decided on its last
       pre-cutoff snapshot (no build ran inside the cutoff). tdhit: {(week, pid): (hit, first_hit, two_hit)}; ymap: {(week, pid, kind): yards}."""
    out = []
    for gid, r in log.items():
        st = r.get("status")
        if st == "missed" or (st == "pending" and not (final_games is not None and gid in final_games and r.get("as_of"))):
            continue
        for e in r.get("edges") or []:
            if e.get("market") in TD:
                h = tdhit.get((week, e["pid"]))
                if h is None: continue
                won = {"any": h[0], "first": h[1], "two": h[2]}[e["market"]]
            else:
                y = ymap.get((week, e["pid"], e.get("kind")))
                if y is None: continue
                won = y >= float(e["line"])
            price = int(e["best"]); dec = 1 + (price / 100 if price > 0 else 100 / -price); stake = float(e.get("stake") or 1.0)
            role = e.get("role") if isinstance(e.get("role"), str) and e.get("role") else "Unknown"
            out.append(dict(season=season, week=week, bet=e["bet"], pid=e["pid"], kind=e.get("kind"), line=e.get("line"), price=price, book=e.get("book"), ev=e.get("ev"), ev_med=e.get("ev_med"),
                            blend_p=e.get("blend_p"), model_p=e.get("model_p"), mkt_p=e.get("mkt_p"), won=bool(won), profit=round(stake * (dec - 1) if won else -stake, 3), stake=stake,
                            ratio=e.get("ratio"), role=role, group=e.get("group") or ("bench" if role == "Bench" else "main"), routed=e.get("routed"), market=e["market"],
                            longshot=price >= 1000, backfill_price=False, fitted=False, rule=e.get("rule"), decision_pull=r.get("as_of"), decision_rule=r.get("rule"), game_id=gid))
    return out
