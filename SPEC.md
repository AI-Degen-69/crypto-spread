# SPEC — Issue #93: RESET P&L leaves stale order rows and live venue-side orders untouched

## Goal
`reset_pnl()` (`strategy/live_trader.py:2275`) clears PnL/trades/timeline but never
touches order state, so Open Orders still shows stale rows (CANCELLED,
ADVANCE_PRE_QUOTE, FILLED, stop) and in `live` mode real orders stay resting on
the CLOB. Implement cancel-and-clear with a live-running safety refusal so the
button visibly empties everything the operator expects.

## Background (current behavior)
- `reset_pnl()` clears per-market PnL/fill flags/drift trackers + engine-wide
  `trades`, `timeline`, `open_positions`, `historical_realized_pnl`, deletes
  `TRADES_FILE`/`META_FILE`. Does NOT clear any order handle.
- `get_open_orders_list()` (`live_trader.py:1254-1437`) rebuilds rows from:
  `cancelled_orders` (:445 → :1428-1437 passthrough), `order_id_up/down` +
  `order_status_up/down` (:373-378 → :1363-1394 ENGINE_ACTIVE),
  `next_order_id_up/down` + `next_quoted` (:396-400 → :1412-1443 ENGINE_ADVANCE),
  `stop_order_id/status/price/side` (:403-406 → :1396-1411 ENGINE_STOP),
  exit handles (:410-413, kept alive but not rendered).
- 5s orders cache `_orders_cache_ts` (`:540`, `:1647-1650`) not invalidated, so a
  fix would still serve stale rows for up to 5s. `seed_demo_data()` already resets
  it (`:2325`); `reset_pnl()` does not.
- State inconsistency: `filled_up=False` is set while `order_status_up=="FILLED"`
  survives → engine flags and dashboard status disagree.
- Live risk: ids left in `order_id_up/down`, `next_order_id_*`, `stop_order_id`
  still rest on the CLOB. Neither `cancel_live_order()` (:738) nor
  `cancel_all_orders()` (:831) is called. Reference: `stop()` (:1940-1957) cancels
  venue-side in live mode; `_handle_window_rollover()` (:3139-3167) cancels
  unfilled legs + stop before clearing handles.

## New behavior (recommendation (a) + (b) as live safety net)
1. **Stopped / paper → cancel-and-clear (a).** Clear on every market:
   `cancelled_orders`, `order_id_up/down`, `order_status_up/down` (→ `"NONE"`),
   `order_time_up/down` (→ `"-"`), `next_order_id_up/down`, `next_order_time_up/down`,
   `next_quoted` (→ False), `next_*` descriptors optional, `stop_order_id/status/price/side/time`
   (status → `"NONE"`), `order_id_exit_up/down`, `order_status_exit_up/down`
   (→ `"NONE"`), `entry_cancelled_timeout` (→ False). Set
   `self._orders_cache_ts = 0.0` so next `get_state()` rebuilds.
2. **Live + running + outstanding → refuse (b).** If `mode == "live"` and
   `is_running` is True and any order handle outstanding, do NOT clear anything;
   return refusal dict with operator message ("Stop first"). Venue-side cancel is
   never fired behind the operator's back on this path.
3. **Live + stopped → cancel venue-side first.** Call the CLOB cancel path
   (reuse `cancel_all_orders()` remote leg WITHOUT its `is_running` side effects,
   or per-order `cancel_live_order()`) before clearing locals, so no id is
   dropped without a cancel attempt. Paper mode → no CLOB interaction.
4. **Endpoint surfaces refusal.** `POST /api/live/control` `reset_pnl` branch
   (`server/osc_dash.py:845-846`) returns refusal as a distinguishable response
   (non-200 + message) instead of a normal state payload.

## Acceptance criteria (mirrors issue #93)
- [ ] Stopped engine: after `reset_pnl()`, `get_open_orders_list()` returns no rows
  for previously-ordered markets (no CANCELLED / ADVANCE_PRE_QUOTE / FILLED / stop).
- [ ] All listed fields in §1 cleared on every market; `_orders_cache_ts == 0.0`.
- [ ] No market left with `filled_up/down == False` while `order_status_up/down == "FILLED"`.
- [ ] Live + running + outstanding → refusal, endpoint response carries Stop-first
  message, no local state cleared on that path.
- [ ] Live + stopped → venue cancel attempted (mocked CLOB asserts cancel call)
  before local handles cleared.
- [ ] Paper → clears everything, zero CLOB interaction.
- [ ] New unit test populates entry + advance + stop + exit + cancelled rows, calls
  `reset_pnl()`, asserts orders list empty.
- [ ] Existing `reset_pnl` tests still pass (or deliberately updated).
- [ ] `python -m pytest -q` fully green.

## Out of scope
- Adverse-drift entry gate (#92).
- `Filled` column rendering `0` (#90 — already landed, do not touch).
- Moving FILLED rows to Positions tab (#91 — lands separately).
- Changing `stop()` / `cancel_all_orders()` semantics; they are the reference,
  not the target.

## Edge cases
- Empty engine (no orders): reset behaves as today, still invalidates cache.
- `cancelled_orders` already long: full clear, not truncate.
- Live stopped but CLOB client missing / cancel raises: surface error, do not
  silently drop ids; locals cleared only after cancel attempt per §3.
- Cache: refusal path must NOT invalidate `_orders_cache_ts` (nothing changed).
- `entry_cancelled_timeout` latch cleared so paper resting rows reappear normally.
