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

## 2026-10-01 — Free odds combination live: SharpAPI primary for DK/FD, The Odds API trimmed

- **SharpAPI** (`sharpapi.py`, `SHARPAPI_KEY` secret) is the primary source for DraftKings and FanDuel anytime TD,
  first TD (FanDuel) and every yardage market, main and alternate lines (kept under The Odds API's keys
  `player_rush_yds`, `player_reception_yds`, `player_pass_yds` and `*_alternate` for the yard ladders). Matching uses
  `player_name`; team-defense and header rows are skipped. Fixtures are paged with `pagination.next_cursor` through
  the outright-heavy event list until the 6-day horizon passes; duplicate listings of a game keep the entry with the
  most books. Spreads and totals from SharpAPI feed the game lines when The Odds API is not pulled.
- **The Odds API** prop pull is trimmed to `player_tds_over` (2+) and `player_pass_tds` (QB ladders); spreads and
  totals unchanged. Credits per Sunday pull on a 16-game slate: **66 before → 34 after** (18 if passing TDs are
  dropped too). The other books (BetMGM, BetRivers, Bovada, BetOnline) no longer contribute anytime/first-TD
  prices; set `ODDS_API_PROPS=all` to restore the full market set.
- **Merge** (`merge_sources` in run_week.py): events are matched by teams; a book present from both sources keeps
  the fresher quote per market (SharpAPI row `timestamp` vs The Odds API market `last_update`); every outcome
  carries `source` and `ts`. If SharpAPI fails or the key is missing, the run falls back to the full Odds API
  market set so the board never goes blank. Started-game skip, partial-board guard, Kalshi liquidity floor and
  odds_history saving are unchanged; SharpAPI is pulled on every run (free), The Odds API only when `decide_odds`
  says so.
- **Dry run, week 4, live SharpAPI + Kalshi, no Odds API credits:** 20 fixtures in 26 SharpAPI requests, 16 of 16
  games with live lines, 358 players priced on anytime TD (DK 1,700 rows, FD 4,910, Kalshi 921 markets), 357 on
  first TD, 41 on 2+ (Kalshi only until Saturday's Odds API pull), per-book holds DK 1.17 / FD 1.21 / Kalshi 1.00,
  pull saved to `odds_history/2026_w4_20261001T0719Z.json`, 31 edges.
- **Bug fixed on the way:** the weak-spot column became a pandas string dtype whose missing values are NaN, which
  is truthy, so the Edge Board loop skipped every player (0 edges). Pushed on 2026-09-28; the first live odds run
  after it would have shown an empty board. The check is now NaN-safe.

## 2026-10-01 — Results tab: two-column layout, bets by week, running charts; two fixes

- Below the scorecard the tab is a two-column grid on desktop (one column under 860 px): left Priced Bets, right
  Market Brier and Closing Line Value. Tables inside cards wrap to fit their column instead of scrolling sideways.
- **Edge bets by week**: one row per week plus a cumulative row with bets, record, units, expected units and ROI,
  and the same four columns for bets shorter than +1000; a market filter (All / Anytime / First TD / 2+ TD /
  Parlays); each week tagged "closing" (priced from the pre-kickoff backfill) or "live" (flag-time prices).
  Parlay paper trades of both modes count as the Parlays market.
- **Charts** (inline SVG, no library): cumulative units by week, actual vs expected, with a toggle for bets shorter
  than +1000; cumulative anytime-TD Brier, model vs books vs blend, row-weighted.
- Role split kept as a compact table inside Priced Bets; parlay paper-trade lines underneath.
- Fix: a missing `backfill_price` value is NaN, which is truthy, so week 3's live bets were being tagged as closing.
- Fix: the `SHARPAPI_KEY` secret value carries a trailing newline, which made the CI run's SharpAPI pull fail with
  an invalid header (the fallback filled the board from the saved pull, as designed); the client now strips the
  key. Re-saving the secret without the newline is tidier but no longer required.
- Checked with a headless render of every tab; the phone-width behaviour rests on the media query, not on a
  measured layout, since no browser is attached to this session.

## 2026-10-01 — Yard ladders: rushing, receiving, passing (shipped for all three)

`yards.py` simulates yards inside the TD game sim so a player's yards and TDs share one game environment: team plays
come from recency-weighted pace tied to the sim's game factor (correlation 0.3), pass rate from the team's
recency-weighted pass share plus a spread term (favorites run more), carries / targets from recency-weighted shares
of team attempts / targets built by the same `build_slate` machinery as the TD shares (same shrinkage, snap cap,
roles, redistribution; volume priors by position and depth rank), sacks removed from both attempts and targets,
yards per touch shrunk 60 touches toward position averages with iid per-touch noise plus a per-game rate shock.
The spread of outcomes is gamma (clipped-normal was the alternative). Per player: mean, median and P(≥ rung) for
every rung the books offer plus the default ladder (rush / rec 25-40-50-60-75-100, pass 200-225-250-275-300).

Backtest gate (`yards_backtest.py`, 2024 + 2025 weeks 4–18, depth-chart regime, n=3000): median absolute error
against a season-average baseline (the player's mean over earlier games this season, else last season) and 5-point
ladder-probability buckets with 1.96·√(p(1−p)/n) ranges, pooled over rungs.

| Kind | Rows | MAE median / baseline | Buckets within range | Largest misses (pred / actual) |
|---|---|---|---|---|
| Rushing | 6,072 | 10.6 / 12.9 | 16 of 20 | 20-25: 22 / 26 ± 3, 50-55: 52 / 60 ± 5 |
| Receiving | 8,836 | 15.0 / 16.9 | 15 of 19 | 15-20: 17 / 15 ± 1, 75-80: 77 / 84 ± 4 |
| Passing | 896 | 58.0 / 66.6 | 15 of 15 | — |

Variants on the way: clipped normal (rush 17/20, rec 6/19, pass 6/16; passing biased 5–10 points high because
sacks were counted as attempts); gamma with sacks out of attempts only (rec 11/19); gamma with sacks out of targets
too is the shipped version. Rushing runs 2–4 points low on the 15–35% rungs (RB medians below actual at high
volume; QB rushing is the reverse), receiving 2 points low at 15–20%. All three beat the baseline by 1.9 / 1.9 /
8.6 yards; shipped with those biases noted, and `yards.SHIP` keeps each kind switchable.

Pricing (`yard_prices.py`) from SharpAPI's DraftKings / FanDuel main and alternate lines (week 4 pull: 88 rushers,
154 receivers, 33 passers with a line; FanDuel carries most alternates, 779 rush / 1,576 rec rungs vs DK's 108 /
164): no-vig both sides where a book quotes Over and Under at the line, otherwise the Over divided by that book's
own main-line hold (DK 1.057, FD 1.065). Two filters: within a book, a higher line can never be likelier than a
lower one, anchored on the book's main line (193 of ~3,000 rungs dropped in week 4; SharpAPI showed DK alternates
like "Over 9.5 +2000" next to a main 25.5 at −113), and when books disagree by more than 30 points, or a rung has
one quote, only prices within 15 / 35 points of the model are used. Blend: fitted model+market when the
leave-one-week-out gate passes, else 50/50 (no graded yard rows yet, so 50/50). An edge needs EV ≥ 5% at the median
book and at the best book, 2+ books, no weak-spot player, and a line of at least 10 yards (100 passing) — sub-10
rungs like "Over 0.5 rush yds" are shown on the ladder but never flagged.

Page: a Yards tab (after Parlays) with median, ladder probabilities, fair odds per rung, best price and book, EV,
and a pick'em column (our median vs the main line, lean Over / Under); yard edges on the Edge Board with role tags
(week 4 from the saved pull: 6 edges, all at main lines, e.g. Omarion Hampton Over 45.5 rush at −114 with the model
at 68% vs the books' 50%). Results: yard edges are graded on actual yards from play-by-play, paper-traded as their
own market (Yards filter in bets by week), and CLV-tracked against the last pre-kickoff pull.

## 2026-10-01 — OddsPapi re-pull with raw ids: weeks 1–3 re-graded at 98% price coverage

Second pass of `oddspapi_backfill.py --run 2026 1 2 3` (62 requests this time: 45 game histories re-pulled with the
raw responses kept, 3 fixtures, 2 name maps, 1 `/v4/players` list, 3 fixtures again and 3 Monday-night games after
the window fix; 115 of 250 used this month). What changed:

- Names now come from `GET /v4/players?sportId=14` (one request, 43,893 ids), cached in `data/oddspapi_players.json`,
  instead of the live board, which only names players priced that week; 0 ids unmapped (232 before).
- The fixtures window ended at 23:59Z on the last game day, so every Monday-night game (00:20Z Tuesday) was missing:
  16 games per week now, not 15.
- "Last, First" flip also collapses split initials ("Moore, D J" → DJ Moore); a nickname fallback against the week's
  slate maps "Cameron Skattebo" / "Kenneth Gainwell" to our Cam / Kenny (same last name, first names sharing three
  letters, unique in the slate, so "Charvarius Ward" stays himself).
- The feed prices defenders too, and a same-name player on another team was being matched by name: the Jaguars'
  Josh Allen at +7500 landed on the Bills' Josh Allen and "won" 75 units in week 2. An outcome whose slate player is
  not on either team in that game is dropped (1 per week).
- Raw histories are cached once (50–110 MB per game) and a TD-markets-only slim copy (~3 MB) is kept, so a
  name-only rerun takes seconds and no requests.

Coverage of slate players: 307 / 314, 344 / 350, 339 / 347 in weeks 1 / 2 / 3 (250 / 287 / 291 before); of players
projected at 15%+, 176 / 182, 172 / 176, 158 / 161. Still unpriced: DJ Moore, De'Von Achane, Woody Marks and Jacory
Croskey-Merritt have no DraftKings / FanDuel rows in the OddsPapi archive at all (no id-0 rows either), so they stay
out; Breece Hall and Caleb Williams are in.

**Model vs market Brier (DK + FD, no-vig median book), updated:**

| | Anytime: model / market / 50-50 | n | First TD: model / market / 50-50 | n |
|---|---|---|---|---|
| Week 1 | 0.1362 / 0.1328 / 0.1336 | 307 | 0.0354 / 0.0343 / 0.0347 | 307 |
| Week 2 | 0.1094 / 0.1084 / 0.1082 | 344 | 0.0384 / 0.0383 / 0.0383 | 344 |
| Week 3 (live books) | 0.1345 / 0.1341 / 0.1336 | 299 | 0.0413 / 0.0417 / 0.0414 | 298 |
| Season to date | 0.1260 / 0.1244 / 0.1244 | 950 | 0.0383 / 0.0381 / 0.0381 | 949 |

Adding the missing players and the Monday games moved week 1 against the model (0.1309 → 0.1362 model, 0.1264 →
0.1328 market) and week 2 toward it (0.1172 → 0.1094, market 0.1176 → 0.1084). Season to date the books lead by
0.0016 on anytime TD and 0.0002 on first TD; the 50/50 blend ties the market on both. The fitted blend still loses
leave-one-week-out (anytime 0.1256 vs 0.1244), so 50/50 stays in use.

**Edge Board on real prices, 1 unit per pick (re-scored):**

| | Picks | Won | Units | Expected | Shorter than +1000: picks / won / units / expected |
|---|---|---|---|---|---|
| Week 1 (closing) | 98 | 4 | −60.2 | +41.2 | 20 / 3 / +3.8 / +4.3 |
| Week 2 (closing) | 56 | 3 | −10.7 | +23.3 | 15 / 1 / −10.7 / +2.6 |
| Week 3 (flag time) | 37 | 5 | +75.4 | +15.2 | 11 / 2 / −1.2 / +2.9 |
| Combined | 191 | 12 | +4.5 | +79.8 | 46 / 6 / −8.1 / +9.8 |

The earlier +43.7 included the mis-attributed Josh Allen win; without it the board is +4.5 units on 191 picks,
carried by one +6600 first-TD hit, and 6–40 for −8.1 units on picks shorter than +1000.

## 2026-10-02 — Game chips in kickoff order

- Every game record now carries `kick_utc`; the page sorts games by kickoff everywhere (chip bar, the first-TD
  receive selectors, the bring-backs Game column), with games already under way moved to the end and greyed.
- Chips show the kickoff in Pacific time ("Thu 5:15 PM") and are grouped with small labels: Thu / Sat / Sun intl
  (before 9 AM PT) / Sun early / Sun late / Sun night / Mon, plus "Started".
- On load the chip bar scrolls to the next game that has not kicked off.

## 2026-10-02 — Paper-only gates for TD singles and yard edges; yards head-to-head vs the book

- **TD singles stay paper-only.** The Edge Board's TD section is labelled "Paper only — model has not beaten
  closing prices" until the season-to-date 50/50 blend beats the no-vig market by at least 0.001 Brier on anytime
  TD (today: blend 0.1244 vs market 0.1244, so the label stays). results.py writes the flag (`paper_only.td`).
- **Yards head-to-head from week 4 on** (`yards_h2h` in results.json, "Yards vs book" card on the Results tab next
  to Market Brier): for every main line we priced, model P(over), the no-vig book P(over) and the outcome; weekly
  and season-to-date Brier for model, book and 50/50 blend, by kind for the season; and "closer to the actual
  yards: our median or the book line" as a count and share (ties excluded from the share). Lines where the player
  had no touches are skipped (books void), as are pushes.
- **Yard edges stay paper-only** ("Paper only — yards model has not beaten the book over 3+ weeks") until the model's
  Brier at the main lines is better than the book's over at least 3 graded weeks (`paper_only.yds`).
- **Fitted blend for yards**: once 3+ weeks are graded, results.py fits the same logistic blend on the pooled yard
  rows (market key `yds`) under the leave-one-week-out gate; run_week uses it for every yard EV when it passes,
  else 50/50.
- Edge Board now shows TD edges and yard edges as two sections, each with its label; the Yards section states the
  10-yard (100 passing) line floor.

## 2026-10-02 — Underdog slip pricer: receptions, pass attempts and half-PPR fantasy inside the game sim; Slips tab

Sim (`yards.py`): every player's receptions (Binomial of the simulated targets at a catch rate shrunk 40 targets
toward the position rate: RB 0.786, TE 0.718, WR 0.633), QB pass attempts and interceptions (per-QB INT rate shrunk
300 attempts toward 2.22%), fumbles lost at league rates per touch (RB 0.47%, WR 0.7%, TE 0.6%) or per dropback
(QB 0.57%), and half-PPR fantasy points (0.5/rec, 0.1/rush+rec yd, 6/TD from the TD sim, QB 0.04/pass yd, 4/pass TD
from the sim's passing-TD count, −2 INT, −2 fumble lost) are all drawn in the same sim game as the yards and TDs, so
a slip's legs share the game environment, the team's plays and pass rate, and each player's own volume draw.

Draws (`run_week.py`): 2,000 sim games per player for Starters and Rotational players plus every QB (194 players in
week 4) are written to `draws.bin` (1.9 MB: receptions, rec / rush yards and pass attempts as bytes, pass yards at
2-yard resolution, fantasy at 0.1) with an index in latest.json; the workflow commits it with the board.

Pricer (`slips.py` and the Slips tab, same arithmetic, checked equal on a 4-leg slip): paste legs as "Player higher
14.5 fantasy" (stats: fantasy, receptions, rec yds, rush yds, pass yds, pass attempts; higher/lower, over/under,
more/less); per leg P(hit) and our median; joint P = share of sim games where every leg clears (keeps same-game and
same-player correlation); product of legs under independence, with the joint ÷ product ratio shown; EV = joint ×
payout − 1 at a typed payout (defaults 2→3x, 3→6x, 4→12x, 5→20x, 6→27x) and the break-even payout. Ties on
whole-number lines count as misses at pricing (Underdog voids the leg). Example from week 4's board: Josh Allen
higher 249.5 pass yds + higher 19.5 fantasy prices at a joint 35.7% against a 29.1% product (legs help each other),
+6.9% EV at 3x.

Log and grading: every priced slip is logged in the browser (localStorage, with a copy-as-JSON export) and the
Results tab's "Slips (this browser)" card grades it once the week is fully graded, from the per-player actuals
results.py now exports (`actuals`: receptions, rec / rush / pass yards, pass attempts, half-PPR points): a leg with
no row (player did not play) or a tie is void, void legs drop the slip to the smaller slip's payout, fewer than two
live legs refunds it. Record, units and expected units are shown; the log lives in the browser that priced it, not
in the repo.

Backtest gate (`yards_backtest.py --tag stats`, 2024 + 2025 weeks 4–18, single legs: receptions, fantasy, pass
attempts; rungs 2–8 receptions, 5–25 fantasy, 25–40 attempts; P(stat ≥ rung) in 5-point buckets with ± ranges, and
MAE of the median against the season-average baseline):

Run one season at a time (the pooled run died on memory): first with the volume draws as shipped, then with
game-to-game variability added. Diagnosis: with fixed per-game shares and pass rate the simulated receptions,
attempts and fantasy were too tight (actual residual variance 1.45× / 2.0× / 1.5× the simulated), so low rungs were
under-priced and high rungs over-priced. Fix (`yards.py`): each sim game draws the player's share of carries /
targets from a Beta around his mean (concentration 15 for carries, 60 for targets; 15 for both over-dispersed
targets and rec yards) and the team's pass rate gets a 0.06 game-script shock. Pooled 2024 + 2025, weeks 4–18:

| Stat | Legs | Buckets in range, before → after | MAE median / baseline |
|---|---|---|---|
| Receptions | 8,900 | 5 of 20 → 16 of 20 | 1.2 / 1.3 |
| Fantasy (half-PPR) | 9,796 | 7 of 20 → 13 of 20 | 3.7 / 4.1 |
| Pass attempts | 896 | 11 of 20 → 16 of 20 | 6.6 / 8.2 |
| Rush yds | 6,072 | 15 of 20 → 14 of 19 | 10.6 / 12.9 |
| Rec yds | 8,836 | 15 of 19 → 13 of 18 | 15.0 / 16.9 |
| Pass yds | 896 | 13 of 15 → 14 of 15 | 57.9 / 66.6 |

Receptions and attempts now sit within 1–2 points of actual across the range; fantasy is still 2–3 points tight at
the tails (7% rungs hit 10%, 83% rungs hit 79%), so fantasy legs near the ends should be read with that in mind.
The yard ladders move by a bucket either way with the same MAE and stay shipped. Shipped; the "calibration
pending" label is gone and the numbers sit on the Results tab ("Slip stats backtest") and in `slips_backtest.json`.

## 2026-10-03 — Bet record restored and frozen; no paper labels; slip log moved into the repo

**Bug:** Saturday's scheduled run graded weeks 1–3 down to nothing (results.json showed 3 bets, all week-4 yards).
Cause: once week 4's first game was final its rows joined the graded table and brought the `weak_spot` column
with them; weeks 1–3 have no tag there, pandas fills NaN, and `if r.get("weak_spot")` is true for NaN, so every TD
row was skipped by the re-scorer. Fixed with the same NaN-safe check used elsewhere (unit-tested on a NaN row).
The record is back at the last reported figures: 12–179 overall (+4.5u, expected +79.8u), 6–40 shorter than
+1000 (−8.1u), Bench 6–143 / Rotational 2–15 / Starter 4–21.

**Frozen record** (`bets_frozen.json`, committed by the workflow): once a week is fully graded its bets are stored
with the rule in force at the time and never recomputed, so a later blend refit or rule change cannot rewrite
history. "Under current rules" is a separate re-score of every week with today's rule and blend, shown as one
clearly labelled line under Priced Bets, never replacing the record. Weeks 1–2 froze on this run; week 3 freezes
on the next run with fresh schedule data (its Monday game is not final in the local copy).

**Page:** every "paper only" label and note is gone (Edge Board banners, the Yards-vs-book sentence, parlay
"paper" tags); the gating flags are still computed in results.json (`paper_only`) as data. Priced Bets shows every
market together with the full toggle (All / Anytime / First TD / 2+ TD / Yards / Parlays, always present) and a
side-by-side line for Yards, TD markets and Parlays under the all-markets headline.

**Slips:** the log now lives in the repo. `python slips.py "..." "..." --payout 12 --log` appends the priced slip to
`slips_log.jsonl`; the Slips tab's "Copy command" button prints that line for the slip just priced, so it can be
logged from any device, and results.py grades the file once the week is complete (void legs drop to the smaller
payout, under two live legs refunds). The browser keeps only the draft text. Same-player legs (a QB's pass yards
and fantasy) are tagged with a note that Underdog may block or reprice them, and the slip is also priced without
them (joint, payout and EV at the smaller size), on the tab, in the CLI and in the log.

## 2026-10-03 — NaN-safety audit: shared helper, shared Edge Board rule, tests

The same bug bit twice: a field read with `.get()` or tested for truth on a DataFrame row where a column was missing
from one slate (NaN after concat) — NaN is truthy, so `if r.get("weak_spot")` skipped every row, and `r.get(x) or 0`
returns NaN. Audit of every `.get` / `if x` / `or` default on row and JSON fields in results.py, run_week.py,
parlay.py, clv.py, yards and odds code:

- `nansafe.py`: `val / flag / text / num / isnan` — missing, None, NaN, pd.NA and NaT all read as absent; works on
  dicts, iterrows Series and itertuples rows. Used everywhere a row field can be NaN.
- `rules.py`: the TD Edge Board rule (`td_edges`) moved out of run_week.py so the live board and the tests share one
  NaN-safe implementation (`qualifying_rows` recounts it with plain masks for the tests). Reproduces the committed
  week-4 board's 45 TD edges exactly.
- Fixed sites beyond the two known ones: `kalshi_liquid` read as True when the Kalshi column was NaN (display
  only), `games` would have crashed on `int(NaN)`, a NaN role would have vanished from the role split (`groupby`
  drops NaN keys) — roles now default to "Unknown"; parlay legs read role / weak spot / books / Kalshi flags safely;
  CLV role split likewise. Raw `.get` remains only on JSON and API dicts, where None is the missing value.
- `test_nan_safety.py` (no pytest needed): helper semantics; the Edge Board rule and the re-scorer on frames where
  one week lacks the weak-spot and Kalshi columns, asserting edges(concat) = edges(A) + edges(B) = an independent
  mask count; NaN in a gate column drops exactly that row; parlay leg pool with NaN fields. `--slow` runs
  results.main() twice on the real slates, once with the weak-spot column removed from the first slate, and asserts
  identical graded-player and bet counts per week. The workflow runs the fast tests before every projection run.

## 2026-10-08 — Week 4 review: parlays paper-traded in both modes, Kalshi placeholder quotes excluded, confirmation filter tested

**Results tab for week 4** — confirmed present in results.json and rendered: yards head-to-head (273 main lines,
model Brier 0.2549 vs book 0.2524 vs 50/50 0.2495; our median closer than the line on 127 of 273, 47%), market
Brier by week (week 4 n=360 anytime), CLV (127 flagged edges, +0.22 pts average, 57% beat the close).

**Parlays.** Only "best value" was being paper-traded, and only by accident of timing: results.py graded the paper
flags from the slate's *final* state, but every later run rebuilds the parlays (games kicked off, prices moved), so
the Thursday "most likely" picks were overwritten by Monday's single-game pool (10 legs from one game, zero
parlays) and the Saturday value parlay replaced Thursday's. Fix: `parlay_log/<season>_w<week>.json` freezes each
mode's top-3 paper parlays the first time they exist in a week (run_week.py, committed by the workflow) and
results.py grades from that log. Week 4's first-flag parlays were recovered from git history (the Oct 1 23:07 UTC
slate): likely 3 (Gibbs + Henry / Walker / McCaffrey at Kalshi, +908 to +1041), value 3 (Boston + Gesicki + Allen
(+ Rice / Warren), all Kalshi longshots). The Saturday-built Hutchinson + Wilson value parlay that was graded
before is no longer in the record; it was a rebuild, not the first flag. Graded: likely 0–3 (−3.0u, expected −1.2u), value 0–3 (−3.0u, expected +2.9u).

**Kalshi placeholder quotes.** Hutchinson's 2+ TD at +4893 was a resting 2c ask with a 1c bid, $489 at the ask, 1,114
contracts of all-time volume and nothing traded in 24 hours: a market-maker placeholder that the $100-at-ask rule
let through. `kalshi.is_liquid` now also requires an ask of at least 4c (at 2c the 1c tick is half the price), a bid
within 2c of the ask, and some trading (24-hour volume, or 100+ contracts all-time). On week 4's quotes: Hutchinson
and Gesicki (4c/2c, no 24h volume) become reference-only, Wilson (5c/4c, 431 traded) stays liquid. Value-mode legs
still need Starter role and 2+ books or a liquid Kalshi quote.

**Confirmation filter (tested on saved pulls, not adopted).** For every logged edge of weeks 3–4 (164 graded), the
no-vig median-book probability was re-priced from the week's first saved pull and from the pull at flag time.
Weeks 1–2 have one pull (the OddsPapi closing backfill) and no flag time, so they carry no movement. The filter
cannot be evaluated yet: 59 edges were flagged on the first pull itself (movement is zero by construction), 44 had
no price at one end (2+ TD and yards appear only once SharpAPI / Kalshi quote them), and only 11 moved by a point
or more:

| Group | n | Record | Units | Expected | CLV | Blend Brier |
|---|---|---|---|---|---|---|
| All logged edges | 164 | 23–141 | +31.2 | +55.9 | +0.18 (n 49) | 0.0816 |
| … shorter than +1000 | 59 | 18–41 | −2.3 | +9.4 | +0.12 (n 18) | 0.1498 |
| Moved toward us (≥ +1 pt) | 6 | 1–5 | −2.0 | +1.3 | 0.00 (n 6) | 0.1050 |
| Moved against us (≤ −1 pt) | 5 | 2–3 | +13.5 | +2.3 | +0.25 (n 4) | 0.2115 |
| Within 1 pt or flagged on the first pull | 109 | 15–94 | +54.3 | +36.5 | +0.19 (n 38) | 0.0857 |
| No movement measurable | 44 | 5–39 | −34.5 | +15.9 | — | 0.0535 |

By market, logged edges: anytime 9–72 (+22.5u, −7.0u under +1000), first TD 1–39 (+27.0u on one +5600 hit), 2+ TD
0–23 (−23.0u, all Kalshi longshots), yards 13–7 (+4.7u). Verdict: no evidence either way on confirmation; the
sample that moved is 11 edges. Because edges are almost always flagged on the first priced pull, a usable version
would compare the flag pull to the *closing* pull, which is what CLV already measures (+0.18 pts, 57% beat the
close over 49 edges). Nothing on the Edge Board changed. `confirm_filter.py` reruns the study.

**Model vs books Brier, season to date (4 weeks):** anytime TD model 0.1272 vs books 0.1264 (50/50 0.1260,
n 1,310); first TD 0.0390 vs 0.0387 (n 1,307); 2+ TD 0.0531 vs 0.0524 (n 230, one week); yards 0.2549 vs 0.2524
(n 273, one week). The books lead every market; the 50/50 blend edges the books only on anytime TD.

## 2026-10-08 — Bet record split: live weeks vs backfill

Weeks 1–2 were never bet live: they were rebuilt after the fact at closing prices under the pre-fix rules that
overrated bench players (7–147, −70.9u against +64.6u expected). They now sit outside the record:

- Every week 1–2 bet carries a `backfill` tag (results.json `bets`, also the `backfill_price` flag from before).
- **Priced Bets** headline, cumulative row, role split, ROI, expected units, parlay lines and the units chart cover
  live weeks only (week 3 onward: 17–69, +91.2u, expected +60.1u), with a toggle "Since current rules (week 4+)"
  (12–37, +15.8u). The scope is remembered in the browser. Weeks 1–2 live in a collapsed **Backfill** section
  under the table with their own rows and cumulative line, excluded from everything above. The bet log is split
  the same way (live log, collapsed backfill log).
- **Market Brier, calibration and CLV** default to live weeks, with a checkbox to include the backfill weeks.
  Live weeks only (659 priced anytime rows over weeks 3–4): model 0.1323 vs books 0.1329, 50/50 blend 0.1318 — the
  model is a hair ahead of the books on the weeks it was actually live; all weeks: 0.1272 vs 0.1264. Season
  figures are re-weighted from the per-week rows in the page. CLV rows exist only for live weeks (backfill has no
  flag time), so that card only changes its label.
- **Weekly scorecard** keeps every week (it measures projections, not bets) and gains a second total row, "live
  weeks only" (2 weeks: Brier 0.131, top-15 hits 17 of 30), next to the all-weeks total.
- results.json: `record_scopes` (live_from 3, rules_from 4, backfill_weeks [1, 2]), `season_total_live`,
  `calibration_live`, and a `backfill` flag on each market-Brier week. "Under current rules" stays a re-score of
  every week including backfill and is labelled as such.

## 2026-10-08 — Yard ladders: pooled market curve, every rung judged, alt-rung edges

Why there were no yard edges: alternate rungs rarely share an exact line across DraftKings and FanDuel, so almost
every rung failed the 2-book / median-book rule. Now (`yard_prices.market_curve`):

- **Market curve per player and stat.** Both books' main line and every alternate rung, no-vig (two-sided rungs
  against their own Under, one-sided alternates against the book's main-line hold), pooled: duplicate lines are
  averaged, the sequence is made non-increasing (pool-adjacent-violators) and joined linearly in log-odds. No
  extrapolation outside the lines the books priced. The curve counts when 2+ books contribute or one book posts
  4+ rungs.
- **Bad rows.** The replay first produced 142 "edges" at +1200 to +2200 with EV above +700%: SharpAPI's DraftKings
  alternates include rows like "Over 14.5 rec yds +2200" (a line the book cannot mean) that the per-book monotone
  filter misses when DK has no two-sided main line to anchor on. Each quote is now checked against the other
  books' own monotone curve at that line (drop if more than 30 pts away), or against the model's ladder when no
  other book covers the line (35 pts). On week 4's Saturday pull that drops 769 quotes; the surviving quotes are
  the only ones a rung can be offered at.
- **Rule.** Every offered rung: 50/50 (fitted when gated) blend of the model's P(≥ line) and the curve at that exact
  line; edge if EV ≥ 5% at the offered price, the line is inside the priced range, the curve is eligible, line ≥ 10
  (100 passing), no weak spot; best-EV rung per player and stat. A rung that is not the book's main line is tagged
  "alt rung", carried as its own market (`yds_alt`) on the Edge Board, in the bets-by-week toggle, CLV (closed on the
  curve when the rung is no longer quoted) and the Results record, graded separately from main-line yard edges.
- **Yards tab** shows per rung: model / market-curve / blend %, offered price and book, EV; plus the curve's rung
  count and books. Hover a rung for the long form.

**Week 4 replay** (`yard_alt_replay.py --week 4 --pull 20261003T1852Z`, model P per rung from the week's slate
interpolated in log-odds, graded on actual yards): 610 player-stats, 249 with an eligible curve, **80 edges, all
alternate rungs**, 69 graded: **19–50, −7.2u against +17.4u expected** (average EV +25%, average price +577). Two
kinds of rung make the board: short rungs where the model is far above the curve (Jeanty over 14.5 rush at −185,
model 96% vs curve 77%: these mostly won) and tail rungs at +600 to +1700 where a 20% model sits over a 10% curve
(these mostly lost). The frozen week-4 record is unchanged; this is a replay, not a re-score.

## 2026-10-08 — Yard ladders, second pass: main lines kept, alt rungs capped at +300 and 2× the curve

The first curve rule let the best-EV rung replace the main line and flagged tail rungs at +600 to +1700 that lost
the way TD longshots do. Now two categories, both always evaluated:

- **Main line** (`yds`): the original rule at the book's main line — 2+ books at that line, EV ≥ 5% at the median
  and best book, model-anchored cross-book check — unchanged from the rule that went 6–1 in week 4.
- **Alt rung** (`yds_alt`): every other rung against the pooled curve, flagged only at prices shorter than +300 and
  when the model's P(≥ line) is at most 2× the curve's; best-EV rung per player and stat. Candidates that clear 5%
  EV but fail the price or ratio test are written to `research/alt_rungs_<season>_w<week>.json` (first sighting,
  committed by the workflow), never flagged; results.py grades them by price band (`research_alt`).
- Yard edges are now graded from the edge log (first flag, flag-time prices, whichever rule flagged them) instead
  of the slate's final edge list, so a rule change between runs cannot drop or replace them. Week 5's 14 alt-rung
  edges flagged under the first rule stay in the log (no `rule` tag) and are graded next to the new rule's edges,
  which carry `rule = "alt rung v2"`.

**Week 4 replay** (`yard_alt_replay.py --week 4 --pull 20261003T1852Z`, Saturday pull, 610 player-stats, 249 with
an eligible curve):

| Category | n | Record | Units | Expected |
|---|---|---|---|---|
| Main line | 4 | 3–1 | +1.7 | +0.3 |
| Alt rungs flagged | 48 | 26–22 | +13.0 | +9.3 |
| … shorter than −150 | 9 | 7–2 | +1.8 | +2.1 |
| … −150 to +150 | 8 | 8–0 | +6.9 | +2.6 |
| … +150 to +300 | 31 | 11–20 | +4.2 | +4.6 |
| Research (not flagged) | 173 | 18–155 | −48.3 | +37.1 |

(The frozen week-4 record's 7 main-line yard bets, 6–1, came from the Sunday pulls; the Saturday pull alone
yields 4.) Every flagged band is positive; the +150 to +300 band is the thin one (11–20, carried by prices). The
research set beyond +300 lost 48 units against +37 expected: the model's tails are too fat on yards, same as on
TDs, and the cap keeps them off the board.
