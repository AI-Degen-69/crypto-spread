# Plan — issue #173: socket trade prints into fill telemetry

Scope: **Standard**. Files: `strategy/live_trader.py`, `tests/test_live_trader.py`.

## Findings that shape the plan

The issue's headline premise is stale. `on_trade` is already plumbed through
`UnifiedStreamBridge` (`strategy/streaming.py:1135`) into the engine
(`strategy/live_trader.py:997`), and socket prints already drive paper fill
detection via `on_ws_trade` → `_try_ws_tape_fill`. What did **not** land is the
telemetry half:

- `_fill_telemetry_worker` derives `printed_size` only from
  `_fetch_price_prints` — the REST data-api tape measured at ~1.4% capture in
  issue #165 — so `printed_size_at_price_since_rest` and `fill_ratio` in
  `run/live_fill_telemetry.jsonl` are biased low by construction.
- The socket prints that would answer that question are collected and then
  discarded: the tick loop drains `pending_ws_trades_up/down` into locals and
  throws them away.
- The record carries no source field, so the #138 analysis cannot separate a
  socket-confirmed fill from a REST-confirmed one.

### Dedup decision (the trade-off the issue asks to settle explicitly)

`printed_size` takes **exactly one source per record**, chosen by per-leg socket
authority — the two are never summed. Summing is what would double-count a print
seen on both paths, and merging a near-complete socket ledger with a ~1.4% REST
sample inflates the numerator of `fill_ratio` by an unknown factor. The chosen
source is recorded as `tape_source` so #138 can stratify instead of guessing.

Fill *detection* needs no change: `_try_ws_tape_fill` and the REST/`get_order`
path both gate on `filled_up`/`filled_down`, so a print seen twice fills once
and a socket outage leaves the REST path exactly as it is today.

## Tasks

- [ ] 1. `MarketLiveState` gains a bounded per-leg socket print ledger, appended
      in `on_ws_trade` and no longer thrown away by the tick loop.
- [ ] 2. `_sum_ws_prints_at_price()` — pure numerator over the ledger, matching
      the `FILL_PRICE_TICK_TOL` tolerance and `since_ts` semantics of
      `_sum_prints_at_price`.
- [ ] 3. `_ws_tape_authoritative(m, leg)` — the three conditions of
      `ws_leg_authoritative` (`scripts/collect_ticks.py:403`): socket connected,
      token subscribed at least `WS_TAPE_WARMUP_SEC`, and a print for that token
      within `WS_TAPE_AUTHORITY_HORIZON_SEC`.
- [ ] 4. `_record_fill_telemetry` snapshots the ledger under the engine lock;
      `_fill_telemetry_worker` picks the source; `_build_fill_record` records
      `tape_source`.
- [ ] 5. Tests covering: socket-sourced `printed_size`, REST fallback when the
      socket is not authoritative, no double count across sources, ledger bound,
      and the new record field.
