# CONSTRAINTS — Issue #136: Add per-window return distribution histogram to backtest dashboard view

## Scope Lock (read first)
1. **Backtest engine untouched:** `backtest/engine.py` and its core simulation logic must not be modified. All histogram data is derived in `server/osc_dash.py` from the existing `per_window` result list.
2. **Dashboard backtest tab only:** Scope is strictly `/api/backtest` and the backtest tab (`#tab-backtest`) in `server/osc_dash.py`. Do not modify `/oscillation` or `/analysis` pages.
3. **Pure client-side chart rendering:** Rendered using the existing Chart.js 4.4.0 library in `server/osc_dash.py`. No server-side PNG generation, no new external npm or pip dependencies.
4. **No query parameter changes:** Do not introduce new query parameters to `/api/backtest`.

## Quality Guardrails
5. **Zero regressions:** Targeted suite covering modified files must pass:
   `python -m pytest tests/test_osc_dash_integration.py -q`.
   Full-repo sweeps stay with CI on push (AGENTS.md testing policy).
6. **Histogram invariant:** For any non-empty backtest result, `sum(bucket["count"] for bucket in pnl_histogram["buckets"]) == n_windows`.
7. **Empty & degenerate state resilience:** When `n_windows == 0`, zero fills occur, or variance is zero, `/api/backtest` returns a well-formed empty/degenerate structure (`buckets: []` or single bar) and the UI renders without JavaScript errors or leaked chart instances.
8. **No suppression:** Strictly forbid skipping, disabling, weakening existing tests, or suppressing linters.
