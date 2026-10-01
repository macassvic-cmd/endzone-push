"""OddsPapi backfill of pre-kickoff player TD prices for past weeks (plan first; nothing runs without --run and a key).

    python oddspapi_backfill.py --plan 2026 1 2 3           # request budget only, no network
    ODDSPAPI_KEY=... python oddspapi_backfill.py --discover  # 4 requests: sports, tournaments, bookmakers, markets -> prints the NFL
                                                             # tournamentId, candidate bookmaker and market ids (confirm before --run)
    ODDSPAPI_KEY=... python oddspapi_backfill.py --run 2026 1 2 3 --books draftkings,fanduel,pinnacle \\
        --markets anytime=<id>,first=<id>,two=<id> --tournament <id>

Per week: 1 fixtures request (tournamentId + from/to <= 10 days) + 1 historical-odds request per game (up to 3
bookmakers each). Each game's full price history is reduced to the LAST snapshot before kickoff and written as an
Odds-API-shaped pull to odds_history/<season>_w<week>_<UTC stamp>Z.json, so results.py / clv.py treat it like our
own pulls (closing = last pre-kickoff snapshot; a stamp of kickoff - 1 min is used). Quota: 250 requests/month on the
free tier, historical counts the same as live; cooldowns 2 s (fixtures), 5 s (historical-odds).
Keys live in the environment / GitHub secrets only.
"""
import sys, os, json, time, urllib.request, urllib.parse, datetime as dt
from odds import TEAM_ABBR

BASE = "https://api.oddspapi.io/v4"
COOLDOWN = {"fixtures": 2.0, "historical-odds": 5.0, "default": 1.0}
MARKET_KEYS = {"anytime": "player_anytime_td", "first": "player_1st_td", "two": "player_tds_over"}


def plan(season, weeks, books=3):
    per_book_sets = -(-books // 3)
    fixtures = len(weeks); hist = 16 * len(weeks) * per_book_sets; disc = 4
    print(f"discovery {disc} + fixtures {fixtures} + historical {hist} (16 games x {len(weeks)} weeks x {per_book_sets} bookmaker set(s) of <= 3) = {disc + fixtures + hist} requests of 250/month")
    print(f"wall time at the 5 s historical cooldown: about {hist * 5 / 60:.0f} min")
    return disc + fixtures + hist


def get(path, params, key):
    q = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(f"{BASE}/{path}?{q}", headers={"X-Api-Key": key, "User-Agent": "endzone-lab/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        out = json.load(r)
    time.sleep(COOLDOWN.get(path, COOLDOWN["default"]))
    return out


def discover(key):
    sports = get("sports", {}, key); print("sports with 'football' in the name:", [s for s in sports if "football" in json.dumps(s).lower()][:5])
    sid = next((s.get("sportId") or s.get("id") for s in sports if "american" in json.dumps(s).lower()), None)
    tours = get("tournaments", {"sportId": sid}, key) if sid else []
    print("tournaments:", [t for t in tours if "nfl" in json.dumps(t).lower()][:3])
    books = get("bookmakers", {}, key); want = ("draftkings", "fanduel", "pinnacle", "kalshi", "betmgm", "caesars")
    print("bookmakers of interest:", [b for b in books if any(w in json.dumps(b).lower() for w in want)])
    markets = get("markets", {}, key)
    print("touchdown markets:", [m for m in markets if "touchdown" in json.dumps(m).lower() or "td" in json.dumps(m).lower()][:20])


def american(dec):
    return int(round((dec - 1) * 100)) if dec >= 2 else int(round(-100 / (dec - 1)))


def last_snapshot_before(history, kick):
    """history: list of {timestamp, price...} -> the entry with the latest timestamp < kick."""
    before = [h for h in history if h.get("timestamp", "") < kick]
    return max(before, key=lambda h: h["timestamp"]) if before else None


def run(key, season, weeks, tournament, books, markets, sched):
    os.makedirs("odds_history", exist_ok=True)
    for wk in weeks:
        g = sched[(sched.season == season) & (sched.week == wk)]
        start, end = g.gameday.min() + "T00:00:00Z", g.gameday.max() + "T23:59:59Z"
        fixtures = get("fixtures", {"tournamentId": tournament, "from": start, "to": end, "statusId": 2}, key)
        events = []
        for fx in fixtures:
            fid = fx.get("fixtureId") or fx.get("id"); kick = fx.get("startTime") or fx.get("startDate")
            hist = get("historical-odds", {"fixtureId": fid, "bookmakers": books}, key)
            home = fx.get("homeName") or fx.get("participant1Name"); away = fx.get("awayName") or fx.get("participant2Name")
            ev = dict(id=str(fid), commence_time=kick, home_team=home, away_team=away, bookmakers=[], props={"bookmakers": []})
            for bk, bdata in (hist.get("bookmakerOdds") or {}).items():
                mk_out = []
                for mkey, mid in markets.items():
                    m = (bdata.get("markets") or {}).get(str(mid))
                    if not m: continue
                    outcomes = []
                    for oid, o in (m.get("outcomes") or {}).items():
                        for pid, pdata in (o.get("players") or {}).items():
                            snap = last_snapshot_before(pdata.get("history") or [pdata], kick)
                            if not snap: continue
                            price = snap.get("american") or american(float(snap["decimal"]))
                            oc = dict(name="Over" if mkey == "two" else "Yes", description=pdata.get("playerName") or o.get("playerName"), price=int(price))
                            if mkey == "two": oc["point"] = 1.5
                            outcomes.append(oc)
                    if outcomes: mk_out.append(dict(key=MARKET_KEYS[mkey], outcomes=outcomes))
                if mk_out: ev["props"]["bookmakers"].append(dict(key=bk, title=bk, markets=mk_out))
            events.append(ev)
        kick_min = min(e["commence_time"] for e in events) if events else None
        stamp = (dt.datetime.fromisoformat(kick_min.replace("Z", "+00:00")) - dt.timedelta(minutes=1)).strftime("%Y%m%dT%H%M") if kick_min else "unknown"
        fn = f"odds_history/{season}_w{wk}_{stamp}Z.json"
        json.dump(events, open(fn, "w")); print("wrote", fn, "games", len(events))


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--plan" in a:
        season, weeks = int(a[1]), [int(w) for w in a[2:] if w.isdigit()]; plan(season, weeks); sys.exit()
    key = os.environ.get("ODDSPAPI_KEY")
    if not key: print("ODDSPAPI_KEY not set; nothing to do"); sys.exit(1)
    if "--discover" in a:
        discover(key); sys.exit()
    if "--run" in a:
        import pandas as pd
        get_arg = lambda k: a[a.index(k) + 1] if k in a else None
        season = int(a[a.index("--run") + 1]); weeks = [int(w) for w in a[a.index("--run") + 2:] if w.isdigit()]
        markets = dict(kv.split("=") for kv in get_arg("--markets").split(","))
        run(key, season, weeks, get_arg("--tournament"), get_arg("--books"), markets, pd.read_parquet("data/sched.parquet"))
