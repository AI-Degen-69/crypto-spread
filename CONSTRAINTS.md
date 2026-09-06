# CONSTRAINTS.md — Issue #81 Quality & Architectural Constraints

## 1. Testing & Zero Regressions
- All existing 265 tests in python -m pytest -q must remain 100% passing.
- New unit and integration tests must cover:
  - Presence of #toastContainer in the DOM and CSS classes (.toast-container, .toast, .toast-merged, .toast-stoploss, .toast-filled).
  - Toast trigger logic for merges, stop-loss exits, and order fills.
  - Page-load history seeding: initial boot must NOT fire toasts for already existing trades/fills.
  - Auto-dismiss (5000ms) and manual close button removal.

## 2. Anti-Cheating & Integrity
- Strictly no disabling, skipping, or mocking out real assertions to achieve passing tests.
- No modifications to the core trading execution logic in strategy/live_trader.py.
- No new external dependencies in equirements.txt (only stdlib, existing FastAPI/Uvicorn/Chart.js/vanilla JS).

## 3. UI & Performance Guardrails
- Toast container must have pointer-events: none so clicks pass through empty spaces to underlying charts and buttons.
- Individual toasts must have pointer-events: auto.
- DOM reconciliation must be lightweight: O(N) where N is the number of active markets/trades.
- Toasts must cleanly auto-dismiss without memory leaks (clear timers and remove DOM nodes).
