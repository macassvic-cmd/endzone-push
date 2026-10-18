"""Discord alert on every NEW edge (first-seen only: the edges clv.log_edges just appended). One message per run, chunked under
Discord's 2,000-character limit. Webhook from DISCORD_WEBHOOK_URL (GitHub secret, same setup as the arc-lab health watchdog); without
it the message is printed and nothing is sent. Never raises: an alert failure must not fail a pull."""
import json, os, urllib.request

LIMIT = 1900


def fmt_price(a):
    return "\u2013" if a is None else f"{int(a):+d}"


def line(e):
    stake = e.get("stake", 1.0)
    return (f"\u2022 **{e['bet']}** \u2014 {e.get('book') or '?'} {fmt_price(e.get('best'))} \u00b7 bet down to {fmt_price(e.get('bet_down_to'))}"
            f" \u00b7 EV {e['ev'] * 100:+.1f}% \u00b7 {e.get('role') or 'Unknown'} {stake:g}u")


def messages(new_edges, season, week, stamp):
    head = f"**Endzone Lab \u00b7 {len(new_edges)} new edge{'s' if len(new_edges) != 1 else ''} \u00b7 {season} week {week} \u00b7 pull {stamp}**\n"
    rule = "\n_Bet only if your book's price is at or better than BET DOWN TO. Nothing inside 30 min of kickoff._"
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
