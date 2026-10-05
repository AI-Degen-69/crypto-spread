# CONSTRAINTS.md — Quality Bar & Boundaries for Issue #445

## 1. Zero Regressions
- Modified and adjacent paths must pass targeted testing:
  ```powershell
  python -m pytest tests/test_backtest_templates.py -q
  ```
- Full test suite is strictly reserved for GitHub Actions CI (never run `python -m pytest` locally).

## 2. Template-System Boundaries
- Do NOT modify `backtest/templates.py` validation rules, `BacktestParams` defaults,
  the fill rules, `strategy/live_trader.py`, or the load-validation logic
  (`server/osc_dash.py:4291-4330`). The preset is data + a copy-if-missing hook only.
- Seeding must never overwrite an existing `run/backtest_templates/<name>.json`.
- `run/` stays gitignored: no seed or runtime file may be committed under `run/`.

## 3. Dependencies & Anti-Cheat
- Standard library + existing project dependencies only. No new third-party libraries.
- No disabling or deleting existing tests; no weakened assertions to force green.
- New seed content requires tests that rebuild `params_hash` from `request_args`
  and assert it matches the stored hash (drift guard, not a hardcoded echo).
