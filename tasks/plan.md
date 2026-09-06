# Task Plan — Issue #81: Floating Side Toast Notifications

## Task 1: DOM Container & CSS Styles for Floating Toasts
- **Target Files**: server/osc_dash.py, 	ests/test_orders_trades_table.py
- **Objective**: Add #toastContainer and .toast-container, .toast, .toast-merged, .toast-stoploss, .toast-filled, .toast-close styling to FULL_APP_HTML.
- **TDD Command**: python -m pytest -k test_toast_container_and_css
- **Acceptance Criteria**: Container is mounted with fixed top-right positioning (z-index: 9999), and all CSS classes are defined.

## Task 2: Toast Helper Function (showToast) & Dismissal Handling
- **Target Files**: server/osc_dash.py, 	ests/test_orders_trades_table.py
- **Objective**: Implement showToast({ type, title, message, durationMs }) with smooth slide-in, fade-out animation, auto-dismiss (5s), and manual close button (×).
- **TDD Command**: python -m pytest -k test_show_toast_dom_and_lifecycle
- **Acceptance Criteria**: Toast DOM elements are constructed with correct classes and structure, auto-dismiss removes the node, and manual close button removes immediately.

## Task 3: State Reconciliation & Diffing Logic (econcileCockpitToasts)
- **Target Files**: server/osc_dash.py, 	ests/test_orders_trades_table.py
- **Objective**: Implement econcileCockpitToasts(st) called within enderCockpitUI(st):
  - Initial boot seeds existing trades and market fills without emitting toasts.
  - Subsequent updates detect new PAIR_MERGE trades (green toast), STOP_EXIT_* trades (red toast), and new leg fills (neutral toast).
- **TDD Command**: python -m pytest -k test_reconcile_cockpit_toasts
- **Acceptance Criteria**: Initial state yields 0 toasts; subsequent updates with new trades/fills trigger the appropriate toast variant with market details.

## Task 4: Full Suite Regression Verification
- **Command**: python -m pytest -q
- **Acceptance Criteria**: All 265+ tests pass with zero regressions.
