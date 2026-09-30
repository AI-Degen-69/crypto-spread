Branch: i365/make-backtest-results-self-explanatory | Issue: #365
# Plan: Dashboard: Make backtest results self-explanatory & Master-Detail Execution Log

## Task 1: KPI Cards Parity & Honest Wording [Design/UI] [Review mode: unit/integration]
- [x] In `server/osc_dash.py`:
  - [x] Remove all occurrences of "for profit" from backtest tooltips and headers.
  - [x] Correct Pair Capture Rate, Win Rate, and Max Drawdown tooltips and sub-labels ("Peak to trough from $0.00 start").
  - [x] Add or update the Pair Cost KPI card showing mean pair cost and merges above $1.00.
  - [x] Unify card rendering between `renderBacktestResult` and `btAppendProvisionalPoints`.

## Task 2: Master-Detail Executed Windows Log Table [Design/UI] [Review mode: browser preview + unit/integration]
- [x] In `server/osc_dash.py` (`renderBacktestTradesPage`):
  - [x] Refactor table headers and rows to Master-Detail structure:
    - Master Row: Market, Total P&L ($ and % on capital invested), Merges (`pairs_count`), Stop Loss (`stops_count`), Dead Zone indicator, Resolution.
    - Click-to-expand disclosure button and state persistence.
  - [x] Implement expanded Child Table:
    - Clean English, strict tabular layout without prose.
    - Visual pair grouping (two consecutive rows without middle border for pairs).
    - Columns: Trade #, Side (UP / DOWN / EXIT), Time (Local time + YouTube-like elapsed mm:ss), Fill Price, Pair Cost (Edge), Duration in trade, Status (MERGED / STOP_LOSS / DEAD_ZONE), Trade P&L (USD + %).
    - Tooltips on hover for detailed explanations.

## Task 3: Integration Tests & Verification [Test] [Review mode: targeted test runner]
- [x] In `tests/test_osc_dash_integration.py`:
  - [x] Update literal string assertions to match corrected copy.
  - [x] Assert zero occurrences of "for profit" in backtest sections.
  - [x] Verify master-detail markup structure and card parity.
  - [x] Run targeted test suite: `python -m pytest tests/test_osc_dash_integration.py -q`.
