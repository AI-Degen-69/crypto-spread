# Issue #221 Findings: Empirical Measurement of GIL Contention on Live Ticks

**Date:** 2026-09-18  
**Target:** Live Trader Engine (`strategy/live_trader.py`) vs Backtest Replay (`server/osc_dash.py:api_backtest`)  
**Scope:** Empirical measurement only (no premature architectural changes).

---

## 1. Executive Summary

Running a full-file backtest sweep in a FastAPI worker thread (`ThreadPoolExecutor`) introduces:
- **Negligible median jitter:** **+5.75 ms** delta at p50 (1012.23 ms idle $\to$ 1017.98 ms under load).
- **Substantial tail latency:** **+583.94 ms** delta at p95 (1027.66 ms idle $\to$ 1611.60 ms under load), with maximum interval reaching **3286.87 ms** (+2257.81 ms difference from the idle maximum).

### Verdict
**NOTABLE.** While typical ticks experience negligible latency, tight CPU loops in the backtester starve the Python GIL, causing occasional burst intervals of 1.6s to 3.3s. For 5-minute binary markets, a 3-second tick interval at window open or during an adverse drift stop-loss exit may affect execution timing.

---

## 2. Methodology & Instrumentation

- **Monotonic High-Resolution Timing:** Added `time.perf_counter()` deltas per market inside `_update_market_strategy` (`strategy/live_trader.py`).
- **Bounded In-Memory Collection:** Each market records up to 1,000 recent intervals in a `collections.deque(maxlen=1000)`.
- **Synthetic Polling (Network I/O Removed):** Market polling was synthetic (zero network I/O noise) to isolate backtest load from network jitter; measured intervals reflect event-loop scheduling, executor thread, and CPU/GIL contention.
- **Dataset Replay:** Full replay of `run/ticks/ticks_2026-09-13.jsonl` (467.07 MB, 164,260 snaps, 550 windows, duration: 34.07 seconds).

---

## 3. Results Breakdown

| Metric | Idle Baseline (ms) | Under Backtest Load (ms) | Contention Delta (ms) | % Change |
| :--- | :--- | :--- | :--- | :--- |
| **Sample Count** | 95 | 145 | - | - |
| **Min Interval** | 1004.18 | 1004.22 | +0.04 ms | +0.0% |
| **p50 (Median)** | 1012.23 | 1017.98 | **+5.75 ms** | **+0.5%** |
| **p95 (Tail)** | 1027.66 | 1611.60 | **+583.94 ms** | **+56.8%** |
| **p99** | 1029.06 | 3286.86 | **+2257.80 ms** | **+219.4%** |
| **Max Interval** | 1029.06 | 3286.87 | **+2257.81 ms** | **+219.4%** |
| **Mean** | 1013.21 | 1147.50 | +134.29 ms | +13.3% |

---

## 4. Architectural Recommendations (Next Steps)

As outlined in Issue #221, now that the number is known ($p95 = 1.61\text{s}$, $\max = 3.29\text{s}$), the solution space is clear:

1. **Process Isolation (Recommended):** Run heavy backtest sweeps in a separate process (`multiprocessing.Process` / `ProcessPoolExecutor` or standalone worker) rather than a threadpool worker. In Python, separate processes have independent GILs and zero contention on the asyncio event loop.
2. **Chunked Yielding (`time.sleep(0)`):** If keeping threadpool, introduce explicit yields inside `_simulate_window` or `iter_ticks` every $N$ snaps (e.g. every 1,000 snaps) to allow the GIL to switch back to the event loop.
3. **Concurrency Capping:** Limit concurrent backtest requests via an async semaphore on `/api/backtest`.

---

## 5. Artifacts Produced
- `strategy/live_trader.py`: Live tick interval tracking via `get_tick_timing_stats()` and `reset_tick_timing_stats()`.
- `tests/test_gil_contention_instrumentation.py`: Targeted unit tests for timing collection and stats.
- `scripts/measure_gil_contention.py`: Standalone CLI benchmark harness.
- `docs/measurements/issue-221-gil-contention.json`: Raw benchmark payload.
