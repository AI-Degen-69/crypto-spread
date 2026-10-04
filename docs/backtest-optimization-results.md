# Quant Backtest Optimization Results — SPREAD-2 Strategy

> **Latest sweeps — 2026-09-23:** §0 below is measured over the **certified
> golden dataset**. The prior results in this file (§1–§6) predate it — they
> were produced under the old fill rule (#226) and pair-cost cap (#227) on
> non-golden data. Where §0 and §1–§6 disagree, §0 is the newer measurement.
>
> **§0 has not been re-measured since, and does not describe the engine running
> today.** Issue #423 (`dd28881`, 2026-10-03) rewrote the entry anchor in
> `backtest/engine.py`: the anchor is now latched at the first in-range
> two-sided mid instead of at delay expiry. That mechanism sets the entry
> price, so it feeds PnL directly. Read §0 as the best available look at the
> certified dataset, not as a measurement of today's engine.
>
> **The sweep artifacts are not in this repository.** `run/` is gitignored, so
> the `run/sweeps/*.json` files cited below cannot be committed, and no reader
> can re-derive these numbers without re-running both sweeps against a locally
> present golden dataset (~3.4 h of simulation).

---

## §0. Golden-dataset sweeps (2026-09-23) — measured on the pre-#423 engine

> **Dataset:** the certified golden set — `run/ticks/golden/`, 6 days
> (2026-09-13 → 2026-09-21), **4,910 windows**, 1,428,888 valid ticks, all 10
> series, `sampling_gap_rate 0.0`, manifest policy `2026-09-20.v2` (certified
> 2026-09-22, see `run/ticks/golden/golden_manifest.json` and
> `docs/golden-tick-dataset.md`).
> **Engine note:** both sweeps ran on the post-#226/#227 engine (single
> hard-coded fill rule, `max_pair_cost 0.99`), so unlike the sections below they
> need no "predates the fill rule" caveat. They **do** predate #423's
> set-and-wait entry anchor, which changed when an entry latches — and therefore
> what an entry is worth.

### §0.A Sweep 1 — full-universe 1D sensitivity

**Run:** `python -m scripts.sweep_backtest run/ticks/golden --preset sensitivity --size 5`
— 22 configurations × 4,910 windows (tuning knobs only, per issue #233:
structural limits held at baseline). **Artifact:** `run/sweeps/golden_sensitivity.json`
(+ `.log`). Both paths are under `run/`, which is gitignored — **the artifact
is not in this repository.** **Cost:** 716s load (1.43M snaps) + 5,260s
simulation.

#### Headline

**Every configuration is EV-negative over the full 10-series universe.** The
best configuration found (`queue=10`, everything else baseline) loses
−15,342¢ over 4,910 windows (−3.12¢/window, PF 0.42); the naive baseline loses
−2,505,529¢ (−510¢/window, PF 0.00). This **supersedes the positive §1–§3
numbers** (e.g. "+3,279.78¢ at off=0.020_q=0_ex=0.08"), which were produced on
1,680 windows of non-golden data under the pre-#226 fill model, and did not
reproduce on certified data when these sweeps ran (2026-09-23).

#### Full 1D results (ranked by total PnL)

| Rank | Config | PnL (cents) | Avg/window | Pair rate | Exit rate | Win rate | PF | Sharpe |
|---:|:---|---:|---:|---:|---:|---:|---:|---:|
| 1 | `queue=10` | −15,342 | −3.12¢ | 11.3% | 6.0% | 9.9% | 0.42 | −7.7 |
| 2 | `queue=25` | −60,654 | −12.35¢ | 15.4% | 13.4% | 11.0% | 0.17 | −15.3 |
| 3 | `queue=50` | −153,059 | −31.17¢ | 22.7% | 23.7% | 12.0% | 0.08 | −20.8 |
| 4 | `queue=100` | −355,691 | −72.44¢ | 37.3% | 41.3% | 12.4% | 0.04 | −28.8 |
| 5 | `queue=200` | −770,953 | −157.02¢ | 54.0% | 59.3% | 10.0% | 0.02 | −44.5 |
| 6 | `exit_5m=0.16` | −1,801,750 | −366.96¢ | 93.1% | 98.8% | 1.9% | 0.00 | −85.1 |
| 7 | `offset=0.040` | −1,839,906 | −374.73¢ | 84.6% | 99.9% | 2.0% | 0.00 | −93.8 |
| 8 | `offset=0.035` | −1,871,983 | −381.26¢ | 88.4% | 99.9% | 2.0% | 0.00 | −92.0 |
| 9 | `exit_5m=0.14` | −1,892,382 | −385.41¢ | 93.8% | 99.3% | 1.7% | 0.00 | −87.9 |
| 10 | `exit_5m=0.12` | −2,007,334 | −408.83¢ | 94.3% | 99.7% | 1.3% | 0.00 | −91.7 |
| 11 | `exit_5m=0.10` | −2,125,100 | −432.81¢ | 94.6% | 99.9% | 1.2% | 0.00 | −95.3 |
| 12 | `offset=0.030` | −2,125,438 | −432.88¢ | 90.0% | 100% | 1.0% | 0.00 | −98.8 |
| 13 | `offset=0.025` | −2,177,130 | −443.41¢ | 93.3% | 100% | 1.2% | 0.00 | −97.7 |
| 14 | `exit_5m=0.08` | −2,290,351 | −466.47¢ | 95.0% | 99.9% | 0.9% | 0.00 | −99.8 |
| 15 | `exit_5m=0.06` | −2,423,480 | −493.58¢ | 94.6% | 100% | 0.5% | 0.00 | −102.5 |
| 16 | `Baseline` (off=2c, q=0, ex=5c) | −2,505,529 | −510.29¢ | 94.4% | 100% | 0.5% | 0.00 | −104.4 |
| 17–20 | `exit_rev ∈ {0.010…0.030}` | −2,505,529 | −510.29¢ | 94.4% | 100% | 0.5% | 0.00 | −104.4 |
| 21 | `offset=0.015` | −2,592,400 | −527.98¢ | 96.5% | 100% | 0.4% | 0.00 | −101.8 |
| 22 | `offset=0.010` | −3,047,949 | −620.76¢ | 97.4% | 100% | 0.1% | 0.00 | −110.0 |

Notes:

- The baseline follows the issue-#233 `BacktestParams` defaults
  (`queue_gate=0`, 5¢ exits, `exit_reversal=0.02`), *not* the old §5
  recommendations — that baseline is the −2.5M¢ row, and every exit_rev step is
  bit-identical to it (the mercy rule disarms the same windows at every tested
  distance when exits fire on ~100% of pairs; consistent with §6, amplified).
- **The queue gate is the dominant lever.** Loosening it from 200 → 10 improves
  PnL ~50× (PF 0.02 → 0.42), a monotonic gradient. The old §2.1 takeaway
  ("being present even when size ahead is large generates positive net fills")
  is **inverted on certified data**: every increment of queue tolerance
  converts into adverse-selection losses faster than into merge profit. The
  gradient has not flattened at `queue=10` — the best measured point is the
  edge of the axis, not an interior optimum.
- **Exit/threshold axes are second-order in this regime**: in the baseline
  regime exits fire on ~100% of pairs, so exit tuning merely rearranges the fee
  bill ($3,682 baseline fees). Exit thresholds become first-order only after
  the entry side puts the strategy in a regime where most pairs survive to
  merge.

#### Per-series: the edge lives in BTC and ETH

Per-series PnL at `queue=10` (best config, −15,342¢ total):

| Series | PnL (cents) | Verdict |
|:---|---:|:---|
| `btc-up-or-down-5m` | **+1,722.92** | Only strongly positive series |
| `eth-up-or-down-5m` | **+778.88** | Positive |
| `btc-up-or-down-15m` | +666.72 | Positive |
| `eth-up-or-down-15m` | +280.81 | Marginal |
| `sol-up-or-down-15m` | −599.28 | Negative |
| `xrp-up-or-down-15m` | −404.70 | Negative |
| `sol-up-or-down-5m` | −2,305.43 | Negative |
| `bnb-up-or-down-5m` | −3,288.06 | Negative |
| `xrp-up-or-down-5m` | −6,190.61 | Strongly negative |
| `bnb-up-or-down-15m` | −6,003.48 | Strongly negative |

BTC and ETH are the deepest books in the universe; BNB and XRP the thinnest.
On thinner books the tape-sweep fill rule is dominated by informed flow. This
**reverses §4** on golden data: BNB-5m was "Top Performer +640¢" there and is
−3,288¢ here. The four BTC/ETH rows sum to **+3,449¢** while the six others
sum to −18,792¢ — motivating Sweep 2.

### §0.B Sweep 2 — BTC/ETH-only offset × fine-queue joint grid

**Run:** focused joint grid, 40 configs = 5 offsets (1.0–3.0¢) × 8 fine queue
gates (0/2/4/6/8/10/15/25), exits pinned at the 5¢ unification,
`exit_reversal=0.02`, size 5, series whitelist
`btc/eth × 5m/15m` — **2,011 of the 4,910 golden windows**. **Artifact:**
`run/sweeps/golden_grid_btceth.json` (+ `.log`). **Cost:** 468s load +
6,896s simulation. The artifact lives under gitignored `run/`.

#### Headline: the BTC/ETH restriction is confirmed, and the queue gate is a phase switch

| Config | PnL (cents) | Pair rate | Exit rate | PF | Sharpe |
|:---|---:|---:|---:|---:|---:|
| `off=0.020, q=4` | **+4,039.7¢** | 14.6% | 1.9% | 2.69 | 6.38 |
| `off=0.020, q=2` | +3,999.7¢ | 14.5% | 1.9% | 2.67 | 6.33 |
| `off=0.020, q=8` | +3,910.7¢ | 14.2% | 1.8% | 2.68 | 6.27 |
| `off=0.020, q=6` | +3,850.2¢ | 14.1% | 1.8% | 2.66 | 6.18 |
| `off=0.015, q=2` | +3,658.8¢ | 20.1% | 3.0% | 2.06 | 5.27 |
| `off=0.015, q=4` | +3,628.8¢ | 20.1% | 3.0% | 2.05 | 5.23 |
| `off=0.015, q=8` | +3,617.2¢ | 19.3% | 3.1% | 2.15 | 6.12 |
| `off=0.020, q=10` | +3,449.4¢ | 13.9% | 2.0% | 2.31 | 5.38 |
| `off=0.030, q=2` | +3,304.2¢ | 6.9% | 1.0% | **4.32** | **7.39** |
| `off=0.030, q=4` | +3,304.2¢ | 6.9% | 1.0% | **4.32** | **7.39** |
| `off=0.025, q=2` | +3,295.0¢ | 9.6% | 1.7% | 2.89 | 6.46 |
| `off=0.015, q=25` | (bottom of the + region) | | | | |
| `off=0.010, q=2` | +1,189.4¢ | 26.0% | | 1.24 | |
| `off=0.010, q=8` | −288.2¢ | 25.3% | | 0.95 | |
| `off=0.010, q=25` | −2,876.9¢ | 25.6% | | 0.67 | |
| `off=0.020, q=0` | **−954,611.8¢** | 96.2% | | 0.00 | |

Findings:

1. **The BTC/ETH-only hypothesis is confirmed.** Best config makes +4,039.7¢
   (≈ +$40) over 2,011 windows, and **all four series contribute positive PnL**
   (BTC-5m +1,762.9, ETH-5m +1,247.7, BTC-15m +666.7, ETH-15m +362.3). On the
   full universe the same regime was −15,342¢ — restricting the universe flips
   the sign, exactly as §0.A predicted.
2. **The queue gate is a phase switch, not a knob.** `q=0` (the old §5
   recommendation) is catastrophic: −954,611.8¢ with 96.2% of windows
   filling-and-exiting. Any gate ≥ 2 lands in the +3,300…+4,000¢ band. The
   strategy only works when it refuses to fill behind meaningful size.
3. **Offset roles are measurable.** 2¢ maximizes absolute PnL; 3¢ maximizes
   quality (PF 4.32, Sharpe 7.39, only 6.9% fill rate); 1¢ is fragile —
   profitable only at q ≤ 4, negative from q=8. The **2¢ × q∈[2,8] region is a
   ~700¢-wide plateau**, a robustness sign: it is not a single-point optimum.
4. **Confirmed supersedes §5 in full:** the old live recommendation
   (`queue_gate = 0`) is the −955k¢ row on certified data.

#### Standing caveats

5-share size; zero merge gas modeled; 2,011 windows from 6 days of one week in
September; two knobs optimized jointly, and the winner's edge over its
neighbors (~100¢) is within one-day regime noise. Correct reading: *the region
off≈2¢, q≈2–8, BTC/ETH-only is worth paper-trading* — not a tuned production
parameter.

#### Next measurements

1. Day-level robustness of the winning region (PnL spread across the 6 golden
   days, not one lucky day).
2. Real merge gas + larger size (50 shares) on the winning region.
3. Only then: exit-axis re-sweep inside this regime.

---

# Prior results (pre-golden, pre-#226/#227 engine) — historical record

> **Dataset:** 199,884 tick snapshots across 1,680 closed condition windows (10 series: BTC, ETH, SOL, BNB, XRP on 5m and 15m) recorded in `run/ticks/`.
> **Simulation Engine:** `scripts/sweep_backtest.py` executing `backtest/engine.py:replay()` with conservative `fill_model="tape"`.
>
> **These numbers predate the fill rule (2026-09-16, issue #226).** `fill_model`
> no longer exists: the engine has one hard-coded rule, and the `"tape"` setting
> these runs used was half of it. Read the table as a record of what was decided,
> not as a measurement of the current engine. The driver behind the
> exit-reversal row (`research/sweeps/run_exit_rev_110.py`) was deleted with the
> knob.
>
> **Item 5 below also predates the pair-cost rule (issue #227).** The value it
> reports, 1.05, was chosen to sit above 1.00 so a sweep could switch off an
> entry-side block. That block is deleted and the knob — now `max_pair_cost`,
> defaulting to 0.99 — is hard-capped at 1.00 and bounds the leg chase only.
> The row is a record of what was set, not a setting anything can reproduce.

---

## 1. Executive Summary

- **Baseline Strategy** (`offset=0.02, queue=50, exit=0.12`): Generated **+8.96¢** total across 1,680 windows (+0.01¢/win) with a 0.1% pair fill rate due to aggressive queue filtering.
- **Optimal Parameter Configuration** (`offset=0.020, queue=0, exit_5m=0.08, exit_rev=0.015`):
  - **Total P&L:** **+3,279.78¢ (+$32.80 USD)** across 1,680 windows (+1.95¢/win).
  - **Profit Factor:** **4.15** (Gross gains 4.15x gross losses).
  - **Win Rate:** **13.7%**; **Pair Rate:** **10.6%**; **Exit Rate:** **5.1%**.
  - **Max Drawdown:** **94.17¢**; **Sharpe Ratio Proxy:** **7.28**.
  - **9 of 10 series profitable** (Top: BNB 5m +640¢, ETH 5m +459¢, XRP 5m +417¢).

---

## 2. Controllable Parameter Sensitivities

### 2.1 Queue Gate (`queue_gate`)
- `queue_gate = 50`: Suppresses fills excessively (pair rate 0.1%, PnL +8.96¢).
- `queue_gate = 0` (Disabled / Always Quote): Captures aggressive market orders that sweep the book across the window, boosting pair capture to **10.6%** and PnL to **+2,517.88¢** (with baseline exits) and **+3,279.78¢** (with optimized exits).
- *Takeaway:* On Polymarket 5m binary books, being present in the order book even when size ahead is large generates positive net fills from liquidity sweeps. **(Superseded: see §0.A/§0.B — inverted on the golden dataset.)**

### 2.2 Entry Offset (`offset`)
- `offset = 0.010` (1.0¢): Pair rate high, but gross margin only 2.0¢/share, leading to higher adverse selection drag on one-sided fills.
- `offset = 0.020` (2.0¢, $0.48 entry): **Sweet spot**. Provides 4.0¢ gross margin on pair merges while capturing 10.6% of oscillating windows.
- `offset = 0.030` (3.0¢, $0.47 entry): Generates 6.0¢ gross margin, achieving strong PnL (+3,216.43¢), but lower pair frequency (8.8%).

### 2.3 Monotonic Exit Threshold (`exit_thresh`)
- `exit_5m = 0.14` (14¢ drift): Cut losses too late; total PnL drops to +2,174.88¢ and Max DD rises to 109.17¢.
- `exit_5m = 0.08` (8¢ drift): **Optimal**. Cuts trending monotonic moves early before adverse settlement loss, reducing Max Drawdown to 94.17¢ and increasing Profit Factor to 4.15. **(Superseded: see §0.A — exits are second-order on certified data.)**

### 2.4 Mean-Reversion Buffer (`exit_reversal`)
- `exit_reversal = 0.015` vs `0.020`: Consistent performance across both, preventing premature stop-outs during temporary oscillation chop.

---

## 3. Joint Optimization Grid Top Rankings (128 Runs)

| Rank | Configuration | PnL (cents) | Avg PnL | Win Rate | Pair Rate | Exit Rate | Max DD | Profit Factor | Sharpe |
|:---:|:---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | `off=0.020_q=0_ex=0.08_rev=0.015` | **+3279.78c** | **+1.95c** | **13.7%** | **10.6%** | **5.1%** | **94.17c** | **4.15** | **7.28** |
| 2 | `off=0.020_q=0_ex=0.08_rev=0.020` | +3279.78c | +1.95c | 13.7% | 10.6% | 5.1% | 94.17c | 4.15 | 7.28 |
| 3 | `off=0.030_q=0_ex=0.08_rev=0.015` | +3216.43c | +1.91c | 11.8% | 8.8% | 5.1% | 104.13c | 4.10 | 7.20 |
| 4 | `off=0.030_q=0_ex=0.08_rev=0.020` | +3216.43c | +1.91c | 11.8% | 8.8% | 5.1% | 104.13c | 4.10 | 7.20 |
| 5 | `off=0.020_q=0_ex=0.10_rev=0.015` | +2923.78c | +1.74c | 13.5% | 10.6% | 5.4% | 94.17c | 3.27 | 6.46 |
| 6 | `off=0.020_q=0_ex=0.10_rev=0.020` | +2923.78c | +1.74c | 13.5% | 10.6% | 5.4% | 94.17c | 3.27 | 6.46 |
| 7 | `off=0.030_q=0_ex=0.10_rev=0.015` | +2713.83c | +1.62c | 11.6% | 8.8% | 5.2% | 139.13c | 3.12 | 6.19 |
| 8 | `off=0.020_q=0_ex=0.12_rev=0.015` | +2517.88c | +1.50c | 13.3% | 10.6% | 5.6% | 101.17c | 2.68 | 5.63 |

---

## 4. Per-Series Profitability Breakdown (Optimal Profile)

| Series | Duration | Total PnL (cents) | Assessment |
|:---|:---:|---:|:---|
| `bnb-up-or-down-5m` | 5m | +640.30c | Top Performer (Strong oscillation) |
| `eth-up-or-down-5m` | 5m | +458.82c | Highly Profitable |
| `xrp-up-or-down-5m` | 5m | +416.76c | Highly Profitable |
| `bnb-up-or-down-15m` | 15m | +411.96c | Highly Profitable |
| `eth-up-or-down-15m` | 15m | +359.94c | Highly Profitable |
| `sol-up-or-down-5m` | 5m | +350.18c | Highly Profitable |
| `xrp-up-or-down-15m` | 15m | +270.68c | Profitable |
| `sol-up-or-down-15m` | 15m | +257.42c | Profitable |
| `btc-up-or-down-5m` | 5m | +191.78c | Profitable |
| `btc-up-or-down-15m` | 15m | -78.06c | Marginal / Trending Drag |

**(Superseded: see §0.A — on the golden dataset the ordering flips: BTC/ETH carry all the PnL; BNB/XRP are the largest losers.)**

---

## 5. Final Live Strategy Recommendations

1. **Resting Offset:** Use `offset = 0.020` (rest bids at `0.48 / 0.48` vs 0.50 mid).
2. **Queue Gating:** Set `queue_gate = 0` (disable rest queue filter).
3. **Monotonic Exit:**
   - 5m default: `0.08` (cut naked leg if mid moves >= 8¢ one-way without reversal).
   - BTC 5m: `0.05` (tightest, highly trending).
   - 15m default: `0.09`.
4. **Mean-Reversion Buffer:** `exit_reversal = 0.015`.
5. **Pair Cost Filter:** `pair_cost_gate = 1.05` (the knob and the filter are both gone — see the header note).

> **Superseded by §0.B.** On the certified golden dataset the §5 profile
> (`queue_gate = 0`) is the −955k¢ row even restricted to BTC/ETH. The confirmed
> region is: **BTC/ETH-only universe, `offset = 0.020`, `queue_gate ∈ [2, 8]`,
> 5¢ exits** — and even that is paper-trading territory pending the §0.B next
> measurements.

---

## 6. Issue #110 — exit_reversal isolated sweep (Sep 9, 2026)

Dedicated 1D sweep of the mercy-rule disarm distance at 0.005 steps, holding
all other params at baseline (`offset=0.02, queue_gate=0, exit_5m=0.08,
fill_model=tape`, size 5). Dataset: `run/ticks/ticks_2026-09-08.jsonl`
(48,766 snaps, 515 windows). Full artifact: `research/sweeps/exit_reversal_110.json`;
driver: `research/sweeps/run_exit_rev_110.py`.

| exit_reversal | exit_rate | pair_rate | total_pnl | avg_pnl | win_rate | max_dd | profit_factor | sharpe |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.010 | 4.27% | 2.14% | -1113.31c | -2.16c | 2.5% | 1144.08c | 0.32 | -2.50 |
| 0.015 | 4.27% | 2.14% | -1113.31c | -2.16c | 2.5% | 1144.08c | 0.32 | -2.50 |
| 0.020 | 4.27% | 2.14% | -1113.31c | -2.16c | 2.5% | 1144.08c | 0.32 | -2.50 |
| 0.025 | 4.27% | 2.14% | -1113.31c | -2.16c | 2.5% | 1144.08c | 0.32 | -2.50 |
| 0.030 | 4.08% | 2.14% | -1089.65c | -2.12c | 2.5% | 1120.42c | 0.32 | -2.45 |

**Reading.** The response is flat across 0.010–0.025 (bit-identical results:
the mercy rule disarms the same windows at every distance in this range) with
a marginal +23.66c edge at 0.030 (one fewer exit: 4.08% vs 4.27%). Exits are
rare to begin with (~4% of windows), so the disarm distance is second-order at
this baseline. This corroborates §2.4 ("consistent performance across both")
with finer granularity on fresh data.

**Recommendation for #111.** Unify live to the backtest default `0.02` — the
0.015 vs 0.020 choice is literally immaterial on this dataset, so there is no
PnL reason to keep the divergence. Do not adopt 0.030 on a +24c single-window
difference; re-test it only if exits become a larger PnL share.
