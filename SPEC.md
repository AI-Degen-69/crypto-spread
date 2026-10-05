# SPEC.md — Issue #442: Overnight Parameter Optimization Sweep on Golden Dataset

## 1. Objective & Scope
Build and execute an autonomous, memory-bounded, highly optimized parameter sweep simulation engine targeting Polymarket crypto UP/DOWN binary markets across 5-minute and 15-minute timeframes for the 5 key crypto assets: **BTC, ETH, BNB, XRP, and SOL** (10 total series).
The simulation runs against the certified 6-day Golden Dataset (`run/ticks/golden/`), benchmarking against baseline parameters, logging every run incrementally, and producing a comprehensive risk-adjusted performance report (`overnight_summary.md`).

## 2. Target Markets & Universe
The 10 series in `strategy/series.py:SERIES`:
- `btc-up-or-down-5m`, `btc-up-or-down-15m`
- `eth-up-or-down-5m`, `eth-up-or-down-15m`
- `bnb-up-or-down-5m`, `bnb-up-or-down-15m`
- `sol-up-or-down-5m`, `sol-up-or-down-15m`
- `xrp-up-or-down-5m`, `xrp-up-or-down-15m`

## 3. Dataset & Memory Architecture
- **Location:** `run/ticks/golden/`
- **Integrity:** Certified 6 days (4,910 windows, ~1.43M ticks).
- **Execution Model:** To prevent RAM exhaustion (each daily tick file is hundreds of MB to 1.4 GB; total ~4.5 GB jsonl), replay must either:
  1. Stream windows via `iter_windows_streaming` / `group_by_cid_indexed`, OR
  2. For multi-iteration sweeps, pre-parse condition windows into a compact lightweight window cache (stripping non-essential tick fields, preserving orderbook top + trades tape necessary for `_simulate_window`), or evaluate parameter batches in streaming passes.
- **Resilience:** Wrap window simulation in per-run try/except blocks so any numerical edge case or degenerate parameter combination logs an error and continues.

## 4. Parameter Space & Baselines
### 4.1 Baseline Configuration
- `offset`: 0.02
- `queue_gate`: 50
- `quote_shares`: 120
- `entry_delay_sec`: 0.0
- `entry_delay_pct`: 0.0
- `enable_leg_chase`: False
- `exit_reversal`: 0.02
- `exit_thresh_by_slug`:
  - `default_5m`: 0.05
  - `default_15m`: 0.05
  - `btc-up-or-down-5m`: 0.05, `btc-up-or-down-15m`: 0.05
  - `eth-up-or-down-5m`: 0.05, `eth-up-or-down-15m`: 0.05
  - `bnb-up-or-down-5m`: 0.05, `bnb-up-or-down-15m`: 0.05
  - `sol-up-or-down-5m`: 0.05, `sol-up-or-down-15m`: 0.05
  - `xrp-up-or-down-5m`: 0.05, `xrp-up-or-down-15m`: 0.05

### 4.2 Search Space Options
1. `offset`: [0.001, 0.002, 0.005, 0.008, 0.01, 0.012, 0.015, 0.018, 0.02, 0.022, 0.025, 0.028, 0.03, 0.035, 0.04, 0.05, 0.06, 0.08, 0.1, 0.15, 0.2, 0.3, 0.4, 0.49]
2. `queue_gate`: [0, 5, 10, 20, 35, 50, 75, 100, 150, 200, 300, 500, 1000, 2500, 5000, 10000, 50000, 100000]
3. `quote_shares`: [5, 10, 20, 35, 50, 75, 100, 120, 150, 200, 250, 300, 500, 750, 1000, 2000, 5000, 10000]
4. `entry_delay_sec`: [0, 1, 2, 3, 5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 300, 600, 900, 1800, 3600]
5. `entry_delay_pct`: [0.0, 0.01, 0.02, 0.03, 0.05, 0.08, 0.1, 0.12, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.75, 1.0]
6. `enable_leg_chase`: [False, True]
7. `exit_reversal`: [0.001, 0.005, 0.01, 0.012, 0.015, 0.018, 0.02, 0.022, 0.025, 0.028, 0.03, 0.035, 0.04, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3, 0.5]
8. `exit_thresh_by_slug` (stop loss): [0.01, 0.02, 0.03, 0.04, 0.045, 0.05, 0.055, 0.06, 0.07, 0.08, 0.09, 0.1, 0.12, 0.14, 0.16, 0.18, 0.2, 0.25, 0.3, 0.4, 0.5]

## 5. Execution & Persistence Engine
- Dedicated runner script: `scripts/run_overnight_sweep.py` (CLI + module) supporting:
  - `--source`: default `run/ticks/golden`
  - `--iterations`: number of search samples (default 100, configurable)
  - `--strategy`: `baseline`, `random`, `grid`, `latin_hypercube`
  - `--out-csv`: path to CSV output (default `run/backtest_results_overnight.csv`)
  - `--summary-md`: path to report output (default `overnight_summary.md`)
- Continuous incremental append to CSV/JSONL after each parameter configuration completes.
- Summary report generation computing:
  - Baseline comparison across all 10 series.
  - Top 3 configurations per asset and overall ranked by Risk-Adjusted Return (Net PnL / Max Drawdown).
  - Win rate, total trades, profit factor, max drawdown.

## 6. Acceptance Criteria
1. Baseline backtest runs cleanly over the golden dataset across all 10 series and outputs benchmark metrics.
2. The sweep engine tests combinations across all requested parameter dimensions.
3. Results are saved incrementally to disk (`run/backtest_results_overnight.csv`).
4. `overnight_summary.md` is populated with structured tables and recommendations.
5. All targeted unit and integration tests pass without regression: `python -m pytest tests/test_backtest_engine.py tests/test_sweep_backtest.py -q`.
