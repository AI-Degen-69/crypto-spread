# Tasks: Issue #49

- [x] Task 1: `positions-api` — Ingest and expose structured open positions list from Polymarket Data API
  - Acceptance: `fetch_polymarket_account_value` and `get_state()` return `positions` list containing structured objects (`asset`, `conditionId`, `size`, `avgPrice`, `curPrice`, `cashPnl`, `title`, `outcome`).
  - Verify: `python -m pytest tests/test_live_trader.py -k test_fetch_polymarket_account_value_positions -q`
  - Files: `strategy/live_trader.py`, `tests/test_live_trader.py`

- [ ] Task 2: `execution-fill-tracking` — True execution fill price recording & slippage-aware PnL
  - Acceptance: When orders fill on CLOB, `MarketLiveState.fill_price_*` records true match price instead of fixed `resting_*`. Pair merge realized PnL reflects `(1.00 - (fill_price_up + fill_price_down)) * shares`.
  - Verify: `python -m pytest tests/test_live_trader.py -k test_fill_price -q`
  - Files: `strategy/live_trader.py`, `tests/test_live_trader.py`

- [ ] Task 3: `cockpit-ui-reflection` — Render true fill prices and open positions on dashboard
  - Acceptance: Cockpit UI cards display actual fill prices (`m.fill_price_up || m.resting_up || 0.48`), and live open positions are exposed.
  - Verify: `python -m pytest tests/test_osc_dash_integration.py -q`
  - Files: `server/osc_dash.py`, `tests/test_osc_dash_integration.py`
