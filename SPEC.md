# SPEC.md — Issue #365: Self-Explanatory Backtest UI & Master-Detail Execution Log

## 1. Overview & Goals
Following the telemetry exposed in Issue #364 (`first_pair_cost`, `mean_pair_edge_cents`, `pairs_count`, `stops_count`, `settlement_mid`), Issue #365 updates the Backtester presentation layer in `server/osc_dash.py`:
1. **Honest Capture-Rate Wording:** Remove all misleading "for profit" copy from KPI cards, tooltips, and table headers.
2. **KPI Cards Parity & Pair-Cost Card:** Add a dedicated card/metric showing pair cost vs $1.00 settle and merges closing above $1.00; ensure provisional and final card rendering stay 100% synchronized.
3. **Master-Detail Executed Windows Log:**
   - **Master Row:** Market, Total P&L ($ and % on capital invested), Merges count, Stop Loss count, Dead Zone count, Resolution (settlement outcome).
   - **Expandable Child Table:**
     - Pure English, clean tabular presentation (no inline explanations in cells).
     - Visual grouping for pairs (no dividing borders between legs of a pair).
     - Columns: `Trade #`, `Side` (UP / DOWN / EXIT), `Time (Elapsed)` (Local time + YouTube-like window elapsed time, e.g. `13:05:12 (0:12)`), `Fill Price`, `Pair Cost (Edge)`, `Duration` (time in market before resolution), `Status` (`MERGED`, `STOP_LOSS`, `DEAD_ZONE`), `Trade P&L (%)`.
     - Auxiliary notes and context rendered strictly via hover tooltips (`title` / tooltip attributes).

## 2. KPI Cards & Wording Corrections
- **Pair Capture Rate Tooltip:** Change from *"Proportion of windows where both legs filled and merged for profit"* to *"Proportion of windows where both legs were captured and merged on CTF"*.
- **Win Rate Tooltip:** Clarify that a win is positive net P&L after all window events.
- **Max Drawdown Hint:** Add sub-label *"Peak to trough from $0.00 start"*.
- **Pair Cost vs $1.00 Settle Card:** Surface average pair cost and how many pairs closed above $1.00.

## 3. Master-Detail Table Architecture
In `server/osc_dash.py` (`renderBacktestTradesPage`):
- Each window renders a primary `<tr>` with a click-to-expand disclosure button and summary columns:
  - `Market` (e.g. `05m BTC`)
  - `Total P&L` (formatted with USD and % of capital invested)
  - `Merges` (`pairs_count`)
  - `Stop Loss` (`stops_count`)
  - `Dead Zone` (indicator of dead-zone expiry / naked leg hold)
  - `Resolution` (`settlement_mid` or win/loss indicator)
- Clicking a row expands a full-width detail row containing the clean child sub-table.
- If multiple events occurred, the sub-table renders all fills, legs, and exits chronologically.

## 4. Verification & Testing
- `tests/test_osc_dash_integration.py` verifies absence of "for profit" strings, markup hooks, and card parity.
