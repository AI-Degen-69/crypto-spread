# CONSTRAINTS: Issue #433 Quality Guardrails

## 1. Zero Regressions on Targeted Suites
The following test suites MUST pass cleanly in < 3 seconds total:
- `python -m pytest tests/test_backtest_engine.py -q`
- `python -m pytest tests/test_param_registry.py -q`
- `python -m pytest tests/test_sweep_backtest.py -q`

## 2. Parity & Baseline Preservation
- `replay(dataset, BacktestParams())` must be byte-for-byte and numeric-identical before and after this change.
- Default `pair_cost_gate = 0.0` must bypass the gate completely with zero overhead on hot loop ticks.

## 3. Anti-Cheat Rules
- No test skipping or deletion (`@pytest.mark.skip`, `xfail`).
- No assertion stripping or weakening.
- No linter suppression comments.
- Do not modify `test_naked_thr_checked_on_gate_failure` invariant check in `tests/test_backtest_engine.py`.

## 4. Architecture & Interface Constraints
- `BacktestParams` is a frozen dataclass; all fields must remain compatible with hash and serialization contracts.
- Registry integrity: every field in `BacktestParams` must exist in `_PARAM_GROUPS` exactly once with valid `param_class` and 7-tuple structure.
- No new third-party dependencies.
