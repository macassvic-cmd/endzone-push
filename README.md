# Endzone Lab

NFL touchdown projections: anytime TD, first TD (with opening-kickoff adjustment), red-zone usage,
bring-backs, live sportsbook edges, and a Results tab that grades every week.

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

## Secrets
`ODDS_API_KEY` (the-odds-api.com). Odds are pulled Sat 10am PT and Sun 9:45am PT only (~95 credits/week).

## Local run
    pip install -r requirements.txt
    python refresh.py && python results.py

## Pick'em scanner (`pickem/`, `pickem.html`)
Prices every Underdog NFL line (fantasy points, yards, receptions, attempts, completions, TDs) off devigged
sportsbook props from The Odds API, then searches every 2–6 pick combo of the best legs for the highest-EV entries.
Fantasy legs are simulated from each player's component props (Underdog half-PPR); same-game legs are correlated
(scale calibrated to DraftKings SGP prices, `PICKEM_CORR_SCALE`), different games independent.
Payout = Underdog base table × each pick's `payout_multiplier` (`PICKEM_BASE` to override).
Runs from `.github/workflows/pickem.yml` Sat 10:30 PT and Sun 8:40 / 12:40 PT (~150 Odds API credits per run).

    python -m pickem.scan            # needs ODDS_API_KEY; UD_MOCK / ODDS_MOCK for offline reruns
    python -m pytest pickem
