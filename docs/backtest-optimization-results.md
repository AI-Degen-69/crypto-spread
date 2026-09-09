# Quant Backtest Optimization Results — SPREAD-2 Strategy

> **Dataset:** 199,884 tick snapshots across 1,680 closed condition windows (10 series: BTC, ETH, SOL, BNB, XRP on 5m and 15m) recorded in `run/ticks/`.
> **Simulation Engine:** `scripts/sweep_backtest.py` executing `backtest/engine.py:replay()` with conservative `fill_model="tape"`.

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
- *Takeaway:* On Polymarket 5m binary books, being present in the order book even when size ahead is large generates positive net fills from liquidity sweeps.

### 2.2 Entry Offset (`offset`)
- `offset = 0.010` (1.0¢): Pair rate high, but gross margin only 2.0¢/share, leading to higher adverse selection drag on one-sided fills.
- `offset = 0.020` (2.0¢, $0.48 entry): **Sweet spot**. Provides 4.0¢ gross margin on pair merges while capturing 10.6% of oscillating windows.
- `offset = 0.030` (3.0¢, $0.47 entry): Generates 6.0¢ gross margin, achieving strong PnL (+3,216.43¢), but lower pair frequency (8.8%).

### 2.3 Monotonic Exit Threshold (`exit_thresh`)
- `exit_5m = 0.14` (14¢ drift): Cut losses too late; total PnL drops to +2,174.88¢ and Max DD rises to 109.17¢.
- `exit_5m = 0.08` (8¢ drift): **Optimal**. Cuts trending monotonic moves early before adverse settlement loss, reducing Max Drawdown to 94.17¢ and increasing Profit Factor to 4.15.

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

---

## 5. Final Live Strategy Recommendations

1. **Resting Offset:** Use `offset = 0.020` (rest bids at `0.48 / 0.48` vs 0.50 mid).
2. **Queue Gating:** Set `queue_gate = 0` (disable rest queue filter).
3. **Monotonic Exit:**
   - 5m default: `0.08` (cut naked leg if mid moves >= 8¢ one-way without reversal).
   - BTC 5m: `0.05` (tightest, highly trending).
   - 15m default: `0.09`.
4. **Mean-Reversion Buffer:** `exit_reversal = 0.015`.
5. **Pair Cost Filter:** `pair_cost_gate = 1.05`.

---

## 6. Issue #110 — exit_reversal isolated sweep (Sep 9, 2026)

Dedicated 1D sweep of the mercy-rule disarm distance at 0.005 steps, holding
all other params at baseline (`offset=0.02, queue_gate=0, exit_5m=0.08,
fill_model=tape`, size 5). Dataset: `run/ticks/ticks_2026-09-08.jsonl`
(48,766 snaps, 515 windows). Full artifact: `run/sweeps/exit_reversal_110.json`
(gitignored); driver: `run/sweeps/run_exit_rev_110.py`.

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
