# Endzone Lab

NFL touchdown projections: anytime TD, first TD (with opening-kickoff adjustment), QB passing-TD ladders,
QB + pass-catcher correlation, bring-backs, and live sportsbook edges.

**Site:** GitHub Pages serves `docs/`. A scheduled GitHub Action reruns the model and commits fresh
`docs/data/latest.json` (Tue, Thu, Sat, Sun morning after inactives, Sun afternoon).

## How the model works
- **Team TDs** from Vegas implied points (live consensus lines when the odds feed is on), fit on 2023–25.
- **Pass/rush split** from each team's recency-weighted expected-TD mix, adjusted for spread and wind (above 10 mph).
- **Player shares** of team rushing and receiving expected TDs, based on field position of carries/targets (not raw TDs).
- **60k-game Monte Carlo** with a shared game environment, giving correlated outcomes for stacks and parlays.
- **First TD** conditional on who receives the opening kickoff (receiving team scores first ~59% at even spread).
- **Backtest (2025, walk-forward):** anytime TD within ~1 pt of observed rate from 10–40%; Brier 0.153 vs 0.166 baseline.

## Setup
1. Settings → Pages → Source: *Deploy from a branch*, branch `main`, folder `/docs`.
2. Settings → Secrets and variables → Actions → New secret `ODDS_API_KEY` (free key at the-odds-api.com).
   Odds are pulled only Sat + Sun morning (~95 credits/week) to stay inside the free 500/month tier.
3. Actions → *Update projections* → *Run workflow* to populate the site the first time.

## Run locally
    pip install -r requirements.txt
    cd nfl && python refresh.py            # next unplayed week
    python run_week.py 2026 4              # explicit week (after refresh has downloaded data)
    python backtest.py                     # 2025 walk-forward calibration
