# SPEC.md — Issue #455: Self-Improving Iterative Sweep Optimizer

## 1. Objective & Scope
A new `--preset iterative` for `scripts/sweep_backtest.py` that climbs from a baseline via best-improvement coordinate descent on in-sample windows, keeps only changes that pass a filled-window sample gate, and confirms the winner against the baseline on a purged chronological holdout. Single-trade winners (RUN_0153 shape) can never become incumbent or confirmed.

## 2. Acceptance Criteria
- Optimizer output beats the baseline on held-out data with >= 30 filled windows (data outcome; tests prove the logic, real-data run confirms).
- A RUN_0153-style config (1 filled window, positive PnL) is rejected by the gate at selection and at confirmation.
- `python -m pytest tests/test_sweep_backtest.py -q` green with no regressions.

## 3. Key Rules
- Sample unit is the **filled window** (window with ≥1 filled leg, counted once) — not `pair_captured or exit_taken` (undercounts settlement-only legs), not `entered` (counts unfilled rests).
- Split is chronological with purge: in-sample `end_ts <= T`, holdout `start_ts >= T`, crossing windows dropped, unclocked windows counted separately.
- Descent: full-neighborhood best-improvement per pass, strict PnL gain to accept, incumbent below gate scores -inf, stop at no-improvement or `--max-passes`.
- Confirmation needs BOTH holdout gate pass AND strict holdout PnL win; baseline retained → not confirmed. Exit code 0 either way.

## 4. Out of Scope
Engine fill rules and execution model, live trader, dashboard, shipping presets, new dependencies, `BacktestParams` defaults/registry, the overnight `total_trades` undercount fix.
