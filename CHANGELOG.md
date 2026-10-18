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

Note: the 23:42 UTC scheduled run on the previous commit failed in results.py (`clv.py`: the yard-rung closing path
variable was shadowed by the curve probability, `os.path.basename(float)`); fixed in this commit, and that run
committed nothing, so the board and record were untouched.

## 2026-10-08 — Pre-registered rule for alt rungs, old-vs-v2 comparison, CI smoke step

**Pre-registered before week 5 grades:** if v2 alt-rung edges in the +150 to +300 price band lose units in week 5,
the alt-rung price cap tightens to +150 from week 6 on. (Week 4's replay had that band at 11–20 for +4.2u, carried
by prices; the shorter bands were 7–2 and 8–0.)

- Week 5 carries both alt-rung rules on the same games: the 14 edges flagged under the first curve rule (frozen in
  the edge log, untagged) and the v2 edges (`rule = "alt rung v2"`). results.py grades them side by side
  (`alt_rules`: n, record, units, expected, by week and by price band) and the Results tab shows the comparison in
  an "Alt-rung rules · old vs v2" card under Yards vs book, with the research set (not flagged) underneath.
- CI: the workflow now runs the NaN-safety tests, refreshes data (`refresh.py --data-only`), smoke-tests
  `results.py` on the committed slates *before* any odds are pulled, then runs the week, grades, and publishes.
  The publish step runs even when grading crashes (`if: always()`), so a finished board is never left unpublished,
  while the job still fails loudly. The 23:42 UTC crash would have stopped at the smoke step with no odds spent.
- NaN-safety audit, helper and tests: already in place since the audit commit (nansafe.py, rules.py,
  test_nan_safety.py fast + `--slow` end to end); nothing further changed there.

## 2026-10-12 — Pre-registered gates (written before the week-5 studies were run)

- **Alt-rung cap test (+350 / +400).** The cap only rises above +300 if the incremental price band (+301 to +350 for a
  +350 cap, +351 to +400 for +400) holds at least 30 bets across at least 3 weeks AND its actual units are at least
  its expected units. Otherwise the cap stays at +300. Written before `alt_cap_study.py` was run on weeks 4–5.
- **Passing-yard rungs** come back onto the Edge Board only when the passing model's Brier beats the books at the
  main lines over at least 3 graded weeks combined (week 5 alone: model 0.2805 vs books 0.2499, our median closer on
  34%). Until then they are hidden and logged to `research/pass_yds_<season>_w<week>.json`.
- **Bench stake** goes back from 0.25u to 1u only after at least 100 bench bets with positive average CLV AND positive
  units once the single biggest win is removed.

## 2026-10-12 — Week 5 consolidated: headline fix, passing yards hidden, bench section at 0.25u, alt-rung cap decision, Results additions, EV / cap / ladder studies

No model changes: projections, shares, priors, sim, blend and yard models are untouched. Accounting, display, Edge Board
filters and research only. Every rule below is in `rules.py` (one place) and published to the page in `latest.json`
(`rules`) and `results.json` (`board_rules`), so the display never re-defines a threshold.

### A. Fixes

**1. Headline vs cumulative mismatch.** The Priced Bets headline (44–132, +127.12u) was built from `results.json`
`bets`, which holds singles only, while the market breakdown under it and the week table were built from
`bets_by_week`, which also carried the 8 paper parlays (44–140, +119.12u): the label said parlays were included, the
number did not. Now results.py builds one set of rows per week with three groups that are never mixed: **singles,
Starter + Rotational** (the headline), **Bench** and **Parlays**; by-market splits are per group; the cumulative row and
a server-side `record` object come from the same rows. Corrected headline (live weeks 3–5, as the week-5 record stood
before MNF): **singles, Starter + Rotational 36–77, +41.23u (expected +21.50u); shorter than +1000 33–49, +33.96u
(expected +13.81u)**; Bench 8–55, +85.89u (one +66u first-TD hit, expected +25.09u); parlays 0–8, −8.00u (expected
+2.60u). The three add back to the old 44–140, +119.12u. `test_reconcile.py` (52 checks) fails the run if the headline,
its market breakdown, the week-table cumulative and an independent recount of `bets` / the parlay log disagree by more
than 0.01u; the workflow runs it after the smoke grading step and after the real one.

**2. Passing-yard edges off the board.** Week 5 at the main lines: passing model Brier 0.2805 vs books 0.2499, our
median closer on 34% (16 of 47); rushing 0.2474 vs 0.2505, receiving 0.2562 vs 0.2555. Passing-yard rungs (main line
and alt) are now `HIDDEN_KINDS` in rules.py: run_week.py routes them out of the Edge Board, the edge log and the
record into `research/pass_yds_<season>_w<week>.json` (first sighting, committed by the workflow); results.py grades
that log silently (`research_pass`) and the Yards-vs-book card shows the gate. The Yards tab no longer lists passing
ladders (the slip pricer still prices pass yards). **Gate to re-enable (pre-registered above): the passing model's
Brier beats the books at the main lines over at least 3 graded weeks combined.** `yards_h2h.pass_gate` tracks it (2
weeks so far). Rushing and receiving are unchanged. Week 5's four passing-yard rungs already in the record stay there
(frozen rules); the "under current rules" view drops them.

**3. Alt-rung cap decision (pre-registered 2026-10-08).** Rule: if v2's +150 to +300 band lost units in week 5 the cap
would tighten to +150 from week 6. Week 5, v2 edges graded from the edge log at flag-time prices: all 18–6, +20.08u
(expected +3.69u); **+150 to +300: 8–6, +12.30u (expected +1.15u)**; −150 to +150: 4–0, +4.30u; shorter than −150:
6–0, +3.48u. The band did not lose, so **the cap stays at +300** (`yards.ALT_MAX_PRICE` unchanged). Beyond +300 stays
research-only: week 5's research set at +300 or longer went 20–64 (+82.1u on two long hits, expected +20.7u), and the
+350 / +400 test below failed its gate. The old-rule edges (first 14, no cap) went 2–11, +10.2u.

**4. Bench plays: kept, separate, 0.25u.** From 2026 week 6 (`rules.BOARD_RULES_WEEK`): Bench edges stay on the Edge
Board in their own **Bench / longshot** section at `BENCH_STAKE` = 0.25u (stake logged with the edge and used by the
grading); the headline record is Starter + Rotational singles; Bench has its own line on Results with record, units,
expected, ROI, CLV (average and % beating the close), CLV by week and the luck band. **2.5× block (newly added; it was
not in place before):** a Rotational player whose model probability is more than `MAX_RATIO_ROT` = 2.5× the no-vig
market probability is blocked from the board and logged to `research/ratio_block_<season>_w<week>.json`; Bench plays
that trip it sit in the bench section; Starters are not blocked. Every edge now carries `ratio` (model ÷ market) and
`stake`. **Bench gate (pre-registered above):** back to 1u only after 100+ bench bets with positive CLV and positive
units once the single biggest win is removed; `bench_gate` in results.json tracks it (63 bets, CLV +0.08 pts, 48%
beat the close, +19.89u without the +66u hit). Bench CLV by week: W3 +0.30 pts (71% of 38), W4 +0.40 (50% of 42), W5
−0.74 (12% of 26). The frozen records are untouched (bets_frozen.json unchanged, week 5 still open until MNF grades);
Results gains a **"weeks 3–5 under current rules"** view next to them: the same bets at the same prices with
passing-yard rungs out (4), Rotational plays over 2.5× out (2), Bench at 0.25u and parlays separate: singles 34–73,
+30.68u (expected +19.28u; shorter than +1000 32–48, +32.41u); bench 8–55, +21.47u at 0.25u (expected +6.27u);
parlays 0–8, −8.0u. The plain blend re-score (every week, 1u, no board rules) stays as its own labelled line.

### B. Results page additions

5. **Shorter than +1000 is the primary record** at the top of Results (33–49, +33.96u), with an "All prices" toggle
   (remembered in the browser); the chart, role table, market lines and the current-rules view follow the toggle.
6. **Luck band** next to actual vs expected: ±2σ with σ computed from the bet prices only (a bet that is exactly fair
   at decimal odds d has variance d − 1 per unit staked, so σ = √Σ stake²(d − 1)); each aggregate in results.json
   carries `var`. Headline singles: actual +41.2u vs expected +21.5u, band ±57.8u (0.7σ, inside). Shorter than
   +1000: +34.0u vs +13.8u, band ±37.4u (1.1σ, inside). Bench: +85.9u vs +25.1u, band ±80.8u (1.5σ, inside).
7. **Alt rungs by player:** each player's alt rungs combined into one line per week, net units (rungs on one player
   are correlated). Live weeks: 27 player-weeks, 17 won / 10 lost, +30.3u (expected +5.8u); shorter than +1000: 25,
   17 / 8, +22.3u. Rows are in `record.*.alt_players` (Drake Maye 2 rungs +14.55u, Justice Hill +9.20u, Jahan Dotson
   3 rungs +3.68u … five players with two rungs each −2.0u).

### C. Research (no board changes)

**8. Expected-value check.** Full re-score (every week including the backfill weeks, today's blend and edge rule, 1u
every role, 297 bets): actual +29.55u vs expected +85.12u. By market:

| Market (full re-score) | All: record | Actual | Expected | Actual − expected | Shorter than +1000: record | Actual | Expected | Actual − expected |
|---|---|---|---|---|---|---|---|---|
| Anytime TD | 16–166 | −23.29 | +64.49 | −87.78 | 11–65 | +1.06 | +15.92 | −14.86 |
| First TD | 10–46 | +19.89 | +11.51 | +8.38 | 8–25 | +17.62 | +6.47 | +11.15 |
| 2+ TD | 0–2 | −2.00 | +1.29 | −3.29 | – | – | – | – |
| Yards (main line) | 13–7 | +4.67 | +2.05 | +2.62 | 13–7 | +4.67 | +2.05 | +2.62 |
| Alt rungs | 20–17 | +30.28 | +5.78 | +24.50 | 19–13 | +22.28 | +4.68 | +17.60 |
| **All** | **59–238** | **+29.55** | **+85.12** | **−55.57** | **51–110** | **+45.63** | **+29.12** | **+16.51** |

The whole overstatement sits in anytime TD at +1000 or longer (101 bets, 5 won, −24.4u against +48.6u expected,
mostly bench players in the backfill weeks); every other market is at or above its expected units. Live weeks only
(the record), Starter + Rotational singles: anytime 3–27, −16.28u vs +7.12u expected; first TD 7–32, +22.87u vs
+7.93u; yards 6–1, +4.36u vs +0.67u; alt rungs 20–17, +30.28u vs +5.78u. Bench live: anytime 7–44, +30.89u vs
+19.75u; first TD 1–9, +57.0u vs +4.05u; 2+ TD 0–2. Nothing changed on the back of this.

**9. Alt-rung cap test, +350 / +400** (`alt_cap_study.py`; gate pre-registered above before the run). First-flag replay
over every saved pull of weeks 4 and 5 (the only weeks with alternate-line prices; weeks 1–3 have no yard markets or
ladders), oldest first, skipping games already kicked off: per pull every offered alternate rung is priced against the
pooled DK+FD curve, model P from the slate ladder interpolated in log-odds, 50/50 blend (what the live week-5 flags
used), EV at the best surviving quote; a rung qualifies with an eligible curve, line ≥ floor, EV ≥ 5%, model ≤ 2×
curve and price < cap; the best-EV rung per player-stat is recorded the first time its (player, stat, line) appears,
at that pull's price. The week-5 +300 replay reproduces 27 of the 28 live v2 edge-log keys (live 18–6 +20.08u,
replay 17–7 +18.69u).

| Cap | Week | Bets | Record | Units | Expected | ROI |
|---|---|---|---|---|---|---|
| +300 | 4–5 | 91 | 52–39 | +33.74 | +16.06 | +37.1% |
| +300 | 4 | 67 | 35–32 | +15.05 | +12.40 | +22.5% |
| +300 | 5 | 24 | 17–7 | +18.69 | +3.66 | +77.9% |
| +350 | 4–5 | 100 | 53–47 | +37.73 | +17.89 | +37.7% |
| +350 | 4 | 74 | 35–39 | +14.16 | +13.87 | +19.1% |
| +350 | 5 | 26 | 18–8 | +23.57 | +4.02 | +90.6% |
| +400 | 4–5 | 107 | 50–57 | +24.30 | +19.35 | +22.7% |
| +400 | 4 | 80 | 34–46 | +7.96 | +14.94 | +10.0% |
| +400 | 5 | 27 | 16–11 | +16.34 | +4.41 | +60.5% |

Incremental bands (bets flagged under the +400 run, by price; "displaced" = the longer rung replaced a sub-+300 rung
for the same player-stat rather than adding a player):

| Band | Week | Bets | Distinct players | Weeks | Record | Units | Expected | ROI | Displaced |
|---|---|---|---|---|---|---|---|---|---|
| +301 to +350 | 4–5 | 22 | 22 | 2 | 7–15 | +7.10 | +2.61 | +32.3% | 11 |
| | 4 | 17 | 17 | 1 | 4–13 | −0.50 | +2.15 | −2.9% | |
| | 5 | 5 | 5 | 1 | 3–2 | +7.60 | +0.46 | +152.0% | |
| +351 to +400 | 4–5 | 27 | 24 | 2 | 3–24 | −13.10 | +4.18 | −48.5% | 18 |
| | 4 | 19 | 19 | 1 | 3–16 | −5.10 | +2.98 | −26.8% | |
| | 5 | 8 | 8 | 1 | 0–8 | −8.00 | +1.19 | −100.0% | |

**Verdict: gate not met for either band** (+301 to +350: 22 bets over 2 weeks, ahead of expected only on 5 week-5
bets; +351 to +400: 27 bets over 2 weeks, −13.1u against +4.2u). **Cap stays at +300.**

**10. Ladder replay** (`ladder_replay.py`). For every player-stat with a flagged alt rung in the +300 replay (61 in week
4, 25 in week 5), at the pull where its lowest rung was flagged: rungs every 10 yards from that lowest flagged rung up
to the highest alternate line offered, each 10-yard step taking the offered line nearest to it within 2.5 yards (steps
with no quote that close are skipped: 192 in week 4, 43 in week 5), price = best surviving quote at that line in the
flag pull, model / curve / blend / EV as in item 9. "+300" drops rungs priced +300 or longer. Staking: (a) flat 1u per
rung; (b) 1u per player-stat split evenly across its rungs; (c) only rungs with EV ≥ 5%, 1u each. 79 ladders graded
(7 without actual yards skipped), 286 rungs uncapped, 123 under the cap.

| Cap | Staking | Week | Rungs | Record | Units | Expected | ROI | Biggest player win | Biggest player loss |
|---|---|---|---|---|---|---|---|---|---|
| +300 | (a) flat 1u/rung | 4–5 | 123 | 70–53 | +27.77 | +2.90 | +22.6% | K. Walker III rush W4 +5.99 | S. Barkley rush W4 −7.00 |
| +300 | (a) | 4 | 99 | 53–46 | +11.64 | +0.37 | +11.8% | | |
| +300 | (a) | 5 | 24 | 17–7 | +16.13 | +2.53 | +67.2% | | |
| +300 | (b) 1u/player split | 4–5 | 123 | 70–53 | +25.79 | +7.80 | +32.6% | M. Washington rec W4 +2.90 | O. Hampton rush W5 −1.00 |
| +300 | (b) | 4 | 99 | 53–46 | +11.17 | +5.28 | +19.6% | | |
| +300 | (b) | 5 | 24 | 17–7 | +14.63 | +2.53 | +66.5% | | |
| +300 | (c) EV ≥ 5% only | 4–5 | 79 | 47–32 | +32.28 | +13.73 | +40.9% | M. Washington rec W4 +2.90 | O. Hampton rush W5 −1.00 |
| +300 | (c) | 4 | 57 | 32–25 | +17.94 | +10.80 | +31.5% | | |
| +300 | (c) | 5 | 22 | 15–7 | +14.34 | +2.93 | +65.2% | | |
| uncapped | (a) flat 1u/rung | 4–5 | 286 | 93–193 | +22.07 | +25.87 | +7.7% | DK Metcalf rec W4 +30.70 | S. Barkley rush W4 −10.00 |
| uncapped | (a) | 4 | 219 | 68–151 | −2.16 | +16.49 | −1.0% | | |
| uncapped | (a) | 5 | 67 | 25–42 | +24.23 | +9.38 | +36.2% | | |
| uncapped | (b) 1u/player split | 4–5 | 286 | 93–193 | +11.41 | +8.77 | +14.4% | DK Metcalf rec W4 +6.14 | O. Hampton rush W5 −1.00 |
| uncapped | (b) | 4 | 219 | 68–151 | −1.21 | +5.58 | −2.1% | | |
| uncapped | (b) | 5 | 67 | 25–42 | +12.62 | +3.19 | +57.4% | | |
| uncapped | (c) EV ≥ 5% only | 4–5 | 213 | 68–145 | +36.08 | +43.86 | +16.9% | R. Odunze rec W4 +23.80 | B. Thomas Jr. rec W4 −7.00 |
| uncapped | (c) | 4 | 153 | 45–108 | +8.64 | +33.71 | +5.7% | | |
| uncapped | (c) | 5 | 60 | 23–37 | +27.44 | +10.15 | +45.7% | | |

Per-player net units, top and bottom: +300 flat: Walker III rush W4 +5.99, M. Washington rec W4 +2.90, Metcalf rec W4
+2.90, G. Wilson rec W5 +2.90, Jeudy rec W4 +2.60 / Barkley rush W4 −7.00, Gibbs rush W4 −3.29, Montgomery rush W4
−1.17. Uncapped flat: Metcalf +30.70, Odunze +23.80, O. Gordon II rush W4 +21.35, Bateman rec W5 +18.64, Walker III
+13.49 / Barkley −10.00, B. Thomas Jr. −7.00, Hampton −6.00, Egbuka −6.00 (W4 and W5). Reading: under the +300 cap the
ladder adds 44 rungs beyond the 79 flagged and every staking version stays ahead of expected; uncapped, the extra 163
tail rungs went 23–140, turned week 4 negative under flat staking and the totals rest on four 7–10-rung ladders
(Metcalf, Odunze, Gordon II, Bateman), which is the correlation the per-player line exists to show. Nothing changes.

### Yards tab cleanup (display only)

**Actionable** card at the top: only the rungs currently flagged under the live rules, read straight from the slate's
edge list (the output of `rules.py` / run_week.py, the same list the Edge Board shows and Results grades; nothing is
re-filtered on the page), one row per bet (player, stat, rung and side, best price and book, EV, blend probability,
stake with a Bench tag), grouped main line → alt rung v2 → bench, "No yard edges right now." when empty; it wraps on a
phone with no horizontal scroll. Player table below: All / Rushing / Receiving, search and position kept; default
columns Player, Team, Role, Stat, Median, Line (book), Lean, P(over), Market; players with no book line hidden unless
"Show unpriced" is ticked; click a player for its priced rungs (Rung | Model | Market | Blend | Best price | Book | EV
plus mean and curve), with rungs that fail the live rules greyed and labelled "outside rules: …" (the reasons use the
thresholds published in `latest.json` `rules`: +300 or longer, model over 2× the curve, EV under 5%, below the line
floor, fewer than 2 books, Rotational over 2.5× the market, thin curve, not the best rung). Passing ladders are not
listed while passing yards are hidden.

### Files changed

`rules.py` (board rules: BOARD_RULES_WEEK, BENCH_STAKE, MAX_RATIO_ROT, HIDDEN_KINDS, stake_for / ratio_of /
ratio_blocked / split_board), `clv.py` (stake and ratio in the edge log; `log_research`), `run_week.py` (board split,
research logs, `rules` published in latest.json), `results.py` (groups, stakes, parlays separate, per-player alt
lines, price variance, `record`, `record_current_rules`, `bench_gate`, `clv_by_week`, pass gate, research grading,
`board_rules`), `test_reconcile.py` (new), `.github/workflows/update.yml` (reconcile check after both grading steps),
`template.html` and `index.html` (Results card, Edge Board bench section, Yards tab), `alt_cap_study.py` and
`ladder_replay.py` (new, research only), `CHANGELOG.md`, plus the regenerated `results.json`.

## 2026-10-12 — Pre-registered gate: anytime TD at +1000 or longer (no change yet) · blend_model.json synced

### Gate (written before any decision, nothing changes before week 8 grades)

**Group:** anytime TD singles priced +1000 or longer at the price taken (the existing `longshot` flag, the same
cutoff as the "shorter than +1000" primary record), live weeks only (weeks 3+, no backfill), every role (Starter,
Rotational, Bench), graded as logged at the stake logged (1u; Bench 0.25u from week 6). One group, no sub-slicing.

**Test:** once week 8 is graded and frozen (the Tuesday grading run after week 8's Monday game), compare the group's
units to its expected units (EV at the price taken × stake) over live weeks 3–8.

**Decision:** units below expected → the group moves to **research-only**: anytime TD edges at +1000 or longer are
routed off the Edge Board and out of the bet record into a research log (the same mechanism as passing-yard rungs:
logged, graded, shown on Results, never staked). Units at or above expected → it stays on the board and the line keeps
tracking. Frozen records are not rewritten either way.

**Tracking (live, Results → Priced bets):** a line "Anytime TD at +1000 or longer" with record, units, expected, ROI,
CLV (flag-time vs closing no-vig probability on the logged edges), luck band, the Starter + Rotational / Bench split,
the by-week figures and the gate status. `results.py` publishes it as `longshot_gate`; `test_reconcile.py` checks it
against an independent recount of the bet list (overall, main, bench) and against its own week rows (56 checks now).
These bets are already inside the singles and bench lines; the line is a view, not an extra stake.

**Where it stands today** (live weeks 3–5, week 5 before MNF; CLV counts logged edges, the record counts graded bets,
as on the bench line):

| | Bets | Record | Units | Expected | CLV (logged edges) |
|---|---|---|---|---|---|
| All roles | 40 | 3–37 | +6.65u | +17.81u | +0.25 pts, 55% beat the close (53) |
| Starter + Rotational | 8 | 0–8 | −8.00u | +2.97u | −0.19 pts, 30% (10) |
| Bench | 32 | 3–29 | +14.65u | +14.84u | +0.36 pts, 61% (43) |
| Week 3 | 16 | 2–14 | +19.60u | +8.27u | +0.44 pts, 90% (20) |
| Week 4 | 13 | 1–12 | −1.95u | +6.35u | +0.77 pts, 53% (17) |
| Week 5 | 11 | 0–11 | −11.00u | +3.19u | −0.53 pts, 13% (16) |

Status: below expected (−11.2u against a ±54.5u luck band, 0.4σ). The full re-score in the week-5 entry (any TD at
+1000 or longer carrying the whole actual-vs-expected gap) is what prompted this gate; the decision waits for week 8.

### blend_model.json: which fit the site uses

`run_week.py` loads `blend_model.json` for every EV on the board, and in the workflow `results.py` runs **before**
`run_week.py` (the smoke step) and refits the file in the runner from the committed slates. So the live site has
always used the fresh refit, never the committed file, which was stale (last committed with the week-3 fit: weeks 1–3,
n 950, no 2+ TD fit). The uncommitted local refit was byte-for-byte the `blend` block CI wrote into `results.json`
(weeks 1–5; anytime n 1624 50/50 in use, first TD n 1590 fitted in use, 2+ TD n 335 50/50 in use). Committed it so the
repo matches what runs. Still to do so it stays in sync: add `blend_model.json` to the workflow's `git add` list in
the Publish step (a one-word change to `.github/workflows/update.yml`, not applied in this session).

### Files changed

`results.py` (`longshot_gate`), `test_reconcile.py` (4 more checks), `template.html` and `index.html` (the tracking
line), `blend_model.json` (synced to the fit in use), `results.json` (regenerated), `CHANGELOG.md`.


## 2026-10-15 — Phantom alt rungs: listed-only board, ladder verification, kickoff and staleness rules

Reported (week 6, verified on the books' sites): the Yards tab showed "Emanuel Wilson Over 19.5 rush yds, BEST -135,
BOOK DraftKings" while DraftKings listed no Wilson rushing rung below 30+ (-2200) and FanDuel none below 30+ (-1400).
Model projections untouched in this ticket.

### Step 0: the exact computation behind the row (from the saved 22:09 UTC pull)

The three numbers come from three different places, which is why they do not reconcile with each other:

- **BEST -135, BOOK DraftKings** is a raw row in the SharpAPI feed: DraftKings `player_rushing_yards` non-main rows for
  Wilson were 4.5 -1800, 9.5 -575, 14.5 -260, **19.5 -135**, 24.5 +125, 29.5 +210, 34.5 +340, 39.5 +550, 44.5 +850,
  49.5 +1300, 54.5 +2000, next to DraftKings' own two-sided 66.5 at -110/-114. That ladder says P(over 54.5) ≈ 5% while
  the main line says P(over 66.5) ≈ 50%: it is not a rushing-yards ladder (it has the shape of a longest-rush ladder
  flattened under the yards market). The old per-row sanity filter dropped only the rows that sat on the wrong side of
  the main (24.5–54.5) and kept 4.5–19.5 because -135 (57%) is above -110 (52%). Its own no-vig P was
  0.5745 / 1.0565 (DraftKings' measured hold) = **0.5437**.
- **MARKET 67.7%** is not the de-vig of -135. It is the pooled DK+FD curve after the cross-check and pool-adjacent-
  violators: FanDuel's genuine 29.5 at -1450 (0.878) was dropped by the cross-check because it sat 0.34 from
  DraftKings' phantom curve interpolated at 29.5, and PAV then pooled the phantom 19.5 (0.5437) with FanDuel's 39.5
  (0.7942) and 49.5 (0.6916): (0.5437 + 0.7942 + 0.6916) / 3 = **0.6765** at all three lines.
- **BLEND 74.2%** = 50/50 of model 0.8067 and curve 0.6765 (no fitted yards blend) = **0.7416**;
  EV = 0.7416 × 1.7407 − 1 = **+29.1%**. The alt rule then passed it: -135 is shorter than +300 and 0.8067 ≤ 2 × 0.6765.
- **RJ Harvey Over 24.5 (+235 FanDuel)**: listed in all three week-6 pulls (FanDuel 4.5 -650, 9.5 -235, 14.5 -113/-113,
  19.5 +152, 24.5 +235, 29.5 +350 …, a consistent ladder). 0.2985 / 1.0654 = **0.2802** is both the quote's no-vig P and
  the curve value (only quote at that line); blend (0.4796 + 0.2802) / 2 = **0.3799**; EV 0.3799 × 3.35 − 1 = **+27.3%**.
  Nothing phantom about it; it was removed from the live board later only by the kickoff rule.

How widespread: anchored on each book's two-sided line, **every DraftKings one-sided yardage ladder in the week 5 and
week 6 pulls contradicted DraftKings' own main line (19 of 19 in the 22:09 pull, 54/55, 118/120, 45/46 … in the
week-5 pulls); FanDuel's did not (0 of 208 in the 22:09 pull, 1 ladder in one week-5 pull).** The feed's
`is_main_line` flag is also unreliable (it lands on rows like "Cooper Kupp Over 39.5 +280" next to a two-sided 21.5), so
the two-sided row, not the flag, is the anchor everywhere now. Javonte Williams Over 19.5 rush (-165 DraftKings) on the
same board was the same defect.

### Fixes (yard_prices.py, rules.py, run_week.py, odds.py, template.html)

1. **Listed-only.** `yard_prices.verify_ladders` replaces the per-row `_sanity`: a book's one-sided rungs for a player
   count only when the whole ladder is consistent with that book's own two-sided line in the same pull (every lower rung
   at least as likely, every higher rung at most as likely, within 3 pts no-vig) and never rises with the line (2 pts).
   One violation rejects every one-sided rung of that book for that player; a ladder with no two-sided line at the book
   cannot be verified and is rejected too; two-sided lines always stay. Rejected ladders are kept for the research view
   (`yards[].unverified`, with the reason) and never priced. An alt-rung edge additionally requires that the named book
   lists that exact rung on the verified board (`listed`), so BEST/BOOK are always a listed quote; the curve stays the
   judge of the rung (unchanged alt rule v2), and the Yards tab now shows the quote's own no-vig P next to the curve value
   when they differ ("Market 67.7% (quote 54.4%)" is exactly the kind of row this exposes).
2. **No extrapolation.** The pooled curve was already undefined outside its range; it is now built only from verified
   ladders, so a phantom ladder can no longer extend the range downward (Wilson's curve now starts at 29.5 and
   `curve_at(19.5)` is None). Every rung carries `range_ok` and `listed`; unlisted rungs are tagged "not listed · research
   only" and rungs outside every verified range "outside every listed range".
3. **Monotonicity gate.** After the yard rows are built, `run_week.py` checks that P(over) never rises with the line on
   the model ladder, the pooled market curve and the blend for every player (research-only rungs included), and on every
   verified per-book ladder (2-pt tolerance, the verifier's). Any violation prints the offending points and fails the
   build (`SystemExit`). `yard_prices.monotone_violations` is the shared helper.
4. **Kickoff cutoff.** `rules.KICKOFF_CUTOFF_MIN = 30`: an edge whose game starts within 30 minutes, or is underway, is
   held off the board (`data.held`, with the reason), never logged for CLV and never graded. Every edge carries
   `kick_utc`; the page re-checks the rule on the viewer's clock, so a board built at 3 PM greys the 5:15 game out at
   4:45 without a rebuild.
5. **Staleness.** Every quote keeps the feed's capture timestamp (`captured`, SharpAPI row timestamps; Odds API market
   `last_update`), every edge and rung shows its price age, and `rules.MAX_PRICE_AGE_MIN` enforces a limit once set.
   **It is `None` (not enforced, age shown) until the user sets the number.**

Replay harness: `AS_OF=<ISO UTC>` fixes "now" for a build (which games are ahead, the cutoff, every age) and
`OUT_DIR=<dir>` writes the slate files elsewhere, so a saved pull can be rebuilt as of its own time:
`ODDS_MOCK=odds_history/2026_w6_20261015T2209Z.json AS_OF=2026-10-15T22:09:00Z OUT_DIR=/tmp/replay KALSHI=off python run_week.py 2026 6`.
`test_ladder_integrity.py` (in the CI test step) covers the Wilson ladder, a consistent ladder, the no-anchor case, a
rising ladder, the monotone helper, the timing rules and a regression on the saved week-6 pull.

### Verification on the week-6 snapshot (22:09 UTC pull, rebuilt as of 22:09 with the old and the new code on the same data)

| Check | Result |
|---|---|
| Zero actionable yard rows whose rung is not in the named book's verified ladder | **PASS** — 18 of 18 yard edges (1 main, 17 alt) match a raw feed row at the named book, line and price |
| Wilson 19.5 and every other unlisted / unverified rung absent from the board | **PASS** — no Wilson yard row, no DraftKings alt-rung edge (19 DraftKings ladders rejected, 0 FanDuel) |
| Monotonicity on every player curve | **PASS** — 541 player curves (model, market curve, blend) and every verified ladder |
| Kickoff cutoff | **PASS** — as of 22:09 nothing held (SEA@DEN 126 min away); as of 23:50 the five SEA/DEN edges are held "kickoff in 25 min"; as of 00:20 "game underway" |
| Removed vs. the old code (same data, same sim draws) | Exactly 2: **Emanuel Wilson Over 19.5 rush (-135 DraftKings)** and **Javonte Williams Over 19.5 rush (-165 DraftKings)**, both "DraftKings ladder unverified: contradicts the book's own line". No FanDuel-listed rung dropped. (The TD edge "Emanuel Wilson First TD +608 Kalshi" differs between the two replays only because the old-code worktree still had the stale blend_model.json; TD edges are outside this ticket.) |

Open item: `edge_log/2026_w6.json` already holds the Wilson 19.5 (-135) and Javonte Williams 19.5 (-165) entries from the
22:09 run, and `results.py` grades yard edges from that log. They were never placeable; decision needed on voiding them
(not changed here).


## 2026-10-16 — Staleness limit 15 min, 14 phantom edge-log entries voided, study provenance, replay hygiene

Follow-up to the 2026-10-15 entry (phantom alt rungs). Model projections untouched.

### 1. `rules.MAX_PRICE_AGE_MIN = 15`

Set by the user. Enforced in `run_week.py` (held edges, with the reason) and re-checked on the viewer's clock in
`template.html`, so the consequence is: **a board is actionable for 15 minutes after its pull**; after that every row sits
under "Held off the board · price captured N min ago (over 15)" until the next run. The 22:09 UTC week-6 pull replayed as of
23:50 holds every yard row and every TD row for age (the two SEA/DEN rows also for kickoff). Kalshi quotes carry no
timestamp of their own, so `kalshi.attach` now stamps each quote with its fetch time (without that every Kalshi-best edge
was held for "price age unknown"; `test_kalshi_quotes_carry_capture_time`). Odds API and SharpAPI quotes already carried
theirs. Board rules (hidden passing rungs, ratio block, bench) now apply before the timing hold, so research-only rows no
longer appear in `data.held`.

### 2. Voided edge-log entries (`void = {reason, at, check, pull}`)

Every yard entry in every edge log (weeks 3–6; weeks 1–3 have no yard markets) was re-checked against the saved pull at
its flag time with `yard_prices.verify_ladders`. 15 entries sit on a ladder the check rejects; the 14 at DraftKings were
voided with reason **"DK ladder unverified — phantom feed price"**:

| Wk | Bet | Price | Flagged | Graded |
|---|---|---|---|---|
| 5 | Bhayshul Tuten Over 24.5 rush yds (alt) | +120 DraftKings | 20261010T1933Z | won +1.20u |
| 5 | Braelon Allen Over 19.5 rush yds (alt) | -165 DraftKings | 20261010T1933Z | won +0.61u |
| 5 | Jaylen Warren Over 14.5 rush yds (alt) | -180 DraftKings | 20261010T1933Z | won +0.56u |
| 5 | Derrick Henry Over 19.5 rush yds (alt) | -205 DraftKings | 20261010T1933Z | won +0.49u |
| 5 | Chase Brown Over 19.5 rush yds (alt) | -150 DraftKings | 20261010T2014Z | won +0.67u |
| 5 | Aaron Jones Sr. Over 19.5 rush yds (alt) | -165 DraftKings | 20261010T2014Z | won +0.61u |
| 5 | Quinshon Judkins Over 19.5 rush yds (alt) | -110 DraftKings | 20261010T2014Z | won +0.91u |
| 5 | Emanuel Wilson Over 14.5 rush yds (alt) | -180 DraftKings | 20261010T2014Z | won +0.56u |
| 5 | Kyren Williams Over 19.5 rush yds (alt) | -160 DraftKings | 20261010T2014Z | won +0.62u |
| 5 | James Cook III Over 19.5 rush yds (alt) | -210 DraftKings | 20261010T2014Z | won +0.48u |
| 5 | J.K. Dobbins Over 14.5 rush yds (alt) | -135 DraftKings | 20261011T1942Z | won +0.74u |
| 5 | Emanuel Wilson Over 24.5 rush yds (alt) | +145 DraftKings | 20261011T1942Z | won +1.45u |
| 6 | Emanuel Wilson Over 19.5 rush yds (alt) | -135 DraftKings | 20261015T2209Z | not graded |
| 6 | Javonte Williams Over 19.5 rush yds (alt) | -165 DraftKings | 20261015T2209Z | not graded |

All 12 graded ones had "won" (they were priced as if a 60-yard back needed 20 yards): **12 wins and +8.88u leave the
record**; headline singles go from 38–72 +47.33u to 26–72 +38.45u, alt rungs from 41 bets +29.38u to 29 bets +20.50u. A
voided entry is out of `results.py` (live weeks via `yard_edge_source`, frozen weeks via `bets_frozen.json`, which lost
the 12 week-5 rows), out of `clv.py` closing/CLV, out of `confirm_filter.py` and `alt_cap_study.py`; `results.json`
carries `voided` and the Results tab lists them ("Voided from the record · 14"). No two-sided main-line entry was
affected (all 25 of them verify as the book's own line); every FanDuel alt entry but one sits on a verified ladder.

**Not voided:** Garrett Wilson Over 99.5 rec yds (alt), +280 FanDuel, week 5, flagged 20261010T1933Z. The check calls
its ladder unverified because the 10/10 pulls carried no FanDuel two-sided receiving line for anyone (12 of 12 FanDuel
ladders in those pulls fail for that reason); the ladder itself (29.5 -1200 … 149.5 +1260) is a genuine receiving-yards
ladder consistent with FanDuel's 68.5 main line in the 10/06 pull. Under the rule going forward it would be held
research-only. User decision 2026-10-16: **keep**; the entry carries `note = "manually verified vs FD 68.5 main line from
earlier pull; no FD two-sided receiving lines in the 10/10 pulls."` (no code exception).

### 3. Which studies and fits used DraftKings one-sided yardage ladders

Scope first: across every saved week 4–6 pull, **1,077 of 1,113 DraftKings one-sided yardage ladders fail the check**
(e.g. 131 of 137 in the 10/03 18:52 week-4 pull, 118 of 120 in the 10/10 20:13 week-5 pull); FanDuel 41 of 2,417, almost
all "no two-sided line in that pull". Weeks 1–3 carried no yard ladders.

- **Contaminated (consumed those ladders through the old `yard_prices` with no verification):** the week-4 replay table
  (`yard_alt_replay.py --week 4 --pull 20261003T1852Z`, CHANGELOG 2026-10-07/08: 48 alt rungs flagged, 26–22, +13.0u),
  the alt-rung cap study (`alt_cap_study.py`, weeks 4–5, +300/+350/+400 tables; its verdict was "cap stays at +300",
  i.e. no change was made on it), the ladder replay (`ladder_replay.py`, weeks 4–5), the yard rows of the confirmation
  study (`confirm_filter.py`, "yards 13–7 +4.7u"), and the week-5 "Alt-rung rules · old vs v2" card (now regraded without
  the voided rows). The pre-registered week-5 decision ("+150 to +300 band loses units → cap tightens to +150") rested on
  FanDuel rungs only: every DraftKings phantom was priced between -210 and +145.
- **Clean:** `yards_backtest.py` (2024–25 play-by-play simulation against actual yards, no odds feed) and
  `blend_model.json` (anytime / first / 2+ TD fits only; a yards blend would be fitted on two-sided main lines).

None rerun here (per the ticket).

### 4. Ladder with alternate rungs but no two-sided line at that book

Marked **unverified** (`yard_prices.ladder_fault`: "no two-sided line at this book to verify the ladder against"), the
whole book ladder is research-only; covered by `test_consistent_ladder_kept_and_no_anchor_rejected`.

### 5. Replay hygiene and page labels

- `OUT_DIR` replays also point `clv.EDGE_DIR` / `clv.RESEARCH_DIR` under the output dir (ODDS_MOCK runs never logged
  edges; this makes it structural). `results.py` refits `blend_model.json` while it runs: a replay started during a
  `results.py` run picked up the refit mid-way (TD edges 44 → 19); do not run them concurrently.
- Page: on data built before the listed-only rule the Yards tab no longer shows a "listed <book>" fallback label; it
  says "listing not verified (built before the listed-only rule)" and "price age unknown", and the footnote cutoff
  defaults to 30.

### Verification (22:09 UTC week-6 pull rebuilt as of 22:09 / 23:50 / 00:20 with `MAX_PRICE_AGE_MIN = 15`)

| Check | Result |
|---|---|
| Zero actionable yard rows whose rung is not listed at the named book at the named price | **PASS** — 18 of 18 match a raw feed row at that book, line and price; named book in `listed` on all 18 |
| Wilson 19.5 / Javonte Williams 19.5 absent (board and held) | **PASS** — no DraftKings alt-rung edge at all |
| Monotonicity on every player curve | **PASS** — 541 curves, every verified ladder, in all three replays |
| Kickoff cutoff | **PASS** — 22:09: nothing held for kickoff; 23:50: RJ Harvey 24.5 and Tory Horton 14.5 held "kickoff in 25 min (under 30)" (final code: 44 board rows, all held, 2 of them for kickoff as well); 00:20: a build drops the started game from the slate (502 curves, 40 board rows, all held for age), while the page shows a pre-kickoff build's SEA/DEN rows as "game underway" on the viewer's clock |
| Staleness | 22:09: every yard row 0.5–1.3 min old, none held for age; 11 Kalshi-best TD rows held "price age unknown" (the saved pull predates the Kalshi stamp); 23:50 / 00:20: every board row held, "price captured 101–132 min ago (over 15)" (plus 9 Kalshi rows "price age unknown"). 23:50 and 00:20 re-run one at a time on the final code |
| Removed vs. the old code (same data, same sim draws) | Exactly 2: Emanuel Wilson Over 19.5 rush (-135 DraftKings) and Javonte Williams Over 19.5 rush (-165 DraftKings). Nothing added, no FanDuel-listed rung dropped |
| Edge logs | 14 voided, 0 unverified DraftKings entries remain; 1 FanDuel no-anchor entry left for the user (above) |

## 2026-10-17 — FanDuel-only re-check of the alt-rung cap (pre-registered), `--books` flag on the two replay scripts

Follow-up to 2026-10-16 §3 (the cap study and the week-4 alt replay had consumed the phantom DraftKings ladders). Nothing
on the board changes; `yards.ALT_MAX_PRICE` stays +300.

**Pre-registered before the reruns** (scratchpad `prereg_fd_only.md`, 2026-10-17): the verdict "cap stays at +300" is
reversed only if (a) raise side: the +301–+350 / +351–+400 band meets the 2026-10-12 gate (≥ 30 graded bets across
≥ 3 weeks with actual ≥ expected; impossible with two weeks of ladders, reported for the record), or (b) tighten side:
FD-only, the +150 to +300 band pooled over weeks 4–5 has **n ≥ 20 graded bets AND actual units < 0 AND actual <
expected**. Below n = 20 the result is "inconclusive" and +300 stays by default until more weeks accrue.

`yard_prices.only_books(events, titles)` plus `--books FanDuel` on `alt_cap_study.py` and `yard_alt_replay.py` drop every
other book's prop ladders before verification (DraftKings' 36 verifying ladders go too; this is a one-book rerun, not
"everything that verifies"). Both ran alone, one at a time, on the saved pulls (10 week-4, 11 week-5), the slate files and
`data/pbp.parquet`; no API calls.

**Alt-rung cap study, FanDuel only** (`alt_cap_study.py --weeks 4,5 --books FanDuel`):

| Cap | Week | Bets | Record | Units | Expected | ROI | (contaminated run) |
|---|---|---|---|---|---|---|---|
| +300 | 4–5 | 61 | 22–39 | +11.23 | +8.05 | +18.4% | 91, 52–39, +33.74 |
| +300 | 4 | 44 | 14–30 | +1.83 | +6.41 | +4.2% | 67, 35–32, +15.05 |
| +300 | 5 | 17 | 8–9 | +9.40 | +1.63 | +55.3% | 24, 17–7, +18.69 |
| +350 | 4–5 | 70 | 23–47 | +15.22 | +9.88 | +21.7% | 100, 53–47, +37.73 |
| +400 | 4–5 | 77 | 20–57 | +1.79 | +11.34 | +2.3% | 107, 50–57, +24.30 |

Every FD-only bet under the +300 cap sits in the +150 to +300 band (61 bets: FanDuel lists no rung shorter than +150
that the rule flags; the 30 bets that vanished were all DraftKings phantoms priced −210 to +145, 30–0 "won"). The
incremental bands are unchanged from 2026-10-12 §9 (+301–+350: 22 bets, 7–15, +7.10u; +351–+400: 27 bets, 3–24,
−13.10u) because those rungs were FanDuel's already; both gates NOT MET. Week-5 sanity: the +300 replay reproduces all
16 keys of the voided-filtered live v2 log (live 8–8 +10.30u, replay 8–9 +9.40u, one extra replay key).

**Week 4 alt replay, FanDuel only** (`yard_alt_replay.py --week 4 --pull 20261003T1852Z --books FanDuel`): 239 player-stats
with an eligible curve (249 before), 0 main-line edges (a main-line edge needs 2 books; the frozen week-4 main-line
record came from the Sunday pulls and is unaffected), **31 alt rungs flagged, 11–20, +4.2u vs +4.6u expected — the
contaminated table's +150 to +300 band exactly** (the 17 flagged bets that are gone were the −150-and-shorter and
−150-to-+150 bands, all DraftKings: 7–2 and 8–0). Research set beyond +300: 169 bets, 18–151, −44.3u (was −48.3u).

**Verdict: HOLDS.** Tighten side: n = 61 ≥ 20, +11.23u > 0 → not triggered (week 4 alone +1.83u, below its +6.41u
expected but positive). Raise side: gate not met. Cap stays at +300. What the re-check changes in the narrative: the
"shorter than +150" bands that looked like free money (15–2 across both scripts) never existed; FanDuel's own ladders
carry the whole alt-rung record, at +150 to +300, modestly ahead of expectation on 61 bets.

## 2026-10-17 — Both Saturday scheduled runs failed at "Run model": the first gated-in yards blend fit inverted every ladder

The 17:55 and 18:35 UTC runs (`39055827830`, `39058769742`) saved their SharpAPI pulls and then failed the yard-curve
monotonicity gate ("build failed: 2647 yard-curve monotonicity violation(s): P(over) rises with the line"); nothing but
the pulls and results.json was published, so the page kept Thursday's build (everything held for age). Reproduced
locally only by running the CI step order — `results.py` (which refits `blend_model.json`) and then `run_week.py` —
on the 18:38 pull; replays on the committed blend model pass.

Cause: with the week-6 Thursday game graded, the yards head-to-head reached 3 graded weeks (4–6, 507 main-line rows)
and `results.py` fitted the pooled `yds` blend for the first time. The fit came out **[0.109, 0.333, −2.701]**: a negative
coefficient on the market no-vig P. It passed the leave-one-week-out gate (0.2486 vs 0.2511 for 50/50) because on
two-sided main lines the market P is ≈ 0.5 everywhere and carries no information, so the coefficient is unidentified;
applied across a ladder (market P from 0.05 to 0.95) it makes the blend fall as the market P rises, i.e. P(over) climbs
with the line for every player (Trevor Lawrence rush: 9.5 → 0.28, 13.5 → 0.50, 19.5 → 0.78 …). The gate did its job.

Fix (`blend.py`): a fitted blend is used only if it rises with both inputs — `blend.monotone(coef)` (b ≥ 0 and c ≥ 0) is
required in `coef_for` and in `fit_market`'s `in_use` (the record keeps the coefficients and a `reason`). The yds blend
therefore stays 50/50, which is what every build so far has used; no model behaviour changes. `any` and `first` now
pass their gates with positive coefficients and are in use (the pre-registered 2026-10 mechanism, not a change here).
Open design question for the user: a yards blend fitted on main lines only cannot be validated for alt rungs; if a fitted
yards blend is ever wanted it needs graded alt-rung rows (model P and curve P away from 0.5) in the fit, under the
backtest gate. Test: `test_blend_with_negative_coefficient_not_used` (7/7 pass). Verified locally in the CI order on the
18:38 pull: yds `in_use: false`, build passes (474 player curves monotone), `test_reconcile` 56 checks pass.

## 2026-10-17 — Pull loops: 10-minute boards for the 3 h before each kickoff cluster, published to the `board` branch

Spec approved by the user 2026-10-17 (windows from the slate's kickoff times, own concurrency group, separate publish branch,
stop at the last kickoff, manual trigger, end-of-cluster commit even on failure, dispatcher ≥ 30 min ahead with a backup, test
before Week 7). Repo is public, so Actions minutes are not metered.

- `pull_windows.py` → `pull_windows.json` (written by every scheduled run after `refresh.py`): clusters of kickoffs, lead 3 h,
  merge when a lead-in starts within one 10-min step of the previous cluster's last kickoff, `chained` when over 340 min.
  Week 7: Thu 21:15Z→Fri 00:15Z (NE@CHI), Sun 10:30→13:30Z (PIT@NO London), Sun 14:00→20:25Z (7 × 1 pm + DEN@ARI 4:05 + GB@DET,
  LA@LV 4:25; 6.42 h, two chained jobs), Sun 21:20→Mon 00:20Z (KC@SEA), Mon 21:15→Tue 00:15Z (DAL@PHI); 115 pulls ≈ 3,000
  SharpAPI requests (free tier, no cap).
- `pull_loop.yml` / `pull_loop.py`: modes `loop` (until `end`), `once` (refresh the board now), `dry_run` (N short iterations);
  concurrency group `pull-loop-<end>` (never `update`, never cancelled); waits for its exact start; `run_week.py` every 10 min
  (SharpAPI + Kalshi, Odds API only with `use_odds`); each board is an orphan snapshot of the tracked tree + generated files
  force-pushed to `board` (GitHub Pages source), so main gets no per-pull commits; `--finalize` runs `if: always()` and commits to
  main once per job the first pull of each UTC hour + the newest pull (every pull is a 30-day run artifact) and the first-seen
  logs merged key-wise against origin/main in a temporary worktree (upstream wins on shared keys), with push retries; a cluster
  over 340 min hands over to the chained `loop2` job via `remaining` / `resume_start`.
- `pull_dispatch.yml` / `pull_dispatch.py`: every 30 min 09–23 UTC, dispatches each cluster due within 90 min that has no
  queued / running / successful run (`run-name` carries the cluster's end as the key); a late firing starts the loop at once.
- `update.yml`: writes `pull_windows.json`, runs `test_pull_loop.py`, and publishes its own board snapshot after its main commit.
- Not changed: model, rules, grading, edge-log semantics (first-seen, voids), the scheduled runs' own commits.

## 2026-10-17 — Results tab: Bench folded into the headline, Priced-bets card simplified

Display and headline definition only; grading, edge logs and the model are untouched. **Headline = every single (Starter +
Rotational + Bench) at the stake it carried** (1u, Bench 0.25u from week 6); parlays stay paper-only on their own line.
`results.py` adds `singles` next to `main` / `bench` in every week row and record, `by_market` now spans the singles
(`main_by_market` keeps the old split), and `test_reconcile.py` checks singles = main + bench = Σ by_market = Σ by_role
for every scope and price split (82 checks). Live record on this basis: shorter than +1000 (primary) 27–64, +31.4u vs
+15.6u expected, +0.8σ (normal); all prices 34–122, +119.1u vs +40.4u (Bench's two long hits).

Card order: three stat cards (Record · Units vs expected · Luck in σ, labelled normal within ±1σ, hot / cold beyond),
segmented market filter (one scrolling row, no wrapping; All singles · Anytime · First TD · 2+ TD · Yards · Alt rungs ·
Starter + Rot. · Bench · Parlays), weekly table + actual-vs-expected chart (Bench included), role table (Bench, Rotational,
Starter, then the headline total row), one row of status badges (Bench stake / 1u unlock at 100 bets, Anytime +1000 gate
status and decision week, Parlays paper-only record), and a collapsed "How this is graded" (stakes, frozen weeks,
expected = EV × stake and the luck band, markets inside the headline, CLV and the bench gate, the longshot gate detail,
parlay detail, current-rules view, blend re-score, backfill). The dot-separated summary block is gone. Phone width: the
page column no longer stretches to the widest table (`.wrap > * {min-width:0}`, `.tbl` scrolls sideways), Results cards are
clamped to the viewport; verified at 390 px in headless Chrome (card 356 px wide, only the tables scroll).

## 2026-10-17 — Every fitted blend pinned to 50/50 pending approval; two new blend gates; Results tab defaults to All prices

- **Approval.** `blend.APPROVED` (empty) lists the markets whose fitted blend may go live; `coef_for` returns 50/50 for
  everything else, whatever the fit says. The fits still run every grading pass and are reported (`results.json` `blend`,
  Market Brier card: in use / reason / gate results). Anytime and first TD therefore run 50/50 again, same as yards. On the
  19:07 UTC pull the pinned build is identical to the 50/50 comparison build: 36 TD edge rows, same keys (the fitted build
  had 35 rows, 45 rows different).
- **Gate 1, between the inputs** (`blend.between_inputs`, tolerance 1e-6): on every fitted row the blend must lie in
  [min(model, market), max(model, market)]. A logistic fit with an intercept or b + c ≠ 1 can leave that interval; the
  2026-10-17 first-TD fit sat outside on 72% of rows (up to 0.12 above both inputs), anytime on 42% (up to 0.07). A 50/50
  blend never does.
- **Gate 2, first-TD game sums** (`blend.game_sums`, tolerance 0.02): per game, Σ blend P(first) over the priced players
  ≤ 1 + 0.02, and ≥ min(Σ model, Σ market) − 0.02. First TD is one-of-N: the true total over every candidate is 1 − P(no
  TD in the game) and the priced set is a subset, so any blend total above 1 is probability mass that does not exist; the
  2 points cover no-vig rounding and the 1–3% of NFL games with no touchdown. The first-TD fit reached 1.05 in a game
  (market itself 1.04 on one no-vig board, model never above 0.91). The lower bound stops a blend from erasing mass both
  inputs agree on. Both gates plus `monotone` are recorded per market; `in_use` requires leave-one-week-out better than
  50/50, every gate, and approval. Tests: `test_blend_gates_and_approval`, `test_blend_with_negative_coefficient_not_used`
  (8/8).
- **Results tab:** "All prices" is the primary view (first radio, default; the stored choice still applies). The Luck
  card already showed σ in both views (the "two long hits" wording was in the 2026-10-17 report, not on the page); on All
  prices it reads +1.6σ · hot for 35–122, +125.3u vs +40.6u expected.

## 2026-10-17 — NFL yardage odds from The Odds API, built behind `YARDS_SOURCE` (not live)

User spec 2026-10-17. Default stays `sharpapi`; the flag is a repository variable (`vars.YARDS_SOURCE`) read by update.yml
and pull_loop.yml, and a per-run input on the Pull loop workflow, so go-live is a settings flip after the replay is approved.

- **Source.** `YARDS_SOURCE=oddsapi` adds `player_rush_yds`, `player_reception_yds` and their `_alternate` markets to the Odds
  API event call (region us); SharpAPI keeps anytime / first TD and everything else, and its yardage rows are still saved in
  the pull (tagged `source=sharpapi`, research only). `merge_sources` keeps both feeds' yardage markets side by side instead of
  letting the fresher one replace the other; `yard_prices.ladder_board(source=…)` reads one feed. Every book still goes
  through `verify_ladders`. If the flag is on but the pull carries no Odds API yardage rows (key missing, budget guard, old
  pull) the build falls back to SharpAPI's ladders and says so (`latest.json` `odds_api.yards_effective`, site notice).
- **Pooling (proposal, implemented as the default for the Odds API source):** a fixed set of regulated US books with their
  own pricing — DraftKings, FanDuel, BetMGM, Caesars, Hard Rock Bet, BetRivers, Fanatics, ESPN BET (`yard_prices.YARD_BOOKS`).
  Left out: Kambi clones (betPARX, theScore Bet, Bally Bet) and the Hard Rock FL mirror, which repeat one feed and would count
  it two or three times in the pooled mean; offshore and sweepstakes books (BetOnline, Bovada, LowVig, MyBookie, BetUS,
  Fliff, ReBet, Courtside), which are not where a flagged rung can be bet and whose alternates are derived. A verified book
  outside the set never prices a rung or enters the curve; `books="all"` is available for research replays.
- **Credits.** Every paid Odds API call is appended to `odds_api_usage.jsonl` (ts, tag, path, cost, used, remaining; tag
  `nfl` for the lab, `pickem` for the scanner), committed by the scheduled runs and the loop finalize. `odds.usage_summary()`
  sums NFL credits for the UTC calendar month. Per pull with yardage: 2 (lines) + 6 markets × games not yet kicked off within
  6 days. Week 7 loops: TNF ≈ 1,628, London ≈ 1,514, Sunday main ≈ 1,992, SNF ≈ 260, MNF ≈ 146 → ≈ 5,540 a week in the loops,
  plus ≈ 350 for the four scheduled pulls and the pick'em scanner's own ≈ 100–150; ≈ 26k a month on the 100K plan.
- **Guard (pull loops only).** Before each loop pull: NFL month-to-date > 40,000 or account remaining < 20,000 → that pull
  runs without The Odds API (SharpAPI + Kalshi; yardage from SharpAPI's verified ladders). Scheduled runs keep the Odds API.
  The site shows a notice line (source, credits used, remaining) on every board and a highlighted warning when the guard
  is tripped, whichever run built the board. `DRY_RUN=1` builds without logging edges / research / paper parlays; the Pull
  loop workflow gained `yards_source`, `publish` and `dry_run` inputs for the replay pull.
- Tests: `test_yards_source.py` (one feed per build, fixed books, usage summary and guard thresholds), ladder and loop tests
  unchanged (8/8, 3/3). Default-mode replay of the 19:07 UTC pull is identical to the pre-change build (62 edges, same yard rows).

### Replay of the 21:11 UTC week-6 pull, SharpAPI yardage vs Odds API yardage (same pull, same sim draws; awaiting approval)

The dry CI pull (`Pull loop`, once, use_odds, yards_source=oddsapi, publish off, DRY_RUN) saved both feeds in one file:
SharpAPI 116 yardage markets (DraftKings 104, FanDuel 12: FanDuel's SharpAPI board was still collapsed, 13 anchor-less ladders)
and Odds API 364 (DraftKings, FanDuel, BetMGM, BetRivers, Caesars, Fanatics, BetOnline, Bovada; region us). Caveat: "old" is
unusually thin here because of that FanDuel dropout; it is the only pull with both feeds.

| Build | Yard edges | Player-stats with a curve | Listed rungs (books) |
|---|---|---|---|
| Old: SharpAPI | 27 (0 main, 27 alt) | 225 of 474 | 2,880 (DraftKings only) |
| New: Odds API, fixed book set | 47 (3 main, 44 alt) | 212 of 474 | 3,554 (DK 2,346, FD 2,096, BetMGM 1,062, BetRivers 972, Fanatics …) |
| New: Odds API, every verified book | 59 (3 main, 56 alt) | 216 of 474 | 5,033 (adds BetOnline 1,892, Bovada …) |

Old → new (fixed set): 27 added, 7 removed, 20 shared. Shared rows: mean |ΔMARKET| 0.013 (max 0.042), mean ΔEV +5.3 pts
(max +26.5: Colby Parkinson 24.5 rec, +217 DK → +290 FD), driven by better listed prices at FanDuel / BetRivers rather than by
the curve; on all 2,252 rungs priced by both builds the curve moved +0.0006 on average (mean |Δ| 0.006, ≥ 5 pts on 5 rungs).
The 7 removed rows are DraftKings rungs displaced by a better-EV rung of the same player on another book (e.g. Geno Smith
Over 19.5 rush +298 DK out, Over 14.5 +235 FD in); the 3 new main-line edges exist because a second book now anchors them.
Fixed set → all verified books: +14 / −2 edges, curves +0.014 on shared rows (max +0.062, Rico Dowdle 29.5 rush), one edge
priced best at Bovada; the offshore quotes lift the pooled curve and the EV, which is the reason for the fixed set.
Full row-level diff: session scratchpad `compare_yards.txt`.

### 2026-10-18 — Odds API yardage approved and switched on (fixed book set), live from week 7

User approval of the replay above. Repository variables set: `YARDS_SOURCE=oddsapi`, `YARDS_FROM_WEEK=7`. The fixed book set
(DraftKings, FanDuel, BetMGM, Caesars, Hard Rock Bet, BetRivers, Fanatics, ESPN BET) is `yard_prices.YARD_BOOKS`; no `YARD_BOOKS`
override is wired anywhere in CI. Model, blends, rules and board logic are untouched.

- New go-live gate (the only code change): `odds.yards_source(week)` returns `sharpapi` for weeks before `YARDS_FROM_WEEK`;
  `run_week.py` pins the env to SharpAPI as soon as the week is known, so the remaining week-6 pulls (Sunday 2026-10-18 and
  Monday) fetch no yardage markets from The Odds API and price exactly as before. From the first week-7 pull on, the Odds API
  yardage markets are fetched (6 markets per event, region us); until week-7 props are posted the build falls back to SharpAPI
  ladders ("no Odds API yardage rows") and says so on the board. Every ladder from every book still passes `verify_ladders`.
- Guards unchanged and active: loop pulls drop The Odds API when NFL month-to-date > 40,000 or account remaining < 20,000;
  every call is logged to `odds_api_usage.jsonl` tagged `nfl` (`pickem` for the scanner); the site shows source, credits and
  any tripped guard.
- Test: `test_yards_source.py::test_go_live_week_gate` (4/4); local DRY_RUN of week 6 under the live flag printed
  "YARDS_SOURCE=oddsapi goes live from week 7; week 6 stays on SharpAPI" and fetched no yardage markets.

### 2026-10-18 03:00 UTC — The Odds API is primary for every NFL prop market (PROPS_SOURCE=oddsapi), live for week 6

User instruction (2026-10-18): Odds API primary for everything SharpAPI fed (yardage main + alt, anytime, first, 2+ TD), fixed book
set DraftKings, FanDuel, BetMGM, Caesars, Hard Rock Bet, BetRivers, Fanatics, ESPN BET (`odds.PRIMARY_BOOKS`), region us; SharpAPI
fallback only (an Odds API call errors, no key, or the credit guard trips) with "Fallback source" on the site; Kalshi, model, blends,
rules and board logic unchanged. Repository variable `PROPS_SOURCE=oddsapi` set 02:38 UTC; no `PROPS_FROM_WEEK` gate, so the Sunday
loops (first at 10:30 UTC) run on it. Revert = set the variable back to `sharpapi` (next pull picks it up, no commit).

- `odds.props_source(week)`, `props_from_week()`, `fallback_reason(events)` (guard tripped / no key / lines call failed / any props
  call errored / games but no prop prices; an empty slate is not a failure), `only_books()` (props only, Kalshi kept, game lines never
  filtered), `FETCH` accounting in `fetch_all`; primary fetches the 4 TD + 4 yardage markets (8 credits per game). `run_week.py`
  fetches SharpAPI only on fallback (or `KEEP_BOTH=1` research pulls that keep both feeds' rows for every market and book); on
  fallback the yard ladders come from SharpAPI's verified DK/FD ladders and the TD board merges both feeds (fresher quote per book),
  exactly the pre-switch behaviour. `latest.json.odds_api` carries props_source / props_effective / fallback / books; the board notice
  reads "Prices: The Odds API (every prop market, region us; books …) + Kalshi" or a highlighted "Fallback source: SharpAPI … reason".
- Fix: dispatched pull loops never had the Odds API key (`use_odds` defaulted off and the dispatcher does not pass it), so the
  yardage-only flip approved earlier would not have reached the loops. `pull_loop.yml` now passes the key whenever PROPS_SOURCE or
  YARDS_SOURCE is oddsapi. Scheduled runs were unaffected.
- Tests: `test_yards_source.py` 5/5 (markets per event, per-event error accounting, every fallback reason, fixed books + Kalshi,
  PROPS_FROM_WEEK gate); ladders 8/8, loops 3/3. Default-mode replay of the 21:11 UTC pull is identical to the pre-change build.

Replay of the 02:33 UTC week-6 pull (KEEP_BOTH: SharpAPI DK/FD + Odds API every us book, same sim draws, AS_OF 02:33:30Z):
old = SharpAPI DK/FD anytime, first, yardage + Odds API 2+ TD / pass TDs from every us book (incl. Bovada, BetOnline); new = Odds API
every market, fixed set + Kalshi. 103 -> 105 edges.

| Market | Old | New | Added | Removed | Shared | Shared rows |
|---|---|---|---|---|---|---|
| Anytime TD | 36 | 28 | 1 | 9 | 27 | books 2–3 -> 5–7; mean \|ΔMARKET\| 0.003 (max 0.010), mean ΔEV +3.7 pts (better best prices at BetMGM / BetRivers) |
| First TD | 8 | 13 | 12 | 7 | 1 | the old path never fetched first-TD prices from The Odds API (SharpAPI gave FanDuel + Kalshi only): 5 books of first-TD prices arrive at once |
| 2+ TD | 21 | 15 | 0 | 6 | 15 | mean \|ΔMARKET\| 0.0002; the fixed set drops Bovada / BetOnline (books 8–9 -> 5–7) |
| Yards main | 3 | 3 | 2 | 2 | 1 | second-book anchors move |
| Yards alt | 35 | 46 | 14 | 3 | 32 | mean \|ΔMARKET\| 0.008 (max 0.034), mean ΔEV +2.0 pts, BetRivers prices best on 11 of 14 added |

Every removed TD row fails the same rule: EV at the median book < 5% once the median runs over 5–7 books instead of 2–3 (e.g. Deion
Burks anytime +2700 FanDuel: EV at best +35%, at the median −13%; Romeo Doubs 2+ TD +2500: median EV +9% -> −1% without the offshore
books). The 12 added first-TD rows are longshots (+3500 to +11000 at DraftKings / BetMGM / FanDuel, 4–6 books) that were invisible
before; the existing role / ratio / bench rules decide their stake. Over the 870 TD player-market rows priced by both builds the
no-vig market moved −0.004 on average (mean |Δ| 0.005; 54 rows by 2+ pts). Model probabilities: identical on every yard edge; on 17 of
299 players p(anytime) moved by at most 0.0005 because INFLATE_POOL scales the shares of *listed* players and the listed set differs by
a few names between feeds (model code untouched). Hard Rock Bet and ESPN BET returned no props in this pull.

Credits with the Odds API in every loop (2 for lines + 8 markets per not-yet-kicked-off game per pull): week 6 clusters Thu 2,158,
Sun 10:30 2,006, Sun 14:00 2,630, Sun 21:20 334, Mon 182 = ~7,310 per week; plus ~460 for the four scheduled runs and the pick'em
scanner's ~150: ~34K per month on the 100K plan, under the 40K NFL month-to-date guard. Guards unchanged (loops only: NFL month-to-date
> 40,000 or account remaining < 20,000 -> fallback source for that pull, flagged on the site); every call logged with tag `nfl`.
- 03:05 UTC fix after the first live once-run (02:39 UTC): it held 88 edges as "price age unknown" because the capture timestamp on
  Odds API quotes was set inside the SharpAPI merge, which primary mode no longer runs. `odds.tag_sources()` now stamps every Odds API
  outcome (source, ts = market last_update) on every pull; replaying the 02:39 pull with the fix holds 0 edges (104 on the board).
  Test added (tag -> board capture time). Republished with a once-run.

### 2026-10-18 — Board workflow: BET DOWN TO, staleness by mode, early-week pulls, Discord alerts (no model changes)

User instruction (2026-10-18). Model, blends, edge rules (EV ≥ 5% at the median and best book, 2+ books) and grading untouched.

1. **Early-week pulls.** `pull_dispatch.py` (every 30 min, now all day) also starts a `Pull loop` once-run at 8 AM, 11 AM, 2 PM, 5 PM,
   8 PM and 11 PM PT on Tuesday–Saturday (DST-aware), skipping a slot inside or within 30 min before a kickoff-loop window;
   idempotent through the run title ("early <slot>"). Before props post a pull costs ~2 credits, so the cadence starts itself on
   the day props post. Credits per week with 14 games (2 for lines + 8 markets per game per pull): kickoff loops ~7,310, early-week
   28 pulls up to ~3,190, four scheduled runs ~460, pick'em ~150 → ~11,100 per week, ~48K per month. NFL month-to-date guard
   raised 40K → 60K (`odds.NFL_MTD_LIMIT`); remaining-credit floor stays 20K. `pull_windows.json` carries the early-pull estimate.
2. **BET DOWN TO.** `rules.bet_down_to(blend_p)` = the worst American price at which EV at the model–market blend is still ≥ 5%
   (decimal 1.05 / blend, rounded without crossing the threshold); set on every edge in `split_board`, logged in the edge log, shown
   next to BEST on the Edge Board and in the yards Actionable card (↓ price).
3. **Staleness.** `rules.price_age_limit()`: 15 min only inside kickoff loops (`IN_LOOP=1`, set by pull_loop for loop / dry_run
   pulls); scheduled and early-week pulls have no age limit, so edges stay on the board until the next pull with their price age
   shown. The 30-min kickoff cutoff applies everywhere. The page states the rule in force: "bet only if your book's price is at or
   better than BET DOWN TO" (and "Kickoff-loop build: prices older than 15 min are held" on loop builds). `latest.json.rules`
   carries max_price_age_min (per build), loop_price_age_min, in_loop and bet_down_to_rule.
4. **Discord.** `alerts.py` posts every first-seen edge (the rows `clv.log_edges` just appended: player, bet, best book + price, BET
   DOWN TO, EV, role and stake) to `DISCORD_WEBHOOK_URL` — the same secret name as the arc-lab health watchdog — chunked under 2,000
   characters; without the secret the run log says what it would have posted. Never fails a pull. The secret must be added to this
   repository (`gh secret set DISCORD_WEBHOOK_URL`).
5. Tests: `test_board_rules.py` (BET DOWN TO threshold at every probability, age limit by mode, split_board field, Discord
   formatting and chunking), `test_pull_loop.py::test_early_week_slots_and_cluster_skip` (slots, grace, DST, cluster skip, credit
   count). Replay of this week's pulls (Tue–Sat availability vs kickoff loops, CLV vs close) reported separately.
- Follow-ups (03:40 UTC): rows frozen in the slate before this change get a BET DOWN TO value when the slate is re-merged;
  `clv.closing_summary` now finds a kicked-off game in older pulls (it looked only in the latest pull, which drops games that have
  started, so no edge ever received its close once its game kicked off; Results CLV figures fill in from the next grading run).
  Week-6 replay (16 saved pulls, as of each pull's own stamp, no age limit): 160 distinct edges so far, 123 first seen in Tue–Sat
  pulls outside loop windows, 37 first seen in the Thursday window (7 of them only there). Thursday game, true close: 5 edges
  flagged Tuesday closed +2.0 pts better on average (3 of 5 beat the close; AJ Barner Over 39.5 rec +7.2, George Holani Over 24.5
  rush +3.7, Emanuel Wilson first TD −1.2); the 2 flagged Thursday closed flat. Sunday/Monday games: interim movement only.

### 2026-10-18 — Cross-venue line shop (display + data), credit audit and projection guard, Underdog pick'em flags (no model changes)

User instruction (2026-10-18): a cross-venue line shop for the Edge Board, display + data only. The edge rule (EV ≥ 5% at the median and at the
best regular book, 2+ books) is untouched and a venue price changes WHERE to bet, never WHETHER a bet is flagged
(`test_lineshop.py::test_flag_set_identical_with_and_without_line_shop`). Polymarket direct was skipped: The Odds API's us_ex region already
carries its props.

**1. Credits (built and reported first).** Every Odds API usage record now carries `pull_type` (scheduled / early / loop / manual / pickem /
local, from `PULL_TYPE` in the workflows) and `region` (us / us_ex), and the pull loops' finalize step commits `odds_api_usage.jsonl` (line-wise
union), so the per-pull-type table comes straight from the log from now on. For the last 7 days it was reconstructed from the CI run logs:

| Pull type (2026-10-11 → 10-18 08:00Z) | Runs | Odds API credits |
|---|---|---|
| Scheduled runs (update.yml) | 12 | ~40 (old key: 188 left on 10/11, exhausted by 10/17; most runs pulled SharpAPI only) |
| Early-week pulls | 0 | 0 (first slots Tue 10/20) |
| Kickoff loops (dispatched) | 0 | 0 (first loop Sun 10/18 10:30Z) |
| Manual once-runs (Pull loop, workflow_dispatch) | 9 with Odds API calls | 640 (638 on the new key) |
| Pick'em scanner | 1 (failed before pulling) | 0 |
| Local replays / probes (this machine) | — | 67 (54 dry pull 02:11Z, 13 us_ex probes) |
| Outside the pipeline (user probing, Phase 1 us_ex) | — | ~24,900 |

The new key (created 2026-10-17 21:07Z, 100,000 credits, reset on the 17th) stood at 74,399 remaining at 08:10Z on 10/18: 25,601 used, of
which ~700 by the pipeline. The 11.6k/day "burn" was the Phase 1 probing, not the scheduled pulls. Planned pipeline burn with PROPS_SOURCE=oddsapi
(kickoff loops ~7,310 + early-week ~3,190 + scheduled ~456 + pick'em ~98 + us_ex ~420 ≈ 11,470 per week ≈ 1,640/day) projects ~25,700 remaining at
the 2026-11-17 reset: above the 10,000 floor, no guard stage. `odds.budget_projection` recomputes this every pull (burn = max(planned from
pull_windows.json, observed trailing 3-day burn from the `remaining` series, so manual probing shows up too): stage 1 (projected < 10,000) skips
the us_ex pulls, stage 2 (still under without us_ex) makes the kickoff loops pull every 20 min; `alerts.notify_guard` posts a Discord message once per
stage per 12 h. us_ex is pulled only for games with ≥ 1 edge on the board, only on scheduled runs (`EXCHANGE_PULL=all` in update.yml) and on a game's
final pre-kickoff loop pull (`final`: kickoff in [30, 30 + step) minutes); early-week and manual once-runs spend nothing on it unless the new
`exchange` input is set. Cost: 1 credit per market returned per game (≤ 6).

**2. Exchange price safety.** Novig, ProphetX and Polymarket quotes carry no size in the feed and are labelled "size unknown, confirm in app";
Kalshi keeps the $100-at-the-ask floor (direct feed; the us_ex copy of Kalshi is dropped). A quote more than 15% better in relative implied
probability than the regular-book median for the same bet is greyed "outlier" and can never be the best place. ProphetX's order-book levels
collapse to the top of book; −9900-style placeholder sides are not quotes. Exchange alt ladders run through `yard_prices.verify_ladders` and the
monotonicity check like sportsbook ladders; failures are research-only (greyed "unverified", never eligible).

**3. Fees** (`venues.py`, sources in ODDS_SOURCES.md "Venue fees"): Kalshi taker 0.07·P·(1−P) (board convention), Polymarket taker 0.05·p·(1−p)
per share (official docs), ProphetX 2% of net winnings (secondary sources only), Novig none on pregame straight trades (secondary). Official pages
for Kalshi, ProphetX, Novig and Underdog were not reachable by automated fetch on 10/18 (rate limit / JS apps / 404s): the table says which figures
are confirmed and which to verify in the app.

**4. Display** (`template.html`, index.html regenerated): BEST / BOOK / Bet down to become one "Best place to bet" column (venue, after-fee price,
size or "size unknown", ↓ bet-down-to); the Kalshi column folds into the venue list; a click on an edge row expands every venue's price for that bet,
best to worst, with fee, size and price age; inside a kickoff window (3 h before kickoff) venue prices older than 15 min are hidden on the viewer's
clock. "UD ✓" tags on Edge Board rows and Yards cards, a "Pick'em" filter on the Yards tab, an Underdog column in the rung table, and the Edge Board
notice shows the credit projection and the line-shop status (venues live, us_ex mode, skipped reason).

**5. Underdog pick'em** (`pickem_ud.py`, kill switch `PICKEM_SOURCE=off`): one fetch per scheduled run (`UD_FETCH=1`), cached in `ud_lines.json`,
loops read the cache. Players matched by name + team + game with the Josh Allen guard (a name on another team is dropped, never attached). For
every Underdog higher/lower at one of our yard rungs or TD lines: break-even per leg for 2-pick 3x, 3-pick 6x and 5-pick flex 10x/2.5x/0.4x
(57.7%, 55.0%, 52.8% at multiplier 1; the line's own payout multiplier scales it), flagged when the blend beats the 3-pick break-even by ≥ 3 pts on
the better side. Payouts are a constant to verify in app (`UD_PAYOUTS` override). DraftKings Pick6 skipped.

**Tests** `test_lineshop.py` (7): fee math per venue, outlier grey-out, exchange ladder gate + monotonicity (bad ladders fail), name matching across
venues incl. same-name players on different teams, break-even table, us_ex gating + guard stages + alert text, flag set identical with and
without the line shop (357 synthetic edges on the saved 03:46Z pull). All other suites still pass (board rules 4, yards source 5, pull loop 4,
ladders 8, nan safety, reconcile). Workflows: update.yml (PULL_TYPE, EXCHANGE_PULL, UD_FETCH, PICKEM_SOURCE, ud_lines.json committed), pull_loop.yml
(PULL_TYPE by mode/label, EXCHANGE_PULL final for loops, `exchange` input), pickem.yml (PULL_TYPE).
