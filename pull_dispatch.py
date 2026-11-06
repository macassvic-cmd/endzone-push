"""Dispatcher (pull_dispatch.yml, every 30 min, stdlib only).
1. Kickoff loops: start the `Pull loop` workflow for every cluster in pull_windows.json whose loop starts within the next LEAD_MIN
   minutes (or should already have started) and has no queued / running / successfully finished run yet. Firing every 30 min is the
   backup: a late or skipped dispatcher run is covered by the next one, and a loop started late begins at once (start = now).
2. Early-week pulls (2026-10-18): one `once` pull (pull + publish + first-seen logs + Discord alert) at every EARLY_HOURS_PT slot
   (8 AM-11 PM PT every 3 h) on Tuesday-Saturday PT, from the first slot after the dispatcher sees the slot until EARLY_GRACE_MIN
   after it; skipped when the slot falls inside a kickoff-loop window (the loop is already pulling every 10 min). Idempotent via the
   run title label "early <slot>". Before props post a pull costs ~2 credits (lines only), so "from the day props first post" is
   automatic. Needs GH_TOKEN with actions: write.

    python pull_dispatch.py [--windows pull_windows.json] [--lead-min 90] [--dry-run] [--now ISO]
"""
import sys, json, subprocess, datetime as dt
from zoneinfo import ZoneInfo

LEAD_MIN = 90
EARLY_HOURS_PT, EARLY_DAYS, EARLY_GRACE_MIN = (8, 11, 14, 17, 20, 23), (1, 2, 3, 4, 5), 45   # Tue..Sat (Mon = 0); a slot is live for 45 min
PT, UTC = ZoneInfo("America/Los_Angeles"), dt.timezone.utc
P = lambda s: dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
ACTIVE = ("queued", "in_progress", "waiting", "requested", "pending")


def early_slots(now, hours=EARLY_HOURS_PT, days=EARLY_DAYS, grace_min=EARLY_GRACE_MIN):
    """UTC datetimes of the early-week slots that are live at `now` (slot <= now < slot + grace), Tue-Sat Pacific time, DST-aware."""
    pt = now.astimezone(PT); out = []
    for day in (pt - dt.timedelta(days=1), pt):
        for h in hours:
            s = day.replace(hour=h, minute=0, second=0, microsecond=0)
            if s.weekday() not in days: continue
            s_utc = s.astimezone(UTC); age = (now - s_utc).total_seconds() / 60
            if 0 <= age < grace_min: out.append(s_utc)
    return sorted(set(out))


def in_cluster(t, clusters, pad_before_min=30, pad_after_min=10):
    """True when t falls inside a kickoff-loop window, padded: a loop starting within 30 min covers the slot, and the loop's last pull
       covers 10 min past its end."""
    return any(P(c["start"]) - dt.timedelta(minutes=pad_before_min) <= t <= P(c["end"]) + dt.timedelta(minutes=pad_after_min) for c in clusters)


def gh_runs():
    r = subprocess.run(["gh", "run", "list", "--workflow", "pull_loop.yml", "--limit", "100", "--json", "displayTitle,status,conclusion,createdAt"], capture_output=True, text=True)
    return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else []


def main(argv):
    a = argv; g = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    dry = "--dry-run" in a; lead = int(g("--lead-min") or LEAD_MIN); fn = g("--windows", "pull_windows.json")
    now = P(g("--now")) if g("--now") else dt.datetime.now(UTC)
    w = json.load(open(fn))
    print(f"{fn}: {w['season']} week {w['week']}, generated {w['generated']}, {len(w['clusters'])} cluster(s); now {now:%Y-%m-%dT%H:%MZ}")
    runs = gh_runs()
    live = [c for c in w["clusters"] if P(c["end"]) > now]
    if not live: print("every cluster is in the past: the windows file is stale until the next scheduled run rewrites it (Tue/Thu/Sat/Sun)")
    for c in live:
        start, end = P(c["start"]), P(c["end"])
        if (start - now).total_seconds() / 60 > lead:
            print(f"  {c['start']} -> {c['end']}: starts in {(start - now).total_seconds() / 60:.0f} min, not yet"); continue
        mine = [x for x in runs if f"-> {c['end']}" in (x.get("displayTitle") or "")]
        active = [x for x in mine if x["status"] in ACTIVE]
        done_ok = [x for x in mine if x["status"] == "completed" and x.get("conclusion") == "success"]
        if active: print(f"  {c['start']} -> {c['end']}: already {active[0]['status']}"); continue
        if done_ok: print(f"  {c['start']} -> {c['end']}: a run already completed successfully"); continue
        eff = max(start, now).strftime("%Y-%m-%dT%H:%M:%SZ")
        cmd = ["gh", "workflow", "run", "pull_loop.yml", "-f", "mode=loop", "-f", f"start={eff}", "-f", f"end={c['end']}", "-f", f"step_min={w['step_min']}"]
        print(f"  {c['start']} -> {c['end']}: DISPATCH (start {eff}; {len(c['games'])} games: {', '.join(c['games'])})" + (" [dry run]" if dry else ""))
        if not dry: subprocess.run(cmd, check=True)
    # early-week pulls
    for s in early_slots(now):
        label = f"early {s:%Y-%m-%dT%H:%MZ}"
        if in_cluster(s, w["clusters"]): print(f"  {label}: inside a kickoff-loop window, the loop covers it"); continue
        mine = [x for x in runs if label in (x.get("displayTitle") or "")]
        if any(x["status"] in ACTIVE or (x["status"] == "completed" and x.get("conclusion") == "success") for x in mine):
            print(f"  {label}: already dispatched"); continue
        cmd = ["gh", "workflow", "run", "pull_loop.yml", "-f", "mode=once", "-f", f"label={label}"]
        print(f"  {label}: DISPATCH once" + (" [dry run]" if dry else ""))
        if not dry: subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main(sys.argv[1:])
