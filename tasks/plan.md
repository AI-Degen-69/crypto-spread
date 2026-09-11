# Plan: Issue #131 — Improve Backtest Execution Diagnostics, Pagination, Trade Transparency, and Metric Semantics

Task Type: Code + Design
Size Tier: Standard
Target Files: backtest/engine.py, server/osc_dash.py, tests/test_backtest_engine.py, tests/test_osc_dash_integration.py

## Task Breakdown

### Task 1: Extend WindowResult & Track Execution Prices in Backtest Engine
- **Files**: `backtest/engine.py`, `tests/test_backtest_engine.py`
- **Type**: Code
- **Description**:
  1. Add fields to `WindowResult`: `entry_price_up`, `entry_price_down`, `exit_price`, `settlement_mid` (optional floats with `None` default for backwards compatibility).
  2. In `_simulate_window`, record `entry_price_up` and `entry_price_down` when each leg fills.
  3. Record `exit_price` upon stop-loss exit or settlement mark.
  4. In `replay()`, populate `trades_sample` with all simulated windows (remove `< 50` ceiling) and include entry/exit price fields.
  5. Write TDD tests verifying trade entry/exit price recording and complete window capture in `tests/test_backtest_engine.py`.
- **Status**: [x]
- **Verification**: `python -m pytest tests/test_backtest_engine.py -q` (53 passed)

### Task 2: Update Server /api/backtest Metrics & Semantics
- **Files**: `server/osc_dash.py`, `tests/test_osc_dash_integration.py`
- **Type**: Code
- **Description**:
  1. In `/api/backtest`, remove `< 50` truncation on `trades_sample` and include `entry_up`, `entry_down`, `exit_price`, `exit_side`, `settlement_mid`.
  2. Disaggregate and enrich `overall` metrics: `pair_rate` (merged pairs count and rate), `win_rate` (profitable windows count and rate, with profitable exits + merged pairs breakdown).
  3. Ensure `winning_windows` counts windows with `pnl_cents > 0`.
  4. Write integration tests in `tests/test_osc_dash_integration.py` verifying API response shape and disaggregated metrics.
- **Status**: [x]
- **Verification**: `python -m pytest tests/test_osc_dash_integration.py -q` (51 passed)

### Task 3: Dashboard UI Enhancements — KPI Cards, Equity Warning, Tooltips & Executed Log Pagination
- **Files**: `server/osc_dash.py`
- **Type**: Design + Code
- **Description**:
  1. Update KPI cards: explicitly show `Pair Capture Rate` (`pairs / total` merged) and `Win Rate` (`profitable / total`).
  2. Cumulative Equity Curve: add warning state/banner when 0 fills occurred in the selected dataset/model.
  3. Per-Series table: add tooltips/headers explaining why oscillating windows may not fill limit orders or may stop-loss exit.
  4. Executed Windows Log: implement client-side pagination controller with page navigation (Prev/Next, Page X of Y), Page Size (25, 50, 100, All), Series filter, Result filter (All, Merged Pairs, Exits, Unresolved), and new columns: `Entry Up`, `Entry Down`, `Exit Price`, `Exit Type`.
- **Status**: [x]
- **Verification**: `python -m pytest tests/test_osc_dash_integration.py -k test_backtest_ui_pagination_and_tooltips_elements` (passed)

### Task 4: Regression Gate & Verification
- **Files**: All test files
- **Type**: Code
- **Description**: Run full test suite across the entire repository to ensure 100% pass rate.
- **Status**: [x]
- **Verification**: `python -m pytest -q` (420 passed)

