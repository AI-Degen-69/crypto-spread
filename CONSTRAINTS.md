# CONSTRAINTS.md — Issue #167: Collector poll round vs. the 1s cadence

## Quality Gates & Hard Thresholds

### 1. Test Suite Integrity
- **Zero Regressions**: 100% pass rate on the full suite (`python -m pytest -q`,
  currently 592 tests passing). Targeted gate files first:
  `tests/test_collect_ticks_smoke.py`, `tests/test_clob_ws_collector.py`,
  `tests/test_verify_tick_data.py`.
- **New Behavior Tests** are required for each of:
  - Gamma cache hit inside a window, re-resolve after `end_ts`, re-resolve after the
    bounded max age, and no caching of a failed lookup.
  - REST tape skipped while the socket is authoritative for a leg; REST tape still
    issued when the socket is disconnected, still warming up, or silent past the
    recency horizon.
  - Fan-out: snapshots written in `SERIES` order, one series raising leaves the rest
    writing, worker count bounded, executor not recreated per tick.
  - `slow_tick` absent on a fast round and present on a forced slow round.
  - Manifest cadence telemetry fields present and correctly typed.
- **No Network in Tests**: every new test runs offline against fakes or monkeypatched
  fetchers, like `tests/test_clob_ws_collector.py` does today.
- **Anti-Cheat**: Strictly forbid disabling tests, deleting assertions, marking tests
  `skip`/`xfail` to get green, or suppressing linter checks.

### 2. Performance Thresholds
- **Round time**: a full 10-series round must complete inside the final
  `TICK_BUDGET_MS` on a normal connection. The measurement must be shown, not claimed.
- **Budget honesty**: `TICK_BUDGET_MS` may only be changed with a measurement that
  justifies the new value. Raising it purely to stop `slow_tick` firing is forbidden.
- **Bounded concurrency**: an explicit `max_workers` ceiling, one process-wide
  executor, no thread-per-series creation inside the tick loop.
- **Connection pool**: `strategy/markets._SESSION` pool size must be at least the
  maximum number of in-flight requests the fan-out can produce, so concurrency does
  not degrade into repeated TLS handshakes.
- **No increase in venue 429s**: the anti-burst property the per-request jitter
  provided (module docstring D2/D4) must be preserved in some form under fan-out.

### 3. Data & Compatibility Invariants
- **Tick Schema Invariant**: records appended to `run/ticks/ticks_YYYY-MM-DD.jsonl`
  must still pass every check in `scripts/verify_tick_data.py`. No field added,
  removed or retyped in the snap record.
- **Write ordering**: within one tick, snapshots are appended in `SERIES` order, and
  every append happens on a single thread. No concurrent writes to the tick file.
- **State ownership**: the module-level `windows` dict and the `stats` dict are
  mutated only on the main thread. Worker threads return values; they do not write
  shared state.
- **Per-series failure isolation** (D2/D4) is preserved: one series failing writes an
  `err` on its own snap or an entry in `errs`, and never aborts the round.
- **Graceful degradation**: with `--no-ws`, or with the socket down, the collector
  must behave exactly as the REST-only path does today.

### 4. Scope & Dependency Boundaries
- **No new external dependencies.** `concurrent.futures` is standard library and is
  already used in `strategy/live_trader.py:973`; nothing else may be added.
- **Do not touch** `strategy/streaming.py`, `backtest/engine.py`, the dashboard, or
  the tick schema.
- **Do not implement socket-served books** in this issue (SPEC §7). It is deferred by
  decision, not forgotten.
- **Windows UTF-8 console safety** must be preserved: no output path that can crash
  under `cp1252`.
