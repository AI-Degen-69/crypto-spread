# CONSTRAINTS — Issue #221: Measure whether a running backtest delays the live tick (GIL contention)

## Scope Lock
1. **Measurement Only**: Strictly no architecture redesign, no process separation, no yielding modifications to replay loops, and no changes to live engine decision/order-flow logic.
2. **Timing Source Integrity**: Tick interval deltas must use monotonic high-resolution time (`time.perf_counter()`) to prevent NTP/wall-clock skew.
3. **Bounded Memory**: Any in-memory recording buffers must be strictly bounded (e.g. `deque(maxlen=1000)`) with zero memory leak risk.
4. **No New Dependencies**: Stdlib and existing dependencies only (no new packages).
5. **No Regressions**: Existing trading state machine transitions, order tracking, and parity invariants must remain 100% identical.

## Quality Guardrails
6. **Targeted Test Gate**:
   - `python -m pytest tests/test_gil_contention_instrumentation.py tests/test_engine_parity.py -q` must pass with 0 failures (<2s).
7. **Anti-Cheat**:
   - No disabling, skipping, or weakening tests.
   - No suppressing warnings or linters.
8. **Empirical Measurement Standard**:
   - Benchmark script must report concrete p50, p95, p99, and max interval values for both idle baseline and under-load backtest sweep.
