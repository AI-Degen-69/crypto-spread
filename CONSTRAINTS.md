# CONSTRAINTS.md — Issue #204: Backtest pair_cost_gate must test resting pair cost

Binding while `fix/backtest-pair-cost-gate-204` is live. These are gates, not
suggestions: a violation blocks the PR.

## 1. Zero regressions

- `python -m pytest -q` stays green (929+ tests).
- Targeted test suites must pass:
  - `tests/test_backtest_engine.py`
  - `tests/test_backtest_cli.py`
  - `tests/test_param_registry.py`
  - `tests/test_osc_dash_integration.py`
  - `tests/test_ev_sweep_lab.py`
- Any existing test asserting that `pair_cost_gate <= 0` disables the gate must continue to pass.
- Tests intentionally asserting legacy touch behavior (e.g. `test_simulate_pair_cost_gate_blocks_wide_touch`) must be updated to assert the corrected resting pair cost behavior, with the change clearly documented.

## 2. Anti-cheat

- No `@pytest.mark.skip`, `xfail`, deleted assertions, or loosened tolerances.
- No `# noqa` / `# type: ignore` added to mask bugs.
- Do not artificially adjust preset constants to disguise divergence.

## 3. Scope discipline

- Files touched:
  - `backtest/engine.py` (gate logic and entered_windows tracking)
  - `tests/test_backtest_engine.py` (unit tests for resting pair cost gate)
  - `server/osc_dash.py` (diagnostics / metrics surfacing and UI clarity)
  - Station II planning files: `SPEC.md`, `CONSTRAINTS.md`, `tasks/plan.md`, `tasks/todo.md`.
- No new external dependencies.
- No premature modifications to other issues (#205, #207, #208, #209, #210, #211, #212, #213, #214).

## 4. Financial & Trading Safety

- Maker resting orders must never be evaluated against taker touch costs for entry eligibility.
- Leg chase capping must strictly preserve `entry_price + chased_opposite <= pair_cost_gate`.
- Clamped and boundary prices must never violate `[0.01, 0.99]`.
- Resting pair cost must be computed accurately from the actual active resting bids `resting_up + resting_down`.

## 5. Evidence

The PR must demonstrate:
- Failing (red) test showing wide touch asks blocking fills when they shouldn't under the old code.
- Passing (green) test verifying resting pair cost gating and successful fills with `pair_cost_gate = 0.98`.
- Backtest execution proof on real tick data showing non-zero fills under default settings.
- Full test suite green run.
