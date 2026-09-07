# Plan — Issue #93: RESET P&L leaves stale order rows and live venue-side orders untouched

Files: `strategy/live_trader.py` (`reset_pnl` :2275-2316, `MarketLiveState` :373-413,
`get_open_orders_list` :1254-1443, `cancel_live_order` :738, `cancel_all_orders` :831-916,
`stop` :1940-1957, `get_state` cache :1647-1650), `server/osc_dash.py`
(`POST /api/live/control` :828-860), `tests/test_live_trader.py` (extend),
`tests/test_osc_dash_integration.py` (extend).

Contract (locked before logic — `api-and-interface-design`):
- `_has_outstanding_orders(self) -> bool`: True if any market holds `order_id_up/down`,
  `next_order_id_up/down`, `stop_order_id` (status not in NONE/CANCELLED/FILLED),
  `order_id_exit_up/down`, or non-empty `cancelled_orders`.
- `_clear_market_order_state(m) -> None`: clears every handle in SPEC §1; statuses →
  `"NONE"`, times → `"-"`, `next_quoted` → False, `entry_cancelled_timeout` → False,
  `stop_price` → None, `stop_side` → None, `cancelled_orders` → `[]`. Caller holds lock.
- `reset_pnl(self) -> dict`: success → `{"ok": True, "refused": False,
  "venue_cancelled": bool, "markets_cleared": int}`; live-running-hot →
  `{"ok": False, "refused": True, "message": "Stop the engine before RESET P&L …"}`.
  Refusal clears nothing and leaves `_orders_cache_ts` untouched.
- `POST /api/live/control` `reset_pnl`: success → `200 + get_state()`; refusal →
  `409 {"ok": False, "error": <message>}` (message usable by `resetCockpitPnL`).

## T1 — Failing test first (TDD red, paper/stopped path)
- **Files:** `tests/test_live_trader.py` (new test beside `:1017`)
- **Do:** Populate `btc-up-or-down-5m` with entry (`order_id_up/down` + RESTING),
  advance (`next_order_id_up/down` + `next_quoted=True`), stop (`stop_order_id` +
  RESTING), exit (`order_id_exit_up/down`), `cancelled_orders=[{…CANCELLED…}]`,
  `_orders_cache_ts=now`. Call `reset_pnl()`, assert `get_open_orders_list()==[]`,
  all handles cleared, `_orders_cache_ts==0.0`. Confirm RED.
- **Verify:** `python -m pytest tests/test_live_trader.py::<new_test> -q` (must FAIL)

## T2 — `reset_pnl` cancel-and-clear core (stopped/paper)
- **Files:** `strategy/live_trader.py` (`reset_pnl` :2275-2316 + new helpers)
- **Do:** Add `_has_outstanding_orders()` + `_clear_market_order_state()` helpers;
  in `reset_pnl()`, after existing PnL clears, clear every market's order state per
  contract, set statuses to `"NONE"` (fixes FILLED-flag contradiction), clear
  `entry_cancelled_timeout`, set `self._orders_cache_ts = 0.0`. All under
  `self._engine_lock`. Return success dict. Paper path: no CLOB calls.
- **Verify:** `python -m pytest tests/test_live_trader.py -q` (new test green)

## T3 — Live-mode safety: refuse-while-hot + cancel-when-stopped
- **Files:** `strategy/live_trader.py` (`reset_pnl`)
- **Do:** At top of `reset_pnl()`: if `mode=="live"` and `is_running` and
  `_has_outstanding_orders()` → return refusal dict, clear nothing. Else if
  `mode=="live"` (stopped) and outstanding → venue cancel burst first (reuse
  `cancel_all_orders()` remote leg or per-order `cancel_live_order()`; do NOT reuse
  its `is_running/quoting_halted` side effects — reset must not stop the engine),
  record `venue_cancelled=True`; cancel errors surface in return dict.
- **Verify:** `python -m pytest tests/test_live_trader.py -q` (add refusal + stopped-live mock-CLOB tests green, paper test asserts no CLOB interaction)

## T4 — Endpoint surfaces refusal message
- **Files:** `server/osc_dash.py` (`:845-846` branch)
- **Do:** Capture `reset_pnl()` return dict; on `refused` → `409 {"ok": False,
  "error": message}` (+ current state for dashboard continuity); on success →
  existing `get_state()` path. Keep `resetCockpitPnL()` (:3634) compatible (success
  shape unchanged).
- **Verify:** `python -m pytest tests/test_osc_dash_integration.py -q` (extend reset branch: stopped → 200 empty orders; live-running-hot → 409 with Stop-first message)

## T5 — Keep existing tests honest + full regression
- **Files:** `tests/test_live_trader.py:120-130,1017-1028`, `tests/test_osc_dash_integration.py:650-675`
- **Do:** Update existing `reset_pnl` tests only where behaviour deliberately changed
  (return dict, cleared handles); assert no other behaviour drift. Run whole suite,
  review diff (no PnL-math / rollover / stop changes, lock discipline intact).
- **Verify:** `python -m pytest -q` (all green, 293 + new)

## Ship
- Branch `fix/reset-pnl-clear-orders`, conventional commit
  `fix(live-trader): reset_pnl cancels and clears outstanding orders with live-running refusal`,
  PR with `@coderabbitai` + `@coderabbitai summary`. Independent of #90/#91/#92.
