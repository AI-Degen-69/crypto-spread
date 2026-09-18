# SPEC — Issue #259: Isolate backtest execution into ProcessPoolExecutor to eliminate GIL contention

## Goal
Isolate `/api/backtest` simulation workloads from FastAPI's shared threadpool into a dedicated `ProcessPoolExecutor(max_workers=1)` with an `asyncio.Semaphore(1)` concurrency cap, giving CPU replay loops an independent Python process and GIL, and completely preventing tail latency / tick starvation on the live trading engine.

## Problem Statement
In Issue #221, empirical measurement proved that CPU-heavy backtest sweeps running in the default threadpool hold the Python GIL, causing severe tail starvation on the live trader engine's 1-second ticks (p99 latency jumping from idle 1,029 ms to 3,286 ms, skipping 2-3 tick intervals). In 5-minute binary markets, multi-second stalls risk missed quote cancellations, late entries, and delayed stop-loss executions.

## Architectural Design
1. **Process Pool Management**:
   - A single-worker `ProcessPoolExecutor(max_workers=1)` managed cleanly within `server/osc_dash.py`.
   - Lazy initialization on first use (or app startup), with graceful shutdown on app exit (`app.add_event_handler("shutdown", ...)` or lifespan).
2. **Top-Level Picklable Worker**:
   - Extraction of pure simulation and aggregation logic into a top-level module function `_run_backtest_simulation_worker(...)`.
   - Passes path strings, scalar values, and plain dicts across the process boundary (guaranteeing Windows `spawn` compatibility).
   - Returns the complete result dictionary formatted identically to the current `/api/backtest` response.
3. **Endpoint Concurrency Control**:
   - `/api/backtest` converted to an `async def` endpoint.
   - Guarded with an `asyncio.Semaphore(1)`: if a backtest is already in flight, return immediate HTTP 429 (`{"error": "Backtest simulation already in progress. Please retry shortly."}`).
   - Asynchronously awaits `loop.run_in_executor(pool, _run_backtest_simulation_worker, ...)` so the FastAPI event loop remains 100% free and responsive.
4. **Benchmark Script Compatibility**:
   - Support `--duration` flag in `scripts/measure_gil_contention.py` to cap stress execution.
   - Adapt `scripts/measure_gil_contention.py` to test the new isolated backtest execution flow without blocking.

## Acceptance Criteria
1. `/api/backtest` simulation executes in a `ProcessPoolExecutor` separate from the main server event loop.
2. Concurrency is capped with `asyncio.Semaphore(1)`, returning clean 429 if a backtest is already in flight.
3. Existing `/api/backtest` response schemas (summary metrics, PnL histogram, equity curve, per-series breakdown) remain 100% identical.
4. `python -m pytest tests/test_osc_dash_integration.py -k test_api_backtest -q` passes without regressions.
5. `python -m scripts.measure_gil_contention --idle-ticks 5 --duration 5 --json` runs successfully.

## Explicit Out of Scope
- Rewriting the core simulation engine in `backtest/engine.py`.
- Altering dashboard frontend UI or client-side charts.
- Splitting the web dashboard and live trader into separate microservices or repositories.
