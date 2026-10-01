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

## 2026-09-28 — Never blank prices, reliable Sunday timing, mobile-QB weak-spot tag

- **Saved-pull reuse.** A run without live odds reuses the newest `odds_history` file for the week for games that
  have not kicked off; the header and Edge Board say "odds as of <time>" and edges are logged for CLV with that
  pull's stamp. Locked games keep their pre-kickoff prices as before. An explicit past week whose games have all
  kicked off now exits without rewriting anything (it used to crash).
- **Scheduling.** Cron slots moved off round minutes with redundancy: Sun and Sat 8:37 (cond), 9:23 (on), 9:52
  (cond) PT, Tue 10:07 and Thu 12:07 (off), Sun 1:15 (off, reuses the saved pull). `decide_odds.py` sets the mode:
  on = pull unless a pull happened in the last 45 minutes; cond = pull only if the newest saved pull is older than
  60 minutes. `repository_dispatch` (`event_type: update`, `client_payload.use_odds`) lets an external scheduler
  kick the run; README has the cron-job.org steps (fine-grained token, this repo only; GitHub gates the dispatches
  endpoint on Contents: write). Times are PDT; they shift an hour when PST starts in November.
- **Weak-spot tag.** QBs with 1.5+ designed runs per game (the under-predicted tiers from the mobile-QB round) are
  tagged "weak spot" on the Anytime and First TD tabs and kept off the Edge Board until that fix lands. Week 3:
  Daniels, Jackson, Allen, Hurts, Murray, Nix, Lawrence, Watson, Willis, Shough.

## 2026-09-28 — Grading fixes from week 3

- **Grading runs.** New no-odds slots Sun 9:07 PM, Mon 7:13 AM and Tue 7:13 AM PT; results.py regrades on every
  run until all games are final, and the scorecard shows "14 of 15 graded" with a partial tag until then.
- **Label.** "Last graded week" is now "<season> Week N top 40 · graded of total games graded".
- **CLV repaired.** The 12:52 PT in-game pull had logged 127 edges with inflated market probabilities (the 166-edge
  board), which made average CLV −34 points. Those entries are purged from `edge_log/2026_w3.json`; the 8 legitimate
  fresh edges from the corrected board are re-logged with that pull's stamp. The closing pull must now be strictly
  before kickoff and hold a complete board for that game (a book pricing 8+ players in the market); otherwise the
  previous pull is used. Week 3 recomputed: 50 edges, average CLV +0.2 pts, 66% beat the close (anytime 74%, first
  TD 42%; starters 44%, bench 71%). The slate's bet record was already clean (37 bets, max market probability 0.26).
- **Honest bet reporting.** Expected units (sum of EV at the prices taken) next to units won, a variance note under
  100 graded bets, and record/units/expected excluding +1000 or longer. Week 3: 5-32 for +75.4u against +13.2u
  expected; excluding +1000 or longer, 2-9 for −1.2u against +2.9u expected.
- **TNF.** The Thursday 12:07 PT run projects the week with the Thursday game (kickoff is after noon) and the
  Saturday run locks it with its pre-game projections; week 3 lacked it only because the workflow did not exist
  that Thursday.

## 2026-09-29 — Scorecard fixes

- **Weeks 1–2 re-backfilled with the current model** (`backfill.py` rewritten): walk-forward, pre-game data only,
  same active-player rules as the live run (last depth chart before the week, Out/Doubtful and inactive-roster
  players removed, snap share over the previous 3 games, rookies' draft buckets, redistribution, role tags). Lines
  are the nflverse schedule lines; no odds or weather. Still tagged "backfill". Week 1 Brier 0.1372, week 2 0.1089.
- **1st TD in our top 3** now shows the expected count (sum of each game's top-3 first-TD probabilities), e.g.
  "2 / 14 exp 4.2". Week 3's 2 of 14 against 4.2 expected is the weak spot of the season so far.
- **"40%+ picks" → "40%+ scored–missed"** with the expected count.
- **Season-total row** at the bottom of the scorecard (Brier weighted by players): 2026 through week 3, 46 of 47
  games graded, scorers 172 / 174.7 expected, top-15 26 / 45 (exp 22.4), 40%+ 30–31 (exp 28.9), first TD top-3
  19 / 46 (exp 13.5), Brier 0.1278.
- **Overflow**: flex children could not shrink, so wide tables widened the page instead of scrolling; `#view` and
  the wrapper's children now have `min-width:0` and `.tbl` a max width, so the table scrolls inside itself.

## 2026-09-29 — Early-season calibration: pattern confirmed in 2024 and 2025, prior-shrinkage fix rejected

The 2026 calibration table (which now includes the re-backfilled weeks 1–2; with them the spread shrank but
remained: 0–5% bucket 3.4% projected vs 5.9% actual, 20–25% 22.3% vs 18.4%) prompted a look at weeks 1–3 in the
2025 and 2024 walk-forward backtests, same depth-chart regime, current model.

| | 0–5% | 5–10% | 10–15% | 15–20% | 20–25% | Brier |
|---|---|---|---|---|---|---|
| 2025 weeks 1–3 (984 rows) | 3.3 / 10.2 | 7.3 / 10.5 | 12.2 / 9.5 | 17.3 / 12.0 | 22.5 / 19.1 | 0.1443 |
| 2025 weeks 4–18 | 3.3 / 7.0 | 7.2 / 5.8 | 12.4 / 11.2 | 17.4 / 17.2 | 22.5 / 22.4 | 0.1317 |
| 2024 weeks 1–3 (1,004 rows) | 3.3 / 5.2 | 7.2 / 10.1 | 12.4 / 6.7 | 17.5 / 9.7 | 22.5 / 20.0 | 0.1247 |
| 2024 weeks 4–18 | 3.2 / 6.3 | 7.2 / 8.1 | 12.5 / 11.8 | 17.4 / 16.2 | 22.4 / 24.5 | 0.1346 |

Same shape in both seasons: the 15–20% bucket scores 10–12% early and 16–17% later; the 5–10% bucket scores about
10% early and 6–8% later. Not 2026 noise.

Fix tested (`EARLY_K_EXTRA`): players with fewer than 3 current-season games get a prior weight of
K × (1 + extra × (5 − week) / 4), fading to normal by week 5, so weeks 5–18 are untouched by construction. On
2024+2025 weeks 1–4 pooled (2,684 shared rows) it makes things worse at every strength: Brier 0.13655 (off) →
0.13680 / 0.13727 / 0.13774 for extra 1 / 2 / 3, top-15 hits 8.00 → 7.88 / 7.75 / 7.62 per week, and the 0–5% and
5–10% buckets stay under-projected (about 7% and 11–12% actual against 3% and 7%). Leaning harder on the
position/slot prior does not fix it: the early-season error is that shares are too concentrated relative to how
teams actually spread touches in September, which a slot-mean prior does not change. **Nothing changed.** Next
candidate: regress each team's share vector toward uniform (not toward slot means) in weeks 1–3, or a smaller
first-season weight on last year's shares.

## 2026-09-30 — 2+ TD market, game-to-game share variability, parlay builder

**2+ TD validation.** The sim's `p_2plus` under-predicted 2+ TD games in both backtests (2025: 2.47% mean vs 3.05%
actual over 5,929 rows; 2024: 2.36% vs 2.94%), and the top-10 2+ candidates per week hit 1.8–2.1 times against 1.6
expected. Fix tested: each sim game draws every player's share from a Beta around his mean (concentration k),
renormalised per team (`SHARE_VAR_KAPPA`).

| k (2024+2025 weeks 1–18 pooled) | 2+ mean pred / act | 2+ Brier | Anytime Brier | Top-15 hits/exp |
|---|---|---|---|---|
| 0 (old) | 2.41 / 3.00 | 0.02747 | 0.13334 | 7.75 / 7.42 |
| 30 | 2.58 / 3.00 | 0.02744 | 0.13337 | 7.86 / 7.38 |
| **15 (kept)** | 2.72 / 3.00 | 0.02742 | 0.13339 | 7.83 / 7.34 |
| 8 | 2.93 / 3.00 | 0.02743 | 0.13348 | 7.67 / 7.29 |

k=15: every 2+ bucket inside its ± range in both seasons and pooled (e.g. 10–15%: 11.8 / 13.4 ± 3.3; 15–20%:
17.1 / 17.8 ± 6.6; 20%+: 23.8 / 32.2 ± 11.9), anytime Brier flat within seed noise, top-15 not hurt. k=8 matches the
mean exactly but costs 0.0003 anytime Brier in 2024. Full-resolution 2025 backtest with k=15: Brier 0.1316.

**Odds.** `player_tds_over` added to the prop pull (Over 1.5 = 2+ TDs; one more market per event, roughly a third
more props credits per pull). One-sided like anytime, so it is de-vigged with its own anchor: 0.68 players with 2+
offensive TDs per game (2024–25). Kalshi's "Player: 2+" markets attach as the same market. The hold measurement for
this market uses only the 1.5 line.

**Page / results.** New "2+ TD" tab (projection, fair odds, best price and book, EV, Kalshi, role tag), 2+ TD edges on
the Edge Board (same rules, market "two"), and in Results: 2+ grading in the top-40 table, paper trades and CLV split
as their own market, market Brier for 2+.

**Parlay builder** (`parlay.py`, "Parlays" tab). Legs: Starter role, priced 2+ TD market with 2+ books or a liquid
Kalshi quote, blended probability, EV ≥ 10% at the best book, no weak-spot QBs. Best 2-, 3- and 4-leg parlays with
every leg from a different game; probability = product of leg probabilities; price = product of best-book decimal odds
or a typed book price; fair odds, EV, "hits about 1 in N", and a 1/8-Kelly stake capped at 0.25% of bankroll. The
top 3 by EV are paper-traded at 1 unit each week; Results reports record, units, expected units and legs hit vs
projected with a luck-vs-legs note.

Backtest (model-only probabilities, top legs with p_any ≥ 0.35 as the Starter stand-in, one per game, 2024+2025
weeks 4–18, 15 parlays per size per season): 2-leg predicted 5.2% hit 10.0% (3 of 30, ± 10.7); 3-leg predicted 1.0%
hit 6.7% (2 of 30, ± 8.9); 4-leg predicted 0.17% hit 0 of 30. Legs: 21.4% projected vs 30.0% hit over 90 legs
(2025 35.6%, 2024 24.4%). Predicted and actual match within the ± range, so the builder ships, with the caveat that
30 parlays per size is a weak test and the top legs so far run above projection rather than below.

Week 4 has no saved odds pull yet, so the 2+ tab and parlays show prices only after Saturday's first pull.

## 2026-09-30 — Parlays tab reworked into two modes; free odds sources evaluated

- **Most likely** (default): Starter legs ranked by the model's 2+ TD probability regardless of market disagreement,
  parlays ranked by joint probability. Works without prices (week 4 shows 15 parlays before Saturday's pull).
- **Best value**: the EV-ranked builder (legs at 10%+ EV with real liquidity).
- Both: every leg from a different game, no weak-spot QBs. Per leg: model and market probability, best price and
  book, leg EV (red when negative) and the break-even price ("needs +X+") to shop books or Kalshi for. Per parlay:
  joint probability, fair odds, offered odds (best-book product or a typed book/SGP price), EV, "hits about 1 in N",
  1/8-Kelly stake capped at 0.25%, shown as "no bet, needs +X+" when EV is not positive at the price.
- Results paper-trades the top 3 priced parlays of each mode separately (1 unit each) with record, units, expected
  units and legs hit vs projected, so the two approaches can be compared.
- `ODDS_SOURCES.md`: SharpAPI, OddsPapi and SportsGameOdds free tiers evaluated from their public docs, with a
  recommended free combination. Headline: OddsPapi's archive starts January 2026, so no 2024–25 prop prices exist
  for a real-price backtest; SportsGameOdds counts one game as one object, so a full Sunday pull is 16 of the 2,500
  monthly objects. Nothing in the pipeline changed.

## 2026-10-01 — OddsPapi backfill: real pre-kickoff prices for 2026 weeks 1–3

`oddspapi_backfill.py --run 2026 1 2 3 --books draftkings,fanduel` (free tier, 52 requests: 1 name map, 3 fixtures,
48 historical). Per game, the last active snapshot before kickoff of DraftKings and FanDuel anytime-TD and first-TD
prices, written to `odds_history/2026_w{1,2,3}_<first kickoff − 1 min>Z.json`. Neither book carried the 1.5-TD
line in the archive, and Pinnacle has no player TD markets at all, so there is no 2+ backfill and no Pinnacle
reference; the planned Pinnacle column is dropped. 15 of 16 games per week came back from the fixtures endpoint.

Names: the archive returns numeric player ids; one live `odds-by-tournaments` call mapped 433 of 690 ids (the rest
belonged to teams whose week-4 props were not posted yet). After flipping "Last, First", 250 / 287 / 291 of our
slate players in weeks 1 / 2 / 3 are priced, and 80–86% of players projected at 15%+ (notable gaps: DJ Moore,
Breece Hall, Caleb Williams, De'Von Achane). The writer now keeps raw responses in `data/oddspapi_raw/` so a later
name map can fill those without new historical requests.

results.py prices backfilled slates from the week's earliest saved pull with the live method (no-vig per book,
median book, 50/50 blend) and builds Edge Board picks with the live rules (2+ books, EV ≥ 5% at the median and best
book). Those picks are at **closing** prices, not flag time, and are tagged `backfill_price`.

**Model vs market Brier (DK + FD, no-vig median book):**

| | Anytime: model / market / blend | n | First TD: model / market / blend | n |
|---|---|---|---|---|
| Week 1 | 0.1309 / 0.1264 / 0.1278 | 250 | 0.0287 / 0.0270 / 0.0277 | 250 |
| Week 2 | 0.1172 / 0.1176 / 0.1163 | 287 | 0.0396 / 0.0392 / 0.0392 | 287 |
| Week 3 (live Odds API books) | 0.1345 / 0.1341 / 0.1336 | 299 | 0.0413 / 0.0417 / 0.0414 | 298 |
| Season to date | 0.1275 / 0.1261 / 0.1259 | 836 | 0.0369 / 0.0364 / 0.0366 | 835 |

The books beat the model slightly overall (anytime 0.1261 vs 0.1275), the model edges them in week 2, and the
50/50 blend is best on anytime TD season to date. Week 1 is the model's worst week by a margin.

**Edge Board on real prices, 1 unit per pick:**

| | Picks | Won | Units | Expected | Excl. +1000 or longer: picks / won / units / expected |
|---|---|---|---|---|---|
| Week 1 (closing) | 102 | 6 | −60.1 | +41.5 | 27 / 5 / +0.9 / +5.8 |
| Week 2 (closing) | 71 | 7 | +28.5 | +25.8 | 26 / 4 / −8.6 / +4.3 |
| Week 3 (flag time) | 37 | 5 | +75.4 | +13.2 | 11 / 2 / −1.2 / +2.9 |
| Combined | 210 | 18 | +43.7 | +80.5 | 64 / 11 / −8.9 / +12.9 |

Shorter than +1000 the board is 11–53 for −8.9 units against +12.9 expected; the overall plus comes from seven
long-shot hits. With 64 priced-under-+1000 picks the variance note still applies, but the model's "edges" at closing
prices have not beaten the books so far.

## 2026-10-01 — Fitted model+market blend (gated), edges re-scored on real prices

`blend.py`: logistic regression of outcome on logit(model) and logit(no-vig market), one fit per market, refit by
results.py on every graded priced row each run (`blend_model.json`); run_week.py uses it for every EV on the Edge
Board and in the parlay builder, replacing the fixed 50/50 and the thin-sample 25/75 rule. Numpy IRLS, no new
dependency. The fit is **used only when it beats 50/50 on leave-one-week-out Brier**; otherwise 50/50 stays in
force. An edge needs EV ≥ 5% at the median book and at the best book, 2+ books, no weak-spot players.

Fit on weeks 1–3 (836 anytime rows, 835 first-TD rows):

| | Coefficients a / b·logit(model) / c·logit(market) | Brier model / market / 50-50 / fitted (in-sample) | Held-out week: fitted vs 50/50 |
|---|---|---|---|
| Anytime | −0.02 / 0.25 / 0.80 | 0.1275 / 0.1261 / 0.1259 / 0.1255 | 0.1272 vs 0.1259 |
| First TD | 1.40 / 0.85 / 0.68 | 0.0369 / 0.0364 / 0.0366 / 0.0360 | 0.0374 vs 0.0366 |

The fit leans about 3:1 on the market for anytime TD and improves in-sample Brier, but with three weeks it loses
to 50/50 on every held-out week (each fold trains on two weeks), so **50/50 remains in use for both markets** and
the gate re-checks weekly as rows accumulate.

Re-score of weeks 1–3 under the live rule from stored prices (closing prices for weeks 1–2, flag time for week 3):

| Blend | Edges | Won | Units | Expected | Excl. +1000: edges / won / units / expected |
|---|---|---|---|---|---|
| Fitted (not in use) | 31 | 3 | −18.1 | +6.7 | 16 / 3 / −3.1 / +3.5 |
| 50/50 (in use) | 210 | 18 | +43.7 | +82.6 | 64 / 11 / −8.9 / +12.9 |

The market-heavy fit cuts the board from 210 to 31 edges and removes nearly all +1000 plays (15 left, 0 won); the
non-longshot subset is still negative under either blend. The bet record now comes from this re-scoring rather
than from the slate's edge list, so backfilled and live weeks are judged by one rule.

OddsPapi name gaps (DJ Moore, Breece Hall, Caleb Williams, Achane): a second name map cannot fill them because the
first run did not keep raw ids; a re-pull with raw saving (about 49 requests) would.
