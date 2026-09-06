# SPEC: Synchronize Live Market Matrix Stopped State & Retain Cancelled Orders (Issue #76)

## Objective
Synchronize the Live Market Matrix cards and the Orders table during stop exits and order cancellations. Ensure stopped-out positions and cancelled quotes display as `FLAT` and inactive in the market matrix, while retaining cancelled orders in the Orders table with status `CANCELED` and no cancel action button until the 5m/15m market window rolls over.

## Background & Problem Statement
In the Live Trading Cockpit (`server/osc_dash.py`), when a market triggers a stop-loss exit (`STOP_EXIT`), the status badge changes to `STOPPED OUT`, but the card continues displaying stale position details (e.g. `LONG UP (5) @ $0.47`) and active target quotes (`Bids: $0.47 / $0.47`). At the same time, the Orders table immediately drops cancelled orders and reports `No orders are resting on the book (0)`. This causes visual desynchronization and operator confusion.

## Scope

### In Scope
1. **Live Market Matrix (`server/osc_dash.py`)**:
   - When `m.status === 'STOP_EXIT'` or `m.exit_taken` is True:
     - Set `Orders & Position` to `FLAT (STOPPED OUT)` or `FLAT`.
     - Suppress active resting bid quotes (`Bids: $X / $Y`) and display inactive/cancelled state (`Bids: CANCELLED (STOPPED OUT)` or greyed-out inactive indicator).
   - When market is stopped (`!st.is_running`), drift-skipped (`DRIFT_SKIPPED`), or timed out (`TIMEOUT_NO_FILL`):
     - Display clear inactive/cancelled indicator instead of active resting bid prices.

2. **Order Lifecycle & Retention (`strategy/live_trader.py`)**:
   - Maintain `cancelled_orders: List[Dict[str, Any]]` on `MarketLiveState`.
   - In `_execute_stop_exit`:
     - Both in live and paper modes, mark the opposite unhedged leg as `CANCELLED` and retain it in `cancelled_orders`.
   - In `_update_market_strategy`:
     - When entry orders are cancelled due to 10% window timeout, adverse drift, or wide touch pair, retain the cancelled orders in `cancelled_orders` with status `CANCELLED`.
   - In `cancel_live_order` / `_clear_order_handles`:
     - Record cancelled orders in `cancelled_orders`.
   - In `get_open_orders_list()`:
     - Merge `cancelled_orders` from all active markets into the returned orders list so they remain visible in the dashboard.
   - In `_handle_window_rollover`:
     - Clear `cancelled_orders` when the market window expires and transitions to the new window.

3. **Orders Table Display (`server/osc_dash.py`)**:
   - Render cancelled orders with status `CANCELED`.
   - Do NOT display an active `✖ Cancel` button for orders in `CANCELLED` / `CANCELED` or `FILLED` status.
   - Update `groupOrdersByPair` so that cancelled legs do not falsely mark an unpaired order group as `Paired`.

4. **Testing**:
   - Backend unit tests in `tests/test_live_trader.py`.
   - Frontend DOM integration tests in `tests/test_orders_trades_table.py`.

### Out of Scope
- Persisting cancelled orders across engine/server restarts (in-memory per window lifecycle only).
- Changing order execution logic, stop-loss price thresholds, or CLOB cancel endpoints.

## Interfaces & Contracts

### 1. `MarketLiveState` (`strategy/live_trader.py`)
```python
@dataclass
class MarketLiveState:
    ...
    cancelled_orders: List[Dict[str, Any]] = field(default_factory=list)
```

### 2. Cancelled Order Dictionary Schema
```python
{
    "order_id": str,
    "market": str,
    "market_slug": str,
    "series_slug": str,
    "token_id": str,
    "side": str,          # e.g. "BUY (DOWN)"
    "price": float,
    "size": int,
    "status": "CANCELLED",
    "source": str,        # "PAPER_SIMULATION" | "CLOB_API" | "ENGINE"
    "time": str,          # "HH:MM:SS"
}
```

### 3. Frontend Order Grouping & Button Logic (`server/osc_dash.py`)
- `groupOrdersByPair`: Only active (non-cancelled) legs count toward `Paired` status. Groups consisting solely of cancelled legs have status `Cancelled`.
- Cancel button rule: `canCancel = oId && oId !== '-' && !isCancelled && !isFilled`.
