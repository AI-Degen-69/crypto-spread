# CONSTRAINTS — Issue #197: Interactive Column Header Sorting

## Quality Guardrails
1. **Zero Regressions**:
   - All 33 existing tests in `tests/test_orders_trades_table.py` must continue to pass without modification or regression.
   - All existing dashboard pages and endpoints (`/`, `/api/live/*`) must remain fully functional.

2. **Vanilla JS Only (No New Dependencies)**:
   - All sorting, DOM manipulation, and indicator styling must be pure vanilla JavaScript and CSS embedded in `server/osc_dash.py`. No external sorting libraries (e.g., DataTables, Lodash).

3. **Pair-Group Structural Integrity**:
   - In Tab 1 (Orders) and Tab 2 (Positions), multi-leg pair groups with `rowspan` must remain grouped together. Sorting must operate on the group level (or lead leg) rather than splitting paired UP and DOWN legs into disconnected table rows.

4. **1-Second Live Refresh Persistence**:
   - Cockpit tables are re-rendered frequently via live SSE ticks and polling in `renderCockpitUI(st)`. The user's chosen sort column and direction (`asc` / `desc`) must persist across updates without resetting to default order or causing visual jumps.

5. **Type-Aware Parsing**:
   - Prices (`$0.48`), sizes (`10`), dollar P&L (`+$0.20`, `-$0.50`), percentages (`+4.2%`), and timestamps (`14:00:01`) must sort by actual numeric or chronological value, never by naive lexicographical ASCII comparison (where `$10.00` would incorrectly sort before `$2.00`).

6. **Accessibility & Clean Semantics**:
   - Use proper `aria-sort="ascending"`, `aria-sort="descending"`, or `aria-sort="none"` on headers.
   - Non-sortable columns (such as the Action/Cancel button column in Orders) must not show sort pointers or triggers.
