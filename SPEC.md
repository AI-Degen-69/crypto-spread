# SPEC: Direct Polymarket Market Hyperlinks in Cockpit Dashboard (Issue #75)

## Objective
Add direct clickable hyperlinks pointing to the active Polymarket live markets in the Cockpit dashboard across both the 🎯 Live Market Matrix cards and the unified Orders & Trades table (Open Orders, Positions, and Closed Trades tabs). All links must open in a new browser tab with `target="_blank"` and `rel="noopener"`, and fall back safely to the series slug if `market_slug` is pending discovery.

## Background & Context
Currently, market labels across the Cockpit dashboard are rendered as static text (e.g. `BTC 5m`, `ETH 5m`). Operators managing automated or paper strategies need immediate access to inspect order books, open interest, and settlement conditions on Polymarket. Hyperlinking directly to `https://polymarket.com/market/{market_slug}` enables instant one-click navigation without manual searching.

## Tech Stack
- Python 3.10+ (`dataclasses`, `FastAPI`, `uvicorn`, `requests`)
- Vanilla ES6 JavaScript embedded in `server/osc_dash.py` (DOM manipulation, template literals)
- Node.js test runner for frontend DOM validation in `tests/test_orders_trades_table.py`
- Pytest test suite (`python -m pytest -q`)

## Interfaces & Contracts

### 1. Backend Dataclasses & Dictionaries (`strategy/live_trader.py`)
- `TradeEvent` dataclass:
  - Add `market_slug: str = ""` field with default value for backwards compatibility.
- `get_open_orders_list()`:
  - Each returned order dict includes `"market_slug": str` and `"series_slug": str`.
- `get_open_positions()`:
  - Each returned position dict includes `"market_slug": str` and `"series_slug": str`.
- `TradeEvent` instantiations in stop-loss, pair-merge, window settle, and demo seeding:
  - Pass `market_slug=mstate.market_slug or ""` (or equivalent).

### 2. Frontend Grouping & Rendering (`server/osc_dash.py`)
- `groupOrdersByPair(orders)`:
  - Preserves `market_slug` and `series_slug` on grouped order objects.
- `groupPositionsByPair(positions, markets)`:
  - Preserves `market_slug` and `series_slug` on grouped position objects.
- Live Market Matrix (`#cockpitMarketGrid`):
  - Wraps the market title/label in `<a href="https://polymarket.com/market/${encodeURIComponent(m.market_slug || item.slug)}" target="_blank" rel="noopener">`.
- Tab 1: Open Orders (`#cockpitOrdersBody`):
  - Wraps the market title in `<a href="https://polymarket.com/market/${encodeURIComponent(grp.market_slug || grp.series_slug || '')}" target="_blank" rel="noopener">`.
- Tab 2: Positions (`#cockpitPositionsBody`):
  - Wraps the market title in `<a href="https://polymarket.com/market/${encodeURIComponent(grp.market_slug || grp.series_slug || '')}" target="_blank" rel="noopener">`.
- Tab 3: Closed Trades (`#cockpitTradesBody`):
  - Wraps the trade market label in `<a href="https://polymarket.com/market/${encodeURIComponent(t.market_slug || t.slug || t.series_slug || '')}" target="_blank" rel="noopener">`.

## Testing Strategy
- Unit test suite in `tests/test_orders_trades_table.py` verifying:
  - Hyperlink URL structure (`https://polymarket.com/market/...`)
  - Target attributes (`target="_blank" rel="noopener"`)
  - Safe fallback to series slug when `market_slug` is absent
  - DOM presence across Matrix cards and all 3 tabs (Orders, Positions, Trades)
- Full regression test run: `python -m pytest -q` (all 242+ tests passing).

## Boundaries & Constraints
- **Always do**: Use `target="_blank" rel="noopener"` on all external links; URI-encode slugs via `encodeURIComponent`; maintain backward compatibility for existing serialized trade logs.
- **Never do**: Alter trading algorithms, execution logic, sizing, or offset pricing; break existing table CSS structure.
