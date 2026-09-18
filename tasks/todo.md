# TODO — Issue #259: Isolate backtest execution into ProcessPoolExecutor to eliminate GIL contention

- [x] TASK-1 [Performance/Architecture]: Extract picklable worker function and implement persistent ProcessPoolExecutor (`server/osc_dash.py`).
- [x] TASK-2 [Backend/Logic]: Update `/api/backtest` to async endpoint with semaphore concurrency capping (`server/osc_dash.py`).
- [x] TASK-3 [QA/TDD]: Add integration tests for process isolation, response parity, and 429 concurrency capping (`tests/test_osc_dash_integration.py`).
- [x] TASK-4 [Research/Benchmark]: Update and verify empirical benchmark runner (`scripts/measure_gil_contention.py`).
