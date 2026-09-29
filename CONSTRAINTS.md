# CONSTRAINTS — Issue #331: Stream backtest results live

Branch: i331/stream-backtest-live | Issue: #331

## Zero regressions
- `python -m pytest tests/test_osc_dash_integration.py -q` must pass (targeted
  suite only — never run the full 932-test suite locally; CI is the merge gate).
- Blocking `/api/backtest` and `/api/backtest/sweep`: status codes, payload
  shape, 429/504 detail texts unchanged. The existing `/api/backtest` contract
  test at `tests/test_osc_dash_integration.py:1444` (`fake_round.jsonl`) and
  the sweep-busy tests must keep passing.
- The worker's returned dict must stay byte-identical: same sort
  (`(first_ts, seq)`), same `limit_windows` slice, same `equity_curve`
  construction, same `params_hash`. No changes to `backtest/engine.py`.
- Final SSE `final` event's result must serialize identically to
  `/api/backtest`'s response on the same input.

## Performance thresholds
- Batch progress emission in the worker (window-count or time-interval
  triggered, plus one final flush); no per-window IPC message flooding.
- Frontend calls `chart.update('none')` at most once per `progress` event —
  no per-point animation.
- Backtest SSE adds no new polling loop hotter than ~50 ms (`queue.get`
  timeout / drain cadence).

## Anti-cheat
- Strictly forbidden: skipping or disabling existing tests, deleting
  assertions, weakening the disconnect/guard tests, or suppressing warnings to
  make suites pass.
- The disconnect test must use a real ASGI-level disconnect, not a mocked
  `is_disconnected` shortcut in the production path.

## Dependencies
- No new external dependencies. `sse_starlette` (>=2.0.0, already in
  requirements.txt) and stdlib `multiprocessing`/`asyncio` only.

## Out of bounds (files that must NOT change)
- `backtest/engine.py`, `scripts/backtest.py`, `scripts/sweep_backtest.py`,
  `strategy/streaming.py`, Live-tab SSE code.
