# Changelog

Judge for model changes: 2025 walk-forward backtest, weeks 4–18, depth-chart active set (`python backtest.py 0.35`,
fast variants via `python search.py`). Brier differences are compared on the rows shared by the variants; the
simulation seed alone moves Brier by about ±0.0001 at n=3000.

## 2026-09-27 — QB rushing fix

Problem: pocket QBs (Stafford, Goff, Rodgers) projected 9–12% anytime TD against a 4–5% market.

Causes found:
- Kneel-downs counted as rush attempts, so a victory-formation kneel at the opponent's 1 credited the QB with a 45%
  expected TD. Stafford had 21 kneels among 35 "carries". Kneels are now excluded everywhere (xTD tables, per-play
  xTD, inside-10 counts, red-zone tab). On its own this is Brier-neutral.
- QBs were stretched with the rest of the pool (listed shares scaled up to 92% of team xTD).
- One flat QB1 rush prior (4.2%) for every quarterback.

Changes (model.py):
- QBs are no longer stretched; teammates keep the same factor as before and the difference goes to "other".
- QB1 rush-share prior = 0.068 + 0.054 × designed runs per game (kneels excluded, capped at 4), fitted on 2025
  depth-chart QB1s. Stafford at 0.46 designed runs/game gets 9.3%, Hurts at 3.6 gets 26%.
- A QB-specific TD-rate table (sneak / designed / scramble by yardline) was built and tested; it over-predicts the
  immobile tier (9.9% vs 8.0%) so it stays off (`QB_OWN_XTD = False`).

Numbers (shared rows, n=3000):

| | Brier | QB Brier | QBs <0.75 designed runs/gm | 2.5+ | Top-15 hits / expected per week |
|---|---|---|---|---|---|
| Before | 0.13180 | 0.13216 | 9.0% pred / 8.0% actual | 23.9 / 28.8 | 7.53 / 7.84 |
| After | 0.13194 | 0.13096 | 8.0 / 8.0 | 24.4 / 28.8 | 7.87 / 7.63 |

Overall Brier is flat within noise; QB Brier improves and the immobile tier is calibrated. Mobile QBs are still
under-predicted (the 1.5–2.5 tier at 20.8% vs 34.5% actual) and are the next QB item.

Week 3 rerun from saved odds: 40 edges (was 45). Stafford 12.0% → 8.4%, Rodgers 11.0% → 5.7%, Goff 9.0% → 8.0%.
Remaining QB edges: Stafford anytime and first TD, Goff anytime; both took a goal-line sneak in 2026 and the
3-game prior does not yet outweigh it.

## 2026-09-27 — Market data round (no model changes)

- **Kalshi as a price source** (`kalshi.py`). Public market-data API, no auth. Series `KXNFLTD` holds per-game
  events with "Player: 1+" markets (anytime TD) and `KXNFLFIRSTTD` the first-TD scorer (players, D/ST, No Touchdown).
  Markets are matched to games by the event ticker's team codes and to players by `odds.norm_name`. Yes ask in
  dollars is the probability (no vig on a single price). Kalshi's fee is charged on the trade, not on winnings:
  taker fee = 0.07 × contracts × P × (1 − P), rounded up to the cent, so the price on the board is the fee-adjusted
  American price (at 0.56 the raw −127 becomes −132). Kalshi appears as one more book on the Edge Board, in the
  per-book hold measurement (anytime 1.02, first TD 1.20 this week) and in odds_history. Week 3 from saved odds:
  687 markets attached across 16 games; Kalshi is the best anytime price for 221 players; 40 → 43 edges.
- **Odds history.** Every real pull is saved to `odds_history/<season>_w<week>_<UTC stamp>Z.json` and committed by
  the workflow. Seeded with the two real week 3 pulls (Sat 08:09Z and 19:29Z).
- **Closing line value** (`clv.py`). Edges are logged the first time they appear (`edge_log/<season>_w<week>.json`)
  with the pull stamp, best price/book, median price and no-vig median-book probability. Closing = the last saved pull
  before that game's kickoff. CLV = closing no-vig probability − flag-time no-vig probability; "beat the close" = CLV
  > 0. results.json gets `clv` (overall, by role, by market, % beating the close) once the week is graded, shown under
  Priced bets. Dry run on week 3 with the 19:29Z pull as the close: 42 edges, average CLV −0.04 pts, 50% beat the
  close (bench 61%, starters 14%).
- **Kalshi liquidity.** Each Kalshi market now records contracts available at the ask, dollars at the ask and 24h
  volume. Kalshi only counts as a real price (best / median / EV) with at least $100 available at the ask; thinner
  quotes are shown on the Edge Board as "ref" and excluded from the no-vig consensus and from edges. Week 3: 534 of
  688 markets are liquid; 205 of the 221 players for whom Kalshi had been the best anytime price survive the filter.
- **Market Brier per week.** `market_brier.any.by_week` (model vs no-vig median-book vs 50/50 blend) added next to the
  season-to-date figure.

## 2026-09-27 — Round 1: hyperparameters, defense adjustment, odds history

Judge: 2025 walk-forward, depth-chart active set, n=3000 sims (`search.py`), Brier on shared rows.

1. **Hyperparameter grid** (144 cells: DECAY 0.80–0.95, PRIOR_SEASON_W 0.3–0.8, PRIOR_K 1.5–5, REC_SLOPE 0.25–0.6),
   picked on weeks 4–11, confirmed on weeks 12–18 without peeking.

   | | Weeks 4–11 Brier | Top-15 hits/exp | Weeks 12–18 Brier | Top-15 hits/exp |
   |---|---|---|---|---|
   | Current (0.88 / 0.55 / K=3 / 0.35; grid cell 0.90) | 0.13613 | 7.75 / 7.76 | 0.12767 | 8.00 / 7.58 |
   | Best pick cell (0.85 / 0.55 / K=5 / 0.60) | 0.13586 | 7.88 / 7.42 | 0.12761 | 7.71 / 7.26 |
   | K=5 only (0.90 / 0.55 / K=5 / 0.35) | 0.13601 | 7.75 / 7.59 | 0.12757 | 7.71 / 7.42 |

   The pick-week gain (0.00027) shrank to 0.0001 on the held-out weeks (seed noise is about 0.0001) and top-15 hits
   fell, so nothing changed. REC_SLOPE is flat across its range; PRIOR_K=5 is the only consistent small signal
   (marginal Brier 0.13603 vs 0.13619 at K=3). Values and the grid outcome are recorded in model.py.
2. **Defense adjustment** (`model.defense_table`): recency-weighted pass share of TDs allowed and red-zone TD rate
   allowed per defense, shrunk to league with K=6 games (pass-share deviation −0.11 to +0.11, RZ ratio 0.86–1.13).

   | Variant | Brier | Top-15 hits/exp |
   |---|---|---|
   | Off | 0.13194 | 7.87 / 7.63 |
   | pass_frac += 0.5 × deviation | 0.13201 | 7.67 / 7.63 |
   | pass_frac += 1.0 × deviation | 0.13217 | 7.67 / 7.65 |
   | λ × (1 + 0.5 × (RZ ratio − 1)) | 0.13180 | 7.67 / 7.69 |
   | λ × (1 + 1.0 × (RZ ratio − 1)) | 0.13208 | 8.00 / 7.74 |

   Pass-share adjustment hurts; the lambda tweak's 0.00014 gain is inside noise and costs top-15 hits. Both stay off
   (weights 0 in model.py), code kept for re-testing with more 2026 data.
3. **Odds history**: every real pull is saved and committed (`odds_history/`), see the market data round above.
