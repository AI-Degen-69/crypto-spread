# CONSTRAINTS.md — Issue #160: Fix paper sim settles mark naked legs to 0.50

## Quality Gates & Hard Thresholds

### 1. Test Suite Integrity
- **Zero Regressions**: 100% pass rate on `python -m pytest tests/test_live_trader.py -q` (123+ tests) and the full test suite (`python -m pytest -q`).
- **New Behavior Tests**: Explicit tests for:
  - Mark-to-book execution with true bid values.
  - Complement bid calculation (`1.0 - opposite_ask`) when direct bid is empty.
  - Latched bid persistence when book is temporarily wiped at rollover.
  - Anti-fallback verification: bid-less / book-less fixture fails loud or raises without silently defaulting to 0.50.
- **Anti-Cheat**: Strictly forbid disabling tests, deleting assertions, or suppressing linter checks.

### 2. Settle & Math Constraints
- **Zero Silent 0.50 Marks**: No settle calculation can silently substitute `0.50` when books exist. If no book or latched quote exists, the engine must raise an exception or log a CRITICAL failure with an explicit unmarkable status.
- **Bounds Invariant**: Any calculated settle mark must strictly satisfy `0.0001 <= mark <= 0.9999`.
- **Auditability**: Every `TradeEvent` with action `WINDOW_SETTLE` must log the method used to establish the exit mark (`direct_bid`, `complement_ask`, `latched_bid`, or `dislocated_error`).

### 3. Dependencies & Code Boundaries
- **No External Dependencies**: Stdlib and existing packages (`fastapi`, `uvicorn`, `requests`, `pytest`) only.
- **No Production Breakage**: Code edits limited to `strategy/live_trader.py`, `scripts/shadow_ev_pilot.py`, and test files.
- **Replay Cross-Check**: Re-running the scoped comparison driver must confirm settle distortion drops from 100% to under 20% of scoped paper P&L.
