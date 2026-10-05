# CONSTRAINTS.md — Quality Bar & Boundaries for Issue #456

## 1. Zero Regressions
- Acceptance gate (from the issue):
  ```powershell
  python -m pytest tests/test_live_trader.py -q
  ```
- Parity + backtest neighbors (both engines change, parity suite must hold):
  ```powershell
  python -m pytest tests/test_quote_range_parity.py -q
  python -m pytest tests/test_backtest_engine.py -q
  ```
- No CI merge gate exists right now: `.github/workflows/tests.yml` was deleted per operator order during #451. Targeted local suites are the only gate; do not claim CI coverage.

## 2. Behavior Boundaries
- Enforce at latch time only: after computing both legs in `strategy/live_trader.py:4250-4258` and `backtest/engine.py:1139-1151`, if either leg falls outside `quote_range`, latch nothing (pair rejected as a unit, retry next tick). Boundary inclusive (legs exactly at lo/hi quote, mirroring the mid gates).
- Check the computed (0.01/0.99-clamped) leg values, not the raw mid-minus-offset.
- Do NOT change the mid gates, stop-price flooring, thresholds, fill rules, chase logic, cancellation, dashboard, or preset values.
- Live and backtest engines must move together — no parity drift (the parity suite pins this).
- Do NOT weaken any existing assertion (quote_range suites pin config round-trip, validation, and parity).

## 3. Dependencies & Anti-Cheat
- Standard library + existing project dependencies only. No new third-party libraries.
- No disabling, deleting, or weakening existing tests or assertions to force green.
- No linter suppressions added to pass checks.
