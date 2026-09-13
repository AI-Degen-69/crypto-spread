# tasks/plan.md — Issue #167: Poll round takes ~2.7s against a 1s cadence

**Issue:** https://github.com/AI-Degen-69/crypto-spread/issues/167
**Size tier:** Standard (2 files of production code + 2 test files; one architectural
decision — introducing bounded concurrency inside the tick loop).
**Task type:** Performance (primary) + Code/Backend. No UI, no schema change.
**Stack:** Python 3, `requests`, `websockets`, `pytest`. Runner: `python -m pytest -q`.
**Spec:** `SPEC.md` · **Gates:** `CONSTRAINTS.md`

**Skill routing for Station III:** `performance-optimization` (primary),
`test-driven-development` (every task is red-green), `api-and-interface-design`
(T3 introduces a new internal boundary), `code-simplification` (each task closes
with a hygiene pass). Verification mode for every task is the automated test
runner; T5 additionally requires a live CLI observation.

---

## T1 — Cache the gamma market resolution per window `[Performance/Backend]` — [x] DONE

**Files:** `scripts/collect_ticks.py`, `tests/test_collect_ticks_smoke.py`

**What is built:** a process-local cache in front of `fetch_live_for_series`.
`fetch_live_for_series` itself stays a pure fetcher; a new `resolve_series_market`
wraps it with the cache so the raw fetcher stays trivially testable.

- Cache entry: the resolved market dict plus the time it was resolved.
- Return the cached entry while `now < entry["end_ts"]` **and** the entry is younger
  than `GAMMA_CACHE_MAX_AGE`.
- Re-resolve otherwise. Never store a `None` result, so a gamma error retries next tick.
- Expose a reset hook so tests (and a restart) start from an empty cache.

**Verification (automated tests, written first):**
- second call inside the window issues no second HTTP fetch;
- a market whose `end_ts` has passed is re-resolved;
- an entry older than `GAMMA_CACHE_MAX_AGE` is re-resolved even mid-window;
- a failed lookup is not cached and is retried;
- a replaced market (different `conditionId`) is picked up after the max age.

**Commit:** `perf(collector): cache the gamma market resolution per window (#167)`

---

## T2 — Gate the REST tape on socket authority `[Performance/Backend]` — [x] DONE

**Files:** `scripts/collect_ticks.py`, `tests/test_clob_ws_collector.py`

**What is built:** a predicate deciding, per leg, whether the REST tape call is worth
making. Today the fallback runs for any leg with no socket rows this second, which on
a healthy socket is most legs most seconds.

- Track, per window, the last time the socket delivered a print for each token, and
  the time the bridge was first seen connected.
- Skip `recent_trades` for a leg when the bridge is connected, has been connected
  longer than a warm-up period, and has printed for that window inside a bounded
  recency horizon.
- Always fall back when the bridge is absent, disconnected, still warming up, or has
  been silent past that horizon — a genuinely quiet market must not be mistaken for a
  dead socket, and a dead socket must not be mistaken for a quiet market.
- `WS_REST_DEDUP_TTL` dedup on the REST path is untouched.

**Verification (automated tests, written first):**
- connected + recent print → `recent_trades` not called for that leg;
- connected but silent past the horizon → `recent_trades` called;
- connected but inside the warm-up period → `recent_trades` called;
- `--no-ws` / disconnected → `recent_trades` always called;
- captured tape content is unchanged in every case where REST does run.

**Commit:** `perf(collector): skip the REST tape while the socket is authoritative (#167)`

---

## T3 — Bounded concurrent fan-out across the slate `[Performance/Backend]` — [x] DONE

**Files:** `scripts/collect_ticks.py`, `strategy/markets.py`,
`tests/test_collect_ticks_smoke.py`

**What is built:** `poll_once` is split along a fetch/commit seam.

- `fetch_series_snapshot(series, duration, label, now, ...)` — pure, thread-safe:
  resolve the market via T1, fetch both books, optionally fetch the REST tape per T2.
  Returns a result object; it touches no module-level state and writes no files.
- The commit half runs on the main thread in `SERIES` order: `windows` upkeep, socket
  tape drain and dedup, snap assembly, `stats`, `write_snap`, window close.
- One process-wide `ThreadPoolExecutor` with an explicit bounded `max_workers`,
  mirroring `strategy/live_trader.py:973`. Created once, reused across ticks, shut
  down on exit alongside the WS bridge.
- Replace the two in-worker jitter sleeps with a bounded per-worker start stagger, so
  the anti-burst property from docstring D2/D4 survives fan-out instead of becoming a
  no-op.
- Raise `strategy/markets._SESSION` pool sizes to cover the in-flight request ceiling.

**Verification (automated tests, written first):**
- snapshots for one tick are written in `SERIES` order regardless of completion order;
- a worker raising leaves the other series writing and records the error;
- `max_workers` is bounded and the executor is not recreated per tick;
- a single-series slate behaves identically to the sequential path;
- `windows` and `stats` end up identical to the sequential implementation for the same
  fixture input.

**Commit:** `perf(collector): fan the series slate out over a bounded thread pool (#167)`

---

## T4 — Tell the truth about the cadence `[Performance/Docs]` — [x] DONE

**Files:** `scripts/collect_ticks.py`, `tests/test_collect_ticks_smoke.py`

**What is built:** the measurement is turned into published fact.

- Re-measure the round with T1-T3 in place; set `TICK_BUDGET_MS` from that number with
  headroom. If the measurement does not support a sub-budget round, the docstring and
  the constant state the real achievable cadence instead — the budget is never raised
  merely to silence the warning.
- Fix the module docstring: the "Same 1-second poll cadence" claim and the "0-80ms
  per-request jitter" claim (the constant is `JITTER_SEC = 0.010`).
- Add cadence telemetry to `manifest.json`: last round ms, peak round ms, and the
  effective sampling interval (round + `POLL_INTERVAL`), so a consumer of
  `run/ticks/*.jsonl` can see the real granularity.
- Delete the dead `deadline` local in `poll_once`.

**Verification (automated tests, written first):**
- a fast round produces no `slow_tick` entry;
- a forced slow round still produces `slow_tick`;
- manifest carries the cadence fields with the right types and survives
  `update_manifest`'s internal-key stripping.

**Commit:** `perf(collector): publish the real tick cadence and retune the budget (#167)`

---

## T5 — Live verification and schema proof `[Performance/Verify]` — [x] DONE

### Measured result

`python -m scripts.collect_ticks --once --out run/ticks-perf`:

```
once done - closed=0 errs=0 - round=5773ms (cold start) - tape_empty_rate=10.0%
```

`errs=0`. The cold opening round is 5.8s and is reported, not budgeted.

`python -m scripts.verify_tick_data run/ticks-perf` - exit 0:

```
Files Checked: 1   Total Lines: 10   Valid Ticks: 10
Corrupt Lines: 0   Crossed Books: 0  Collector Errors: 0
```

Warm rounds, socket connected, `tape_rest_skipped=30`, median of 5:

| Series | Before (avg) | After (median) | Per series before | Per series after |
|---|---|---|---|---|
| 1 | 267 ms | 198 ms | 267 ms | 198 ms |
| 2 | 527 ms | 241 ms | 264 ms | 120 ms |
| 5 | 1344 ms | 302 ms | 269 ms | 60 ms |
| 10 | 2691 ms | 405 ms | 269 ms | 41 ms |

Per-series cost falls from flat ~269 ms to 41 ms at full slate: the curve is no
longer linear, which was the point. A full slate now costs 2.0x a single series
instead of 10x. Effective sampling interval: ~3.8 s to ~1.4 s.

Full suite: `python -m pytest -q` - 617 passed.

---

### Original task

**Files:** none expected; documentation touch-ups only if a claim turns out stale.

**What is done:**
- `python -m scripts.collect_ticks --once --out run/ticks-perf` — record the printed
  `errs=` count and the round time.
- `python -m scripts.verify_tick_data.py` equivalent invocation against the fresh
  output — the tick schema must pass unchanged.
- Re-run the scaling benchmark against 1 / 2 / 5 / 10 series and record the new table,
  confirming the curve is no longer linear at ~269 ms per series.
- Full suite: `python -m pytest -q`.

**Verification:** observed CLI output, pasted with the numbers. "No errors" is not
evidence; the round time and the `errs` count are.

**Commit:** `docs(collector): record the measured post-fan-out cadence (#167)` — only
if a file actually changes.

---

## Improvement proposed and deferred

**Serve the order book from the #165 socket instead of REST `full_book`.**
The bridge already maintains book state (`get_book_for_token`). Using it would delete
the two `full_book` calls — ~184 ms of the ~269 ms that remains per series — and raise
book freshness from once-per-round to every venue update.

**Not adopted in this plan.** The tick files are the replay dataset, so their books
have to be right; switching provenance needs proof that delta application,
`tick_size_change` handling and post-reconnect resync reconstruct the REST book
exactly. That is a cross-check campaign, not a task inside a latency fix. Recorded in
`SPEC.md` §7 so the option is not lost. If the operator wants it folded in, it becomes
its own issue after #167 lands.

---

## Interview

Requirements were fully clear from the issue — `interview-me` was skipped. The issue
carries measurements, three ranked candidate fixes, explicit acceptance criteria and a
verification command; the only open judgement call was the deferred proposal above,
which is surfaced rather than silently adopted.
