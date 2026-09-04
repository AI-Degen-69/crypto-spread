# Tasks: Issue #59 Unified Orders & Trades Table

- [x] Task 1: CSS styling & unified HTML shell in `server/osc_dash.py`
  - Acceptance: `#orders-trades-card` replaces 3 cards, renders `.ot-tabs` with active count pills and 3 table panes. Existing table IDs preserved.
  - Verify: `python -m pytest tests/test_osc_dash_integration.py -q`
  - Files: `server/osc_dash.py`

- [ ] Task 2: Pure pair grouping & formatting helpers in `server/osc_dash.py`
  - Acceptance: `groupOrdersByPair`, `groupPositionsByPair`, `formatSignedMoneyPct`, `switchOtTab` correctly group UP above DOWN, compute rowspans, format signed values (`-$0.25 (-25.0%)`), and save to `localStorage`.
  - Verify: Node test harness & syntax check.
  - Files: `server/osc_dash.py`

- [ ] Task 3: Render Open Orders (9 cols), Positions (8 cols), and Closed Trades (8 cols)
  - Acceptance:
    - Tab 1: `Time`, `Market`, `Side`, `Price`, `Size`, `Filled`, `Total Cost`, `Status`, `Action`. Excludes `Remaining`, `Queue Ahead`, `Age`.
    - Tab 2: `Time`, `Market`, `Side`, `Size`, `Base Cost`, `Market Value`, `Unrealized $ (%)`, `Realized $ (%)`. Excludes `Cost`.
    - Tab 3: `Time`, `Market`, `Cause`, `Shares`, `Base Cost`, `Exit Price`, `Gain / Loss $ (%)`, `Details`.
    - Live counts in tab pills. Sentence case empty states.
  - Verify: `python -m pytest tests/test_osc_dash_integration.py -q`
  - Files: `server/osc_dash.py`, `strategy/live_trader.py`

- [ ] Task 4: Behavioural unit tests in `tests/test_orders_trades_table.py`
  - Acceptance: Unit and integration tests verify column counts, absence of excluded columns, pair grouping, mark value calculations, and tab switcher logic.
  - Verify: `python -m pytest tests/test_orders_trades_table.py -q` and `python -m pytest -q`
  - Files: `tests/test_orders_trades_table.py`
