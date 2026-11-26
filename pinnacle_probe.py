"""One-call check: does The Odds API's eu region return Pinnacle for an NFL player prop? (value finder, ODDS_SOURCES.md)

    python pinnacle_probe.py <event_id> <market>        # exactly ONE paid call: /events/<id>/odds?regions=eu&markets=<market>

Run through CI only (workflow pinnacle_probe.yml); the key comes from ODDS_API_KEY and is never printed or written. Prints the credit cost
(x-requests-last), every bookmaker returned with its outcome counts, and Pinnacle's rows if present; writes pinnacle_probe.json (the raw
response plus the cost headers, no key) for the run's artifact.
"""
import json, os, sys, urllib.request, urllib.parse, urllib.error

BASE = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl"


def main(event_id, market, region="eu"):
    key = (os.environ.get("ODDS_API_KEY") or "").strip()
    if not key: raise SystemExit("ODDS_API_KEY is not set: nothing called, 0 credits")
    q = urllib.parse.urlencode(dict(apiKey=key, regions=region, oddsFormat="american", markets=market))
    try:
        with urllib.request.urlopen(f"{BASE}/events/{event_id}/odds?{q}", timeout=30) as r:
            hdr = {h: r.headers.get(h) for h in ("x-requests-last", "x-requests-used", "x-requests-remaining")}
            data = json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace").replace(key, "<key>")[:500]
        print(f"HTTP {e.code}: {body}"); print("cost headers:", {h: e.headers.get(h) for h in ("x-requests-last", "x-requests-remaining")})
        raise SystemExit(1)
    books = data.get("bookmakers") or []
    print(f"event: {data.get('away_team')} @ {data.get('home_team')}, kickoff {data.get('commence_time')}")
    print(f"call: region {region}, market {market} | credit cost (x-requests-last): {hdr['x-requests-last']} | remaining: {hdr['x-requests-remaining']}")
    print(f"bookmakers returned: {len(books)} -> {[b.get('key') for b in books]}")
    for b in books:
        for m in b.get("markets", []):
            sides = {}
            for o in m.get("outcomes", []): sides[o.get("name")] = sides.get(o.get("name"), 0) + 1
            players = sorted({o.get("description") for o in m.get("outcomes", []) if o.get("description")})
            print(f"  {b.get('key')}: {m.get('key')} last_update {m.get('last_update')} outcomes {len(m.get('outcomes', []))} sides {sides} players {len(players)}")
    pin = [b for b in books if "pinnacle" in (b.get("key") or "").lower()]
    print("PINNACLE RETURNED:", "YES" if pin else "NO")
    for b in pin:
        for m in b.get("markets", []):
            for o in m.get("outcomes", [])[:12]: print("   ", o.get("description"), o.get("name"), o.get("point"), o.get("price"))
    json.dump(dict(event_id=event_id, market=market, region=region, headers=hdr, pinnacle=bool(pin), response=data), open("pinnacle_probe.json", "w"), indent=1)


if __name__ == "__main__":
    if len(sys.argv) < 3: raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2], *(sys.argv[3:4]))
