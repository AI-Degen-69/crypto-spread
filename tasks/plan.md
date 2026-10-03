# Plan: Issue #399 — Remove unnecessary decimal points on whole dollar Y-axis labels

Branch: `i399/remove-unnecessary-decimal-points-on-whole-dollar` | Issue: `#399`

## Classification & Routing
- **Size tier**: Small (clean tick formatting helper adoption in `server/osc_dash.py` + tests)
- **Task type**: Design / UI / Code (`frontend-ui-engineering`, `test-driven-development`, `incremental-implementation`)

## Summary & Problem Analysis
- **Problem**: In `server/osc_dash.py`, several chart Y-axis tick formatting callbacks (such as in `equityChartInstance` and `btProvisionalChart`) format values using `'$' + Number(v).toFixed(2)`, which results in whole-dollar labels with redundant decimal zeros (`$2.00`, `$0.00`, `$-5.00`) instead of clean formatting (`$2`, `$0`, `$-5`).
- **Solution**:
  1. Leverage and verify `formatSweepMoneyTick(v)` in `server/osc_dash.py` to ensure it formats whole integers as `$N` / `$-N`, zero as `$0`, and fractional values as `$N.XX` (e.g., `$2.50`, `$-1.25`).
  2. Replace legacy inline `'$' + Number(v).toFixed(2)` callbacks in `equityChartInstance` (line ~8162) and `btProvisionalChart` (line ~8433) with `formatSweepMoneyTick(v)`.
  3. Ensure `btProvisionalHist` and `pnlHistChartInstance` ticks format whole dollar amounts cleanly without trailing zeros.
  4. Add regression tests in `tests/test_theme_tokens.py` / `tests/test_osc_dash_integration.py` ensuring all money tick callbacks in `FULL_APP_HTML` produce clean labels.

## CodeRabbit Intake Note
- Adopted: N/A (no CodeRabbit comment on issue #399).
- Rejected: N/A.
- Unverified: N/A.

## Improvement Proposal (Adopted by Default)
- **Proposal**: Unify all money-axis tick callbacks across the dashboard (`equityChartInstance`, `btProvisionalChart`, `sweepChartOptions`) to reuse the single shared `formatSweepMoneyTick(v)` helper, ensuring consistent whole-dollar formatting across both the Backtest Replay and Sweep Visual charts without duplicate logic.
- **Evidence**: `server/osc_dash.py:8162` and `8433` hardcode `callback: function(v){ return '$' + Number(v).toFixed(2); }`, while `server/osc_dash.py:9831` already defines `formatSweepMoneyTick(v)` with clean whole-dollar stripping (`if (s.endsWith('.00')) s = s.slice(0, -3);`).

## Tasks

- [x] **Task 1 (S)**: `[Design/UI]` Unify chart money-axis tick callbacks to use clean whole-dollar formatting in `server/osc_dash.py`
  - Target files: `server/osc_dash.py`
  - Details:
    - Ensure `formatSweepMoneyTick(v)` is defined and accessible for all chart configurations.
    - Update `equityChartInstance` y-axis tick callback from `callback: function(v){ return '$' + Number(v).toFixed(2); }` to `callback: function(v){ return formatSweepMoneyTick(v); }`.
    - Update `btProvisionalChart` y-axis tick callback from `callback: function(v){ return '$' + Number(v).toFixed(2); }` to `callback: function(v){ return formatSweepMoneyTick(v); }`.
    - Update `pnlHistChartInstance` and `btProvisionalHist` x-axis callbacks if needed to clean whole dollar values cleanly.
  - Depends on: None
  - Verification: Node.js evaluation of `FULL_APP_HTML` callbacks and browser visual check

- [x] **Task 2 (S)**: `[Testing]` Add regression test coverage for clean whole-dollar tick formatting
  - Target files: `tests/test_theme_tokens.py`, `tests/test_osc_dash_integration.py`
  - Details:
    - Enhance `test_format_sweep_money_tick` in `tests/test_theme_tokens.py` to cover cases specified in Issue #399: `$2`, `$0`, `$-5`, `$2.50`, `$-1.25`.
    - Add assertion in test suite ensuring no chart Y-axis tick callbacks retain the raw `toFixed(2)` without whole-number trimming.
  - Depends on: Task 1
  - Verification: `python -m pytest tests/test_theme_tokens.py -q`

- [x] **Task 3 (XS)**: `[Verify]` Verify targeted tests and dashboard syntax
  - Target files: `server/osc_dash.py`, `tests/test_theme_tokens.py`
  - Details:
    - Run targeted pytest test suites to ensure 100% pass rate.
  - Depends on: Task 1, Task 2
  - Verification: `python -m pytest tests/test_theme_tokens.py tests/test_osc_dash_integration.py -q`

## Checkpoints
- Checkpoint 1 (after Task 1): All chart tick callbacks in `server/osc_dash.py` use `formatSweepMoneyTick`.
- Checkpoint 2 (after Task 2 & 3): Targeted tests pass with zero regressions.
