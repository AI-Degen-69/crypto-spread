# CONSTRAINTS — Issue #259: Isolate backtest execution into ProcessPoolExecutor

## Scope Lock
1. **Target Isolation**: Heavy CPU loops in `/api/backtest` (tick iteration, window simulation, metrics calculation) must execute in a dedicated `ProcessPoolExecutor(max_workers=1)`.
2. **Concurrency Cap**: Concurrency must be capped using `asyncio.Semaphore(1)`. If another backtest is in flight, return a clean HTTP 429 (`{"error": "..."}`) rather than queuing unbounded work.
3. **Picklable Contracts**: All inputs passed across process boundaries must be primitives/dicts; results must be standard serializable JSON dicts.
4. **No Core Engine Changes**: No modifications to `backtest/engine.py` logic or live trader trading rules.
5. **No New Dependencies**: Use Python stdlib `concurrent.futures.ProcessPoolExecutor` and `asyncio.Semaphore` only.

## Quality Guardrails
6. **Targeted Test Gate**:
   - `python -m pytest tests/test_osc_dash_integration.py -k test_api_backtest -q` must pass with 0 failures (<2s).
   - Additional concurrency tests verifying HTTP 429 under concurrent backtest execution.
7. **Empirical Measurement Verification**:
   - `python -m scripts.measure_gil_contention --idle-ticks 5 --duration 5 --json` must execute cleanly and demonstrate negligible GIL contention.
8. **Anti-Cheat**:
   - No disabling, skipping, or weakening tests.
   - Zero regression to existing `/api/backtest` query parameters, clamping rules, or response payloads.
