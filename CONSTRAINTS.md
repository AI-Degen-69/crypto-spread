# CONSTRAINTS.md — Quality Guardrails for Issue #335

## 1. Zero Regressions
- All targeted tests in `tests/test_osc_dash_integration.py` must pass.
- `tests/test_theme_tokens.py` must pass.
- Existing tests (`test_sweep_base_point_equals_a_backtest_with_the_same_settings`, `test_sweep_axis_moves_only_its_own_parameter`, `test_sweep_button_sends_every_control_to_both_endpoints`, etc.) must be preserved and remain green.

## 2. Server Logic & API Invariants
- Do not alter `SWEEP_AXES` values, types, or structure in `server/osc_dash.py`.
- Do not alter server-side simulation logic, worker concurrency limits, or `/api/backtest/sweep` JSON response schema.
- Preserve the 6-key override semantics of `exit_stop` in `_sweep_params_for_value`.

## 3. UI Invariants & Non-Deceptive Copy
- The override note must clearly distinguish between *submitted values* from the form and the tested sweep points. Do not claim submitted values are "server-confirmed effective values".
- Preserve `btSweepMeta` status messages during sweep execution (`waiting…`, `sweeping…`, elapsed time, `best overall`, `best market`).
- Ensure no layout shift or breaking CSS/JS in the Sweep Visual card.

## 4. Test Discipline & Anti-Cheat
- Non-vacuous testing: all backtest and sweep fixture assertions must guard `pairs > 0` and `windows > 0`.
- No skipping tests, deleting assertions, or suppressing linter/pytest warnings.
- Node.js tests in `test_osc_dash_integration.py` must gracefully handle environments without Node installed via `pytest.skip`.
