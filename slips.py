"""Underdog-style slip pricer from the saved sim draws (the Slips tab does the same in the browser).

    python slips.py "Puka Nacua higher 14.5 fantasy" "Josh Allen lower 34.5 pass attempts" ... [--payout 12]
    python slips.py --file legs.txt

Draws: run_week.py writes draws.bin (N sim games per player, every stat from the same game-environment draw) and an
index under latest.json["draws"]. Per leg: P(hit) = share of sim games where the stat clears the line; the slip's
joint P = share where every leg clears, which keeps same-game and same-player correlation; product-of-legs P assumes
independence. EV = joint P x payout - 1 with payout the total return per unit staked (Underdog "12x").
Ties (whole-number lines) count as misses here; Underdog voids the leg and drops to the smaller slip's payout.
"""
import json, re, struct, sys, os
import numpy as np
from odds import norm_name

PAYOUT = {2: 3.0, 3: 6.0, 4: 12.0, 5: 20.0, 6: 27.0}          # default total return per unit by legs (editable on the page)
LOG = "slips_log.jsonl"                                        # one priced slip per line (--log); committed; results.py grades it
COLS = ["rec", "rec_yds", "rush_yds", "pass_yds", "pass_att", "fpts"]   # order of results.json["actuals"] rows
STATS = {"fpts": "fantasy", "rec": "receptions", "rec_yds": "rec yds", "rush_yds": "rush yds", "pass_yds": "pass yds", "pass_att": "pass att"}
STAT_WORDS = [("pass_att", r"pass(ing)?\s*att(empts|s)?|attempts"), ("pass_yds", r"pass(ing)?\s*(yds|yards)"), ("rec_yds", r"rec(eiving)?\s*(yds|yards)|receiving"),
              ("rush_yds", r"rush(ing)?\s*(yds|yards)|rushing"), ("rec", r"receptions?|catches|\brec\b"), ("fpts", r"fantasy|fpts|\bfp\b|points|\bpts\b")]
HIGHER = r"\b(higher|more|over|o)\b"; LOWER = r"\b(lower|less|under|u)\b"


def parse_leg(text):
    """'Puka Nacua higher 14.5 fantasy' / 'Josh Allen - 34.5 pass attempts - lower' -> dict(name, stat, line, dir) or None."""
    t = text.strip().replace("—", " ").replace("–", " ").replace("-", " ").lower()
    if not t: return None
    dr = "higher" if re.search(HIGHER, t) else "lower" if re.search(LOWER, t) else None
    stat = next((k for k, pat in STAT_WORDS if re.search(pat, t)), None)
    m = re.search(r"(?<![\w.])(\d+(?:\.\d+)?)(?![\w.])", t)
    if not (dr and stat and m): return None
    name = re.sub(HIGHER + "|" + LOWER, " ", t); name = re.sub(m.group(0), " ", name, count=1)
    for _, pat in STAT_WORDS: name = re.sub(pat, " ", name)
    name = re.sub(r"[^a-z' ]", " ", name); name = re.sub(r"\s+", " ", name).strip()
    return dict(name=name, stat=stat, line=float(m.group(1)), dir=dr)


def load_draws(latest="latest.json"):
    D = json.load(open(latest)); idx = D.get("draws")
    if not idx: raise SystemExit("no draws index in latest.json (run_week.py writes it)")
    buf = open(idx["file"], "rb").read(); n = idx["n"]
    players = []
    for pl in idx["players"]:
        vals = {}; off = pl["offset"]
        for st in pl["stats"]:
            spec = idx["layout"][st]; w = 2 if spec["bytes"] == 2 else 1
            raw = np.frombuffer(buf, dtype=np.uint16 if w == 2 else np.uint8, count=n, offset=off); off += n * w
            vals[st] = (raw.astype(float) - spec.get("offset", 0)) / spec.get("scale", 1)
        players.append(dict(pid=pl["pid"], name=pl["name"], team=pl["team"], pos=pl["pos"], norm=norm_name(pl["name"]), vals=vals))
    return D, players


def find_player(players, name):
    nn = norm_name(name); hit = [p for p in players if p["norm"] == nn]
    if len(hit) == 1: return hit[0]
    parts = nn.split()
    if len(parts) >= 2:
        hit = [p for p in players if p["norm"].split()[-1] == parts[-1] and p["norm"][0] == parts[0][0]]
        if len(hit) == 1: return hit[0]
    return None


def price(legs, players, payout=None):
    """legs: parsed dicts. Returns dict(legs=[...], p_joint, p_prod, payout, ev, breakeven) or an error string per leg."""
    masks, out = [], []
    for lg in legs:
        p = find_player(players, lg["name"])
        if not p: out.append(dict(**lg, error="player not in this week's sim")); continue
        v = p["vals"].get(lg["stat"])
        if v is None: out.append(dict(**lg, player=p["name"], error=f"{STATS[lg['stat']]} not simulated for a {p['pos']}")); continue
        hit = v > lg["line"] if lg["dir"] == "higher" else v < lg["line"]
        masks.append(hit)
        out.append(dict(**lg, pid=p["pid"], player=p["name"], team=p["team"], p=float(hit.mean()), median=float(np.median(v)), mean=float(v.mean())))
    ok = [m for m in masks]
    if len(ok) != len(legs) or len(ok) < 2:
        return dict(legs=out, error="every leg must price and a slip needs 2+ legs")
    joint = float(np.all(ok, axis=0).mean()); prod = float(np.prod([m.mean() for m in ok]))
    pay = payout or PAYOUT.get(len(ok))
    res = dict(legs=out, n_legs=len(ok), p_joint=joint, p_prod=prod, payout=pay, ev=(joint * pay - 1) if pay else None, breakeven=(1 / joint) if joint > 0 else None)
    # same-player legs (e.g. a QB's pass yards and fantasy): Underdog may block or reprice them, so also price the slip without the extras
    seen, keep = set(), []
    for i, lg in enumerate(out):
        dup = lg["pid"] in seen; seen.add(lg["pid"]); lg["same_player"] = dup
        if not dup: keep.append(i)
    if len(keep) < len(out):
        res["same_player"] = True
        if len(keep) >= 2:
            j2 = float(np.all([ok[i] for i in keep], axis=0).mean()); pay2 = PAYOUT.get(len(keep)) if payout is None else (payout * PAYOUT.get(len(keep), 1) / PAYOUT.get(len(ok), 1))
            res.update(p_joint_ex=j2, n_legs_ex=len(keep), payout_ex=pay2, ev_ex=(j2 * pay2 - 1) if pay2 else None)
        else:
            res.update(p_joint_ex=None, n_legs_ex=len(keep))
    return res


def grade(sl, A):
    """Grade a logged slip against actuals {pid: [rec, rec_yds, rush_yds, pass_yds, pass_att, fpts]} (a fully graded week).
       No row = did not play = void; a tie is void; void legs drop the slip to the smaller payout; under two live legs = refund."""
    legs = []
    for g in sl["legs"]:
        row = A.get(g["pid"])
        if row is None: legs.append({**g, "res": "void"}); continue
        v = row[COLS.index(g["stat"])]
        res = "void" if v == g["line"] else "hit" if ((v > g["line"]) if g["dir"] == "higher" else (v < g["line"])) else "miss"
        legs.append({**g, "actual": v, "res": res})
    live = [g for g in legs if g["res"] != "void"]
    if len(live) < 2: return dict(status="refund", units=0.0, legs=legs)
    pay = sl["payout"] if len(live) == sl["n_legs"] else (PAYOUT.get(len(live)) or sl["payout"] * PAYOUT.get(len(live), 1) / PAYOUT.get(sl["n_legs"], 1))
    won = all(g["res"] == "hit" for g in live)
    return dict(status="won" if won else "lost", units=round(pay - 1, 3) if won else -1.0, payout=pay, legs=legs)


def command(texts, payout=None):
    """The CLI line that prices and logs this slip (the Slips tab's copy-command button prints it)."""
    q = " ".join('"' + t.replace('"', "'") + '"' for t in texts)
    return f"python slips.py {q}" + (f" --payout {payout:g}" if payout else "") + " --log"


if __name__ == "__main__":
    a = sys.argv[1:]
    payout = float(a[a.index("--payout") + 1]) if "--payout" in a else None
    skip = {i + 1 for i, x in enumerate(a) if x in ("--payout", "--file")}
    texts = open(a[a.index("--file") + 1]).read().splitlines() if "--file" in a else [x for i, x in enumerate(a) if not x.startswith("--") and i not in skip]
    legs = [parse_leg(t) for t in texts if t.strip()]
    bad = [t for t, l in zip([t for t in texts if t.strip()], legs) if l is None]
    if bad: raise SystemExit("could not parse: " + " | ".join(bad))
    D, players = load_draws()
    r = price(legs, players, payout)
    for lg in r["legs"]:
        print(f"  {lg.get('player', lg['name']):24} {lg['dir']:6} {lg['line']:6g} {STATS[lg['stat']]:11} " + (f"P {lg['p']:.3f}  median {lg['median']:.1f}" if "p" in lg else lg["error"]) + ("  [same player as an earlier leg]" if lg.get("same_player") else ""))
    if "error" in r: raise SystemExit(r["error"])
    print(f"joint P {r['p_joint']:.4f} | product of legs {r['p_prod']:.4f} | payout {r['payout']}x -> EV {r['ev']*100:+.1f}% | break-even {r['breakeven']:.1f}x | week {D['week']} draws {D['draws']['n']}")
    if r.get("same_player"):
        print("same-player legs: Underdog may block or reprice them. Without them: " + (f"{r['n_legs_ex']} legs, joint P {r['p_joint_ex']:.4f}, payout {r['payout_ex']}x -> EV {r['ev_ex']*100:+.1f}%" if r.get("p_joint_ex") is not None else "fewer than 2 legs left"))
    if "--log" in a:
        import time
        rec = dict(id=int(time.time() * 1000), ts=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), season=D["season"], week=D["week"], n_legs=r["n_legs"], p_joint=r["p_joint"], p_prod=r["p_prod"],
                   p_joint_ex=r.get("p_joint_ex"), payout=r["payout"], ev=r["ev"], same_player=bool(r.get("same_player")),
                   legs=[{k: lg.get(k) for k in ("pid", "player", "team", "stat", "line", "dir", "p", "median", "same_player")} for lg in r["legs"]], texts=texts)
        with open(LOG, "a", encoding="utf-8") as f: f.write(json.dumps(rec) + "\n")
        print(f"logged to {LOG} (commit it; results.py grades it once week {D['week']} is complete)")
