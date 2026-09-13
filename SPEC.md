# SPEC.md — Issue #167: Poll round takes ~2.7s against a 1s cadence

## 1. Goal
Bring one full 10-series round of `scripts/collect_ticks.py` inside `TICK_BUDGET_MS`
on a normal connection, so `slow_tick` stops firing on every tick and becomes a real
degradation signal. Where a round genuinely cannot be made fast enough, state the
achievable cadence honestly in the docstring and in `manifest.json` instead of
raising the budget to silence the warning.

## 2. Background & Evidence
- The collector docstring claims a "Same 1-second poll cadence" and `POLL_INTERVAL`
  is `1.0`, but a measured round takes ~2691 ms, so the real sampling interval is
  round + sleep = ~3.8 s.
- `poll_once` compares the round against `TICK_BUDGET_MS = 2000.0` and appends
  `slow_tick:<ms>` to `errs`. Because the condition is true every tick, an operator
  sees a permanent `errs=1` on `--once`, and a genuinely slow round is
  indistinguishable from the baseline.
- Measured scaling is exactly linear at ~269 ms per series (267 / 527 / 1344 /
  2691 ms for 1 / 2 / 5 / 10 series), which proves the slate is polled strictly
  sequentially with no concurrency.
- Per-series cost is four sequential HTTP round-trips — gamma `fetch_live_for_series`
  ~39 ms, `full_book(up)` ~90 ms, `full_book(down)` ~94 ms, `recent_trades` ~97 ms —
  plus two jitter sleeps of 0-10 ms. That is 40 HTTP round-trips per tick.
- This predates issue #165. The WebSocket collector neither caused the problem nor
  addresses it, but it does make the REST tape call redundant most seconds, which
  this issue can now exploit.

## 3. In Scope

### 3.1 Cache the gamma market resolution per window
File: `scripts/collect_ticks.py`

- `fetch_live_for_series` re-resolves a market whose `conditionId`, `slug`, tokens,
  `start_ts` and `end_ts` cannot change until the window rolls.
- Introduce a process-local cache keyed by series slug, holding the resolved market
  plus the wall-clock time it was resolved.
- Invalidate on: (a) `now >= end_ts` — the window rolled and a new market must be
  resolved; (b) a bounded max age, so a mid-window market replacement or a cancelled
  market is still picked up rather than pinned for the whole window.
- Never cache a failure. A gamma error must re-resolve on the next tick.
- Expected saving: 10 HTTP calls and ~390 ms per tick.

### 3.2 Gate the REST tape on socket authority
File: `scripts/collect_ticks.py`

- After #165, `recent_trades` is called for any leg that produced no socket rows this
  second. On a healthy socket that is most legs most seconds, because most seconds
  genuinely have no trades — so the call is pure latency for an empty result.
- Only fall back to REST for a leg when the socket is not demonstrably authoritative
  for it: the bridge is disconnected, or it has been connected for less than a
  warm-up period, or it has delivered no print for this window within a bounded
  recency horizon.
- The existing `WS_REST_DEDUP_TTL` cross-source dedup continues to govern correctness
  when the REST path does run; this change is only about when the call is worth
  making at all.
- Expected saving: up to ~970 ms per tick on a healthy socket.

### 3.3 Bounded concurrent fan-out across the slate
Files: `scripts/collect_ticks.py`, `strategy/markets.py`

- The 10 series are fully independent and already isolated per-series for failures.
- Split `poll_once` into a pure per-series fetch step and a main-thread commit step:
  - **Fetch (worker thread, no shared mutable state):** resolve the market via the
    cache, fetch both books, optionally fetch the REST tape. Returns a plain result
    object carrying the books, the raw tape map and any per-series error string.
  - **Commit (main thread, sequential in `SERIES` order):** create or update the
    `windows` entry, drain the socket tape, dedup, build the snap, update `stats`,
    append the line to disk, close finished windows.
- All mutation of `windows` and `stats`, and every file append, stays on the main
  thread, so snapshot ordering in `ticks_<day>.jsonl` is unchanged and no lock is
  needed around the tick file.
- Concurrency is bounded by an explicit `max_workers`, and the executor is created
  once for the process rather than per tick.
- `strategy/markets._SESSION` currently pools 8 connections. The pool must be sized
  to at least the number of in-flight requests the fan-out can produce, or urllib3
  discards and re-handshakes connections and the change loses its own benefit.
- Anti-burst behaviour is preserved. The per-request jitter exists to stop
  synchronized 30-rps bursts at window boundaries (module docstring D2/D4). Under
  fan-out, jitter between two calls inside one worker no longer de-synchronizes
  anything, so it is replaced by a bounded per-worker start stagger that keeps the
  same anti-burst property across the slate.
- Expected result: ~2.7 s collapses to roughly one series' latency.

### 3.4 Tell the truth about the cadence
File: `scripts/collect_ticks.py`

- Re-measure the round after 3.1-3.3 and set `TICK_BUDGET_MS` from the measurement
  with headroom, not from a wish.
- Correct the module docstring. The stated "1-second poll cadence" and the stated
  "0-80ms per-request jitter" (the constant is `JITTER_SEC = 0.010`, i.e. 0-10 ms)
  must both match the code.
- Publish the observed cadence in `manifest.json` so downstream consumers of
  `run/ticks/*.jsonl` stop assuming 1 s granularity: last and peak round duration,
  and the effective sampling interval.
- Remove the dead `deadline` local in `poll_once` (assigned, never read).

## 4. Out of Scope
- Serving the order book from the #165 socket instead of REST `full_book`. This is
  the largest remaining cost (~184 ms of the ~269 ms per series) but it changes the
  provenance of the replay dataset and needs its own correctness campaign. See §7.
- Any change to the tick record schema, to `backtest/engine.py`, or to the dashboard.
- Changing `POLL_INTERVAL` semantics beyond documenting the real interval.
- Changing the WebSocket client itself (`strategy/streaming.py`).

## 5. Acceptance Criteria
1. A full 10-series round completes inside `TICK_BUDGET_MS` on a normal connection,
   measured and shown — or `TICK_BUDGET_MS` and the docstring state the real
   achievable cadence, justified by the measurement. The budget is never simply
   raised to silence the warning.
2. `slow_tick` is absent from `errs` on a healthy `--once` run and still present when
   a round genuinely degrades, proved by a test that forces a slow round.
3. Bounded concurrency only. No unbounded thread creation, no per-tick executor, and
   the anti-burst stagger the jitter existed for is preserved.
4. Zero change to the tick schema: `scripts/verify_tick_data.py` passes on freshly
   collected output.
5. The real sampling interval is visible to an operator in `manifest.json` and stated
   correctly in the module docstring.
6. Snapshot write order within a tick is unchanged (`SERIES` order), and one series
   failing still leaves the other nine writing normally.

## 6. Edge Cases
- Gamma returns a different `conditionId` mid-window (market replaced): the bounded
  cache age must let the collector pick it up.
- The window rolls between the cache read and the book fetch: the snap is stamped
  with the resolved `end_ts`, and the window closes on the next tick as it does today.
- The socket connects and then goes silent because the market genuinely has no
  trades: this must not be mistaken for a dead socket and must not permanently
  suppress REST.
- The socket drops mid-round: legs already fetched keep their result, and the next
  tick sees `ws_connected=False` so REST resumes for every leg.
- A worker raises: that series records an `err` on its snap or an entry in `errs`,
  and the remaining series still write.
- Fewer series than workers, or a single-series run: the fan-out must behave
  identically to the sequential path.

## 7. Deferred Proposal — socket-served books
`CLOBStreamCollectorBridge.get_book_for_token()` already exposes the book state the
#165 client maintains from `book` snapshots and `price_change` deltas. Serving
snapshots from it would remove the two `full_book` calls — about 68% of the remaining
per-series cost — and would raise book freshness from once-per-round to every venue
update. It is deferred, not dropped, because the replay dataset's integrity depends
on the book being right: it needs proof that delta application, `tick_size_change`
handling and post-reconnect resynchronization reconstruct the REST book exactly,
which is a cross-check campaign of its own.
