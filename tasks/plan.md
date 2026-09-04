# Implementation Plan: Display Actual Polymarket Position Values and Fill History (Issue #49)

## Capability Map & Dependencies
- `positions-api` (Module 1): Fetch and parse structured open positions list from Polymarket Data API.
- `execution-fill-tracking` (Module 2, depends on Module 1): Update fill detection in `LiveTraderEngine._on_tick` and pair merge / stop loss PnL math.
- `cockpit-ui-reflection` (Module 3, depends on Modules 1 & 2): Update Cockpit cards and trades table in `server/osc_dash.py`.

## Tasks
1. **Task 1 (`positions-api`)**: Update `fetch_polymarket_account_value` in `strategy/live_trader.py` to parse and return structured open `positions` list; expose in `/api/live/account` and `get_state()`.
2. **Task 2 (`execution-fill-tracking`)**: Update CLOB fill detection in `LiveTraderEngine._on_tick` to record true execution price in `fill_price_up` and `fill_price_down`, and update pair merge realized PnL to `(1.00 - (fill_price_up + fill_price_down)) * shares`.
3. **Task 3 (`cockpit-ui-reflection`)**: Update `server/osc_dash.py` Cockpit cards to display actual fill prices (`m.fill_price_* || m.resting_*`) and show open positions list.
