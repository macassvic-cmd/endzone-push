"""SharpAPI test pull: one NFL game, DraftKings + FanDuel, report TD and yardage markets, name mapping, request count.

    SHARPAPI_KEY=... python sharpapi_probe.py            # 3 requests: markets, events, one event's odds
Reads the key from the environment only. Touches nothing in the pipeline; prints a report and writes
runs/sharpapi_probe.json (the raw odds rows for the one game, no key inside) for inspection.
"""
import os, sys, json, collections, datetime as dt, urllib.request, urllib.parse
from odds import norm_name, TEAM_ABBR

BASE = "https://api.sharpapi.io/api/v1"
KEY = os.environ.get("SHARPAPI_KEY")
if not KEY:
    print("SHARPAPI_KEY is not set in this shell; run as:  SHARPAPI_KEY=<key> python sharpapi_probe.py"); sys.exit(1)
N = 0


def get(path, **params):
    global N; N += 1
    q = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(f"{BASE}/{path}" + (f"?{q}" if q else ""), headers={"X-API-Key": KEY, "User-Agent": "endzone-lab/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} on {path}: {e.read()[:1200]}"); return None


def rows(d):
    return d.get("data", d) if isinstance(d, dict) else d


# 1. market catalogue
mk = rows(get("markets", league="nfl")) or []
print(f"\n== /markets?league=nfl: {len(mk)} markets")
tdlike = [m for m in mk if any(w in json.dumps(m).lower() for w in ("touchdown", "td", "yard", "rush", "receiv", "pass"))]
for m in tdlike[:60]:
    print("  ", {k: m.get(k) for k in ("key", "slug", "id", "name", "market_type", "category") if k in m} or m)

# 2. events: pick the next NFL game that has not started
ev = rows(get("events", league="nfl", limit=100)) or []
print(f"\n== /events: {len(ev)} events; keys of one: {sorted(ev[0].keys()) if ev else None}")
now = dt.datetime.now(dt.timezone.utc).isoformat()
def start(e): return e.get("start_time") or e.get("event_start_time") or e.get("commence_time") or ""
isgame = lambda e: e.get("event_type") == "fixture" and e.get("away_team") and e.get("home_team")
print("event types:", collections.Counter(e.get("event_type") for e in ev), "| games with both teams:", sum(1 for e in ev if isgame(e)))
upcoming = sorted([e for e in ev if start(e) > now and isgame(e)], key=lambda e: (-(any(str(b).lower() in ("draftkings", "fanduel") for b in (e.get("books") or []))), start(e)))
if not upcoming:
    print("no upcoming event found; first events:", ev[:2]); sys.exit(1)
g = upcoming[0]
print("picked:", {k: g.get(k) for k in ("id", "home_team", "away_team", "start_time", "event_type", "book_count", "market_count", "books")})
eid = g.get("id") or g.get("event_id")

# 3. all odds for that event (one request), DK + FD only
od = rows(get(f"events/{eid}/odds")) or []
json.dump(od, open("runs/sharpapi_probe.json", "w"), indent=0)
print(f"\n== /events/{eid}/odds: {len(od)} rows; keys of one: {sorted(od[0].keys()) if od else None}")
books = collections.Counter(r.get("sportsbook") for r in od); print("rows per sportsbook:", dict(books))
dkfd = [r for r in od if str(r.get("sportsbook", "")).lower() in ("draftkings", "fanduel", "dk", "fd")]
by = collections.defaultdict(lambda: collections.Counter())
for r in dkfd: by[r.get("market_type") or r.get("market")][r.get("sportsbook")] += 1
print("\n== DK/FD market types (rows per book):")
for m, c in sorted(by.items(), key=lambda x: str(x[0])): print(f"   {m}: {dict(c)}")
td = [r for r in dkfd if any(w in str(r.get("market_type", "")).lower() for w in ("touchdown", "td"))]
yd = [r for r in dkfd if "yard" in str(r.get("market_type", "")).lower()]
print(f"\nTD rows {len(td)} | yardage rows {len(yd)}")
for r in td[:6] + yd[:6]:
    print("   ", {k: r.get(k) for k in ("sportsbook", "market_type", "selection", "selection_type", "line", "odds_american", "is_main_line", "player", "player_name")})
alt = collections.Counter((r.get("market_type"), r.get("selection")) for r in yd)
multi = sum(1 for k, v in alt.items() if v > 1)
print(f"yardage (market, player) pairs with more than one line (alternate lines present): {multi} of {len(alt)}")

# 4. name mapping against our player list
try:
    ours = {norm_name(p["name"]): p["name"] for p in json.load(open("latest.json", encoding="utf-8"))["players"]}
except Exception:
    ours = {}
names = sorted({str(r.get("selection") or r.get("player_name") or r.get("player") or "") for r in td + yd})
names = [n for n in names if n and n.lower() not in ("over", "under", "yes", "no")]
matched = [n for n in names if norm_name(n) in ours]
print(f"\n== names: {len(names)} distinct player strings in DK/FD TD+yardage rows; {len(matched)} match our board via norm_name")
print("   unmatched sample:", [n for n in names if norm_name(n) not in ours][:15])
print("   matched sample:", [(n, ours[norm_name(n)]) for n in matched[:8]])

# 5. request count
print(f"\n== requests used by this probe: {N}")
print("full Sunday pull estimate: 1 events list + 16 x /events/{id}/odds = 17 requests (all books, all markets per game), ~1.5 min at 12 req/min; no credit cost on the free tier")
