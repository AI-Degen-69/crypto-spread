# Task Plan: Issue #76 — Synchronize Live Market Matrix Stopped State & Retain Cancelled Orders

## Overview
Implement complete synchronization between `LiveTraderEngine` order lifecycle, the Live Market Matrix cards, and the Orders table. Stopped positions/quotes reflect `FLAT` and inactive in the matrix, while cancelled orders remain visible in the orders table with `CANCELED` status and without active cancel buttons until window rollover.

---

### Task 1: Add Cancelled Order Retention to `strategy/live_trader.py`
- **Target File**: `strategy/live_trader.py`
- **Details**:
  - Add `cancelled_orders: List[Dict[str, Any]] = field(default_factory=list)` to `MarketLiveState`.
  - In `_execute_stop_exit`, record the unhedged opposite leg as cancelled in both live and paper modes, append to `mstate.cancelled_orders`, and set status to `CANCELLED`.
  - In `_update_market_strategy` (entry timeout / adverse open / drift skip), record cancelled entry orders into `mstate.cancelled_orders` with status `CANCELLED`.
  - In `cancel_live_order` / `_clear_order_handles`, ensure cancelled orders are preserved in `mstate.cancelled_orders`.
  - In `get_open_orders_list()`, include `cancelled_orders` from all markets in the returned list.
  - In `_handle_window_rollover`, clear `mstate.cancelled_orders` when rolling over to a new window period.
- **Verification**: Run `pytest tests/test_live_trader.py` and `pytest tests/test_entry_timeout.py`.

---

### Task 2: Synchronize Live Market Matrix Cards in `server/osc_dash.py`
- **Target File**: `server/osc_dash.py`
- **Details**:
  - In `renderCockpitUI`:
    - When `m.status === 'STOP_EXIT'` or `m.exit_taken`: display `posStr` as `FLAT (STOPPED OUT)`.
    - When `m.status === 'TIMEOUT_NO_FILL'`: ensure `posStr` displays `FLAT`.
    - When market is not quoting (stopped out, timed out, drift skipped, pair merged, or bot stopped):
      - Suppress active resting bid quotes and display informative inactive state (e.g. `Bids: CANCELLED (STOPPED OUT)`, `Bids: CANCELLED (TIMEOUT_NO_FILL)`, `Bids: MERGED / COMPLETE`, or `Bids: INACTIVE (BOT STOPPED)`).
- **Verification**: Run `pytest tests/test_orders_trades_table.py`.

---

### Task 3: Update Orders Table Rendering & Grouping in `server/osc_dash.py`
- **Target File**: `server/osc_dash.py`
- **Details**:
  - Add `.ot-tag-cancelled` CSS styling matching theme palette.
  - Update `groupOrdersByPair(orders)`:
    - Exclude cancelled legs when evaluating paired status so partially cancelled pairs show `Partial` or `Cancelled` / `Unpaired`.
    - If all legs in a group are cancelled, set `grp.status = 'Cancelled'`.
  - In the Orders table row loop:
    - Render cancelled status as `CANCELED` with `pill-mono ot-tag-cancelled`.
    - Omit or disable the `✖ Cancel` action button for orders that are already `CANCELLED` / `CANCELED` or `FILLED`.
- **Verification**: Run `pytest tests/test_orders_trades_table.py`.

---

### Task 4: Add Automated Unit & DOM Integration Tests
- **Target Files**: `tests/test_live_trader.py`, `tests/test_orders_trades_table.py`
- **Details**:
  - Test `_execute_stop_exit` in paper and live modes verifies opposite leg is marked `CANCELLED` and present in `get_open_orders_list()`.
  - Test entry timeout preserves cancelled orders in `get_open_orders_list()`.
  - Test window rollover cleans up `cancelled_orders`.
  - Test Node DOM harness verifies stopped-out card shows `FLAT (STOPPED OUT)`, inactive bids, and orders table renders `CANCELED` without cancel button.
- **Verification**: Run `pytest tests/test_live_trader.py tests/test_orders_trades_table.py`.

---

### Task 5: Full Regression Testing & Validation
- **Details**:
  - Run full test suite: `python -m pytest -q`.
  - Ensure 100% pass rate with zero regressions across all 248+ tests.
