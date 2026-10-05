# CONSTRAINTS.md — Quality Bar & Boundaries for Issue #451

## 1. Zero Regressions
- Acceptance gate (from the issue):
  ```powershell
  python -m pytest tests/test_live_trader.py -q
  ```
- Targeted neighbors:
  ```powershell
  python -m pytest tests/test_dead_zone_parity.py -q
  python -m pytest tests/test_stop_orders.py -q
  ```
- Full test suite is strictly reserved for GitHub Actions CI (never run `python -m pytest` locally).

## 2. Behavior Boundaries
- Change exactly one predicate: the dead-zone expiry block condition (`strategy/live_trader.py:4907`). Add `not mstate.exit_taken`, mirroring the stop triggers at 4947–4949 and 4973–4975.
- Do NOT change `_execute_stop_exit`, fill-flag semantics (`filled_up`/`filled_down` stay set after exit), stop thresholds, or trigger-note wording (note wording belongs to #452).
- Do NOT touch the live-mode cancel branch, `cancel_live_order`, the CLOB client, the backtest engine, or dashboard rendering.
- Do NOT fix the malformed `_resolve_exit_bid(slug, mstate, naked_side)` fallback call at line 4925 (signature is `(self, mstate, side)` returning a tuple) — report it as a separate issue instead.
- First expiry exit must fire and record exactly as before (same price, shares, PnL, note).

## 3. Dependencies & Anti-Cheat
- Standard library + existing project dependencies only. No new third-party libraries.
- No disabling, deleting, or weakening existing tests or assertions to force green.
- No linter suppressions added to pass checks.
