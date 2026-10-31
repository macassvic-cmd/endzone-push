# Endzone Lab

NFL touchdown projections: anytime TD, first TD (with opening-kickoff adjustment), 2+ TD, parlays, yard ladders
(rushing / receiving / passing, simulated inside the same game sim), red-zone usage, bring-backs, live sportsbook
edges, and a Results tab that grades every week.

Site: GitHub Pages from the repo root (`index.html` reads `latest.json` and `results.json`).
A scheduled GitHub Action (`.github/workflows/update.yml`) reruns everything Tue/Thu/Sat/Sun.

## Files
- `refresh.py` – downloads nflverse data into `data/`, then runs `run_week.py`
- `run_week.py` – projects the next unplayed week, pulls odds (`odds.py`) and weather (`weather.py`), writes `latest.json` + `slate_<season>_w<week>.json`
- `model.py` – team TD model, player xTD shares, Monte Carlo game sim
- `redzone.py` – red-zone / end-zone usage table
- `results.py` – grades saved slates vs actual scorers, writes `results.json`
- `backfill.py` – rebuilds pre-game slates for past weeks (`python backfill.py 2026 1 2`)
- `backtest.py` – 2025 walk-forward calibration
- `nansafe.py` / `rules.py` – NaN-safe field access and the shared Edge Board rule; `python test_nan_safety.py [--slow]` checks that a missing column never drops rows
- `yards.py` – yard ladders (volume + efficiency inside the game sim); `yard_prices.py` prices them from the SharpAPI DK/FD main and alternate lines; `yards_backtest.py` is the 2024+2025 gate (`python yards_backtest.py --dist gamma --tag gamma`)
- `slips.py` – Underdog-style slip pricer from `draws.bin` (2,000 sim games per player written by run_week.py; the Slips tab prices the same way in the browser and keeps its log in that browser)
- `oddspapi_backfill.py` – pre-kickoff DK/FD TD prices for past weeks from OddsPapi (raw histories cached under `data/oddspapi_raw/`, names from `/v4/players`)

## Secrets
`ODDS_API_KEY` (the-odds-api.com). Odds are pulled around Sat 9:23am and Sun 9:23am PT (~95 credits/week); the
8:37 and 9:52 slots only pull if the newest saved pull is older than 60 minutes, and any run skips the pull if one
happened in the last 45 minutes (`decide_odds.py`). A run without a fresh pull reuses the newest file in
`odds_history/` for games that have not kicked off and labels the board "odds as of <time>".

## Scheduling and an external kick (cron-job.org)
GitHub cron can fire late (the Sunday 9:45 run went off at 12:51 PT once), so the workflow has three Sunday and
three Saturday slots and also accepts a `repository_dispatch` event. To add an external scheduler:
1. GitHub → Settings → Developer settings → Fine-grained personal access tokens → Generate. Repository access: only
   `endzone-push`. Permissions: **Contents: Read and write** (the `dispatches` endpoint is gated on Contents for
   fine-grained tokens; Actions: write is not enough on its own). Copy the token.
2. cron-job.org → Create cronjob. URL `https://api.github.com/repos/macassvic-cmd/endzone-push/dispatches`,
   method POST. Headers: `Authorization: Bearer <token>`, `Accept: application/vnd.github+json`,
   `X-GitHub-Api-Version: 2022-11-28`, `Content-Type: application/json`.
   Body: `{"event_type":"update","client_payload":{"use_odds":"on"}}` (`"cond"` to pull only if the newest saved
   pull is older than 60 minutes, `"off"` for no pull). Schedule e.g. Sundays 09:25 America/Los_Angeles.
3. Save. A 204 response means the workflow was queued; check Actions → Update projections.
The idempotence rule above means an external kick and a GitHub cron minutes apart cost one pull, not two.

## Local run
    pip install -r requirements.txt
    python refresh.py && python results.py

## Decision-pull record, card cap and stakes (2026-10-29)

- **Primary record** = the capped card at each game's decision pull, at that pull's prices (`decision.py`, `decision_log/<season>_w<week>.json`).
  Sunday early / late games: the first build at or after 9:23 AM Pacific (the last actionable build before 9:23 if the first one after it is inside
  the 30-min kickoff cutoff). TNF / SNF / MNF / London / Saturday: the last build before the cutoff. A game with no build at its decision time is
  "missed" and has no primary-record bets. The first-seen edge log stays as the secondary research record.
- **Card cap**: 25 actionable bets per kickoff window (the `pull_windows.json` cluster), one alt rung per player-stat. Quota (2026-10-31):
  main-line yard edges (the Results `by_market` bucket `yds`; alt rungs do not count) get first claim on up to 10 of the 25 slots by EV at the
  median book, the rest fill by EV at the median, and unused floor slots flow back so the card still reaches 25 when enough edges exist. A row
  on the card only by the floor is tagged "yard floor". The rest show greyed as "over card cap" with their rank, go to `research/card_cap_*.json`
  and never reach the edge log, Discord or the record.
- **Stakes**: alt rungs 0.25u for every role (created at that stake); Bench plays 0.25u; first-TD edges priced +2000 or longer with model P more
  than 2x the market sit in the bench section at 0.25u whatever the role. The flag rule itself is unchanged.
- **Blends**: `blend.blend()` is the one blend function. Anytime TD, 2+ TD and yards run 50/50 everywhere (board, grading, research). First TD
  runs the fitted weeks 1-6 blend stored in `blend_model.json` (coef ≈ [1.192, 0.649, 0.753]), restored by CEO ruling 2026-10-31 over the
  between-inputs and game-sum gates it fails (`blend.GATE_WAIVED`), frozen as stored (`blend.freeze_approved`: the weekly refit is reported
  beside it, never used) and applied from the week 8 Sunday decision pull on; weeks 3-7 stay frozen under the 50/50 ruler.
- **2+ TD**: no actionable edges (CEO ruling 2026-10-31, `rules.DEAD_MARKETS`): never on the card, in Discord, in the edge log or the record.
  The 2+ TD tab is a likelihood list (players by model P(2+ TD), fair price, best price and book for reference) for 2-3 player parlays; the
  parlay builder still reads p_2plus. The two graded week-5 2+ TD bets stay in the frozen record.
- **My bets**: `python placed_bets.py add "Name Anytime TD" --book FanDuel --price +650 --stake 1` logs a placed bet to `placed_bets.jsonl`;
  graded on its own Results card, never in the model record.
- Scheduled crons in `update.yml` are written in PST from 2026-11-01 (the Sunday 9:23 PT run is 17:23 UTC).
- The loop finalize commits new log directories file by file (`git status --untracked-files=all`, fixed 2026-10-31: `decision_log/` had been
  listed as one directory line and never reached main).

## Credits rule (2026-10-18)
**No Odds API calls outside CI.** `odds._get` refuses a live call unless the process runs in GitHub Actions or `ODDS_LIVE=1` is set;
`python run_week.py` on a laptop replays the newest saved pull for the week (`odds_history/`, same as `ODDS_MOCK=<file>`) and says so;
`python run_week.py --live` is the only way to spend credits by hand. Tests use saved pulls and fixtures. The plan (100K credits) resets
on the **1st of each month**; `odds.budget_projection` projects the balance at the reset from the planned pipeline burn and the trailing
3-day observed burn, and under 10,000 projected it skips the us_ex line-shop pulls first, then halves the kickoff-loop cadence, with a
Discord alert (`DISCORD_WEBHOOK_URL`). Check the balance for free: `GET https://api.the-odds-api.com/v4/sports/?apiKey=...` and read the
`x-requests-remaining` header.

## Pick'em scanner (`pickem/`, `pickem.html`)
Prices every Underdog NFL line (fantasy points, yards, receptions, attempts, completions, TDs) off devigged
sportsbook props from The Odds API, then searches every 2–6 pick combo of the best legs for the highest-EV entries.
Fantasy legs are simulated from each player's component props (Underdog half-PPR); same-game legs are correlated
(scale calibrated to DraftKings SGP prices, `PICKEM_CORR_SCALE`), different games independent.
Payout = Underdog base table × each pick's `payout_multiplier` (`PICKEM_BASE` to override).
Runs from `.github/workflows/pickem.yml` Sun 8:40 PT (~105 Odds API credits); extra runs via Actions → Run workflow.

    python -m pickem.scan            # needs ODDS_API_KEY; UD_MOCK / ODDS_MOCK for offline reruns
    python -m pytest pickem

## Pull loops (10-minute boards before each kickoff cluster)

`pull_windows.py` turns the week's kickoff times (nflverse schedule, Eastern -> UTC, DST automatic) into loop windows: one per
distinct kickoff, 3 h before it to the kickoff; a kickoff whose window starts within one 10-min step of the previous cluster's last
kickoff joins it (1 pm + 4:05/4:25 = one loop; London 9:30 stays separate). The scheduled runs write `pull_windows.json` a week
ahead. `pull_dispatch.yml` (cron `13,43 * * * *`, off the hour because GitHub delays or drops round-minute schedules under load: on
2026-10-18 a `*/30` cron fired 5 times in 19 hours) starts a `Pull loop` run for each cluster due within 90 min that has no run yet; it also
accepts `repository_dispatch` type `pull_dispatch` from an external cron (same cron-job.org recipe as above, body
`{"event_type":"pull_dispatch"}`, every 30 min), and every finished loop dispatches the next cluster due within 3 h itself, so Sunday's
loops chain even when the scheduler skips. `pull_loop.yml` waits for its exact start, then every 10 min pulls SharpAPI + Kalshi (Odds API
only with `use_odds`), builds, and publishes an orphan snapshot to the `board` branch, which GitHub Pages serves; it stops at the
cluster's last kickoff, hands over to a chained second job past the 6 h cap, and commits to main once per job (first pull of each
UTC hour + the newest pull, first-seen edge/research/parlay logs merged key-wise against origin/main; every pull is a run artifact),
in a step that runs even if the loop failed. Refresh the board by hand: Actions -> Pull loop -> Run workflow (mode `once`).
