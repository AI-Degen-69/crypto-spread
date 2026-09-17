# Task Plan — Issue #197: Interactive Column Header Sorting

## Overview
Implement interactive ASC/DESC column header sorting across all three tabs in the Live Cockpit tables (`#cockpitOrdersTable`, `#cockpitPositionsTable`, `#cockpitTradesTable`), with type-aware comparison, pair-group integrity preservation, visual indicators, and live refresh persistence.

---

## Tasks

- [ ] **TASK-1 [Design/UI]**: CSS Styling & Accessible Header Markup in `server/osc_dash.py`
  - Target files: `server/osc_dash.py:2120-2135`, `server/osc_dash.py:2830-2895`
  - Add sortable table header CSS classes (`.th-sortable`, hover highlight, cursor pointer, `.sort-icon`, `.sort-icon.asc`, `.sort-icon.desc`).
  - Update `<thead>` HTML for `#cockpitOrdersTable`, `#cockpitPositionsTable`, and `#cockpitTradesTable` with click handlers `sortOtTable('orders', '...')`, data attributes (`data-col`), and accessibility attributes (`aria-sort="none"`). Ensure Action column is explicitly non-sortable.
  - Verification: Node DOM test & browser rendering check in `tests/test_orders_trades_table.py`.

- [ ] **TASK-2 [Frontend/JS]**: Core Sorting Logic & Pair-Group Preservation in `server/osc_dash.py`
  - Target files: `server/osc_dash.py:3280-3350`
  - Implement `otSortState` (`{ orders: { col: null, dir: 'asc' }, positions: { col: null, dir: 'asc' }, trades: { col: 'time', dir: 'desc' } }`) with localStorage backup.
  - Implement type-aware comparator helper `compareOtValues(valA, valB, type, dir)`.
  - Implement group-aware sorter for `groupedOrders` (Tab 1), `groupedPos` (Tab 2), and flat array sorter for `trades` (Tab 3).
  - Verification: Node script unit test in test harness verifying comparator on numbers, timestamps, currencies, percentages, and strings.

- [ ] **TASK-3 [Frontend/JS]**: Live Refresh Integration & UI Updates in `renderCockpitUI`
  - Target files: `server/osc_dash.py:5925-6170`
  - Integrate sorting step directly into `renderCockpitUI(st)` prior to HTML generation for each tab so sort order persists on every 1s SSE tick.
  - Implement `sortOtTable(tab, col)` function: updates state, toggles direction (`asc` -> `desc` -> `none`/`asc`), updates header arrow indicators (`▲`/`▼`) and `aria-sort`, and triggers immediate re-render using cached state.
  - Verification: Fast DOM re-render test simulating consecutive live ticks with active sort.

- [ ] **TASK-4 [QA/Tests]**: Comprehensive Test Suite in `tests/test_orders_trades_table.py`
  - Target files: `tests/test_orders_trades_table.py`
  - Add automated tests covering:
    1. Header attributes: sortable classes, `data-col`, `aria-sort`, and non-sortable Action column.
    2. Column header click event toggling (ASC / DESC / none).
    3. Type-aware sorting (verifying `$10.00` > `$2.00`, positive vs negative P&L, timestamps).
    4. Group integrity: multi-row pairs (`rowspan`) staying strictly intact under all sort columns.
    5. Refresh persistence: sort state remaining applied after `renderCockpitUI` receives a new state tick.
  - Verification: Run `python -m pytest tests/test_orders_trades_table.py -q` ensuring 100% pass rate.
