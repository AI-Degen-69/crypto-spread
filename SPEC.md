# SPEC — Issue #331: Stream backtest results live

Branch: i331/stream-backtest-live | Issue: #331

## Goals
- While a backtest runs, the Backtest tab's equity curve fills in live (batched
  per settled windows) and running counters (windows done, provisional total
  P&L) update, instead of a spinner until the whole run ends.
- The final rendering after the run is identical to today's: same payload, same
  charts.
- The blocking `/api/backtest` endpoint keeps its current contract; `/api/backtest/sweep`
  stays blocking (out of scope).

## Approach (from the operator-approved defaults in the issue)
- New endpoint `GET /api/backtest/stream` (SSE via `sse_starlette`,
  `EventSourceResponse`), alongside the untouched blocking `/api/backtest`.
- Cross-process transport: lazily-created module-level `spawn`-context
  `multiprocessing.Manager`; a fresh `manager.Queue()` proxy per run passed to
  the worker as a picklable argument (plain `mp.Queue` cannot be submitted via
  `ProcessPoolExecutor` and the pool uses `spawn`, so fork inheritance is out).
- Worker: optional `progress_queue=None` + batch params on
  `_run_backtest_simulation_worker`; emits batched preview points in completion
  order with the final curve's size scaling (`pnl_cents * size`, 2-decimals);
  queue failures disable emission without failing the run; the returned dict
  stays byte-identical (same sort, `limit_windows` slice, curve construction).
- SSE envelopes: JSON in `data:` with `type` ∈ {`progress`, `final`, `error`}.
  `final` carries the exact worker result dict.
- Cancellation/timeout: on disconnect (`request.is_disconnected()` or
  `CancelledError`) or `BACKTEST_TIMEOUT_SEC` expiry, synchronously terminate
  the pool (`_terminate_backtest_pool()`: detach `_BACKTEST_POOL`, terminate
  processes, `shutdown(wait=False, cancel_futures=True)`) and release the
  guards via a per-run release-once helper. Next run gets a rebuilt pool via
  lazy `get_backtest_pool()`. The submitted task's `finally` also calls the
  release-once helper for eventual release if the generator never starts.
- Frontend: `runBacktest` reads the stream via `fetch` + `res.body.getReader()`
  under the existing `AbortController` (no `EventSource` — it auto-reconnects
  and would start duplicate runs). `renderBacktestResult(data)` is extracted
  unchanged from today's success-render body and runs on `final`. A provisional
  chart (dataset label "Cumulative PnL ($) — provisional", pointRadius 0) is
  created at stream start and appended on each `progress` with
  `update('none')`; running windows + P&L shown beside the elapsed timer.
  429 after an intentional abort is retried a small bounded number of times.

## Acceptance criteria
- [ ] Equity chart gains appended points during the run (batched), without
      waiting for completion.
- [ ] Final rendering identical to today; blocking `/api/backtest` unchanged.
- [ ] Client disconnect terminates the worker, releases
      `_BACKTEST_RUNNING`/`_BACKTEST_SEMAPHORE`, and allows an immediate next run.
- [ ] New tests in `tests/test_osc_dash_integration.py`: ≥1 `progress` event
      precedes exactly one `final`; serialized `equity_curve` in `final` equals
      `/api/backtest`'s (with and without `limit_windows`); direct-ASGI
      disconnect releases guards and terminates the pool; HTML contains
      `/api/backtest/stream`, `getReader`, provisional label,
      `renderBacktestResult`.
- [ ] `python -m pytest tests/test_osc_dash_integration.py -q` passes.

## Edge cases
- Worker exception → one `error` SSE event with the blocking endpoint's detail,
  guards released.
- Queue put failure → emission silently disabled, run completes normally.
- `limit_windows` set → live points include all filtered windows (completion
  order), final curve is still the truncated, sorted one.
- No windows / empty ticks dir → validation path returns the same error shapes
  as `/api/backtest` before streaming starts.
- Stale task after pool termination fails with broken-pool error; its
  release-once call is a no-op so it cannot clear a newer run's guards.

## Out of scope
- `/api/backtest/sweep` streaming; Live tab SSE; `strategy/streaming.py`;
  `backtest/engine.py`; CLI scripts; any change to backtest semantics.
