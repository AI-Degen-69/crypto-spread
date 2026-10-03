# CONSTRAINTS.md — Issue #419 (sub-dollar price params → cents inputs/displays)

Locked by Station II (`ii-plan-issue #419`). Guardrails for Stations III–V.
Open questions resolved from code + CodeRabbit plan (see `tasks/plan.md`);
nothing left to ask the operator.

## Zero regressions
- `python -m pytest tests/test_osc_dash_integration.py tests/test_param_registry.py -q`
  must pass before handoff (targeted suites only locally; full suite is CI's job).
- `btControlQuery()` strings stay dollar-identical to today (`offset=0.05` when
  `btOffset` holds `5`) — pinned by a new round-trip test, not eyeballing.
- `/api/live/config` server cents-heuristic tests stay unchanged and green.
- Jungle King baselines/candidates stay dollar values; only label selection changes.

## Scope freeze
- Converted surfaces only: backtest + cockpit price inputs, sweep held rows,
  price-axis tick labels, quote-range display, registry `display` metadata,
  `/api/params/spec` pass-through, `_jk_label()` canonical-label preference.
- No engine internals (`_clamp_to_spec`, `_build_backtest_params`,
  `_sweep_params_for_value`, fields, fill rule), no CLI flags, no venue
  constants, no `research/jungle-king/` data, no non-price inputs, no market-
  result displays. No new external dependencies (shared JS helpers only).

## Performance thresholds
- Conversion helpers are pure arithmetic (no I/O, no per-tick cost — they run
  only on input read/write and card render). No measurable latency budget change.

## Anti-cheat
- Forbid skipping/disabling tests, deleting assertions, suppressing linters.
- The held-card pins (`0.020`/`0.07`/`0.030`) and registry label pins MUST be
  updated to the new cents convention with equal-or-stronger assertions — never
  deleted or weakened to pass.
- `CONSTRAINTS.md` for issue #410 does not apply to this ticket.

## Out of scope
- Engine/API/CLI dollar contract, venue constants, non-price inputs, Jungle King
  manifest values, market-result displays, theme, y-axes, sweep engine/maths.
