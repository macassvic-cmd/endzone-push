# Free odds sources — evaluation (2026-09-30)

Scope: can a free tier replace or supplement The Odds API for NFL anytime TD, 2+ TD, first TD and alternate
yardage props from DraftKings and FanDuel, and can any free source give past prices for backtesting?
Everything below is from public documentation; nothing in the live pipeline changed. Keys, if created, go in
GitHub secrets only.

## 1. SharpAPI (sharpapi.io)

| | Free tier |
|---|---|
| Books | 2: DraftKings and FanDuel |
| Limits | 12 requests/min, no monthly cap (≈17k/day) |
| Delay | 60 s (pre-match and schedule only; no streaming, no "best odds"/EV features) |
| Props | "Passing yards, rushing yards, receiving yards, touchdowns, completions, interceptions, receptions and combo props" — generic category `player_prop`; the public docs do not enumerate market slugs, so **anytime TD vs 2+ TD vs first TD and alt lines are unconfirmed** until `/api/v1/markets` is called with a key |
| Historical | Enterprise only |
| IDs | Teams: stable `numerical_id`, slug `id`, `abbreviation`; players: **name strings only** (no IDs, no external mappings), so matching is `odds.norm_name` as today. Game IDs are stable across preseason → regular season → postseason |
| Cheapest paid | Hobby $79/month (5 books, real-time) |

Verdict: the only free source that explicitly serves DK and FD props with a usable rate limit. The open question is
market granularity (first TD and alternate yardage especially). **Create the key when you want this confirmed**; the
first call I'd make is `GET /api/v1/markets?league=nfl` to list the prop slugs, then one `/odds` pull for a game to
check names and the 2+ line. Sign-up is email only, no card.

## 2. OddsPapi (oddspapi.io)

| | Free tier |
|---|---|
| Limits | 250 requests/month; per-endpoint cooldowns (historical endpoint 5 s, a 304 still counts) |
| Books | "350+"; the docs' examples are offshore/European (188BET, 18Bet); **DraftKings/FanDuel not confirmed in the docs** |
| Historical | Included free, player props included in the same `/historical-odds` response, one fixture and up to 3 bookmakers per request, full price history per market |
| Archive start | **"All historical odds data since January 2026"** |

Verdict on the backtest idea: **no — 2024–2025 player prop prices are not available**; the archive starts January
2026, so the only NFL props in it are the 2025 playoffs and the 2026 season. What it could do: backfill 2026 weeks 1–3
anytime-TD prices (our own `odds_history` only starts week 3) at one request per game, three books each, about 50
requests, if DK/FD or Pinnacle turn out to be in the bookmaker list. That would extend the model-vs-market Brier and
CLV tables by two weeks, not give a two-season backtest. Not worth a key until the bookmaker list is checked.

## 3. SportsGameOdds (sportsgameodds.com)

| | Free ("Amateur") tier |
|---|---|
| Quota | 2,500 objects/month, 10 requests/min, 10-minute update frequency |
| Object | "the top-level items returned in a request … if a call to /events returns 10 events, that counts as 10 objects" — **one game is one object regardless of how many markets or books it carries** |
| Books | 9: FanDuel, DraftKings, BetMGM, Caesars, ESPN BET, Bovada, Unibet, PointsBet, William Hill |
| Props | Included; odds come inline in `/events` under `odds`, keyed by oddID `{statID}-{statEntityID}-{periodID}-{betTypeID}-{sideID}`, e.g. `touchdowns-{PLAYER_ID}-game-ml-yes` (anytime), `touchdowns-{PLAYER_ID}-game-ou-over` (over a line, 2+ at 1.5), `rushing_yards-…-game-ou-over`; per-book prices under `byBookmaker.draftkings` / `.fanduel` with `odds` and `available` |
| IDs | Player IDs are name-based with league suffix (`LEBRON_JAMES_NBA` style), so mapping is a normalised-name join, same as today |
| Historical | Not on the free tier |

Does 2,500/month cover a full Sunday pull? **Yes, comfortably.** A full slate is 16 events = 16 objects per pull
with every TD and yardage market and all nine books inline. Our current cadence (Sat and Sun 9:23, plus the
conditional 8:37/9:52 slots, roughly 4 pulls a week) is about 64 objects a week, ~260 a month, a tenth of the quota.
Even a pull every hour on Sunday morning stays under 10% of it. The 10-minute delay is fine for pre-kickoff pricing.
What is unconfirmed from the docs: whether first-TD scorer exists as a statID, and how deep the alternate-line
ladders go.

## Recommended free combination (no change until approved)

1. **SportsGameOdds as the primary pull** for TD and yardage props: one object per game, nine books including DK and
   FD, inline per-book prices. Replace the Odds API props call (currently ~63 credits per pull for four markets)
   with one `/events` request per slate. Keep Kalshi as it is.
2. **SharpAPI as the DK/FD cross-check** (and fallback) once its market slugs are confirmed; 12 req/min is more
   than enough for a slate.
3. **Keep The Odds API only for game lines** (spreads/totals, 2 credits a pull) or drop it if SportsGameOdds'
   game-level odds prove adequate; its remaining credits then last the season.
4. **Skip OddsPapi** for now: nothing before January 2026 and no confirmed US books. Revisit only to backfill 2026
   weeks 1–3 if its bookmaker list includes DK/FD.

Migration risk to check before switching: name normalisation across three vendors (SportsGameOdds and SharpAPI are
both name-based), market equivalence for the 2+ line (Over 1.5 vs a "2+" market), and the measured-hold anchors,
which were calibrated on The Odds API's book set.

Keys: `SPORTSGAMEODDS_API_KEY` and `SHARPAPI_KEY` as repository secrets; never in files.

## OddsPapi backfill plan: 2026 weeks 1–3 pre-kickoff TD prices (planned, not run)

Feasible on the free tier. From the docs: historical requests count like live ones (250/month), player props come
back in the same response as main markets (`playerName` set on prop outcomes), `/v4/historical-odds` takes one
`fixtureId` and up to 3 `bookmakers` per request and returns each market's full timestamped price history, and
`/v4/fixtures` lists finished games (`statusId=2`) for a `tournamentId` over a window of at most 10 days.

Request budget (`python oddspapi_backfill.py --plan 2026 1 2 3`):

| Step | Requests |
|---|---|
| Discovery: sports, tournaments, bookmakers, markets (once) | 4 |
| Fixtures, one per week | 3 |
| Historical odds, one per game, 3 bookmakers each (16 × 3 weeks) | 48 |
| **Total for DraftKings + FanDuel + Pinnacle** | **55 of 250** |
| Same with a second bookmaker set (e.g. BetMGM, Caesars, Kalshi if listed) | 103 |

Wall time: the historical endpoint has a 5-second cooldown, so about 4 minutes for 48 games.

What each game yields: for anytime TD, first TD and the touchdowns over/under line (2+ = Over 1.5) from each
bookmaker, the last snapshot before kickoff, written as an Odds-API-shaped pull to
`odds_history/<season>_w<week>_<kickoff-1min>Z.json`. results.py and clv.py then grade and score those weeks
exactly like our own pulls: model-vs-market Brier and the Edge Board EV for weeks 1–3 on real prices, and a closing
price for CLV. It does not give flag-time prices (no edges were logged then), so CLV for those weeks is not
available; the Brier and EV comparison is.

Unknowns that only a key resolves (the `--discover` step, 4 requests): the NFL `tournamentId`, whether DraftKings
and FanDuel are in the bookmaker list (the docs only show offshore examples; Pinnacle is confirmed), the market
ids for anytime / first / touchdowns over-under in American football, and whether the archive holds complete
week-1 prop histories (September 10–14, 2026; the archive began January 2026).

Sequence on your go-ahead: create the free key → add it as the `ODDSPAPI_KEY` repository secret and export it locally
for the one-off run → `--discover` (4 requests) → confirm ids with you → `--run 2026 1 2 3` (51 requests) → commit the
three pull files → regrade. Nothing runs until then.

## SharpAPI test pull (2026-10-01, free key, 3 requests) — Steelers @ Browns, Thursday night

`python sharpapi_probe.py` (key from the environment only). Endpoints that worked: `GET /api/v1/markets?league=nfl`
(130 market ids), `GET /api/v1/events?league=nfl&limit=100` (the list is mostly outrights: 92 of 100; fixtures are
`event_type: "fixture"`, filter client-side or page with `cursor`), `GET /api/v1/events/{id}/odds` (every market and
book for the game in one response, 1,503 rows). Accepted event filters per the API's own error message: `league`,
`date`, `date_range`, `books`, `is_player_prop`, `is_main_line`, `market`, `limit`, `cursor`, `devig_book`, …

**Books returned on the free tier:** DraftKings (709 rows) and FanDuel (794) only, although the event lists 40
books including Kalshi, Pinnacle, Novig, ProphetX, PrizePicks and Underdog (paid tiers).

**TD markets, this game:**

| Market id | DraftKings | FanDuel |
|---|---|---|
| `anytime_touchdown_scorer` | 29 rows / 28 players | 29 / 29 |
| `first_touchdown_scorer` | none | 29 / 29 |
| `last_touchdown_scorer` | none | 29 / 29 |
| `player_touchdowns` (over/under, the 2+ line) | none | none |
| `player_passing_touchdowns` | 2 QBs, 0.5 line | 2 QBs, 0.5 and 1.5 |

So anytime TD from both books, first TD from FanDuel only, and **no 2+ TD line at either book for this game** even
though `player_touchdowns` exists in the catalogue; the 2+ market would still come from The Odds API / Kalshi.

**Yardage markets:** `player_rushing_yards`, `player_receiving_yards`, `player_passing_yards` (plus combos,
receptions, attempts, longest reception/rush). Alternate lines come as separate rows with `is_main_line: false`
and `is_alternate_line: true` and a numeric `line` (DK: up to 14 rushing / 10 receiving / 27 passing lines per
player; FD: up to 13 / 16 / 11). Receiving yards: 13 players at each book; rushing: 6; passing: 2.

**Names:** use the `player_name` field, not `selection` (FanDuel's ladders put "Aaron Rodgers 10+" in `selection`).
Names are clean ("Harold Fannin Jr.", "DK Metcalf"). Of 30 `player_name` strings in the TD markets, 21 matched our
week-4 board through `odds.norm_name` with zero wrong-team matches; the 9 unmatched were two defenses, an "Anytime
TD" header row, and six deep-bench players outside our depth-chart cut (Carsen Ryan, Eli Heidenreich, Kaden Wetjen,
Michael Burton, Riley Nowakowski, Travis Homer). No mapping work needed beyond skipping non-player rows.

**Freshness:** every row carries `timestamp` (all 06:24Z for this pull, i.e. one snapshot) and
`is_stale_pregame_price` (0 flagged).

**Request count for a full Sunday:** 1–2 events-list requests (outrights crowd the list; use `cursor` or
`date_range`) + 16 × `/events/{id}/odds` = **about 18 requests**, 1.5 minutes at 12 requests/min, no credits.
Each game response includes every DK/FD market, so TD and yardage props come in the same call.

**Verdict:** a free DK/FD source for anytime TD, first TD (FD) and full alternate-yardage ladders, with clean
names. It does not replace The Odds API for the 2+ line or for the other books, and it only ever shows two books.
Fits as the cross-check/ladder source in the recommended combination above. Pipeline unchanged.

### OddsPapi backfill run (2026-10-01): done

52 requests (about 71 for the month with discovery). Auth is an `apiKey` query parameter; `odds-by-tournaments`
takes exactly one `bookmaker`. Historical ids need the live name map ("Last, First" format); 433 of 690 ids mapped.
Pinnacle has no player TD markets; DK/FD had no 1.5-TD line in the archive. Numbers in CHANGELOG.md.
