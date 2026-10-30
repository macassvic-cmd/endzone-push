"""Discord alert on every NEW edge (first-seen only: the edges clv.log_edges just appended). One message per run, chunked under
Discord's 2,000-character limit. Webhook from DISCORD_WEBHOOK_URL (GitHub secret, same setup as the arc-lab health watchdog); without
it the message is printed and nothing is sent. Never raises: an alert failure must not fail a pull."""
import json, os, urllib.request

LIMIT = 1900


def fmt_price(a):
    return "\u2013" if a is None else f"{int(a):+d}"


def line(e):
    stake = e.get("stake", 1.0)
    bv = e.get("best_venue")
    venue = f" \u00b7 best place {bv['venue']} {fmt_price(bv['net'])} after fees{' (' + bv['size_label'] + ')' if bv.get('size_label') else ''}" if bv and bv.get("venue") != e.get("book") else ""
    rank = f" \u00b7 card #{e['cap_rank']}" if e.get("cap_rank") else ""
    routed = f" ({e['routed']} \u2192 bench)" if e.get("routed") else ""
    return (f"\u2022 **{e['bet']}** \u2014 {e.get('book') or '?'} {fmt_price(e.get('best'))} \u00b7 bet down to {fmt_price(e.get('bet_down_to'))}"
            f" \u00b7 EV {e['ev'] * 100:+.1f}% \u00b7 {e.get('role') or 'Unknown'} {stake:g}u{routed}{rank}{venue}")


GUARD_STATE, GUARD_REPEAT_H = "odds_guard_alert.json", 12


def guard_message(b):
    acts = "; ".join(b.get("actions") or []) or "none"
    obs = "\u2013" if b.get("observed_per_day") is None else f"{b['observed_per_day']:,}"     # no backslash inside an f-string expression: CI runs Python 3.11
    return (f"**Endzone Lab \u00b7 Odds API credit guard stage {b['stage']}**\n"
            f"Remaining {b['remaining']:,} \u00b7 burn {b['burn_per_day']:,}/day (planned {b['planned_per_day']:,}, observed {obs}) "
            f"\u00b7 projected **{b['projected_at_reset']:,}** at the {b['reset_at'][:10]} reset, floor {b['floor']:,}.\nActions: {acts}.")


def notify_guard(b, webhook=None, state=GUARD_STATE, now=None):
    """Discord alert when the credit-budget guard (odds.budget_projection) is at stage 1+. Once per stage per GUARD_REPEAT_H hours (state file in the
       working tree, never committed), so a kickoff loop pulling every 10 min does not repeat it. Never raises."""
    import datetime as dt
    if not b or not b.get("stage"): return "guard not tripped"
    now = now or dt.datetime.now(dt.timezone.utc)
    try: st = json.load(open(state)) if os.path.exists(state) else {}
    except ValueError: st = {}
    last = st.get(str(b["stage"]))
    if last:
        try:
            if (now - dt.datetime.fromisoformat(last.replace("Z", "+00:00"))).total_seconds() < GUARD_REPEAT_H * 3600: return f"stage {b['stage']} already alerted at {last}"
        except ValueError: pass
    webhook = (webhook or os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
    msg = guard_message(b)
    if not webhook: return "no DISCORD_WEBHOOK_URL: would post the guard alert"
    try:
        post(msg, webhook)
        st[str(b["stage"])] = now.strftime("%Y-%m-%dT%H:%M:%SZ"); json.dump(st, open(state, "w"))
        return f"posted guard alert (stage {b['stage']})"
    except Exception as e:
        return f"guard alert failed: {type(e).__name__}: {str(e)[:120]}"


def messages(new_edges, season, week, stamp):
    head = f"**Endzone Lab \u00b7 {len(new_edges)} new edge{'s' if len(new_edges) != 1 else ''} \u00b7 {season} week {week} \u00b7 pull {stamp}**\n"
    rule = "\n_Bet only if your book's price is at or better than BET DOWN TO. Nothing inside 30 min of kickoff. Capped card: 25 bets per kickoff window, one alt rung per player-stat._"
    out, cur = [], head
    for e in sorted(new_edges, key=lambda e: -e["ev"]):
        ln = line(e) + "\n"
        if len(cur) + len(ln) + len(rule) > LIMIT:
            out.append(cur.rstrip()); cur = ""
        cur += ln
    out.append(cur.rstrip() + rule)
    return out


def post(text, webhook):
    req = urllib.request.Request(webhook, data=json.dumps({"content": text}).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "User-Agent": "endzone-lab/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status


def notify_new_edges(new_edges, season, week, stamp, webhook=None):
    """Post the first-seen edges of this run. Returns a short status string for the run log."""
    if not new_edges: return "nothing new"
    webhook = (webhook or os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
    msgs = messages(new_edges, season, week, stamp)
    if not webhook:
        return f"no DISCORD_WEBHOOK_URL: would post {len(msgs)} message(s) for {len(new_edges)} edge(s)"
    sent = 0
    for m in msgs:
        try:
            post(m, webhook); sent += 1
        except Exception as e:                                   # never fail the pull over an alert
            return f"posted {sent}/{len(msgs)}; failed: {type(e).__name__}: {str(e)[:120]}"
    return f"posted {sent} message(s) for {len(new_edges)} new edge(s)"
