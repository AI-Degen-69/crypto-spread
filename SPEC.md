# SPEC — Issue #87: Pre-placed Resting Stop-Loss Orders

## Goal
Eliminate reactive taker stop-loss exits. When one leg of a 5m spread fills, the engine
immediately pre-places a resting SELL protection order for that position. The stop rests
in the book (maker priority, zero dispatch latency) and executes or cancels per an
explicit OCO lifecycle.

## Background (current behavior)
- Single-leg fill → engine holds unhedged position and quotes opposite entry.
- Adverse drift `>= exit_thresh (0.05)` → `_execute_stop_exit` fires a reactive
  marketable SELL crossing the spread: taker fees, slippage, 100–500ms dispatch latency.

## New behavior
1. **Stop staging (post single-leg fill)**
   - Immediately after `filled_up` or `filled_down` is set (live AND paper), stage
     stop-loss protection for the filled leg at
     `stop_price = clamp(fill_price - exit_thresh, 0.01, 0.99)` (round 2).
   - Venue capability: Polymarket binary CLOB accepts standard limit orders only
     (no `tr.tpsl` triggers via py_clob_client), and a SELL priced at the stop
     threshold would cross the bid immediately — instantly exiting the freshly
     filled leg. Therefore the stop is maintained as a pre-signed zero-latency
     buffer in memory (status `STAGED`), submitted only when the adverse-drift
     trigger fires via the existing `_execute_stop_exit` monitored exit
     (cancels opposite entry + records `STOP_EXIT`). Paper mode simulates the
     stop filling when the bid touches the stop price.
2. **OCO lifecycle**
   - **Case A — pair completes:** cancel the resting stop BEFORE pair merge
     (`PAIR_MERGED`). Stop must never survive a hedged pair.
   - **Case B — stop fills:** cancel the unhedged opposite entry order, record the
     trade as `STOP_EXIT` (reuse `_execute_stop_exit` accounting path with
     `exit_price = stop fill price`), transition to flat.
   - **Case C — window rollover:** cancel any resting stop alongside entry orders;
     reset stop state fields; no leaked open orders.
3. **Stop fill detection**
   - Live: poll `client.get_order(stop_id)` each tick (status MATCHED/FILLED or
     size_matched >= shares).
   - Paper: simulated — stop counts as filled when `bid <= stop_price`.
   - Detection runs while single-leg filled and not yet exited.
4. **Observability**
   - Resting stop appears in `get_open_orders_list()` with `source: "ENGINE_STOP"`
     and side `SELL (UP)` / `SELL (DOWN)`, so it renders in the dashboard Orders
     table with live status until cancelled/filled.
   - New `MarketLiveState` fields: `stop_order_id`, `stop_order_status`, `stop_price`,
     `stop_side`, `stop_order_time`.

## Acceptance criteria (mirrors issue #87)
- [ ] Single-leg fill → stop protection order staged automatically (live + paper).
- [ ] Opposite leg fills → resting stop cancelled before merge.
- [ ] Stop fills → opposite entry cancelled, `STOP_EXIT` trade recorded, flat.
- [ ] Resting stop visible in `get_open_orders_list()` and dashboard Orders table.
- [ ] Window rollover cancels resting stops; no leaked orders.
- [ ] `python -m pytest -q` fully green (existing 186 tests + new tests).

## Out of scope
- Changing dual-sided maker entry quotes (`resting_up`/`resting_down`).
- Changing default `offset = 0.02` or `exit_thresh = 0.05`.
- Native `tr.tpsl` conditional triggers (not supported on binary CLOB via current client).
- Anything beyond the 5m/15m binary universe.

## Edge cases
- Pair merge is blocked (deferred, retried next tick) if a venue-side stop
  cancellation fails (`CANCEL_FAILED`); the stop handle is retained.
- Buffer cancel failure never silently clears the handle.
- Opposite leg fills and stop trigger in the same tick → pair-completion cancellation
  wins if pair already complete; otherwise stop path proceeds.
- Stop already staged → do not duplicate (idempotent placement guard).
- Rollover while stop staged → cancel stop, settle via existing
  `WINDOW_SETTLE` accounting.
- Opposite leg fills and stop fills in the same tick → pair-completion cancellation
  wins if pair already complete; otherwise stop path proceeds.
- Stop already placed → do not duplicate (idempotent placement guard).
- Rollover while stop pending fill → cancel stop, settle via existing
  `WINDOW_SETTLE` accounting.
