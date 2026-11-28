"""Personal bet log (CEO assignment 2026-10-20, built 2026-10-29): the bets YOU placed, graded on their own "My bets" card on the Results tab and
never mixed into the model record (the record is the decision-pull card; this file is what was actually bet, at the price and stake taken).

    python placed_bets.py add "Jauan Jennings Anytime TD" --book FanDuel --price +650 --stake 1 [--week 8] [--note "..."]
    python placed_bets.py add "Kyren Williams Over 79.5 rush yds" --book DraftKings --price -110 --stake 0.25
    python placed_bets.py list [--week 8]

Each line of placed_bets.jsonl: {"ts", "season", "week", "bet", "pid", "team", "market", "kind", "line", "book", "price", "stake", "note"}.
`add` resolves the player against the week's slate (slate_<season>_w<week>.json, default latest.json) so grading is by player id, not name text.
Markets: "Anytime TD" | "First TD" | "2+ TD" | "Over <line> rush|rec|pass yds". results.py grades the file once the week is complete:
TD markets on the graded scorer sets, yardage on actual yards (a push or no-touch game is void).
"""
import json, os, re, sys, datetime as dt

LOG = "placed_bets.jsonl"
MARKET_RX = re.compile(r"^(?P<name>.+?)\s+(?:(?P<td>Anytime TD|First TD|2\+ TD)|Over\s+(?P<line>\d+(?:\.\d+)?)\s+(?P<kind>rush|rec|pass)\s+yds)(?:\s+\(alt\))?$", re.I)
TD = {"anytime td": "any", "first td": "first", "2+ td": "two"}


def parse_bet(text):
    """'Name Anytime TD' -> (name, market, kind, line); 'Name Over 79.5 rush yds' -> (name, 'yds', 'rush', 79.5). None when unreadable."""
    m = MARKET_RX.match(text.strip())
    if not m: return None
    if m.group("td"): return m.group("name").strip(), TD[m.group("td").lower()], None, None
    return m.group("name").strip(), "yds", m.group("kind").lower(), float(m.group("line"))


def load(fn=LOG):
    rows = []
    if not os.path.exists(fn): return rows
    for ln in open(fn, encoding="utf-8"):
        ln = ln.strip()
        if not ln: continue
        try: rows.append(json.loads(ln))
        except ValueError: continue
    return rows


def resolve(name, slate):
    """Player id and team from the slate by exact (case-insensitive) name, else by a unique last-name + initial match."""
    pl = slate.get("players") or []
    nm = name.lower().strip()
    hit = [p for p in pl if (p.get("name") or "").lower() == nm]
    if len(hit) != 1:
        parts = nm.split()
        hit = [p for p in pl if (p.get("name") or "").lower().split()[-1:] == parts[-1:] and (p.get("name") or "").lower().startswith(parts[0][:1])]
    if len(hit) != 1: return None, None
    return hit[0]["pid"], hit[0].get("team")


def add(text, book, price, stake, week=None, note="", slate_fn=None, fn=LOG):
    parsed = parse_bet(text)
    if not parsed: raise SystemExit(f"could not read the bet {text!r}: use 'Name Anytime TD' | 'Name First TD' | 'Name 2+ TD' | 'Name Over 79.5 rush|rec|pass yds'")
    name, market, kind, line = parsed
    slate_fn = slate_fn or (f"slate_{json.load(open('latest.json'))['season']}_w{week}.json" if week and os.path.exists("latest.json") else "latest.json")
    slate = json.load(open(slate_fn)) if os.path.exists(slate_fn) else {}
    pid, team = resolve(name, slate)
    if not pid: raise SystemExit(f"{name!r} not found (uniquely) in {slate_fn}: use the name as the board shows it")
    row = dict(ts=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), season=slate.get("season"), week=week or slate.get("week"), bet=text.strip(), pid=pid, team=team,
               market=market, kind=kind, line=line, book=book, price=int(str(price).replace("+", "")), stake=float(stake), note=note)
    with open(fn, "a", encoding="utf-8") as f: f.write(json.dumps(row) + "\n")
    return row


def grade(rows, tdhit, ymap, graded_weeks):
    """Grade the log on the model's outcome lookups. tdhit: {(week, pid): (hit, first_hit, two_hit)}; ymap: {(week, pid, kind): yards};
       graded_weeks: {(season, week)} fully graded. Returns dict(n, graded, won, void, pending, units, staked, roi, rows)."""
    out = []
    for r in rows:
        wk, sn = int(r.get("week") or 0), r.get("season")
        g = dict(r, status="pending")
        if (sn, wk) in graded_weeks:
            if r.get("market") in ("any", "first", "two"):
                h = tdhit.get((wk, r.get("pid")))
                if h is None: g["status"] = "void"
                else: g["status"] = "won" if {"any": h[0], "first": h[1], "two": h[2]}[r["market"]] else "lost"
            else:
                y = ymap.get((wk, r.get("pid"), r.get("kind")))
                if y is None or y == float(r.get("line") or 0): g["status"] = "void"
                else: g["status"] = "won" if y > float(r["line"]) else "lost"
                g["actual"] = y
        price = int(r.get("price") or 0); dec = 1 + (price / 100 if price > 0 else 100 / -price) if price else 1.0; stake = float(r.get("stake") or 1.0)
        g["units"] = round(stake * (dec - 1), 3) if g["status"] == "won" else round(-stake, 3) if g["status"] == "lost" else 0.0
        out.append(g)
    done = [g for g in out if g["status"] in ("won", "lost")]
    staked = sum(float(g.get("stake") or 1.0) for g in done)
    return dict(n=len(out), graded=len(done), won=sum(1 for g in done if g["status"] == "won"), void=sum(1 for g in out if g["status"] == "void"),
                pending=sum(1 for g in out if g["status"] == "pending"), units=round(sum(g["units"] for g in done), 2), staked=round(staked, 2),
                roi=round(sum(g["units"] for g in done) / staked, 4) if staked else None, rows=out)


def main(argv):
    if not argv or argv[0] not in ("add", "list"): print(__doc__); return
    a = argv[1:]; g = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    if argv[0] == "list":
        wk = g("--week")
        for r in load():
            if wk and str(r.get("week")) != str(wk): continue
            print(f"{r['ts']} W{r['week']} {r['bet']} | {r['book']} {r['price']:+d} x {r['stake']:g}u {('| ' + r['note']) if r.get('note') else ''}")
        return
    text = a[0]
    row = add(text, g("--book", "?"), g("--price", "+100"), g("--stake", "1"), week=int(g("--week")) if g("--week") else None, note=g("--note", ""))
    print("logged:", json.dumps(row))


if __name__ == "__main__":
    main(sys.argv[1:])
