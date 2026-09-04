# Implementation Plan: Unified Orders & Trades Table (Issue #59)

## Overview
Replace the 3 separate Cockpit cards in `server/osc_dash.py` with a single unified `#orders-trades-card` component featuring 3 tab-switched views: **Open Orders**, **Positions**, and **Closed Trades**. The table groups orders and positions by pair, displays pair cost alongside pair status (`Paired`, `Partial`, `Unpaired`), displays `Time` as Column 1 across all tabs, and includes behavioural unit tests in `tests/test_orders_trades_table.py`.

## Architecture Decisions
1. **Unified Container `#orders-trades-card`**: Replaces the 3 vertically stacked card divs in Cockpit view. Preserves underlying table and body IDs (`cockpitOrdersTable`, `cockpitPositionsTable`, `cockpitTradesTable`) for backward compatibility with existing integration tests.
2. **Column Consistency**: Column 1 is always `Time` (action creation or execution timestamp) and Column 2 is always `Market` across all three views.
3. **Tab Persistence**: Persist active view in `localStorage` under `crypto-spread-ot-view`. On reload, default to `'orders'`.
4. **Pure Builders**: Separate data grouping (`groupOrdersByPair`, `groupPositionsByPair`) from DOM rendering so logic can be independently verified.

## Task List

### Phase 1: Structure & Styling
- [x] Task 1: CSS styling & unified HTML shell in `server/osc_dash.py`
  - Acceptance: `#orders-trades-card` renders tab navigation (`.ot-tabs`), count pills, and 3 tab panes. Old 3 separate card containers are removed.
  - Verify: `python -m pytest tests/test_osc_dash_integration.py -q`
  - Files: `server/osc_dash.py`

### Checkpoint 1: Structure
- [x] All 178 existing tests pass.
- [x] UI shell displays cleanly with no syntax errors.

### Phase 2: JavaScript Builders & Table Renderers
- [x] Task 2: Pure pair grouping & formatting helpers
  - Acceptance: `groupOrdersByPair`, `groupPositionsByPair`, `formatSignedMoneyPct`, and `switchOtTab` correctly group legs (UP above DOWN), calculate rowspans, format signed money/percentages, and save tab state.
  - Verify: Unit checks in test harness.
  - Files: `server/osc_dash.py`

- [x] Task 3: Render Open Orders, Positions, and Closed Trades
  - Acceptance:
    - Tab 1 renders 9 columns (`Time`, `Market`, `Side`, `Price`, `Size`, `Filled`, `Total Cost`, `Status`, `Action`), excludes clutter (`Remaining`, `Queue Ahead`, `Age`).
    - Tab 2 renders 8 columns (`Time`, `Market`, `Side`, `Size`, `Base Cost`, `Market Value`, `Unrealized $ (%)`, `Realized $ (%)`), excludes separate `Cost`.
    - Tab 3 renders 8 columns (`Time`, `Market`, `Cause`, `Shares`, `Base Cost`, `Exit Price`, `Gain / Loss $ (%)`, `Details`).
    - Tab badges reflect live item counts.
    - Timestamp logging supported from engine orders/positions.
  - Verify: `python -m pytest tests/test_osc_dash_integration.py -q`
  - Files: `server/osc_dash.py`, `strategy/live_trader.py`

### Checkpoint 2: Feature Complete
- [x] All three tabs render live and mock states properly.
- [x] Tab switching works without page refresh.

### Phase 3: Test Suite & Verification
- [x] Task 4: Comprehensive behavioural test suite in `tests/test_orders_trades_table.py`
  - Acceptance: Tests verify 9/8/8 columns, absence of excluded columns, pair grouping, mark values, signed money formatting (`-$0.25`), and empty states.
  - Verify: `python -m pytest tests/test_orders_trades_table.py -q` and `python -m pytest -q`.
  - Files: `tests/test_orders_trades_table.py`

## Risks and Mitigations
| Risk | Impact | Mitigation |
|---|---|---|
| Breaking existing tests that expect `#cockpitPositionsTable` or `#cockpitOrdersTable` | High | Keep existing table and tbody IDs inside each tab pane |
| Order or position without matching opposing leg | Medium | Graceful fallback to `Unpaired` / `Partial` status with `rowspan=1` |
| Negative money formatting bug (`$-0.25`) | Low | Dedicated `formatSignedMoneyPct` utility strictly emitting `-$0.25` |
