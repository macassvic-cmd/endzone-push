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
Keys live in the environment / GitHub secrets only. Raw histories, fixtures and the /players list are cached under
data/oddspapi_raw/ and data/oddspapi_players.json, so a re-run that only re-maps names spends no requests.
"""
import sys, os, re, json, time, urllib.request, urllib.parse, datetime as dt
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
    q = urllib.parse.urlencode({**{k: v for k, v in params.items() if v is not None}, "apiKey": key})   # OddsPapi auth = apiKey query param
    req = urllib.request.Request(f"{BASE}/{path}?{q}", headers={"User-Agent": "endzone-lab/1.0"})
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
    af = [m for m in markets if m.get("sportId") == 14 and m.get("playerProp")]
    print(f"american-football player-prop markets: {len(af)}")
    for m in af: print("   ", m.get("marketId"), m.get("marketName"), "|", m.get("marketType"), "| hcp", m.get("handicap"), "| period", m.get("period"), "| outcomes", [o.get("outcomeName") for o in m.get("outcomes", [])][:4])
    # one recent NFL fixture: which TD markets does each book price?
    nfl = next((t for t in tours if "nfl" in json.dumps(t).lower()), None)
    tid = (nfl or {}).get("tournamentId") or (nfl or {}).get("id")
    if tid:
        now = dt.datetime.now(dt.timezone.utc); fx = get("fixtures", {"tournamentId": tid, "from": (now - dt.timedelta(days=9)).strftime("%Y-%m-%dT00:00:00Z"), "to": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "statusId": 2}, key) or []
        print("recent NFL fixtures:", len(fx), "| keys:", sorted(fx[0].keys()) if fx else None, "| sample:", {k: fx[0].get(k) for k in ("fixtureId", "id", "startTime", "homeName", "awayName", "participant1Name", "participant2Name")} if fx else None)
        if fx:
            fid = fx[0].get("fixtureId") or fx[0].get("id")
            want = [b for b in books if any(w in json.dumps(b).lower() for w in ("draftkings", "fanduel", "pinnacle"))]
            slugs = ",".join(str(b.get("slug") or b.get("bookmakerId") or b.get("id")) for b in want[:3])
            od = get("historical-odds", {"fixtureId": fid, "bookmakers": slugs}, key) or {}
            print("historical-odds keys:", sorted(od.keys()) if isinstance(od, dict) else type(od))
            json.dump(od, open(os.environ.get("ODDSPAPI_DUMP", "oddspapi_sample.json"), "w"))
            bo = od.get("bookmakerOdds") or od.get("bookmakers") or {}
            def walk(x, depth=0, path="root"):
                if depth > 4: return
                if isinstance(x, dict):
                    ks = list(x.keys()); print("   " * depth + f"{path}: dict keys {ks[:6]}{' ...' if len(ks) > 6 else ''}")
                    if ks: walk(x[ks[0]], depth + 1, str(ks[0]))
                elif isinstance(x, list):
                    print("   " * depth + f"{path}: list len {len(x)}")
                    if x: walk(x[0], depth + 1, "[0]")
                else: print("   " * depth + f"{path}: {type(x).__name__} {str(x)[:60]}")
            walk(od)
            for bk, bdata in bo.items():
                mks = bdata.get("markets") or {}
                props = {mid: m for mid, m in mks.items() if any((o.get("players") or {}) for o in (m.get("outcomes") or {}).values())}
                print(f"  {bk}: markets {len(mks)} | with player outcomes {len(props)} | ids {list(props)[:12]}")
                for mid, m in list(props.items())[:3]:
                    o = next(iter(m.get("outcomes").values())); pl = next(iter(o.get("players").values()))
                    print(f"     market {mid} {m.get('name') or m.get('marketName')}: outcome keys {sorted(o.keys())[:8]} | player keys {sorted(pl.keys())[:10]} | history len {len(pl.get('history') or [])}")


def american(dec):
    return int(round((dec - 1) * 100)) if dec >= 2 else int(round(-100 / (dec - 1)))


def last_snapshot_before(history, kick):
    """history: list of {createdAt, price (decimal), active} -> the latest active entry created before kickoff."""
    before = [h for h in history if h.get("createdAt", "") < kick and h.get("active", True)]
    return max(before, key=lambda h: h["createdAt"]) if before else None


NFL_TOURNAMENT = 31                                   # OddsPapi tournamentId for the NFL (sportId 14)
MARKETS = {"anytime": ("14388", "14388"), "first": ("14390", "14390"), "two": ("148551", None)}   # marketId, Yes-outcome id (None = pick the Over)


def flip_name(n):
    """OddsPapi gives 'Last, First' (and 'Fannin Jr., Harold'); our normaliser wants 'First Last Jr.'."""
    if not n or "," not in n: return n
    last, first = [x.strip() for x in n.split(",", 1)]
    m = re.match(r"^(.*?)\s+(Jr\.?|Sr\.?|II|III|IV|V)$", last)
    first = re.sub(r"^([A-Z]) ([A-Z])$", lambda m: m.group(1) + m.group(2), first)   # 'Moore, D J' -> 'DJ Moore'
    return f"{first} {m.group(1)} {m.group(2)}" if m else f"{first} {last}"


PLAYERS_FILE = "data/oddspapi_players.json"      # GET /v4/players?sportId=14 (one request, ~44k ids); data/ is gitignored


def player_names(key, books):
    """playerId -> playerName. Preferred: the saved /players list (every American-football player, so ids priced in past
       weeks but not this week still resolve). Fallback: the live odds-by-tournaments board, which only names players
       priced right now (the first backfill left 232 ids unnamed that way)."""
    names = {}
    if os.path.exists(PLAYERS_FILE):
        _collect(json.load(open(PLAYERS_FILE)), names); return names
    for bk in books.split(","):                      # one call per book (the endpoint takes exactly one bookmaker); ids are global
        d = get("odds-by-tournaments", {"tournamentIds": NFL_TOURNAMENT, "bookmaker": bk}, key)
        _collect(d, names)
    return names


def _collect(d, names):
    def walk(x):
        if isinstance(x, dict):
            if x.get("playerName") and (x.get("playerId") is not None):
                names[str(x["playerId"])] = x["playerName"]
            for k, v in x.items():
                if isinstance(v, (dict, list)): walk(v)
                elif k == "playerName" and v and "playerId" not in x:
                    pass
        elif isinstance(x, list):
            for v in x: walk(v)
    walk(d)
    # some shapes keep names keyed by playerId inside "players": {id: {playerName: ..}}
    def walk2(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if isinstance(v, dict) and v.get("playerName") and k not in names: names[str(k)] = v["playerName"]
                if isinstance(v, (dict, list)): walk2(v)
        elif isinstance(x, list):
            for v in x: walk2(v)
    walk2(d)


def _slate_matcher(season, wk):
    """Two slate-based guards for a feed that names players by id and prices defenders too.
       fix(name): nickname fallback, 'Cameron Skattebo' -> 'Cam Skattebo', 'Kenneth Gainwell' -> 'Kenny Gainwell': same
       last-name letters and first names sharing at least their first three letters, unique in the slate ('Charvarius
       Ward' must not become 'Cam Ward'). team(name): the slate team of a slate player, so an outcome in a game that
       player's team is not in (the Jaguars' Josh Allen priced +7500 in Jacksonville's game) is dropped rather than
       attached to the Bills' Josh Allen."""
    from odds import norm_name
    fn = f"slate_{season}_w{wk}.json"
    if not os.path.exists(fn): return (lambda n: n), (lambda n: None)
    slate = [(p["name"], p.get("team")) for p in json.load(open(fn)).get("players", [])]
    exact = {norm_name(n): t for n, t in slate}
    keys = {}
    for n, _ in slate:
        parts = norm_name(n).split()
        if len(parts) >= 2: keys.setdefault("".join(parts[1:]), []).append((parts[0], n))
    def fix(n):
        if norm_name(n) in exact: return n
        parts = norm_name(n).split()
        if len(parts) < 2: return n
        hits = [name for first, name in keys.get("".join(parts[1:]), []) if len(first) >= 3 and len(parts[0]) >= 3 and (first.startswith(parts[0][:3]) and parts[0].startswith(first[:3]))]
        return hits[0] if len(hits) == 1 else n
    return fix, lambda n: exact.get(norm_name(n))


def run(key, season, weeks, books, sched, names=None):
    """One fixtures request per week, one historical-odds request per game (<= 3 books). Writes Odds-API-shaped pulls."""
    os.makedirs("odds_history", exist_ok=True)
    names = names if names is not None else player_names(key, books)
    print("player names mapped:", len(names))
    unmapped = set()
    for wk in weeks:
        fix_name, slate_team = _slate_matcher(season, wk); wrong_team = 0
        g = sched[(sched.season == season) & (sched.week == wk)]
        start = g.gameday.min() + "T00:00:00Z"                                                   # Monday night kicks off after 00:00Z Tuesday
        end = (dt.date.fromisoformat(g.gameday.max()) + dt.timedelta(days=1)).isoformat() + "T12:00:00Z"
        fx_fn = f"data/oddspapi_raw/{season}_w{wk}_fixtures.json"; os.makedirs("data/oddspapi_raw", exist_ok=True)
        if os.path.exists(fx_fn): fixtures = json.load(open(fx_fn))
        else:
            fixtures = get("fixtures", {"tournamentId": NFL_TOURNAMENT, "from": start, "to": end, "statusId": 2}, key); json.dump(fixtures, open(fx_fn, "w"))
        events = []
        for fx in fixtures:
            fid, kick = fx["fixtureId"], fx["startTime"]
            raw_fn = f"data/oddspapi_raw/{season}_w{wk}_{fid}.json"; slim_fn = raw_fn.replace(".json", "_td.json")
            if os.path.exists(slim_fn):
                hist = json.load(open(slim_fn))                   # TD markets only (the full raw file is 50-110 MB per game)
            else:
                if os.path.exists(raw_fn):
                    hist = json.load(open(raw_fn))                # resume: raw ids already saved, no request spent
                else:
                    hist = get("historical-odds", {"fixtureId": fid, "bookmakers": books}, key)
                    json.dump(hist, open(raw_fn, "w"))             # raw ids, re-nameable later (data/ is gitignored)
                keep = {mid for mid, _ in MARKETS.values()}
                hist = {**hist, "bookmakers": {bk: {**bd, "markets": {m: v for m, v in (bd.get("markets") or {}).items() if m in keep}}
                                               for bk, bd in (hist.get("bookmakers") or {}).items()}}
                json.dump(hist, open(slim_fn, "w"))
            ev = dict(id=str(fid), commence_time=kick, home_team=fx.get("participant1Name"), away_team=fx.get("participant2Name"),
                      bookmakers=[], props={"bookmakers": []})
            for bk, bdata in (hist.get("bookmakers") or {}).items():
                mk_out = []
                for mkey, (mid, yes_oid) in MARKETS.items():
                    m = (bdata.get("markets") or {}).get(mid)
                    if not m: continue
                    outcomes = []
                    for oid, o in (m.get("outcomes") or {}).items():
                        if yes_oid and oid != yes_oid: continue             # Yes side only for anytime / first
                        for pid, hist_rows in (o.get("players") or {}).items():
                            snap = last_snapshot_before(hist_rows, kick)
                            if not snap or not snap.get("price"): continue
                            nm = fix_name(flip_name(names.get(str(pid)))) if names.get(str(pid)) else None
                            if not nm: unmapped.add(str(pid)); continue
                            _t = slate_team(nm)
                            if _t and _t not in (TEAM_ABBR.get(ev["home_team"]), TEAM_ABBR.get(ev["away_team"])): wrong_team += 1; continue   # same name, other team
                            oc = dict(name="Over" if mkey == "two" else "Yes", description=nm, price=american(float(snap["price"])))
                            if mkey == "two": oc["point"] = 1.5
                            outcomes.append(oc)
                    if outcomes: mk_out.append(dict(key=MARKET_KEYS[mkey], outcomes=outcomes))
                if mk_out: ev["props"]["bookmakers"].append(dict(key=bk, title={"draftkings": "DraftKings", "fanduel": "FanDuel", "pinnacle": "Pinnacle"}.get(bk, bk), markets=mk_out))
            events.append(ev)
        kick_min = min(e["commence_time"] for e in events) if events else None
        stamp = (dt.datetime.fromisoformat(kick_min.replace("Z", "+00:00")) - dt.timedelta(minutes=1)).strftime("%Y%m%dT%H%M") if kick_min else "unknown"
        fn = f"odds_history/{season}_w{wk}_{stamp}Z.json"
        json.dump(events, open(fn, "w")); print("wrote", fn, "games", len(events), "| priced players:", sum(len(o) for e in events for b in e["props"]["bookmakers"] for m in b["markets"] for o in [m["outcomes"]]), "| dropped as another team's namesake:", wrong_team)
    print("player ids without a name (skipped):", len(unmapped))


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
        get_arg = lambda k, d=None: a[a.index(k) + 1] if k in a else d
        season = int(a[a.index("--run") + 1]); weeks = [int(w) for w in a[a.index("--run") + 2:] if w.isdigit()]
        run(key, season, weeks, get_arg("--books", "draftkings,fanduel"), pd.read_parquet("data/sched.parquet"))
