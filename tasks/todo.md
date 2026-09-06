# Tasks: Issue #76 Synchronize Matrix & Retain Cancelled Orders

- [x] Task 1: Add cancelled order retention per window to `MarketLiveState`, `_execute_stop_exit`, `_update_market_strategy`, `get_open_orders_list`, and rollover in `strategy/live_trader.py`
- [x] Task 2: Synchronize Live Market Matrix card UI: show `FLAT (STOPPED OUT)` and inactive/cancelled bids when stopped or timed out in `server/osc_dash.py`
- [x] Task 3: Update Orders table: render status `CANCELED`, disable cancel button for cancelled/filled orders, update pair grouping in `server/osc_dash.py`
- [x] Task 4: Add comprehensive backend and DOM integration tests in `tests/test_live_trader.py` and `tests/test_orders_trades_table.py`
- [x] Task 5: Run full test suite regression (`python -m pytest -q`) — 253 passed
