# CONSTRAINTS.md — Quality Bar & Boundaries for Issue #452

## 1. Zero Regressions
- Acceptance gate (from the issue):
  ```powershell
  python -m pytest tests/test_live_trader.py -q
  ```
- Targeted neighbor (staged-stop fixtures exercise the same trigger blocks):
  ```powershell
  python -m pytest tests/test_stop_orders.py -q
  ```
- No CI merge gate exists right now: `.github/workflows/tests.yml` was deleted per operator order during #451. Targeted local suites are the only gate; do not claim CI coverage.

## 2. Behavior Boundaries
- Change note construction ONLY in the UP trigger (`strategy/live_trader.py:4942-4964`) and DOWN trigger (`4968-4990`). Extract a local drift-breach boolean, capture the staged stop price before the metadata clear, select the note by which condition fired (drift breach takes precedence when both are true).
- Do NOT change trigger conditions, `exit_thresh`, guards, `sell_bid`, `_execute_stop_exit`, cancellation, or the metadata-clear order/fields.
- Drift-breach note text stays byte-identical: `f"Adverse drift {drift:.3f} >= {thresh:.2f}"`.
- Staged-stop note must contain the side ("UP"/"DOWN", dashboard fallback at `server/osc_dash.py:7114-7115`) and the threshold formatted `:.2f` (existing `"0.03"` assertion at `tests/test_live_trader.py:2984`); must NOT contain "Adverse drift" and must NOT use ">=" between drift and threshold.
- Do NOT touch dead-zone exits, live venue reconciliation notes, the dashboard, the demo `TradeEvent` note (`live_trader.py:3670`), or the malformed `_resolve_exit_bid` fallback call (separate issue).
- Do NOT weaken any existing assertion (only one existing test asserts stop-note content: `test_live_trader.py:2984`).

## 3. Dependencies & Anti-Cheat
- Standard library + existing project dependencies only. No new third-party libraries.
- No disabling, deleting, or weakening existing tests or assertions to force green.
- No linter suppressions added to pass checks.
