# Implementation Plan: Direct Polymarket Links in Dashboard (Issue #75)

## Overview
Add direct clickable hyperlinks pointing to the active Polymarket live markets in the Cockpit dashboard across both the 🎯 Live Market Matrix cards and the Orders & Trades table (Open Orders, Positions, and Closed Trades tabs). All links will open in a new browser tab with `target="_blank"` and `rel="noopener"`, and fall back safely to the series slug if `market_slug` is pending discovery.

## Architecture Decisions
1. **Backward-Compatible Dataclass Extension**: Add `market_slug: str = ""` to `TradeEvent` with a default empty string so existing persisted JSON records in `run/trades.json` load without schema migration errors.
2. **Comprehensive Slug Propagation**: Expose both `market_slug` and `series_slug` across all order sources in `get_open_orders_list()` (CLOB API, active engine, advance next window, paper simulation) and `get_open_positions()`.
3. **Robust Client-Side URL Formation**: Construct Polymarket market URLs using `https://polymarket.com/market/${encodeURIComponent(market_slug || series_slug)}` with `target="_blank" rel="noopener"` and clear visual cues (e.g. `↗` symbol and hover accent).
4. **End-to-End Test Validation**: Verify both Python API serialization and client-side JavaScript DOM rendering using the existing Node.js test harness in `tests/test_orders_trades_table.py`.

## Task List

### Phase 1: Backend Data Model & Slug Propagation
- [x] Task 1: Extend `TradeEvent` dataclass and propagate `market_slug` in `strategy/live_trader.py`
  - **Acceptance**: `TradeEvent` has `market_slug: str = ""`. All `TradeEvent` instances in `_execute_stop_exit`, `_reconcile_live_positions`, `_run_execution_cycle`, and `_seed_demo_positions` pass `market_slug`.
  - **Verify**: `python -m pytest tests/test_live_trader.py -q`
  - **Files**: `strategy/live_trader.py`

- [ ] Task 2: Propagate `market_slug` and `series_slug` in `get_open_orders_list` and `get_open_positions`
  - **Acceptance**: Returned order dictionaries and position dictionaries contain `market_slug` and `series_slug`.
  - **Verify**: `python -m pytest tests/test_live_trader.py -q`
  - **Files**: `strategy/live_trader.py`

### Phase 2: Frontend Grouping & Dashboard UI Hyperlinks
- [ ] Task 3: Update `groupOrdersByPair` and `groupPositionsByPair` in `server/osc_dash.py`
  - **Acceptance**: Grouped records preserve `market_slug` and `series_slug` from underlying legs.
  - **Verify**: `python -m pytest tests/test_orders_trades_table.py -q`
  - **Files**: `server/osc_dash.py`

- [ ] Task 4: Render hyperlinks in Live Market Matrix and Orders & Trades tabs
  - **Acceptance**:
    - `#cockpitMarketGrid` card headers link to `https://polymarket.com/market/{slug}`.
    - Tab 1 (Open Orders) `mktCell` links to `https://polymarket.com/market/{slug}`.
    - Tab 2 (Positions) `mktCell` links to `https://polymarket.com/market/{slug}`.
    - Tab 3 (Closed Trades) market column links to `https://polymarket.com/market/{slug}`.
    - All links have `target="_blank" rel="noopener"` and fall back to series slug if `market_slug` is absent.
  - **Verify**: `python -m pytest tests/test_orders_trades_table.py -q`
  - **Files**: `server/osc_dash.py`

### Phase 3: Automated Verification & Regression Suite
- [ ] Task 5: Add automated unit & Node DOM tests for market hyperlinks
  - **Acceptance**: Tests verify link URLs, attributes (`target="_blank"`, `rel="noopener"`), and fallbacks across matrix cards and all three tabs.
  - **Verify**: `python -m pytest tests/test_orders_trades_table.py -q`
  - **Files**: `tests/test_orders_trades_table.py`

- [ ] Task 6: Run full test suite regression
  - **Acceptance**: All 242+ tests pass with zero regressions.
  - **Verify**: `python -m pytest -q`

## Risks and Mitigations
| Risk | Impact | Mitigation |
|------|--------|------------|
| `market_slug` not yet discovered (market initializing) | Low | Automatically fall back to series slug (`market_slug || series_slug`). |
| Persisted legacy trade records missing `market_slug` | Low | Set default `market_slug: str = ""` on `TradeEvent` dataclass so `TradeEvent(**d)` never errors. |
| Malformed slug string causing broken URL | Low | Wrap slug with `encodeURIComponent` before embedding in `href`. |

## Open Questions
- None. Requirements and implementation boundaries are fully locked in.
