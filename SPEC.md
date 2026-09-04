# Spec: Display Actual Polymarket Position Values and Fill History (Issue #49)

## 1. Objective
Enable accurate live execution reporting and slippage-aware PnL accounting across the engine and dashboard.
Today, `LiveTraderEngine` and the Live Cockpit dashboard assume fixed 0.48 entry quotes for position values and pair merge profits. When live orders fill with slippage or adverse fills, the dashboard shows theoretical 0.48 prices, and pair merge profit is computed as `(1.00 - (0.48 + 0.48)) * shares = $0.20`, ignoring real execution prices.

This change:
1. Ingests and preserves full position details (`avgPrice`, `curPrice`, `size`, `initialValue`, `currentValue`, `cashPnl`) from Polymarket Data API (`https://data-api.polymarket.com/positions?user={wallet}`).
2. Records true executed match prices into `MarketLiveState.fill_price_up` and `MarketLiveState.fill_price_down` when orders match on CLOB or via positions reconciliation.
3. Computes realized pair merge PnL using actual fill prices: `(1.00 - (fill_price_up + fill_price_down)) * shares`.
4. Updates the Live Cockpit dashboard UI to display actual average fill prices (`m.fill_price_up || m.resting_up`) and exposes open positions in `/api/live/state` and `/api/live/account`.

---

## 2. Capability Map (Phase 0)

| Module ID | Responsibility | Depends on |
|---|---|---|
| `positions-api` | Fetch, parse, and expose full open positions array from Polymarket Data API `/positions` | — |
| `execution-fill-tracking` | Ingest real match prices on fill from CLOB/positions; compute slippage-aware realized & unrealized PnL | `positions-api` |
| `cockpit-ui-reflection` | Update dashboard position cards & trades table to display true fill prices and live positions | `execution-fill-tracking`, `positions-api` |

Build order: `positions-api` `→` `execution-fill-tracking` `→` `cockpit-ui-reflection`

---

## 3. Tech Stack
- Python 3.11+
- FastAPI, Uvicorn, Requests
- Vanilla JavaScript & HTML/CSS (in `server/osc_dash.py`)
- Pytest for automated unit and integration tests

---

## 4. Commands
- Run test suite: `python -m pytest -q`
- Run live trader tests: `python -m pytest tests/test_live_trader.py -q`
- Run dashboard tests: `python -m pytest tests/test_osc_dash_integration.py -q`
- Start dashboard server: `python -m uvicorn server.osc_dash:app --host 127.0.0.1 --port 8802`

---

## 5. Project Structure
```
strategy/
  live_trader.py       -> fetch_polymarket_account_value, fill price tracking, pair merge PnL, get_state()
server/
  osc_dash.py          -> /api/live/account, /api/live/state, Cockpit UI cards & tables
tests/
  test_live_trader.py  -> Unit tests for position parsing, fill price recording, and slippage PnL
SPEC.md                -> This specification
```

---

## 6. Code Style
Use dataclasses, explicit typing, and thread safety with `_engine_lock`. Keep functions pure where possible.

```python
# Example: Slippage-aware pair merge profit calculation
fill_up = mstate.fill_price_up if mstate.fill_price_up is not None else mstate.resting_up
fill_down = mstate.fill_price_down if mstate.fill_price_down is not None else mstate.resting_down
pair_profit_usd = (1.00 - (fill_up + fill_down)) * self.shares
```

---

## 7. Testing Strategy
- Unit tests in `tests/test_live_trader.py`:
  1. Test `fetch_polymarket_account_value` preserves structured positions list (`size`, `avgPrice`, `curPrice`, `cashPnl`, `asset`, `title`).
  2. Test CLOB order fill detection records actual match price (`price` / `associate_trades`) instead of default `resting_up`.
  3. Test pair merge realized PnL correctly computes `(1.00 - (fill_up + fill_down)) * shares` when fills experience slippage (e.g., 0.49 + 0.485 `→` 0.025/share).
  4. Test unrealized PnL reflects actual entry fill price rather than initial resting price.
- Integration tests in `tests/test_osc_dash_integration.py`:
  1. Test `/api/live/account` and `/api/live/state` include `positions` payload.
  2. Ensure full suite passes (`176+ passed`).

---

## 8. Boundaries
- **Always do:**
  - Fall back gracefully to `resting_up` / `resting_down` if fill price or position lookup is unavailable.
  - Maintain thread safety with `with self._engine_lock:`.
  - Preserve backward compatibility for paper mode and demo mode.
  - Run `python -m pytest -q` before committing.
- **Ask first:**
  - Modifying backtest engine logic (`backtest/engine.py`).
  - Adding external dependencies to `requirements.txt`.
- **Never do:**
  - Commit private keys or secrets.
  - Hardcode 0.48 in live accounting formulas.
  - Break existing `/api/live/*` contract for UI consumers.

---

## 9. Success Criteria
- [ ] `fetch_polymarket_account_value` returns `positions` list containing structured objects (`asset`, `conditionId`, `size`, `avgPrice`, `curPrice`, `cashPnl`, `title`, `outcome`).
- [ ] In `LiveTraderEngine._on_tick`, when a live order fills on CLOB, `MarketLiveState.fill_price_up` and `MarketLiveState.fill_price_down` record the actual match price from the order/trade.
- [ ] Pair merge realized PnL formula is `(1.00 - (fill_price_up + fill_price_down)) * shares`.
- [ ] `/api/live/state` returns open positions list and actual fill prices.
- [ ] Cockpit UI cards in `server/osc_dash.py` render actual average fill price instead of fixed `$0.48`.
- [ ] All unit and integration tests pass (`python -m pytest -q`).

---

## 10. Open Questions
- None identified. Requirements and contracts are fully specified in Issue #49 and aligned with Polymarket Data API.
