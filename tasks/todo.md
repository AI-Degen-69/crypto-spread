# TODO — Issue #136: Add per-window return distribution histogram to backtest dashboard view

- [x] TASK-1 [Backend/Logic]: Compute `pnl_histogram` in `/api/backtest` in `server/osc_dash.py` with bucket counts invariant `sum(count) == n_windows`.
- [x] TASK-2 [Design/UI]: Add `#chartPnlHist` canvas and container to `#tab-backtest` in `server/osc_dash.py`.
- [x] TASK-3 [Frontend/Logic]: Implement Chart.js histogram rendering in `runBacktest()` in `server/osc_dash.py`.
- [x] TASK-4 [QA/Tests]: Add/update test coverage in `tests/test_osc_dash_integration.py` and ensure `python -m pytest tests/test_osc_dash_integration.py -q` passes cleanly.
