# Task Plan — Issue #221: Measure whether a running backtest delays the live tick (GIL contention)

**Size tier:** Small — lightweight monotonic timing instrumentation in `strategy/live_trader.py`, a dedicated benchmark runner script `scripts/measure_gil_contention.py`, and targeted tests. Zero architecture or decision logic change.
**Task type:** Performance / Research (empirical measurement of GIL contention on asyncio live loop).

## Context & Background

- The live trading engine runs an asyncio loop `_run_loop()` at 1s resolution (`strategy/live_trader.py:3790`), calling `_tick_all_markets()` which dispatches polls via `loop.run_in_executor` and updates markets via `_update_market_strategy(slug, res, now)`.
- Backtests run via `/api/backtest` in `server/osc_dash.py:701`, executed by FastAPI in an `anyio` threadpool worker (`run_in_threadpool`).
- Because Python's Global Interpreter Lock (GIL) is contested between CPU-heavy pure-Python worker threads (backtest loops iterating over hundreds of thousands of snaps) and the asyncio thread, we must measure whether backtests introduce measurable jitter/delay to the 1s live tick.
- This issue is strictly an empirical measurement: "This is a measurement, not a redesign. Nothing should be changed until there is a number."

## Tasks

- [x] **TASK-1 [Performance/Instrumentation]**: Instrument per-market live tick intervals in `LiveTraderEngine`
  - Target: `strategy/live_trader.py`
  - What is built:
    - Add `_last_strategy_tick_perf: Optional[float] = None` and `_tick_intervals: collections.deque[float] = field(default_factory=lambda: collections.deque(maxlen=1000))` to `MarketLiveState`.
    - In `_update_market_strategy`: measure elapsed monotonic time via `time.perf_counter()` since last call for that market slug, recording interval in `mstate._tick_intervals`.
    - Add `get_tick_timing_stats(self, slug: Optional[str] = None) -> Dict[str, Any]` to `LiveTraderEngine` computing sample count, min, p50, p95, p99, max, and mean. If `slug` is None, return both aggregated and per-market breakdowns.
    - Zero strategy or order-flow side effects.
  - Helper skill: `performance-optimization`
  - Verify: Targeted test checking interval accumulation and percentiles.

- [x] **TASK-2 [QA/TDD]**: Add unit tests for timing instrumentation
  - Target: `tests/test_gil_contention_instrumentation.py`
  - What is built:
    - Test interval tracking across consecutive simulated ticks.
    - Test percentile calculations (`p50`, `p95`, `p99`, `max`) with known distributions and empty states.
    - Test that reset or restart clears or preserves stats cleanly.
    - Test parity gate: verify zero mutation of quoting or state transitions.
  - Helper skill: `test-driven-development`
  - Verify: `python -m pytest tests/test_gil_contention_instrumentation.py -q`.

- [x] **TASK-3 [Research/Benchmark]**: Create measurement runner `scripts/measure_gil_contention.py`
  - Target: `scripts/measure_gil_contention.py`
  - What is built:
    - Dedicated CLI benchmark runner supporting `--idle-ticks`, `--tick-file`, `--json`, and `--synthetic-poll`.
    - Phase 1: Run live trader loop idle to establish baseline tick interval distribution (p50/p95/max).
    - Phase 2: Concurrently launch real backtest sweep in a background worker thread via `api_backtest` / `_simulate_window` while live loop runs.
    - Phase 3: Collect tick intervals under active backtest load.
    - Phase 4: Output comparison table (idle vs backtest) and compute GIL contention jitter delta.
    - Phase 5: Conclude with definitive verdict on whether delay is negligible for 5m window entry timing.
  - Helper skill: `performance-optimization`
  - Verify: `python -m scripts.measure_gil_contention --idle-ticks 5` smoke run.

- [x] **TASK-4 [Execution & Report]**: Run benchmark on real tick data and record empirical findings
  - Target: Run on `run/ticks/ticks_2026-09-18.jsonl` (or largest available).
  - What is done: Execute the full benchmark, record the exact p50 / p95 / max numbers, and provide the definitive answer to Issue #221.
  - Helper skill: `performance-optimization`
  - Verify: All numbers logged and confirmed reproducible.

## Verification Matrix
| Task | Method |
|---|---|
| TASK-1 | Unit tests & code inspection in `strategy/live_trader.py` |
| TASK-2 | `python -m pytest tests/test_gil_contention_instrumentation.py -q` |
| TASK-3 | `python -m scripts.measure_gil_contention --help` & smoke run |
| TASK-4 | Full execution with output metrics & p50/p95/max comparison |

## Post-build gates (Station IV)
- `python -m pytest tests/test_gil_contention_instrumentation.py tests/test_engine_parity.py -q` passes with 0 failures (<2s).
- Measurement produces clear empirical data answering the GIL contention question.
