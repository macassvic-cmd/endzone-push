"""Download fresh nflverse data into data/ then project the next slate."""
import os, sys, subprocess, datetime as dt
import nflreadpy as nfl

season = int(os.environ.get("SEASON") or (dt.date.today().year if dt.date.today().month >= 8 else dt.date.today().year - 1))
os.makedirs("data", exist_ok=True)
yrs = list(range(season - 3, season + 1))
jobs = {
    "pbp": lambda: nfl.load_pbp(yrs), "sched": lambda: nfl.load_schedules(yrs),
    "rosters": lambda: nfl.load_rosters_weekly([season]), "inj": lambda: nfl.load_injuries([season]),
    "dc": lambda: nfl.load_depth_charts([season]), "snaps": lambda: nfl.load_snap_counts([season]),
}
for name, fn in jobs.items():
    try:
        fn().to_pandas().to_parquet(f"data/{name}.parquet"); print("ok", name)
    except Exception as e:
        print("FAILED", name, e)
        if name in ("pbp", "sched", "dc", "rosters"): raise
        if not os.path.exists(f"data/{name}.parquet"):
            import pandas as pd; pd.DataFrame(columns=["week", "gsis_id", "report_status"]).to_parquet(f"data/{name}.parquet")
if "--data-only" not in sys.argv:                      # CI refreshes data, smoke-tests results.py, then runs the week separately
    subprocess.run([sys.executable, "run_week.py", *[a for a in sys.argv[1:] if a != "--data-only"]], check=True)
