"""Underdog pick'em flags for the Edge Board and the Yards tab (read-only, own module, kill switch PICKEM_SOURCE=off).

Underdog lists higher/lower lines with a per-pick payout multiplier (1.0 on a standard pick; 0.86 / 1.11 style adjustments on shaded lines)
and no odds. For every Underdog line that matches one of our yard rungs or TD lines (player by name + team + game, with the same-name /
different-team guard of the Josh Allen fix: a name that matches a slate player on another team is dropped, never attached), this module
reports the break-even win rate per leg for the standard payouts and flags the leg when our model-market blend beats break-even by
FLAG_MARGIN or more (either side on the Yards tab; an Edge Board row, which is always a higher/over bet, is tagged only on the higher side).

Payouts (UD_PAYOUTS env overrides, JSON {"2-pick": [[2, 3.0]], ...}; verify in app, Underdog changes them): standard 2-pick 3x, 3-pick 6x;
5-pick flex 5/5 10x, 4/5 2.5x, 3/5 0.4x. A leg's own payout multiplier scales the structure's payouts when its break-even is computed.
Break-even p for a structure with n legs: the i.i.d. per-leg hit rate at which sum_k C(n,k) p^k (1-p)^(n-k) payout_k = 1.
Cadence: one Underdog fetch per scheduled run (UD_FETCH=1, update.yml), cached in ud_lines.json; every other pull reads the cache.
DraftKings Pick6 is skipped (no public feed).
"""
import json, math, os, re, urllib.request
from odds import norm_name

UD_URL = os.environ.get("UNDERDOG_BOARD_URL", "https://api.underdogfantasy.com/v1/over_under_lines")
CACHE = "ud_lines.json"
FLAG_MARGIN, FLAG_REF = 0.03, "3-pick"
DEFAULT_PAYOUTS = {"2-pick": [[2, 3.0]], "3-pick": [[3, 6.0]], "5-pick flex": [[5, 10.0], [4, 2.5], [3, 0.4]]}
STATS = {"Rush Yards": ("yds", "rush"), "Receiving Yards": ("yds", "rec"), "Anytime TD": ("td", "any"), "Rush + Rec TDs": ("td", "any"), "First TD Scorer": ("td", "first")}
UD_ABBR = {"JAC": "JAX", "LAR": "LA", "WSH": "WAS", "ARZ": "ARI", "BLT": "BAL", "CLV": "CLE", "HST": "HOU"}   # Underdog codes that differ from nflverse


def enabled():
    return (os.environ.get("PICKEM_SOURCE") or "underdog").strip().lower() not in ("off", "0", "false", "none")


def payouts():
    raw = os.environ.get("UD_PAYOUTS")
    if not raw: return DEFAULT_PAYOUTS
    try: return {k: [[int(a), float(b)] for a, b in v] for k, v in json.loads(raw).items()}
    except (ValueError, TypeError): return DEFAULT_PAYOUTS


def breakeven(structure, mult=1.0):
    """Per-leg i.i.d. hit rate at which the entry's EV is zero. structure = [[k_hits, payout], ...] (n = max k)."""
    n = max(k for k, _ in structure)
    def ev(p):
        return sum(math.comb(n, k) * p ** k * (1 - p) ** (n - k) * pay * mult for k, pay in structure) - 1
    lo, hi = 0.0, 1.0
    if ev(hi) < 0: return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if ev(mid) < 0: lo = mid
        else: hi = mid
    return round(hi, 4)


def fetch_board(url=UD_URL):
    if os.environ.get("UD_MOCK"): return json.load(open(os.environ["UD_MOCK"]))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (endzone-lab line shop)"})
    with urllib.request.urlopen(req, timeout=60) as r: return json.load(r)


def _f(x):
    try: return float(str(x).replace("+", ""))
    except (TypeError, ValueError): return None


def normalize(raw, stamp):
    """Underdog response -> [{player, team, game, kickoff, stat, kind, line, higher: {price, mult}, lower: {...}}] for the stats we price (NFL only)."""
    games = {g["id"]: g for g in raw.get("games", []) if g.get("sport_id") == "NFL"}
    abbr = {}
    for g in games.values():
        m = re.match(r"\s*([A-Z]{2,3})\s*@\s*([A-Z]{2,3})", g.get("abbreviated_title") or g.get("title") or "")
        if m: abbr[g["away_team_id"]], abbr[g["home_team_id"]] = m.group(1), m.group(2)
    apps = {a["id"]: a for a in raw.get("appearances", []) if a.get("match_id") in games}
    players = {p["id"]: p for p in raw.get("players", [])}
    out = []
    for line in raw.get("over_under_lines", []):
        if line.get("status") not in (None, "active"): continue
        st = ((line.get("over_under") or {}).get("appearance_stat") or {})
        if st.get("display_stat") not in STATS: continue
        app = apps.get(st.get("appearance_id"))
        if not app: continue
        pl = players.get(app.get("player_id")) or {}; g = games[app["match_id"]]
        value = _f(line.get("stat_value"))
        if value is None: continue
        sides = {}
        for opt in line.get("options") or []:
            side = {"higher": "higher", "lower": "lower"}.get(opt.get("choice"))
            if side and opt.get("status") in (None, "active"): sides[side] = dict(price=_f(opt.get("american_price")), mult=_f(opt.get("payout_multiplier")) or 1.0)
        if not sides: continue
        team = abbr.get(app.get("team_id"), "?"); team = UD_ABBR.get(team, team)
        gm = [UD_ABBR.get(t, t) for t in (abbr.get(g["away_team_id"], "?"), abbr.get(g["home_team_id"], "?"))]
        market, kind = STATS[st["display_stat"]]
        out.append(dict(player=f"{pl.get('first_name', '')} {pl.get('last_name', '')}".strip(), team=team, game=f"{gm[0]}@{gm[1]}", game_teams=gm,
                        kickoff=g.get("scheduled_at"), ud_stat=st["display_stat"], market=market, kind=kind, line=value, **sides))
    return dict(fetched=stamp, source="underdog", n=len(out), lines=out)


def lines(now_iso, fetch=False, cache=CACHE, write_cache=True):
    """The Underdog lines to use this build: a fresh pull when `fetch` (one per scheduled run), else the cached file. None when disabled or
       nothing is available. Never raises."""
    if not enabled(): return None
    if fetch:
        try:
            d = normalize(fetch_board(), now_iso)
            if write_cache: json.dump(d, open(cache, "w"), separators=(",", ":"))      # compact: the cache is rewritten by every scheduled run
            return d
        except Exception as e:
            print("underdog fetch failed:", e)
    if os.path.exists(cache):
        try: return json.load(open(cache))
        except ValueError: return None
    return None


def match_players(ud, slate):
    """slate = [(pid, name, team)]. For each Underdog line, the slate pid with the same normalised name AND team; a name that only matches
       players on other teams is dropped (the Jaguars' Josh Allen never attaches to the Bills' Josh Allen) and counted in `dropped`."""
    idx = {}
    for pid, name, team in slate: idx.setdefault(norm_name(name), []).append((pid, team))
    hits, dropped = [], []
    for l in ud:
        cands = idx.get(norm_name(l["player"]), [])
        same = [pid for pid, t in cands if t == l["team"] and l["team"] in l.get("game_teams", [l["team"]])]
        if len(same) == 1: hits.append((l, same[0]))
        elif cands: dropped.append(dict(player=l["player"], ud_team=l["team"], slate_teams=sorted({t for _, t in cands})))
    return hits, dropped


def evaluate(l, blend_higher):
    """Break-even per structure for both sides (the leg's own payout multiplier applied), the better side, its margin over the reference
       structure's break-even, and the flag."""
    pays = payouts(); be = {}
    for name, struct in pays.items():
        be[name] = {s: breakeven(struct, (l.get(s) or {}).get("mult", 1.0)) for s in ("higher", "lower") if l.get(s)}
    p = {"higher": blend_higher, "lower": 1 - blend_higher}
    margins = {s: round(p[s] - be[FLAG_REF][s], 4) for s in ("higher", "lower") if l.get(s) and be.get(FLAG_REF, {}).get(s) is not None}
    best = max(margins, key=margins.get) if margins else None
    flags = {s: bool(m >= FLAG_MARGIN - 1e-9) for s, m in margins.items()}
    return dict(ud_stat=l["ud_stat"], line=l["line"], blend_higher=round(blend_higher, 4), higher=l.get("higher"), lower=l.get("lower"), breakeven=be, margins=margins,
                side=best, margin=margins.get(best), flag=any(flags.values()), flag_higher=flags.get("higher", False), flag_lower=flags.get("lower", False), ref=FLAG_REF)


def flag(ud, slate, yard_rows, td_blend):
    """Attach Underdog evaluations: yard rungs get rung['ud'] where Underdog lists the same line (blend = the rung's market blend, else none);
       TD lines (0.5) use td_blend[(pid, 'any'|'first')]. Returns a summary dict with every evaluated leg (for the page and the run log)."""
    if not ud: return None
    hits, dropped = match_players(ud.get("lines", []), slate)
    rows = {(y["pid"], y["kind"]): y for y in yard_rows}
    legs, matched = [], 0
    for l, pid in hits:
        if l["market"] == "yds":
            y = rows.get((pid, l["kind"]))
            if not y: continue
            r = next((g for g in y["rungs"] if abs(g["line"] - l["line"]) < 1e-9), None)
            if not r: continue
            bl = r.get("curve_blend") if r.get("curve_blend") is not None else r.get("blend")
            matched += 1
            if bl is None: r["ud"] = dict(ud_stat=l["ud_stat"], line=l["line"], higher=l.get("higher"), lower=l.get("lower"), flag=False, no_blend=True); continue
            r["ud"] = evaluate(l, float(bl)); legs.append(dict(r["ud"], pid=pid, name=y["name"], team=y["team"], market="yds", kind=l["kind"]))
        else:
            if l["line"] != 0.5: continue
            bl = td_blend.get((pid, l["kind"]))
            if bl is None: continue
            matched += 1
            ev_ = evaluate(l, float(bl)); legs.append(dict(ev_, pid=pid, name=l["player"], team=l["team"], market=l["kind"], kind=None))
    flagged = [x for x in legs if x["flag"]]
    return dict(source="underdog", fetched=ud.get("fetched"), ud_lines=len(ud.get("lines", [])), matched=matched, evaluated=len(legs), flagged=len(flagged),
                dropped_other_team=dropped[:50], payouts=payouts(), flag_margin=FLAG_MARGIN, ref=FLAG_REF, legs=legs)
