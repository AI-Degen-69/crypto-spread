# Task Plan — Issue #259: Isolate backtest execution into ProcessPoolExecutor to eliminate GIL contention

**Size tier:** Standard — refactoring `/api/backtest` execution into a dedicated process pool with semaphore concurrency control in `server/osc_dash.py`, integration tests with concurrency gates in `tests/test_osc_dash_integration.py`, and benchmark script alignment in `scripts/measure_gil_contention.py`.
**Task type:** Performance / Code (multiprocessing execution isolation & concurrency capping).

## Context & Problem
- Issue #221 confirmed that running CPU-intensive backtest sweeps via `/api/backtest` inside FastAPI's shared threadpool locks the Python GIL, delaying the live trading engine's 1s ticks up to 3.28s (p99 blowup).
- Issue #259 resolves this by executing the simulation inside an isolated `ProcessPoolExecutor(max_workers=1)`. Because it runs in an independent OS process with its own GIL, the main process event loop and live trader experience 0ms GIL delay.
- An `asyncio.Semaphore(1)` ensures only one backtest runs at a time, rejecting concurrent sweeps with HTTP 429 to avoid CPU thrashing.

## Tasks

- [x] **TASK-1 [Performance/Architecture]**: Extract picklable worker function and implement persistent ProcessPoolExecutor
  - Target: `server/osc_dash.py`
  - What is built:
    - Define top-level module function `_run_backtest_simulation_worker(...)` taking picklable parameters (paths, knob values) and returning the full backtest results dictionary.
    - Implement persistent lazy-initialized `ProcessPoolExecutor(max_workers=1)` with clean FastAPI shutdown hook.
    - Maintain Windows spawn-compatibility (all arguments/returns strictly pickleable).
  - Helper skill: `performance-optimization`
  - Verify: Pure function unit invocation test and clean process pool execution.

- [x] **TASK-2 [Backend/Logic]**: Update `/api/backtest` to async endpoint with semaphore concurrency capping
  - Target: `server/osc_dash.py`
  - What is built:
    - Convert `api_backtest` to `async def api_backtest(...)`.
    - Introduce `_BACKTEST_SEMAPHORE = asyncio.Semaphore(1)`.
    - If semaphore is locked, immediately return HTTP 429 `{"error": "Backtest simulation already in progress. Please retry shortly."}`.
    - Run simulation via `await loop.run_in_executor(get_backtest_pool(), _run_backtest_simulation_worker, ...)`.
  - Helper skill: `performance-optimization`
  - Verify: Existing endpoint response schemas match 100%.

- [x] **TASK-3 [QA/TDD]**: Add integration tests for process isolation, response parity, and 429 concurrency capping
  - Target: `tests/test_osc_dash_integration.py`
  - What is built:
    - Ensure all existing `test_api_backtest_*` tests pass without regression.
    - Add test verifying concurrent requests return 429 when a backtest is already running.
    - Verify pickling and process execution on temporary fixture datasets.
  - Helper skill: `test-driven-development`
  - Verify: `python -m pytest tests/test_osc_dash_integration.py -k test_api_backtest -q`.

- [x] **TASK-4 [Research/Benchmark]**: Update and verify empirical benchmark runner
  - Target: `scripts/measure_gil_contention.py`
  - What is built:
    - Add `--duration` CLI option to cap benchmark stress phase.
    - Ensure script successfully runs both idle baseline and under-load stress test with isolated backtest execution.
  - Helper skill: `performance-optimization`
  - Verify: `python -m scripts.measure_gil_contention --idle-ticks 5 --duration 5 --json`.

## Verification Matrix
| Task | Method |
|---|---|
| TASK-1 | Process worker serialization test |
| TASK-2 | `client.get("/api/backtest")` endpoint test in `test_osc_dash_integration.py` |
| TASK-3 | `python -m pytest tests/test_osc_dash_integration.py -k test_api_backtest -q` |
| TASK-4 | `python -m scripts.measure_gil_contention --idle-ticks 5 --duration 5 --json` |

## Post-build gates (Station IV)
- `python -m pytest tests/test_osc_dash_integration.py -k test_api_backtest -q` passes with 0 failures (<2s).
- Benchmark script confirms negligible GIL contention under backtest load.
