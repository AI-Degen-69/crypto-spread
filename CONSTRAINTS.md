# CONSTRAINTS.md — Quality Bar & Boundaries for Issue #455

## 1. Zero Regressions
- Acceptance gate (from the issue):
  ```powershell
  python -m pytest tests/test_sweep_backtest.py -q
  ```
- No other suite is touched by this change (sweep layer only). If engine-adjacent behavior shifts, also run `tests/test_backtest_engine.py -q`.
- No CI merge gate exists right now: `.github/workflows/tests.yml` was deleted per operator order during #451. Targeted local suites are the only gate; do not claim CI coverage.

## 2. Behavior Boundaries
- All changes stay in `scripts/sweep_backtest.py` (+ tests + one glossary line). Do NOT change `backtest/engine.py` (ADR-0002 fill rule, ADR-0001 parity), `scripts/run_overnight_sweep.py`, the live trader, the dashboard, `research/jungle-king/param_ranges.json`, `BacktestParams` defaults, or the parameter registry.
- Tune knob-class fields by default; structural limits only via `--include-structural` (ADR-0003).
- Do NOT gate or re-rank existing presets (sensitivity/grid/assets/random keep current behavior and output shape).
- Stdlib only — no third-party optimizer dependencies.
- Do NOT fix the `total_trades` undercount in `scripts/run_overnight_sweep.py` — record it as a noticed-but-not-touching row instead.
- Do NOT weaken any existing assertion in `tests/test_sweep_backtest.py`.

## 3. Dependencies & Anti-Cheat
- Standard library + existing project dependencies only.
- No disabling, deleting, or weakening existing tests or assertions to force green.
- No linter suppressions added to pass checks.
