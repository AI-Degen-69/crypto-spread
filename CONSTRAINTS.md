# CONSTRAINTS — Issue #371

Branch: `i371/backtest-tab-lags-freezes-and-sometimes-gets-st`

## Zero regressions
- Targeted suite only, locally: `python -m pytest tests/test_osc_dash_integration.py -q`.
  The full 932-test suite is CI's merge gate — never run it locally.
- All existing streaming/HTML-contract tests must keep passing. Static string assertions
  (e.g. `test_backtest_stream_frontend_contract`) may be updated only when an identifier
  moved; assertions must never be weakened.
- New behaviour requires new tests (Phase 5).

## Numerical anti-drift (anti-cheat)
- No displayed value may change. The prefix-sum oracle (`round(sum(prefix)/len(prefix), 4)`
  for every prefix) is the binding contract for `mean_pair_cost`, `mean_pair_edge_cents`,
  `pairs_above_settle`, and `pnl_sample_cents`.
- Forbidding: deleting assertions, skipping/disabling tests, suppressing linters,
  loosening thresholds to make tests pass.

## Performance thresholds
- Per-flush server cost: constant, independent of windows processed.
- Client render during a run: at most one scheduled render per ~500 ms.
- Backtest tab stays interactive during a full-dataset run; progress updates ≥1/s.
- Stream failure visibility: ≤ a few seconds after a lost/stalled connection (client
  watchdog), not `BACKTEST_TIMEOUT_SEC` (900 s).

## Dependencies
- No new external dependencies. Use existing: `requests`, `pytest`, `httpx`, Node (already
  used by existing Node-harness tests), sse-starlette.

## Explicitly untouchable
- `backtest/engine.py`, `BacktestParams`, parameter registry, `/api/params/spec`.
- Final-result aggregation and the zero-window return.
- Sweep and blocking endpoints' cleanup semantics.
- The uncommitted working-tree diff: keep and extend; never revert.
