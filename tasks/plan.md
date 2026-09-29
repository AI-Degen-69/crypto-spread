# Plan — Issue #331: stream backtest results live

Branch: i331/stream-backtest-live | Issue: #331
Size tier: **Standard** (2 zones of one file `server/osc_dash.py` + one test file; no new deps; single architectural decision already locked via the issue's default assumptions).
Task type: **Code** (backend/SSE + frontend JS), verification via automated integration tests.
Spec: `SPEC.md` · Guardrails: `CONSTRAINTS.md`

## CodeRabbit plan intake (costed once)
- **Adopted:** overall phase structure (worker progress channel → SSE endpoint → frontend reader → tests), the spawn-context `multiprocessing.Manager` + per-run `manager.Queue()` design choice, `fetch`+`getReader` over `EventSource` (no auto-reconnect duplicate runs), synchronous pool-terminate + release-once guard cleanup, worker-side batching, batch size 1 / `queue.Queue` test harness, direct-ASGI disconnect test technique.
- **Rejected:** splitting into 12 micro-tasks across 4 phases (merged into 5 atomic tasks); the separate "shared helpers" phase kept but folded into the endpoint task since `api_backtest`'s body is small enough to extract in one edit.
- **Left [UNVERIFIED] → now resolved from code:** all cited seams exist — `_run_backtest_simulation_worker` (`server/osc_dash.py:1546`, per-window loop `1591-1598`), guards at `1494-1496`, `BACKTEST_TIMEOUT_SEC` `1505`, `get_backtest_pool` `1508`, `shutdown_backtest_pool` `1538`, `api_backtest` `2245` (timeout handler `2440-2450`), `api_live_stream` `2853`, `runBacktest` `6307`, `setBacktestLoadingState` `6021`, `startBtTimer` `6056`, shutdown hook `3433`. CodeRabbit's line numbers drifted slightly (e.g. `:1578-1592` → `1591-1598`, `:6186-6300` → `6307+`); treat live code as truth.
- Open question from the issue's `needs-answers` flag — all four "Open questions" are resolved by adopting the issue's own stated defaults (Manager Queue; completion-order provisional points; batched emission; new `/api/backtest/stream`). No operator input required.

## Dependency graph
T1 → T2 → T3 → T4 → T5 (linear: queue infra unblocks worker emission, which unblocks the endpoint, which unblocks the frontend reader; tests cover the whole vertical slice and are written per-task TDD where practical).

## Tasks

### T1 — Worker progress channel + shared guards helpers `[Backend/Logic]` — size M — **[x] DONE**
- Files: `server/osc_dash.py`, `tests/test_osc_dash_integration.py`.
- Lazy `spawn`-context `multiprocessing.Manager` singleton (dead-manager recreation), `_new_backtest_progress_queue()` factory (monkeypatchable in tests), shutdown cleanup inside `shutdown_backtest_pool()`/app shutdown (`:3433`).
- Optional `progress_queue=None`, `progress_batch_windows`, `progress_batch_interval` params on `_run_backtest_simulation_worker`; defaults preserve current behavior. Emission: per filtered window append preview point with final-curve size scaling (`pnl_cents * size`, round 2), running provisional total + `windows_done`; flush by count/interval and once after the loop before sort/limit; queue-put failure disables emission silently.
- Extract `_terminate_backtest_pool()` (detach pool, terminate processes, `shutdown(wait=False, cancel_futures=True)`) and a per-run release-once helper; existing `/api/backtest` + `/api/backtest/sweep` timeout handlers call the terminate helper (504 texts unchanged).
- **Depends on:** — · Verify: unit-style assertions in `tests/test_osc_dash_integration.py` that the worker with a `queue.Queue` emits batched progress with correct provisional totals and an unchanged final dict; existing tests green.

### T2 — SSE endpoint `GET /api/backtest/stream` `[Backend/Logic]` — size M — **[x] DONE**
- Files: `server/osc_dash.py`.
- Same query params + validation/429/4xx error shapes as `api_backtest` (extract request-preparation into a helper both endpoints call, preserving every return value). Under `_BACKTEST_LOCK`: 429 if busy, else set `_BACKTEST_RUNNING`, acquire semaphore, create queue, submit worker via `run_in_executor` in a task whose `finally` calls release-once. `EventSourceResponse` async generator (pattern of `api_live_stream`): JSON envelopes `type=progress|final|error`; drain queue via `asyncio.to_thread` with short timeout handling `queue.Empty`; check `request.is_disconnected()` each iteration; drain remaining then emit exact `final` dict; worker exception → `error` event. On incomplete end (disconnect or `BACKTEST_TIMEOUT_SEC`) call `_terminate_backtest_pool()` + release-once synchronously in the generator's `finally`.
- **Depends on:** T1 · Verify: new tests — `progress` before exactly one `final`; serialized `equity_curve` equality vs `/api/backtest` (± `limit_windows`); last provisional cumulative equals unlimited final total.

### T3 — Direct-ASGI disconnect test: guards released immediately `[Debug]` — size S — **[x] DONE**
- Files: `tests/test_osc_dash_integration.py`.
- Blocking worker (one progress put then `threading.Event.wait`); monkeypatched `ThreadPoolExecutor` pool + `queue.Queue` factory; drive `app` with custom `receive`/`send`, returning `http.disconnect` after the first `progress` chunk. Assert after return: `_BACKTEST_RUNNING` false, semaphore unlocked, `_terminate_backtest_pool` called, immediate follow-up request not 429; then release the event and assert the stale task does not clear a newer run's guards.
- **Depends on:** T2 · Verify: this test; run targeted file suite.

### T4 — Frontend: incremental equity curve via stream reader `[Design/UI]` — size M — **[x] DONE**
- Files: `server/osc_dash.py` (inline JS only).
- Extract success-render body of `runBacktest` verbatim into `renderBacktestResult(data)`. `runBacktest` fetches `/api/backtest/stream` with existing `btControlQuery` + `AbortController`; parse `res.body.getReader()` chunks, split on blank lines, JSON-parse `data:` lines, controller-identity check before each render; non-OK → existing JSON `detail` failure path. Provisional chart at stream start (label "Cumulative PnL ($) — provisional", pointRadius 0), append per `progress` (`(cumulative_pnl_cents/100).toFixed(2)`, running-index labels, one `update('none')`), windows-done + provisional P&L next to the elapsed timer; `final` → `renderBacktestResult(data.result)`; `error` → existing failure path; ignore `AbortError`; bounded 429-retry with short delay after an intentional abort.
- **Depends on:** T2 · Verify: HTML-string contract tests — `/api/backtest/stream`, `getReader`, provisional label, `renderBacktestResult` present; existing abort/stale-guard/shared-query assertions keep passing.

### T5 — Final verification gate `[Debug]` — size XS — **[x] DONE** (246 dash-integration + 151 engine tests green)
- Files: none (verification only).
- **Depends on:** T1–T4 · Verify: `python -m pytest tests/test_osc_dash_integration.py -q` green; `python -m pytest tests/test_backtest_engine.py -q` green (unchanged-engine sanity); quick `python -m scripts.backtest run/ticks` smoke if a dataset exists; leave full suite to CI.

## Checkpoints
- After T2: backend streams end-to-end (curl-able); one-line progress report in Mode A.
- After T4: feature complete; report before Station IV.

## Improvement proposal (evidence-based, adopted by default)
- **Simplification:** reuse the *existing* `shutdown_backtest_pool()` as the anchor for manager cleanup instead of adding a parallel shutdown path — evidence, `server/osc_dash.py:3433` already calls `shutdown_backtest_pool()` on app shutdown, so hanging manager cleanup there covers every exit path with zero new wiring. Adopted into T1.
