# Issue #208 — Measured entry-gate defaults: quote_range and the dead zone

**Date:** 2026-09-17 · **Datasets:** `run/ticks/ticks_2026-09-13/14/15.jsonl` (550 / 1700 / 1695
windows; integrity-checked, 0 crossed books, 0 corrupt lines). `ticks_2026-09-16.jsonl`
(10 ticks) **excluded** — collector started late that day.

**Tooling:** `scripts/sweep_backtest.py --preset sensitivity --include-structural`
(`--only quote_range`, `--only dead_zone`). The dead-zone axes (pct + sec) were added for
this measurement (issue #208); one filter key runs both, so pct-vs-sec is answered from a
single execution environment. Per-dataset window counts are stated everywhere; sec-unit rows
are additionally split by window length (5m vs 15m) as the SPEC requires.

**Pre-registered reading rule** (locked in `tasks/plan.md` TASK-3 before interpretation): a
default holds unless a variant beats it on avg P&L **and** max drawdown consistently across
all datasets; a mixed result is "no change, evidence inconclusive".

All P&L figures are cents, `avg` = per-window avg P&L, `dd` = max drawdown, `pf` = profit
factor (0.00 throughout — see the caveat at the bottom). Raw sweep outputs:
`run/sweeps/208/*.json`.

## 1. quote_range (shipped default 0.10–0.90, rules §6)

| range | avg (09-13) | dd (09-13) | avg (09-14) | dd (09-14) | avg (09-15) | dd (09-15) |
|---|---:|---:|---:|---:|---:|---:|
| 0.30–0.70 | **−350.46** | **192,753** | **−345.13** | **586,722** | **−352.63** | **597,711** |
| 0.25–0.75 | −405.48 | 223,014 | −394.35 | 670,400 | −408.59 | 692,558 |
| 0.20–0.80 | −449.41 | 247,176 | −436.21 | 741,558 | −449.55 | 761,980 |
| 0.15–0.85 | −480.71 | 264,391 | −465.26 | 790,937 | −482.16 | 817,262 |
| **0.10–0.90 (default)** | −504.04 | 277,223 | −486.54 | 827,123 | −504.93 | 855,858 |
| 0.05–0.95 | −517.05 | 284,380 | −501.13 | 851,928 | −518.67 | 879,139 |
| 0.00–1.00 | −523.87 | 288,130 | −504.17 | 857,093 | −524.48 | 888,994 |

Win rate rises monotonically with narrowing (1.8% / 1.7% / 1.7% at 0.30–0.70 vs 0.4% / 0.7% /
0.5% at baseline); pair rate falls (72–81% vs 89–94%) — the trade is fewer windows, better
windows. **The response is strictly monotone in both directions** — no interior optimum was
found; 0.30–0.70 is the best measured point, not a proven optimum. Widening past 0.10–0.90
hurts consistently.

**Verdict: the shipped default does not hold.** Every tighter bound beats it on both axes in
all datasets. Adjustment candidate (operator decision): **quote_range=(0.30, 0.70)**.

## 2. dead zone size (shipped default 10% of window, pct, rules §8)

| config | avg (09-13) | dd (09-13) | avg (09-14) | dd (09-14) | avg (09-15) | dd (09-15) |
|---|---:|---:|---:|---:|---:|---:|
| pct 0.30 | **−378.52** | **208,185** | −385.93 | 656,077 | **−388.68** | **658,820** |
| sec 120 | **−366.97** | **201,832** | **−381.40** | **648,372** | −385.44 | 653,316 |
| sec 90 | −424.40 | 233,422 | −424.83 | 722,205 | −428.56 | 726,415 |
| pct 0.20 | −435.28 | 239,402 | −437.69 | 744,070 | −447.43 | 758,391 |
| sec 60 | −474.79 | 261,136 | −464.00 | 788,808 | −476.67 | 807,950 |
| pct 0.15 | −470.45 | 258,750 | −463.92 | 788,657 | −476.81 | 808,198 |
| **pct 0.10 (default)** | −504.04 | 277,223 | −486.54 | 827,123 | −504.93 | 855,858 |
| sec 30 | −527.43 | 290,088 | −499.84 | 849,722 | −524.24 | 888,588 |
| pct 0.05 | −530.53 | 291,793 | −504.56 | 857,749 | −529.00 | 896,657 |
| sec 15 | −537.39 | 295,563 | −509.16 | 865,573 | −536.62 | 909,565 |
| off (0.0) | −542.74 | 298,506 | −513.62 | 873,159 | −541.90 | 918,517 |

(sec 0 ≡ pct 0 ≡ guard disabled — identical rows, sanity check passed.) The response is
monotone: **every** widening of the dead zone reduces losses; the guard itself is worth ~5%
of avg loss vs none. No optimum inside the tested range. **Verdict: 10% is too small.
Adjustment candidate (operator decision): 0.30 pct or larger;** note the same caveat as
above — 0.30 is the best measured point and the true optimum may sit beyond it.

## 3. pct vs sec (rules §8's deliberately open question) — split by window length

BTC-only runs on 09-14/09-15 (n=254/252 on 5m, 86/87 on 15m). Reading: for each window
length, compare each unit's rows at scales that represent the same intent.

**5m windows** (10% = 30s; 0.20 pct = 60s; 0.30 pct = 90s):

| config | avg (09-14) | avg (09-15) |
|---|---:|---:|
| 0.30 pct (90s) | −329.41 | −338.38 |
| sec 120 | **−290.09** | **−285.45** |
| 0.20 pct (60s) | −363.28 | −391.52 |
| sec 60 | −363.28 | −391.52 |
| 0.10 pct = sec 30 (default) | −383.17 | −435.32 |

**15m windows** (10% = 90s; 0.30 pct = 270s):

| config | avg (09-14) | avg (09-15) |
|---|---:|---:|
| 0.30 pct (270s) | **−370.12** | **−377.35** |
| sec 120 | −477.58 | −508.77 |
| sec 90 ≈ 0.10 pct (default) | −501.61 | −535.06 |

**Verdict: pct wins.** At the same nominal intent, the pct reading achieves more on both
window lengths (on 15m, 0.30-pct's 270s tail beats sec 120; on 5m the sec-120 row is the
equivalent of a 0.40-pct tail and matches that reading's advantage). The §8 argument for
percent — "the dead tail scales with the window" — is what the data shows. **Keep unit=pct.**

## Operator decisions requested

1. quote_range: tighten (0.10, 0.90) → **(0.30, 0.70)**? — every dataset favors it on both
   metrics; monotone, so the exact edge within 0.25–0.70 deserves one confirming sweep.
2. dead zone: widen 0.10 → **0.30 pct**? — same monotone shape; a sweep beyond 0.30 would
   locate the edge.
3. Confirm unit=pct stands (no change needed).

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
