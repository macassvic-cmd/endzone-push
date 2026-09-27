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

## 2026-09-27 — Round 2: fitted xTD, redistribution, rookies and new arrivals

Judge as in round 1; Brier on the rows shared by all variants (base 4945 rows). Base: Brier 0.13194, top-15 7.87
hits vs 7.63 expected per week.

1. **Fitted xTD** (`fit_xtd.py`, coefficients in `xtd_model.json`, applied by `model.fitted_xtd`). Logistic models on
   2022–2024 regular season: rush on yardline (plus log and goal-line indicators), down, distance, goal-to-go,
   shotgun and the QB-carry flag; targets on yardline, air yards, end-zone flag, pass location, down, distance,
   goal-to-go. The xTD models themselves are better out of sample on 2025 (log-loss rush 0.0972 vs 0.1001 for the
   bucket tables, targets 0.1152 vs 0.1266; gradient boosting no better than logistic) and calibrate well by
   bucket. Plugged into the share model they worsen player Brier to 0.13214 and cut top-15 hits to 7.47, although
   the 30–35% bucket improves (32.4/34.0 vs 38.8 actual). **Off** (`XTD_FITTED = False`), kept for re-testing.
2. **Redistribution when a regular is out** (`REDIST`). Measured on 2023–2026 single-absence cases (1,024): when a
   player with ≥8% share misses a game, his listed same-position teammates take only about a third of his share,
   the next man up (least-sampled same-position player) about a fifth, other positions about 15%, and the rest is not
   replaced, whereas proportional stretching hands all of it to the listed players. The model now computes the
   stretch as if the absent player were playing and then splits his share 35% same position (proportional), 20% next
   man up, 15% other positions, 30% unreplaced. Brier 0.13194 → **0.13170** (beyond the ±0.0001 seed noise), top-15
   7.67 / 7.46 (−3 hits over 225 picks, inside noise). **Kept.** Team-specific shrinkage toward the default (K≈4) is
   not implemented: with 1–3 prior absences per player the estimate returns the league fractions anyway.
3. **Rookies and new arrivals** (`NEW_PLAYERS`, `ROOKIE_W`, `MOVER_W`). Depth-listed active players with no history
   get a row at their slot prior, rookies' priors are nudged by draft round (2022–25 rookie-season shares: RB rush
   R1 0.35 / R2–3 0.22 / R4–7 0.11 / UDFA 0.03; WR rec 0.21 / 0.084 / 0.043 / 0.015; TE rec 0.12 / 0.09 / 0.05 /
   0.015, blended 50/50 with the slot prior), and veterans on a new team keep half their sample weight. On top of
   redistribution: Brier 0.13168 (no change), top-15 hits 7.47; the 184 added no-history players hit 2.2% against
   4.9% predicted, and halving their prior only lowered top-15 hits further (7.27). **Off** (code kept; the rookie
   table is in `ROOKIE_PRIOR`).

Week 3 rerun from saved odds with redistribution on: 47 edges (38 bench, 7 starters, 2 rotational), 9 shown by
default. QB edges: Stafford anytime and first TD, Goff anytime, Winston anytime.

## 2026-09-27 — Mobile-QB under-prediction: diagnosed, no change kept

**Diagnosis** (2025 weeks 4–18, model lambdas and pass fractions vs actual, by QB1 designed runs per game):

| Tier | QB-games | Team rush TDs proj / act | Pass frac proj / act | QB share of rush TDs proj / act | P(QB rush TD) proj / act |
|---|---|---|---|---|---|
| <0.75 | 226 | 0.89 / 0.85 | 0.64 / 0.65 | 0.105 / 0.094 | 8.5% / 8.0% |
| 0.75–1.5 | 115 | 0.87 / 0.93 | 0.64 / 0.62 | 0.163 / 0.215 | 13.2% / 15.7% |
| 1.5–2.5 | 55 | 1.06 / 1.38 | 0.60 / 0.50 | 0.235 / 0.342 | 21.8% / 32.7% |
| 2.5+ | 52 | 1.00 / 0.98 | 0.60 / 0.58 | 0.326 / 0.353 | 27.0% / 28.8% |

For the mid tier it is both parts: the team's rushing TDs run 30% above projection (pass fraction 0.60 vs 0.50) and
the QB's share of them is 0.34 vs 0.24. The 2.5+ tier is calibrated on both in 2025.

**Variants tested** (`model.py` hooks, all off): (a) lighter prior for QBs with 20+ games (K 1.5); (b) pass_frac −=
0.03 / 0.06 × (designed runs − 1); (c) separate prior line above 1.5 designed runs (0.12 + 0.08 × dr); (d) the
QB-specific TD-rate table for designed runs and scrambles only. Judged on pick weeks 4–11 and confirm weeks 12–18 in
2025 and again in 2024.

- 2025 confirm weeks, mid tier projected / actual: base 21.5 / 34.6; (a) 21.7; (b 0.03) 22.7; (c) 23.3; (d) 25.0.
  Overall Brier moved by 0.00002–0.0003 (worse in every case but within noise for a–c), QB Brier worse for b–d
  because the 2.5+ tier scored 15.0% in those weeks against 30–39% projected; top-15 hits 7.71 → 7.71 (a, c), 7.57
  (b 0.03), 7.29 (b 0.06).
- 2024: the mid tier is 16 / 15 QB-games and scored 37.5% then 6.7%; the 2.5+ tier scored 36% then 59% against 25%
  projected. (b) and (c) help QB Brier in 2024 and hurt it in 2025 for the same reason: they move the 2.5+ tier.
- A steeper single prior slope (smooth: QB_PRIOR_B 0.08 and 0.10) was the remaining candidate; its runs were stopped
  by a low-memory event and are untested.

**Decision:** nothing kept. The aggregate under-prediction of mobile QBs is real in both seasons, but every tier is
15–30 QB-games of 3–5 quarterbacks and flips sign between halves, so no tested adjustment improves the mid tier on
held-out weeks without hurting overall or QB Brier. Revisit with 2026 data, smooth-slope test first.

## 2026-09-27 — Fix: in-game odds pulls inflated every market probability

The delayed Sunday run pulled odds at 12:52 PT, after the early games had kicked off. Books strip an in-game board
down to a few players, so the per-game overround measurement (sum of implied Yes prices ÷ the 4.1-scorer anchor)
collapsed to ~0.07 and every no-vig market probability was divided by it: the live board showed 166 edges with
market probabilities summing to 7.3 per team. Fixes in `odds.py`: `fetch_all` skips games that have already
started (also saves credits); `measure_hold` ignores boards with fewer than 8 priced players and clamps the
overround to [1.0, 2.0]. Regenerated from the same pull: 22 fresh edges for the six late games plus 25 locked from
earlier kickoffs.
