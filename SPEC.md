# Spec: Unified Orders & Trades Table (Issue #59)

## 1. Objective
Replace the 3 fragmented Cockpit tables in `server/osc_dash.py` (Active Orders, Polymarket Positions, Trade Log) with a single, unified "Orders & Trades" component (`#orders-trades-card`) containing 3 tabs:
1. **Open Orders**: Resting CLOB limit orders, grouped by pair (Up above Down), 9 columns (`Time` first, `Remaining`/`Queue Ahead`/`Age` removed).
2. **Positions**: Held shares, grouped by pair with shared pair-level cells (`Market`, `Market Value`, `Unrealized $ (%)`, `Realized $ (%)`), 8 columns (`Time` first, `Cost` removed, `Base Cost` for share basis).
3. **Closed Trades**: Execution history log, 8 columns (`Time` first, `Cause` [Merged/Stop-Loss/Settled], `Base Cost`, `Exit Price`, and combined `Gain / Loss $ (%)`).

Consistent layout across tabs: Column 1 is always `Time` (action creation/execution timestamp), Column 2 is always `Market`.

Add tab badges with live counts, tab persistence to `localStorage` (`crypto-spread-ot-view`), sentence-case empty states, and strict signed money formatting (`-$0.25 (-25.0%)`, `--` for missing marks).

---

## 2. Tech Stack
- Python 3.11+ (FastAPI, Uvicorn, Requests)
- Vanilla JavaScript & Modern CSS (in `server/osc_dash.py`)
- Node.js (v24+) & Pytest for test runner execution

---

## 3. Commands
- Run all tests: `python -m pytest -q`
- Run table tests: `python -m pytest tests/test_orders_trades_table.py -q`
- Run integration tests: `python -m pytest tests/test_osc_dash_integration.py -q`
- Start dashboard: `python -m uvicorn server.osc_dash:app --host 127.0.0.1 --port 8802`

---

## 4. Project Structure
```
server/
  osc_dash.py                  -> Unified #orders-trades-card HTML, CSS, and JS renderers
strategy/
  live_trader.py               -> Timestamp inclusion on order/position dicts
tests/
  test_orders_trades_table.py  -> Unit tests for pair grouping, column headers, mark value, and tab state
  test_osc_dash_integration.py -> Verification of served HTML elements and API endpoints
SPEC.md                        -> This specification
```

---

## 5. Column Specifications & UI Contracts

### Tab 1: Open Orders (9 Columns)
- `Time`: Creation timestamp (e.g. `14:02:11`).
- `Market`: Rowspan per pair, title, pair status pill (`Paired` / `Partial` / `Unpaired`), pair cost (`$X.XX` or `--`).
- `Side`: `Up` or `Down` badge.
- `Price`: Bid limit price (`$0.48`).
- `Size`: Order shares (`5`).
- `Filled`: Matched shares (`0`).
- `Total Cost`: `Price * Size` (`$2.40`).
- `Status`: `OPEN`, `PARTIAL`, `PENDING`.
- `Action`: `✖ Cancel` button (cancels resting limit order on Polymarket CLOB).
- Deliberately excluded: `Remaining`, `Queue Ahead`, `Age`.

### Tab 2: Positions (8 Columns)
- `Time`: Position entry/creation timestamp.
- `Market`: Rowspan per pair, title, pair status pill, pair cost.
- `Side`: `Up` or `Down` badge.
- `Size`: Held shares.
- `Base Cost`: Cost basis per share (`$0.480`).
- `Market Value`: Rowspan per pair. `$1.00 * min(size_up, size_down) + remainder * mid`. If mid unavailable, `--`.
- `Unrealized $ (%)`: Rowspan per pair. Combined signed dollar and percent: `+$0.15 (+15.6%)` or `-$0.10 (-10.4%)`.
- `Realized $ (%)`: Rowspan per pair. Combined signed realized dollar and percent.

### Tab 3: Closed Trades (8 Columns)
- `Time`: Execution timestamp.
- `Market`: Asset & timeframe label.
- `Cause`: Pill badge (`Merged`, `Stop-Loss`, `Settled`).
- `Shares`: Share count.
- `Base Cost`: `UP + DOWN` or single-leg fill (`$0.48 + $0.48`).
- `Exit Price`: Merge `$1.00` or stop exit price.
- `Gain / Loss $ (%)`: Combined cash P&L and return: `+$0.20 (+4.2%)` / `-$0.25 (-25.0%)`.
- `Details`: Execution notes and stop triggers.

---

## 6. Code Style & Honesty Rules
- Signed money formatting: always `-$0.25 (-25.0%)` or `+$0.25 (+4.2%)`, never `$-0.25`.
- Missing marks or prices: `--`, never `$0.00`.
- Empty states in sentence case:
  - Orders: `No orders are resting on the book.`
  - Positions: `No open positions held in account.`
  - Trades: `No closed trades recorded in this session.`
- Pure JavaScript helper functions: `groupOrdersByPair`, `groupPositionsByPair`, `formatSignedMoneyPct`, `calculatePairMarkValue`.

---

## 7. Testing Strategy
- Create `tests/test_orders_trades_table.py`:
  1. Test HTML contains `#orders-trades-card`, `.ot-tabs`, 3 tab buttons, and target tables.
  2. Test Open Orders table headers match exactly the 9 specified columns (`Time`, `Market`, `Side`, `Price`, `Size`, `Filled`, `Total Cost`, `Status`, `Action`), and verify `Remaining`, `Queue Ahead`, `Age`, and `Leg` are not present.
  3. Test Positions table headers match exactly the 8 specified columns (`Time`, `Market`, `Side`, `Size`, `Base Cost`, `Market Value`, `Unrealized $ (%)`, `Realized $ (%)`), and verify `Cost`, `Avg Price`, and `Leg` are not present.
  4. Test Closed Trades table headers match exactly the 8 specified columns (`Time`, `Market`, `Cause`, `Shares`, `Base Cost`, `Exit Price`, `Gain / Loss $ (%)`, `Details`), and verify `Action`, `Entry Price`, and `P&L ($)` are replaced.
  5. Test pure JS helper logic (pair grouping, rowspan calculation, mark value, signed formatting) using a Node runner script in test assertions.
- Run `tests/test_osc_dash_integration.py` to ensure existing assertions remain green.

---

## 8. Boundaries
- **Always do:**
  - Preserve backward-compatible element IDs where integration tests expect them (`cockpitPositionsTable`, `cockpitPositionsBody`, `cockpitPositionsCount`, `cockpitOrdersCount`, etc.).
  - Ensure all 178 baseline pytest tests pass.
  - Keep CSS scoped with `.ot-` prefix to avoid style collisions.
- **Ask first:**
  - Changing API response structures on `/api/live/state`.
- **Never do:**
  - Reinstate excluded columns (`Remaining`, `Queue Ahead`, `Age`, `Cost`).
  - Render `$-0.25` or `$0.00` for missing mark values.

---

## 9. Success Criteria
- [ ] Unified card `#orders-trades-card` with 3 tabs replaces the 3 separate cards in Cockpit view.
- [ ] Active tab button shows live count pill and switches views with zero reload.
- [ ] Selected tab persists in `localStorage.getItem('crypto-spread-ot-view')`.
- [ ] Column 1 is consistently `Time` and Column 2 is `Market` across all 3 tabs.
- [ ] Open Orders uses `Side` (`Up` / `Down`), includes `Action` (`✖ Cancel`), and excludes `Remaining`, `Queue Ahead`, `Age`.
- [ ] Positions uses `Side`, `Base Cost`, `Market Value`, `Unrealized $ (%)`, `Realized $ (%)`, and excludes separate `Cost`.
- [ ] Closed Trades uses `Cause` (`Merged`, `Stop-Loss`, `Settled`), `Base Cost`, `Exit Price`, and `Gain / Loss $ (%)`.
- [ ] `tests/test_orders_trades_table.py` passes all tests.
- [ ] All 178+ pytest tests pass with zero regressions.

---

## 10. Open Questions
- None.
