# Plan — Issue #87: Pre-placed Resting Stop-Loss Orders

Files: `strategy/live_trader.py`, `server/osc_dash.py` (orders table renders the
engine order list already — verify only), `tests/test_live_trader.py`
(+ new `tests/test_stop_orders.py`).

## T1 — State fields + place_stop helper (TDD)
- **Files:** `strategy/live_trader.py`, `tests/test_stop_orders.py`
- **Do:** Add `MarketLiveState` fields `stop_order_id: Optional[str]`,
  `stop_order_status: str = "NONE"`, `stop_price: Optional[float] = None`,
  `stop_side: Optional[str] = None`, `stop_order_time: str = "-"`.
  Add `place_stop_order(mstate, side)` helper: idempotent guard
  (`stop_order_id` set → return), computes
  `stop_price = clamp(fill_price - exit_thresh, 0.01, 0.99)` (round 2), submits
  SELL via `place_live_quote` in live mode / assigns `paper_stop_{slug}` in paper
  mode, records `stop_order_status = "RESTING"` + `stop_order_time`.
- **Tests (write first, red):** helper creates a resting stop with correct price and
  status; second call does not duplicate.
- **Verify:** `python -m pytest tests/test_stop_orders.py -q`

## T2 — Stop placement on single-leg fill
- **Files:** `strategy/live_trader.py`
- **Do:** In `_update_market_strategy`, immediately after each single-leg fill
  transition (`filled_up` XOR `filled_down`, live + paper paths), call
  `place_stop_order`. If CLOB placement fails, log WARNING and leave the existing
  reactive `_execute_stop_exit` path as fallback.
- **Tests (red first):** paper-mode single UP fill → `stop_order_id` set, status
  RESTING, `stop_side == "UP"`; same for DOWN.
- **Verify:** `python -m pytest tests/test_stop_orders.py tests/test_live_trader.py -q`

## T3 — Pair-completion cancellation (OCO Case A)
- **Files:** `strategy/live_trader.py`
- **Do:** In the pair-completion block (before `PAIR_MERGED` accounting), cancel the
  resting stop: live → `cancel_live_order(stop_order_id)`; paper → just reset.
  Clear `stop_order_id/status/price/side` regardless of cancel result.
- **Tests (red first):** single UP fill places stop → DOWN fills → `cancel_live_order`
  called with the stop id (live, mocked) and stop fields cleared; `PAIR_MERGED` reached.
- **Verify:** `python -m pytest tests/test_stop_orders.py -q`

## T4 — Stop-fill detection → entry cancel + STOP_EXIT (OCO Case B)
- **Files:** `strategy/live_trader.py`
- **Do:** Per-tick detection while single-leg filled and not exited:
  live → poll `client.get_order(stop_id)` (MATCHED/FILLED or size_matched ≥ shares);
  paper → `bid <= stop_price`. On fill: set stop status FILLED, delegate accounting
  to `_execute_stop_exit(side, exit_price=stop_price)` which already cancels the
  opposite entry and records the `STOP_EXIT` trade.
- **Tests (red first):** paper mode: UP filled, stop at fill−0.05, drop bid →
  opposite entry cancelled, trade with action `STOP_EXIT` recorded, status
  `STOP_EXIT`; live mode: mocked `get_order` returns MATCHED → same.
- **Verify:** `python -m pytest tests/test_stop_orders.py tests/test_live_trader.py -q`

## T5 — Window rollover cleanup (OCO Case C)
- **Files:** `strategy/live_trader.py`
- **Do:** In `_handle_window_rollover`, cancel any resting stop (live) alongside the
  entry-order cancels; reset all stop fields in the new-window reset block.
- **Tests (red first):** stop resting → rollover → cancel called (live, mocked),
  stop fields reset to defaults in both modes.
- **Verify:** `python -m pytest tests/test_stop_orders.py tests/test_live_trader.py -q`

## T6 — Open orders + dashboard visibility
- **Files:** `strategy/live_trader.py`, `tests/test_stop_orders.py`
- **Do:** In `get_open_orders_list()`, merge the resting stop (when
  `stop_order_id` and status not in CANCELLED/FILLED/NONE) as
  `{"side": "SELL (UP|DOWN)", "status": stop_order_status, "source": "ENGINE_STOP"}`.
  Dashboard orders table consumes this list already — verify rendering with an
  integration check only if a schema field is missing.
- **Tests (red first):** single-leg fill → `get_open_orders_list()` contains the
  stop with source ENGINE_STOP and side SELL (UP).
- **Verify:** `python -m pytest tests/test_stop_orders.py tests/test_orders_trades_table.py -q`

## T7 — Full regression + self-audit
- **Files:** —
- **Do:** Run the whole suite, fix fallout, review the diff (correctness, edge cases,
  lock discipline), verify no leaked orders on every path.
- **Verify:** `python -m pytest -q` (all 186+ existing + new tests green)

## Ship
- Branch `feat/pre-placed-stop-orders`, conventional commit
  `feat(live-trader): pre-place resting stop-loss orders on single-leg fill`,
  PR titled `@coderabbitai` with `@coderabbitai summary` in the body.
