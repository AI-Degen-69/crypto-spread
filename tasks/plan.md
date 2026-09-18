# Task Plan — Issue #136: Add per-window return distribution histogram to backtest dashboard view

**Size tier:** Small — 2 files (`server/osc_dash.py` and `tests/test_osc_dash_integration.py`).
**Task type:** Code + Design/UI.

## Context
- `/api/backtest` simulates every window and returns `equity_curve` and `trades_sample`.
- Aggregate stats (`win_rate`, `avg_pnl_cents`) hide outcome skew, tail risk, and dispersion.
- Adding a bucketed per-window P&L histogram to `/api/backtest` and rendering it with Chart.js on the backtest tab makes distribution shape instantly visible on every parameter sweep.

## Tasks

- [x] **TASK-1 [Backend/Logic]**: Compute `pnl_histogram` in `/api/backtest`
  - Target files: `server/osc_dash.py`
  - Build: Implement helper `_compute_pnl_histogram(per_window, size)` returning `{bucket_width_cents, buckets: [{lo, hi, count}], n, mean_cents, median_cents}`. Handle empty dataset, single-value/zero-variance degenerate cases, and ensure `sum(count) == n_windows`. Return `pnl_histogram` on success and the empty fallback shape on early error returns.
  - Helper skill: `api-and-interface-design`
  - Verify: Targeted pytest assertions on `/api/backtest` response.

- [x] **TASK-2 [Design/UI]**: Add `#chartPnlHist` canvas and container to `#tab-backtest`
  - Target files: `server/osc_dash.py`
  - Build: Add a dedicated card/panel directly below `#chartEquity` in `#btOverallCard` with header "Per-Window P&L Distribution (Histogram)", statistical badge `<span id="btPnlHistStats"></span>`, and `<canvas id="chartPnlHist" height="140"></canvas>`.
  - Helper skill: `frontend-ui-engineering`
  - Verify: HTML structure test in `tests/test_osc_dash_integration.py`.

- [x] **TASK-3 [Frontend/Logic]**: Render Chart.js histogram in `runBacktest()`
  - Target files: `server/osc_dash.py`
  - Build: Hook into `runBacktest()`. Destroy any existing `chartPnlHist` instance via `destroyChartInstance('chartPnlHist')`. Format bucket labels (e.g. `-$0.05..$0.00`), color bars by sign (green `--up`, red `--down`, neutral for zero), populate stats header (`n`, `Δ`, `mean`, `median`), and handle empty/zero-fill states gracefully without errors.
  - Helper skill: `frontend-ui-engineering`
  - Verify: Integration test checking JavaScript render logic and canvas bindings.

- [x] **TASK-4 [QA/Tests]**: Integration test coverage for histogram endpoint and UI
  - Target files: `tests/test_osc_dash_integration.py`
  - Build: Extend `test_api_backtest_simulation` and add targeted tests validating:
    1. `pnl_histogram` shape and bucket count sum invariant (`sum(count) == n_windows`).
    2. Empty/error response returns well-formed empty `pnl_histogram`.
    3. HTML contains `#chartPnlHist` canvas and histogram update code.
  - Helper skill: `test-driven-development`
  - Verify: `python -m pytest tests/test_osc_dash_integration.py -q` passes 100%.

## Verification Matrix
| Task | Method |
|---|---|
| TASK-1 | `pytest tests/test_osc_dash_integration.py -k test_api_backtest` |
| TASK-2 | `pytest tests/test_osc_dash_integration.py -k test_backtest_html` |
| TASK-3 | `pytest tests/test_osc_dash_integration.py -k test_backtest_html` |
| TASK-4 | `python -m pytest tests/test_osc_dash_integration.py -q` (all green) |
