"""Dispatcher (pull_dispatch.yml, every 30 min, stdlib only): start the `Pull loop` workflow for every cluster in pull_windows.json
whose loop starts within the next LEAD_MIN minutes (or should already have started) and has no queued / running / successfully
finished run yet. Firing every 30 min is the backup: a dispatcher run that is late or skipped is covered by the next one, and a
loop started late simply begins at once (start = now). Needs GH_TOKEN with actions: write.

    python pull_dispatch.py [--windows pull_windows.json] [--lead-min 90] [--dry-run]
"""
import sys, json, subprocess, datetime as dt

LEAD_MIN = 90
a = sys.argv[1:]; g = lambda k, d=None: a[a.index(k) + 1] if k in a else d
dry = "--dry-run" in a; lead = int(g("--lead-min") or LEAD_MIN); fn = g("--windows", "pull_windows.json")
P = lambda s: dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
now = dt.datetime.now(dt.timezone.utc)
w = json.load(open(fn))
print(f"{fn}: {w['season']} week {w['week']}, generated {w['generated']}, {len(w['clusters'])} cluster(s); now {now:%Y-%m-%dT%H:%MZ}")
live = [c for c in w["clusters"] if P(c["end"]) > now]
if not live:
    print("every cluster is in the past: the windows file is stale until the next scheduled run rewrites it (Tue/Thu/Sat/Sun)"); sys.exit(0)
r = subprocess.run(["gh", "run", "list", "--workflow", "pull_loop.yml", "--limit", "100", "--json", "displayTitle,status,conclusion,createdAt"], capture_output=True, text=True)
runs = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else []
for c in live:
    start, end = P(c["start"]), P(c["end"])
    if (start - now).total_seconds() / 60 > lead:
        print(f"  {c['start']} -> {c['end']}: starts in {(start - now).total_seconds() / 60:.0f} min, not yet"); continue
    mine = [x for x in runs if f"-> {c['end']}" in (x.get("displayTitle") or "")]
    active = [x for x in mine if x["status"] in ("queued", "in_progress", "waiting", "requested", "pending")]
    done_ok = [x for x in mine if x["status"] == "completed" and x.get("conclusion") == "success"]
    if active: print(f"  {c['start']} -> {c['end']}: already {active[0]['status']}"); continue
    if done_ok: print(f"  {c['start']} -> {c['end']}: a run already completed successfully"); continue
    eff = max(start, now).strftime("%Y-%m-%dT%H:%M:%SZ")
    cmd = ["gh", "workflow", "run", "pull_loop.yml", "-f", "mode=loop", "-f", f"start={eff}", "-f", f"end={c['end']}", "-f", f"step_min={w['step_min']}"]
    print(f"  {c['start']} -> {c['end']}: DISPATCH (start {eff}; {len(c['games'])} games: {', '.join(c['games'])})" + (" [dry run]" if dry else ""))
    if not dry: subprocess.run(cmd, check=True)
