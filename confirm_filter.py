"""Confirmation-filter study (analysis only, nothing on the board changes): does a flagged edge do better when the
market has already moved toward us by flag time?

    python confirm_filter.py [--season 2026]

For every logged edge (edge_log/<season>_w<week>.json: flag-time prices from live pulls), the no-vig probability at
the median book is re-priced from the week's FIRST saved pull and from the pull at flag time; movement = flag minus
first, in probability points for our side (Yes / Over). Groups: all edges; moved toward us (>= +1 pt); moved against
us (<= -1 pt); unchanged / within 1 pt. Per group: n, record, units at the flag-time best price, expected units
(EV at that price), average CLV (flag vs last pre-kickoff pull, from results.json), Brier of the blend probability;
overall and for prices shorter than +1000. Weeks priced only from the OddsPapi closing backfill (one pull, no flag
time) carry no movement and are reported separately. Also prints model vs books Brier season-to-date per market.
"""
import glob, json, os, re, sys
import numpy as np, pandas as pd
import odds as O, yard_prices as YP, clv as C, yards_backtest as YB
from nansafe import val, text, num, isnan

MK = {"any": ("player_anytime_td", None), "first": ("player_first_td", None), "two": ("player_tds_over", 1.5)}


def stamp_key(s):
    return re.sub(r"Z$", "", s)


def pull_cache(season, week, cache={}):
    key = (season, week)
    if key not in cache:
        out = []
        for st, fn in C.pulls(season, week):
            ev = json.load(open(fn)); board = O.prop_board(ev); hold = O.measure_hold(ev); yb, yh = YP.ladder_board(ev)
            out.append(dict(stamp=stamp_key(st), fn=fn, board=board, hold=hold, yb=yb, yh=yh))
        cache[key] = out
    return cache[key]


def market_p(pull, e):
    """No-vig median-book probability for the edge's side in this pull, or None."""
    name = e["bet"].split(" Over ")[0] if e["market"] == "yds" else re.sub(r" (Anytime TD|First TD|2\+ TD)$", "", e["bet"])
    if e["market"] == "yds":
        ps = YP.price_rung(pull["yb"], pull["yh"], e["kind"], name, float(e["line"]))
    else:
        mk, pt = MK[e["market"]]; ps = O.price_summary(pull["board"], mk, name, pt, pull["hold"])
    return None if not ps else float(ps["market_p"])


def outcomes(season):
    p = pd.read_parquet("data/pbp.parquet"); s = pd.read_parquet("data/sched.parquet")
    final = set(s[s.home_score.notna()].game_id)
    td = p[(p.touchdown == 1) & p.td_player_id.notna()]
    off = td[(td.rush_touchdown == 1) | (td.pass_touchdown == 1)]
    scored = off.groupby("game_id").td_player_id.apply(set).to_dict()
    multi = off.groupby(["game_id", "td_player_id"]).size(); two = multi[multi >= 2].reset_index().groupby("game_id").td_player_id.apply(set).to_dict()
    first = td.sort_values(["game_id", "play_id"]).groupby("game_id").first().td_player_id.to_dict()
    ymap = {(int(r.week), r.pid, r.kind): float(r.y) for r in YB.actual_yards(p, season).itertuples()}
    return final, scored, two, first, ymap


def main(season=2026):
    final, scored, two, first, ymap = outcomes(season)
    res = json.load(open("results.json")) if os.path.exists("results.json") else {}
    clv_rows = {(r["week"], r["bet"]): r.get("clv") for r in res.get("clv_rows", [])}
    rows = []
    for fn in sorted(glob.glob(f"edge_log/{season}_w*.json")):
        week = int(re.search(r"_w(\d+)\.json$", fn).group(1)); log = json.load(open(fn))
        slate = json.load(open(f"slate_{season}_w{week}.json")) if os.path.exists(f"slate_{season}_w{week}.json") else {"players": []}
        game_of = {p["pid"]: p["game_id"] for p in slate["players"]}
        pulls = pull_cache(season, week)
        if not pulls: continue
        for key, e in log.items():
            gid = game_of.get(e["pid"])
            if not gid or gid not in final: continue
            flag = stamp_key(e["flagged"]); at_flag = next((pl for pl in pulls if pl["stamp"] == flag), None); first_pull = pulls[0]
            p0 = market_p(first_pull, e); p1 = market_p(at_flag, e) if at_flag else None
            move = None if p0 is None or p1 is None or at_flag is first_pull else p1 - p0
            if e["market"] == "any": won = e["pid"] in scored.get(gid, set())
            elif e["market"] == "first": won = first.get(gid) == e["pid"]
            elif e["market"] == "two": won = e["pid"] in two.get(gid, set())
            else:
                y = ymap.get((week, e["pid"], e["kind"])); won = None if y is None else y >= float(e["line"])
            if won is None: continue
            dec = O.decimal(e["best"])
            rows.append(dict(week=week, bet=e["bet"], market=e["market"], role=e.get("role"), price=e["best"], won=bool(won), profit=dec - 1 if won else -1.0,
                             ev=e["ev"], blend_p=e["blend_p"], p_first=p0, p_flag=p1, move=move, clv=clv_rows.get((week, e["bet"])), longshot=e["best"] >= 1000,
                             first_is_flag=at_flag is first_pull))
    df = pd.DataFrame(rows)
    if df.empty:
        print("no graded logged edges"); return

    def tally(g, label):
        if g.empty: return f"{label:38} n 0"
        c = g.clv.dropna()
        return (f"{label:38} n {len(g):3} | {int(g.won.sum())}-{int((~g.won).sum())} | units {g.profit.sum():+6.1f} | expected {g.ev.sum():+6.1f} | "
                f"CLV {c.mean()*100:+.2f} pts (n {len(c)}) | blend Brier {((g.blend_p - g.won.astype(float)) ** 2).mean():.4f}")

    print(f"logged edges graded: {len(df)} over weeks {sorted(df.week.unique())} | with movement measurable: {int(df.move.notna().sum())} "
          f"(flag-time pull = first pull for {int(df.first_is_flag.sum())}, no price at one end for {int((df.move.isna() & ~df.first_is_flag).sum())})")
    groups = [("all edges", df), ("moved toward us (>= +1 pt)", df[df.move >= 0.01]), ("moved against us (<= -1 pt)", df[df.move <= -0.01]),
              ("within 1 pt / first pull = flag", df[df.move.notna() & (df.move.abs() < 0.01) | df.first_is_flag]), ("no movement measurable", df[df.move.isna() & ~df.first_is_flag])]
    for label, g in groups:
        print(tally(g, label)); print(tally(g[~g.longshot], "   shorter than +1000"))
    print("\nby market (all logged edges):")
    for mk, g in df.groupby("market"): print(tally(g, f"   {mk}")); print(tally(g[~g.longshot], "      shorter than +1000"))
    mb = res.get("market_brier", {}); yh = res.get("yards_h2h")
    print("\nmodel vs books Brier, season to date:")
    for mk, lbl in (("any", "anytime TD"), ("first", "first TD"), ("two", "2+ TD")):
        if mk in mb: print(f"   {lbl:11} model {mb[mk]['model']:.4f}  books {mb[mk]['market']:.4f}  50/50 {mb[mk]['blend']:.4f}  n {mb[mk]['n']} over {mb[mk]['weeks']} weeks")
    if yh: print(f"   {'yards':11} model {yh['season']['model']:.4f}  books {yh['season']['book']:.4f}  50/50 {yh['season']['blend']:.4f}  n {yh['season']['n']} over {yh['season']['weeks']} week(s)")
    return df


if __name__ == "__main__":
    a = sys.argv[1:]
    main(int(a[a.index("--season") + 1]) if "--season" in a else 2026)
