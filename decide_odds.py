"""Decide whether this run should spend Odds API credits. Prints and writes use_odds=true|false to $GITHUB_OUTPUT.

    python decide_odds.py on     # pull unless a pull already happened in the last IDEMPOTENT_MIN minutes
    python decide_odds.py cond   # pull only if the newest saved pull is older than STALE_MIN minutes
    python decide_odds.py off    # never
The newest pull is the latest odds_history/<season>_w<week>_<UTC stamp>Z.json (committed by every run that pulls).
"""
import glob, os, re, sys, datetime as dt

IDEMPOTENT_MIN, STALE_MIN = 45, 60


def newest_pull_age_min():
    stamps = []
    for fn in glob.glob("odds_history/*_w*_*.json"):
        m = re.search(r"_(\d{8}T\d{4})Z\.json$", fn)
        if m: stamps.append(dt.datetime.strptime(m.group(1), "%Y%m%dT%H%M").replace(tzinfo=dt.timezone.utc))
    if not stamps: return float("inf"), None
    last = max(stamps)
    return (dt.datetime.now(dt.timezone.utc) - last).total_seconds() / 60, last


def main():
    mode = (sys.argv[1] if len(sys.argv) > 1 else "off").lower()
    age, last = newest_pull_age_min()
    use = (mode == "on" and age > IDEMPOTENT_MIN) or (mode == "cond" and age > STALE_MIN)
    why = f"mode={mode} last_pull={last.strftime('%Y-%m-%dT%H:%MZ') if last else 'none'} age={age:.0f}min"
    print(f"use_odds={'true' if use else 'false'} ({why})")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"use_odds={'true' if use else 'false'}\nwhy={why}\n")


if __name__ == "__main__":
    main()
