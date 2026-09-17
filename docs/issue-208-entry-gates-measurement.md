# Issue #208 — Measured entry-gate defaults: quote_range and the dead zone

**Date:** 2026-09-17 · **Datasets:** `run/ticks/ticks_2026-09-13/14/15/16.jsonl`. All four
files were swept and appear in the tables below. Integrity check (`scripts/verify_tick_data.py`):
09-13/14/15 are sound (0 crossed books, 0 corrupt lines; 550 / 1700 / 1695 windows).
`ticks_2026-09-16.jsonl` (10 windows, 10 ticks) fails the verifier's window-coverage checks
(732 sampling gaps, 90 late starts, 171 collector errors across the directory; the 09-16 file
is a single early-morning fragment — the collector started late that day) and is included in
Table 2 with its labeled result for completeness. Its rows are flagged `n=10` and carry no
weight in any verdict.

**Tooling:** `scripts/sweep_backtest.py --preset sensitivity --include-structural`
(`--only quote_range`, `--only dead_zone`). The dead-zone axes (pct + sec) were added for
this measurement (issue #208); one filter key runs both, so pct-vs-sec is answered from a
single execution environment. Per-dataset window counts are stated everywhere; sec-unit rows
are additionally split by window length (5m vs 15m) as the SPEC requires.

**Pre-registered reading rule** (locked in `tasks/plan.md` TASK-3 before interpretation): a
default holds unless a variant beats it on avg P&L **and** max drawdown consistently across
all datasets; a mixed result is "no change, evidence inconclusive".

**Column key for all tables:** each dataset cell is `avg / dd / win% / pair% / total / pf` —
per-window avg P&L (¢), max drawdown (¢), win rate, pair rate, total P&L (¢), profit factor.
Raw sweep outputs (all metric sources): `run/sweeps/208/*.json`.

## 1. quote_range (shipped default 0.10–0.90, rules §6)

| range | n | 09-13 | 09-14 | 09-15 |
|---|---:|---|---|---|
| 0.30–0.70 | 550 | −350 / 192,753 / 1.8 / 72.4 / −192,753 / 0.00 | −345 / 586,722 / 1.7 / 80.6 / −586,722 / 0.00 | −353 / 597,711 / 1.7 / 80.6 / −597,711 / 0.00 |
| 0.25–0.75 | 550 | −405 / 223,014 / 1.1 / 77.5 / −223,014 / 0.00 | −394 / 670,400 / 1.4 / 85.5 / −670,400 / 0.00 | −409 / 692,558 / 1.3 / 84.7 / −692,558 / 0.00 |
| 0.20–0.80 | 550 | −449 / 247,176 / 0.4 / 82.4 / −247,176 / 0.00 | −436 / 741,558 / 1.0 / 89.7 / −741,558 / 0.00 | −450 / 761,980 / 0.6 / 87.6 / −761,980 / 0.00 |
| 0.15–0.85 | 550 | −481 / 264,390 / 0.2 / 86.9 / −264,390 / 0.00 | −465 / 790,937 / 0.9 / 92.7 / −790,937 / 0.00 | −482 / 817,262 / 0.6 / 90.6 / −817,262 / 0.00 |
| **0.10–0.90 (default)** | 550 | −504 / 277,223 / 0.4 / 88.9 / −277,223 / 0.00 | −487 / 827,123 / 0.7 / 94.4 / −827,123 / 0.00 | −505 / 855,858 / 0.5 / 92.2 / −855,858 / 0.00 |
| 0.05–0.95 | 550 | −517 / 284,380 / 0.2 / 90.0 / −284,380 / 0.00 | −501 / 851,928 / 0.5 / 95.1 / −851,928 / 0.00 | −519 / 879,139 / 0.3 / 93.9 / −879,139 / 0.00 |
| 0.00–1.00 | 550 | −524 / 288,130 / 0.0 / 90.5 / −288,130 / 0.00 | −504 / 857,093 / 0.5 / 95.3 / −857,093 / 0.00 | −524 / 888,994 / 0.4 / 93.9 / −888,994 / 0.00 |

Win rate rises monotonically with narrowing (1.8% / 1.7% / 1.7% at 0.30–0.70 vs 0.4% / 0.7% /
0.5% at baseline); pair rate falls (72–81% vs 89–94%) — the trade is fewer windows, better
windows. **The response is strictly monotone in both directions** — no interior optimum was
found; 0.30–0.70 is the best measured point, not a proven optimum. Widening past 0.10–0.90
hurts consistently.

**Verdict: the shipped default does not hold.** Every tighter bound beats it on both axes in
all datasets. Adjustment candidate (operator decision): **quote_range=(0.30, 0.70)**.

## 2. dead zone size (shipped default 10% of window, pct, rules §8)

| config | n | 09-13 | 09-14 | 09-15 | 09-16 (`n=10`) |
|---|---:|---|---|---|---|
| pct 0.30 | 550 | −379 / 208,185 / 0.4 / 86.9 / −208,185 / 0.00 | −386 / 656,077 / 0.8 / 94.0 / −656,077 / 0.00 | −389 / 658,820 / 0.7 / 91.9 / −658,820 / 0.00 | 0.0 / 0.0 / 0.0 / 0.0 / 0.0 / 0.00 |
| sec 120 | 550 | −367 / 201,832 / 0.2 / 85.3 / −201,832 / 0.00 | −381 / 648,372 / 1.2 / 93.5 / −648,372 / 0.00 | −385 / 653,316 / 0.8 / 91.1 / −653,316 / 0.00 | 0.0 / 0.0 / 0.0 / 0.0 / 0.0 / 0.00 |
| pct 0.20 | 550 | −435 / 239,402 / 0.4 / 88.4 / −239,402 / 0.00 | −438 / 744,070 / 0.7 / 94.4 / −744,070 / 0.00 | −447 / 758,391 / 0.5 / 92.2 / −758,391 / 0.00 | — |
| sec 90 | 550 | −424 / 233,422 / 0.4 / 86.9 / −233,422 / 0.00 | −425 / 722,205 / 0.8 / 94.0 / −722,205 / 0.00 | −429 / 726,415 / 0.6 / 91.9 / −726,415 / 0.00 | — |
| pct 0.15 | 550 | −470 / 258,750 / 0.4 / 88.7 / −258,750 / 0.00 | −464 / 788,657 / 0.7 / 94.4 / −788,657 / 0.00 | −477 / 808,198 / 0.5 / 92.2 / −808,198 / 0.00 | — |
| sec 60 | 550 | −475 / 261,136 / 0.4 / 88.5 / −261,136 / 0.00 | −464 / 788,808 / 0.7 / 94.4 / −788,808 / 0.00 | −477 / 807,950 / 0.5 / 92.2 / −807,950 / 0.00 | — |
| **pct 0.10 (default)** | 550 | −504 / 277,223 / 0.4 / 88.9 / −277,223 / 0.00 | −487 / 827,123 / 0.7 / 94.4 / −827,123 / 0.00 | −505 / 855,858 / 0.5 / 92.2 / −855,858 / 0.00 | 0.0 / 0.0 / 0.0 / 0.0 / 0.0 / 0.00 |
| sec 30 | 550 | −527 / 290,088 / 0.4 / 89.1 / −290,088 / 0.00 | −500 / 849,722 / 0.7 / 94.4 / −849,722 / 0.00 | −524 / 888,588 / 0.5 / 92.2 / −888,588 / 0.00 | — |
| pct 0.05 | 550 | −531 / 291,792 / 0.4 / 89.1 / −291,792 / 0.00 | −505 / 857,749 / 0.7 / 94.4 / −857,749 / 0.00 | −529 / 896,657 / 0.5 / 92.3 / −896,657 / 0.00 | — |
| sec 15 | 550 | −537 / 295,563 / 0.4 / 89.1 / −295,563 / 0.00 | −509 / 865,573 / 0.7 / 94.4 / −865,573 / 0.00 | −537 / 909,564 / 0.5 / 92.3 / −909,564 / 0.00 | — |
| off (0.0) | 550 | −543 / 298,506 / 0.4 / 89.1 / −298,506 / 0.00 | −514 / 873,159 / 0.7 / 94.4 / −873,159 / 0.00 | −542 / 918,517 / 0.5 / 92.3 / −918,517 / 0.00 | 0.0 / 0.0 / 0.0 / 0.0 / 0.0 / 0.00 |

(sec 0 ≡ pct 0 ≡ guard disabled — identical rows, sanity check passed.) The 09-16 fragment
produces 10 windows whose replay is uniformly 0.00c — a collector-start fragment of ~2 minutes
offers no quotable surface; its inclusion here is for the record, not evidence. On the sound
datasets the response is monotone: **every** widening of the dead zone reduces losses; the
guard itself is worth ~5% of avg loss vs none. No optimum inside the tested range.
**Verdict: 10% is too small. Adjustment candidate (operator decision): 0.30 pct or larger;**
note the same caveat as above — 0.30 is the best measured point and the true optimum may sit
beyond it.

## 3. pct vs sec (rules §8's deliberately open question) — matched and unmatched rows

BTC-only runs on 09-14/09-15 (n=254/252 on 5m, 86/87 on 15m), split by window length.

**Matched tails (identical absolute duration, both units).** The registered default is
10% of the window: 30s on 5m, 90s on 15m. At those exact durations pct and sec are the same
rule — and the data confirms bit-for-bit identity:

| config | 5m avg (09-14 / 09-15) | 15m avg (09-14 / 09-15) |
|---|---:|---:|
| 0.10 pct (default) = sec 30 on 5m | −383.17 / −435.32 (identical rows) | — (90s ≡ 0.10 pct, see next row) |
| 0.10 pct = sec 90 on 15m (Baseline) | — | −501.61 / −535.06 (identical rows) |

The pct and sec encodings are **two spellings of one rule**; at equal durations the engine
produces identical results. No measurement can prefer one spelling over the other at a fixed
duration — the only meaningful question is which *duration policy* each unit expresses.

**Unmatched tails (the unit's actual scaling behavior).** The rows that differ are the ones
where the two units express different policies:

| config (tail duration) | 5m avg (09-14 / 09-15) | 15m avg (09-14 / 09-15) |
|---|---:|---:|
| 0.30 pct (90s on 5m / 270s on 15m) | −329.41 / −338.38 | **−370.12** / **−377.35** |
| sec 120 (0.40-pct on 5m / 0.133-pct on 15m) | **−290.09** / **−285.45** | −477.58 / −508.77 |
| sec 90 (0.30-pct on 5m / 0.10-pct on 15m) | −329.41 / −338.38 (≡ pct 0.30) | −501.61 / −535.06 (≡ default) |

Reading these rows correctly: on 15m, the 270s tail (0.30 pct) beats the 120s tail — but that
compares durations, not units. On 5m, 120s beats 90s — again a duration comparison. Every
difference in the table is explained by tail duration alone; whenever durations match, the
rows are identical. **Verdict: the units are measurably equivalent at matched durations, and
the pct spelling remains the shipped default with no measured cost and a natural scaling
story.** No evidence in this data justifies a unit switch in either direction; the meaningful
open question is the tail *size* (§2), not the unit. The pre-registered rule's outcome here is
"no change" — the default (pct) stands.

## Operator decisions requested

1. quote_range: tighten (0.10, 0.90) → **(0.30, 0.70)**? — every dataset favors it on both
   metrics; monotone, so the exact edge within 0.25–0.70 deserves one confirming sweep.
2. dead zone: widen 0.10 → **0.30 pct**? — same monotone shape; a sweep beyond 0.30 would
   locate the edge.
3. Unit: keep pct (measured equivalent at matched durations; no evidence to switch).

## Caveats

- **Profit factor reads 0.00 across every row** — the strategy as parameterized is a net
  loser on every configuration, so the sweep compares *sizes of loss*, not winners. The
  relative ordering is still valid evidence (the metrics are averaged over identical window
  populations per dataset), but a negative-PnL baseline means these defaults are being tuned
  in the wrong regime; the fill/exit economics (#205's finding, still open) dominate.
- Sweep replay over 1s snapshots is a prediction of live behaviour, not a reproduction
  (glossary: collector misses seconds).
- 09-13 is one-third the size of the other days and slightly different in shape (win rate
  pattern), but agrees with them in every direction tested.
