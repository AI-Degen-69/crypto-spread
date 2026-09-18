# TODO — Issue #221: Measure whether a running backtest delays the live tick (GIL contention)

- [x] TASK-1 [Performance/Instrumentation]: Instrument per-market live tick intervals in `LiveTraderEngine` (`strategy/live_trader.py`).
- [x] TASK-2 [QA/TDD]: Add unit tests for timing instrumentation (`tests/test_gil_contention_instrumentation.py`).
- [x] TASK-3 [Research/Benchmark]: Create measurement runner script (`scripts/measure_gil_contention.py`).
- [x] TASK-4 [Execution & Report]: Run benchmark on real tick data and record empirical findings.
