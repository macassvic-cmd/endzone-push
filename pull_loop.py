"""Pull-loop runner: the body of the `Pull loop` workflow (pull_loop.yml). Ops code; the model and the rules are untouched.

    python pull_loop.py --mode loop|once|dry_run [--start ISO] [--end ISO] [--step-min 10] [--iterations 2] [--max-minutes 340] [--no-push]
    python pull_loop.py --finalize [--step-min 10] [--no-push]          # commit kept pulls + first-seen logs to main (if: always())
    python pull_loop.py --publish-only [--no-push]                       # one board snapshot (update.yml uses it too)

Loop: wait until --start, then every --step-min minutes (measured pull to pull) run `python run_week.py` (SharpAPI + Kalshi; The Odds
API only when ODDS_API_KEY is set) and publish the board: an orphan snapshot of the tracked tree plus the generated files, force-pushed
to refs/heads/board, which GitHub Pages serves. Stops at --end (the cluster's last kickoff), after --iterations (dry_run), after one pull
(once), or at --max-minutes, where it writes remaining=true / resume_start to $GITHUB_OUTPUT so the chained second job carries on.
Finalize commits to main ONE commit per job: the first pull of each UTC hour plus the newest pull (every pull also goes to pulls_out/
for the run's artifact), and the first-seen logs (edge_log, research, parlay_log) merged key-wise against origin/main in a temporary
worktree (upstream wins on shared keys, our new keys are added), pushed with retries. The scheduled runs keep their own commits.
"""
import os, sys, json, glob, time, shutil, tempfile, subprocess, datetime as dt

GENERATED = ["latest.json", "results.json", "draws.bin", "pickem_latest.json", "pull_windows.json"]
LOG_DIRS = ["edge_log", "research", "parlay_log", "odds_api_usage.jsonl"]
PULLS_OUT = "pulls_out"
BOT = dict(GIT_AUTHOR_NAME="endzone-bot", GIT_AUTHOR_EMAIL="actions@users.noreply.github.com",
           GIT_COMMITTER_NAME="endzone-bot", GIT_COMMITTER_EMAIL="actions@users.noreply.github.com")


def sh(cmd, check=True, capture=False, cwd=None):
    r = subprocess.run(cmd, check=check, cwd=cwd, text=True, capture_output=capture, env={**os.environ, **BOT})
    return r.stdout.strip() if capture else r.returncode


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def parse(s):
    if not s: return None
    d = dt.datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def out(**kv):
    fn = os.environ.get("GITHUB_OUTPUT")
    for k, v in kv.items():
        print(f"output {k}={v}")
        if fn: open(fn, "a").write(f"{k}={v}\n")


def publish_board(stamp, push=True):
    """Orphan snapshot (tracked tree + generated files) -> refs/heads/board, force-pushed. The index is restored afterwards."""
    files = [f for f in GENERATED + sorted(glob.glob("slate_*.json")) if os.path.exists(f)]
    sh(["git", "add", "-f", "--", *files])
    tree = sh(["git", "write-tree"], capture=True)
    commit = sh(["git", "commit-tree", tree, "-m", f"board {stamp}"], capture=True)
    sh(["git", "reset", "-q"])
    if push:
        for _ in range(3):
            if sh(["git", "push", "--force", "origin", f"{commit}:refs/heads/board"], check=False) == 0: break
            time.sleep(10)
        else: raise SystemExit("board push failed 3 times")
    print(f"board snapshot {commit[:10]} ({len(files)} generated files){'' if push else ' [not pushed]'}", flush=True)
    return commit


def run_pull():
    """One build. Budget guard (loops only): NFL credits this UTC month over odds.NFL_MTD_LIMIT, or the account's remaining credits
       under odds.REMAINING_FLOOR, drop The Odds API from this pull (SharpAPI + Kalshi only; with PROPS_SOURCE=oddsapi the board
       says "fallback source"). The scheduled runs (update.yml) are not guarded. Either way run_week.py puts the state on the site."""
    env = dict(os.environ)
    if env.get("ODDS_API_KEY"):
        sys.path.insert(0, os.getcwd()); import odds as O
        u = O.usage_summary()
        if u["guard"]["tripped"]:
            print("ODDS API BUDGET GUARD: " + "; ".join(u["guard"]["reasons"]) + " -> this pull runs without The Odds API", flush=True)
            env["ODDS_API_KEY"] = ""; env["ODDS_GUARD_TRIPPED"] = "1"
        else:
            print(f"odds api budget: NFL credits this month {u['nfl_mtd']:,} (limit {u['guard']['mtd_limit']:,}), remaining {u['remaining']} (floor {u['guard']['remaining_floor']:,})", flush=True)
    r = subprocess.run([sys.executable, "run_week.py"], env=env)
    if r.returncode: raise SystemExit(f"run_week.py failed ({r.returncode})")


def sleep_until(t, label):
    s = (t - utcnow()).total_seconds()
    if s > 0:
        print(f"{label}: sleeping {s / 60:.1f} min until {t:%H:%M:%S}Z", flush=True); time.sleep(s)


def loop(mode, start, end, step_min, iterations, max_minutes, push):
    t_begin = utcnow()
    if start: sleep_until(start, "waiting for the window")
    n = 0; remaining = False
    while True:
        t0 = utcnow()
        if end and t0 >= end: print(f"last kickoff {end:%H:%M}Z passed: stop"); break
        if mode == "dry_run" and n >= iterations: break
        if (t0 - t_begin).total_seconds() / 60 > max_minutes:
            remaining = bool(end and t0 < end); print(f"job cap {max_minutes} min reached; remaining={remaining}"); break
        print(f"\n===== pull {n + 1} at {t0:%Y-%m-%d %H:%M:%S}Z (mode {mode}, step {step_min} min, end {end})", flush=True)
        if mode in ("loop", "dry_run"): os.environ["IN_LOOP"] = "1"          # kickoff loop: the 15-min price-age limit applies (rules.price_age_limit)
        run_pull(); n += 1
        if os.environ.get("PUBLISH", "1") == "0": print("PUBLISH=0: board not published")
        else: publish_board(t0.strftime("%Y-%m-%dT%H:%MZ"), push)
        if mode == "once": break
        nxt = t0 + dt.timedelta(minutes=step_min)
        if end and nxt >= end: print("the next pull would fall after the last kickoff: stop"); break
        sleep_until(nxt, "next pull")
    out(pulls=n, remaining=str(remaining).lower(), resume_start=utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))


def merge_first_seen(theirs, ours):
    """Upstream (theirs) wins on shared keys; keys only we have are added (first-seen log semantics)."""
    m = dict(theirs or {})
    for k, v in (ours or {}).items(): m.setdefault(k, v)
    return m


def select_pulls(paths, step_min):
    """Pull files to commit: the first pull of each UTC hour (stamp minute < step) plus the newest one."""
    paths = sorted(paths)
    if not paths: return []
    keep = [p for p in paths if int(os.path.basename(p).split("_")[-1][11:13]) < step_min]   # ..._YYYYMMDDTHHMMZ.json -> MM
    if paths[-1] not in keep: keep.append(paths[-1])
    return keep


def changed(paths):
    paths = [p for p in paths if os.path.exists(p) or os.path.isdir(p)] or paths
    st = subprocess.run(["git", "status", "--porcelain", "--", *paths], text=True, capture_output=True, check=True).stdout
    return sorted({ln[3:].strip() for ln in st.splitlines() if ln.strip()})   # "XY path": never strip the line itself


def finalize(step_min, push):
    new_pulls = [p for p in changed(["odds_history"]) if p.endswith(".json")]
    os.makedirs(PULLS_OUT, exist_ok=True)
    for p in new_pulls: shutil.copy(p, PULLS_OUT)
    keep = select_pulls(new_pulls, step_min)
    logs = [p for p in changed(LOG_DIRS) if p.endswith(".json")]
    print(f"finalize: {len(new_pulls)} new pull(s), keeping {len(keep)} for main ({', '.join(os.path.basename(k) for k in keep)}); {len(logs)} log file(s) changed")
    if not keep and not logs: print("nothing to commit"); return
    for attempt in range(4):
        sh(["git", "fetch", "-q", "origin", "main"])
        wt = tempfile.mkdtemp(prefix="pub_")
        sh(["git", "worktree", "add", "-q", "--detach", wt, "origin/main"])
        try:
            for p in keep:
                os.makedirs(os.path.join(wt, os.path.dirname(p)), exist_ok=True); shutil.copy(p, os.path.join(wt, p))
            for p in logs:
                r = subprocess.run(["git", "show", f"origin/main:{p}"], capture_output=True, text=True)
                theirs = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else {}
                merged = merge_first_seen(theirs, json.load(open(p)))
                os.makedirs(os.path.join(wt, os.path.dirname(p)), exist_ok=True)
                json.dump(merged, open(os.path.join(wt, p), "w"), indent=0)
                print(f"   {p}: upstream {len(theirs)} keys + {len(merged) - len(theirs)} new")
            sh(["git", "add", "-A", "--", *keep, *logs], cwd=wt)
            if sh(["git", "diff", "--cached", "--quiet"], check=False, cwd=wt) == 0: print("no changes vs origin/main"); return
            msg = f"Pull loop {utcnow():%Y-%m-%d %H:%M} UTC: {len(new_pulls)} pull(s), {len(keep)} kept, logs merged"
            sh(["git", "commit", "-q", "-m", msg], cwd=wt)
            if not push: print("[not pushed]", msg); return
            if sh(["git", "push", "origin", "HEAD:main"], check=False, cwd=wt) == 0: print("pushed:", msg); return
            print(f"push rejected (attempt {attempt + 1}); re-merging against the new origin/main"); time.sleep(15)
        finally:
            sh(["git", "worktree", "remove", "--force", wt], check=False)
    raise SystemExit("finalize: push failed 4 times")


if __name__ == "__main__":
    a = sys.argv[1:]; g = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    push = "--no-push" not in a
    step = int(g("--step-min") or 10)
    if "--finalize" in a: finalize(step, push)
    elif "--publish-only" in a: publish_board(utcnow().strftime("%Y-%m-%dT%H:%MZ"), push)
    else: loop(g("--mode", "once"), parse(g("--start")), parse(g("--end")), step, int(g("--iterations") or 2), float(g("--max-minutes") or 340), push)
