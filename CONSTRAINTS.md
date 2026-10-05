# CONSTRAINTS.md — Quality Bar & Boundaries for Issue #449

## 1. Zero Regressions
- Modified and adjacent paths must pass targeted testing:
  ```powershell
  python -m pytest tests/test_live_trader.py -q
  python -m pytest tests/test_stop_orders.py -q
  ```
- Full test suite is strictly reserved for GitHub Actions CI (never run `python -m pytest` locally).

## 2. Behavior Boundaries
- Do NOT change the live-mode cancel branch, its call arguments, call order, or ignored return values (`strategy/live_trader.py:5122-5127`).
- Do NOT change `cancel_live_order`, `_cancel_succeeded`, `cancel_all_orders`, the CLOB client, the backtest engine, or dashboard rendering.
- Do NOT change the early return on failed stop cancellation, the caller metadata order (`_update_market_strategy`), or `_cancel_stop_order` semantics (paper STAGED stops already clear locally).
- Paper branch must never call `cancel_live_order` or any CLOB client method.
- If existing tests at `tests/test_live_trader.py:1571-1589` or `2690-2716` fail because of the `None` resting-price reset, fix the paper reset scope — never delete or weaken the tests.

## 3. Dependencies & Anti-Cheat
- Standard library + existing project dependencies only. No new third-party libraries.
- No disabling, deleting, or weakening existing tests or assertions to force green.
- No linter suppressions added to pass checks.
