"""Pull-loop windows from the week's kickoff times (nflverse schedule as refreshed by refresh.py, Eastern as listed -> UTC
through America/New_York, so DST is automatic).

    python pull_windows.py [--season 2026] [--week 7] [--lead-h 3] [--step-min 10] [--write pull_windows.json]

Default week: the next REG week with an unplayed game. One window per distinct kickoff, [kickoff - lead, kickoff]; a
kickoff whose window starts within one cadence step of the previous cluster's last kickoff joins that cluster (1 pm +
4:05/4:25 -> one loop; London 9:30 and 1 pm, 30 min apart, stay separate). A loop stops at its cluster's last kickoff. A
cluster longer than MAX_JOB_MIN runs as two chained jobs (GitHub's 6 h job cap). The JSON is read by the dispatcher
workflow (pull_dispatch.yml, stdlib only) and written by the scheduled runs (update.yml) so it is always a week ahead.
"""
import sys, json, datetime as dt
import pandas as pd

LEAD_H, STEP_MIN, MAX_JOB_MIN = 3, 10, 340          # 340 min: a job stops there and hands over to the chained second job (cap 360)


def clusters(kicks, lead_h=LEAD_H, step_min=STEP_MIN):
    """kicks: iterable of tz-aware UTC Timestamps (one per game; duplicates fine) -> list of dicts (start, end, kicks)."""
    out = []
    for k in sorted(set(kicks)):
        start = k - pd.Timedelta(hours=lead_h)
        if out and start <= out[-1]["end"] + pd.Timedelta(minutes=step_min):
            out[-1]["end"] = k; out[-1]["kicks"].append(k)
        else:
            out.append(dict(start=start, end=k, kicks=[k]))
    return out


import os
LINES_CREDITS = 2                             # Odds API: spreads+totals, once per pull
MARKETS_PER_EVENT = 8 if os.environ.get("PROPS_SOURCE", "").strip().lower() == "oddsapi" else 6   # per not-yet-kicked-off game: 4 TD + 4 yardage markets when The Odds API is primary, else 2 TD + 4 yardage (region us)
HORIZON_DAYS = 6                              # odds.fetch_all prices games up to 6 days out


def credits_for(cluster_start, cluster_end, kicks, step_min=STEP_MIN, markets=MARKETS_PER_EVENT):
    """Credits one loop spends: per pull, 2 for lines + markets x (games not yet kicked off and within the 6-day horizon)."""
    tot, t = 0, cluster_start
    while t <= cluster_end:
        live = sum(1 for k in kicks if t < k <= t + pd.Timedelta(days=HORIZON_DAYS))
        tot += LINES_CREDITS + markets * live; t += pd.Timedelta(minutes=step_min)
    return int(tot)


def week_windows(sched, season, week, lead_h=LEAD_H, step_min=STEP_MIN):
    s = sched[(sched.season == season) & (sched.week == week) & (sched.game_type == "REG")].copy()
    s["kick_utc"] = pd.to_datetime(s.gameday + " " + s.gametime.fillna("13:00")).dt.tz_localize("America/New_York").dt.tz_convert("UTC")
    cl = clusters(s.kick_utc, lead_h, step_min)
    rows = []
    for c in cl:
        games = s[s.kick_utc.isin(c["kicks"])]
        dur = (c["end"] - c["start"]).total_seconds() / 60
        rows.append(dict(start=c["start"].strftime("%Y-%m-%dT%H:%M:%SZ"), end=c["end"].strftime("%Y-%m-%dT%H:%M:%SZ"), minutes=int(dur),
                         pulls=int(dur // step_min) + 1, chained=bool(dur > MAX_JOB_MIN), odds_api_credits=credits_for(c["start"], c["end"], list(s.kick_utc), step_min),
                         kickoffs=[k.strftime("%Y-%m-%dT%H:%MZ") for k in c["kicks"]],
                         games=[f"{a}@{h}" for a, h in zip(games.away_team, games.home_team)]))
    return dict(season=int(season), week=int(week), lead_h=lead_h, step_min=step_min, max_job_min=MAX_JOB_MIN,
                odds_api_credits=sum(r["odds_api_credits"] for r in rows), early=early_pull_credits(rows, int(len(s))),
                scheduled=dict(pulls=4, credits=4 * (LINES_CREDITS + MARKETS_PER_EVENT * int(len(s)))), pickem=dict(credits=7 * int(len(s))),
                exchange=dict(markets_per_game=6, credits=6 * int(len(s)) * 5, note="us_ex (Novig, ProphetX, Polymarket, Kalshi): games with an edge, 4 scheduled pulls + 1 final pre-kickoff pull per game; <= 6 credits per game per pull"),
                credits_note=f"{LINES_CREDITS} for lines + {MARKETS_PER_EVENT} markets per game not yet kicked off, per pull ({'PROPS_SOURCE' if MARKETS_PER_EVENT == 8 else 'YARDS_SOURCE'}=oddsapi, region us)",
                generated=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), clusters=rows)


def early_pull_credits(clusters, n_games, hours_per_day=6, days=5, markets=None):
    """Early-week pulls (pull_dispatch: 8 AM-11 PM PT every 3 h, Tue-Sat): slots not inside a kickoff-loop window x (lines + markets x
       every game of the week). An upper bound: before props post a pull costs ~2 credits."""
    import datetime as dt
    from zoneinfo import ZoneInfo
    markets = MARKETS_PER_EVENT if markets is None else markets
    if not clusters: return dict(slots=0, credits=0)
    P = lambda s: dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    last = max(P(c["end"]) for c in clusters).astimezone(ZoneInfo("America/Los_Angeles"))
    # the week's Tuesday..Saturday before the last kickoff (Sunday/Monday games): slots at 8, 11, 14, 17, 20, 23 PT
    sunday = (last - dt.timedelta(days=(last.weekday() + 1) % 7)).replace(hour=0, minute=0, second=0, microsecond=0)
    slots = [(sunday - dt.timedelta(days=7 - d)).replace(hour=h) for d in range(2, 7) for h in (8, 11, 14, 17, 20, 23)]
    n = sum(1 for s in slots if not any(P(c["start"]) - dt.timedelta(minutes=30) <= s.astimezone(dt.timezone.utc) <= P(c["end"]) + dt.timedelta(minutes=10) for c in clusters))   # same padding as pull_dispatch.in_cluster
    return dict(slots=n, credits=int(n * (LINES_CREDITS + markets * n_games)))


def next_week(sched, now=None):
    now = now or pd.Timestamp.now(tz="UTC")
    s = sched[sched.game_type == "REG"].copy()
    s["kick_utc"] = pd.to_datetime(s.gameday + " " + s.gametime.fillna("13:00")).dt.tz_localize("America/New_York").dt.tz_convert("UTC")
    fut = s[s.home_score.isna() & (s.kick_utc > now)].sort_values("kick_utc")
    return (int(fut.season.iloc[0]), int(fut.week.iloc[0])) if len(fut) else None


if __name__ == "__main__":
    a = sys.argv[1:]; g = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    sched = pd.read_parquet("data/sched.parquet")
    if g("--week"): season, week = int(g("--season", 2026)), int(g("--week"))
    else:
        nw = next_week(sched)
        if not nw: raise SystemExit("no unplayed REG game in the schedule")
        season, week = nw
    w = week_windows(sched, season, week, float(g("--lead-h", LEAD_H)), int(g("--step-min", STEP_MIN)))
    print(f"{w['season']} week {w['week']}: {sum(len(c['games']) for c in w['clusters'])} games, {len(w['clusters'])} loop(s), {sum(c['pulls'] for c in w['clusters'])} pulls at {w['step_min']} min")
    for c in w["clusters"]:
        print(f"  {c['start'][:16]}Z -> {c['end'][:16]}Z  {c['minutes'] / 60:.2f} h, {c['pulls']} pulls, {len(c['games'])} games, ~{c['odds_api_credits']} Odds API credits with yardage" + ("  [chained: 2 jobs]" if c["chained"] else "") + " | " + ", ".join(c["games"]))
    print(f"  week total with The Odds API in every loop: ~{w['odds_api_credits']} credits ({w['credits_note']})")
    print(f"  early-week pulls (Tue-Sat, 8 AM-11 PM PT every 3 h, outside loop windows): {w['early']['slots']} pulls, up to ~{w['early']['credits']} credits; week total ~{w['odds_api_credits'] + w['early']['credits']}")
    if g("--write"):
        json.dump(w, open(g("--write"), "w"), indent=1); print("written", g("--write"))
