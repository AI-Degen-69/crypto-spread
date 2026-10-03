# CONSTRAINTS.md — Issue #410 (sweep-visual bars + queue ticks)

Locked by Station II (`ii-plan-issue #410`). Guardrails for Stations III–V.
Open questions resolved from code + CodeRabbit plan (see `tasks/plan.md`);
nothing left to ask the operator.

## Zero regressions
- `python -m pytest tests/test_osc_dash_integration.py -q -k "sweep"` must pass
  while iterating; full file + `tests/test_theme_tokens.py -q` before handoff.
- Uniform axes (`offset`, `late_entry`, categorical) render byte-identical tick
  count/label content — pinned by new tests, not eyeballing.
- Small per-market cards keep their #390 planner untouched; the width plugin
  must be a proven no-op on uniform axes.

## Scope freeze
- `sweepChartOptions` x-axis + bar datasets only. No y-axis, theme/colour,
  sweep engine, backtest maths, or data changes. No new dependencies
  (Chart.js public plugin hooks only — no private controller overrides).

## Anti-cheat
- Forbid skipping/disabling tests, deleting assertions, suppressing linters.
- The pre-existing `test_sweep_categorical_axis_rendering_node` failure is
  NOT to be "fixed" by weakening it — if the diff touches its path, prove the
  failure signature is unchanged vs base.

## Out of scope
- Y axis, theme logic, sweep engine/maths, underlying data, #174, #396.
