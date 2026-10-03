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

## Pick'em scanner (`pickem/`, `pickem.html`)
Prices every Underdog NFL line (fantasy points, yards, receptions, attempts, completions, TDs) off devigged
sportsbook props from The Odds API, then searches every 2–6 pick combo of the best legs for the highest-EV entries.
Fantasy legs are simulated from each player's component props (Underdog half-PPR); same-game legs are correlated
(scale calibrated to DraftKings SGP prices, `PICKEM_CORR_SCALE`), different games independent.
Payout = Underdog base table × each pick's `payout_multiplier` (`PICKEM_BASE` to override).
Runs from `.github/workflows/pickem.yml` Sun 8:40 PT (~105 Odds API credits); extra runs via Actions → Run workflow.

    python -m pickem.scan            # needs ODDS_API_KEY; UD_MOCK / ODDS_MOCK for offline reruns
    python -m pytest pickem
