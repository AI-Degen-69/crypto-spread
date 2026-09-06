# Tasks: Issue #75 Direct Polymarket Links

- [x] Task 1: Extend `TradeEvent` dataclass with `market_slug: str = ""` and pass `market_slug` in all trade instantiations (`strategy/live_trader.py`)
- [x] Task 2: Add `market_slug` and `series_slug` to `get_open_orders_list()` and `get_open_positions()` (`strategy/live_trader.py`)
- [x] Task 3: Preserve `market_slug` and `series_slug` in `groupOrdersByPair` and `groupPositionsByPair` (`server/osc_dash.py`)
- [x] Task 4: Add direct Polymarket market hyperlinks to Live Market Matrix cards and all 3 tabs (Open Orders, Positions, Closed Trades) in `server/osc_dash.py`
- [x] Task 5: Add automated unit & Node DOM tests for market hyperlinks in `tests/test_orders_trades_table.py`
- [x] Task 6: Run full test suite regression (`python -m pytest -q`)
