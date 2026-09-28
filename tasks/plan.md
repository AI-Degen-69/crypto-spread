# Plan — Issue #307: sweepable engine knobs

Branch: `i307/sweep-expose-more-registered-engine-parameters-as` | Issue: #307

Stack: Python (backtest sweep CLI + engine registry), pytest. Size: **Standard**
(2 files, one architectural decision: single mixed `entry_delay` axis).
Type: **Code** (`test-driven-development`, `incremental-implementation`).

## CodeRabbit intake (read once, scaffolding only)
- Adopted: three axes (`entry_delay`, `leg_chase`, `naked_leg`) with declared
  ladders; class from `param_class_for()`; append-after-legacy row order;
  generic `axis=` prefix filter; `random` second-RNG extension; test list.
- Rejected: `quote_shares` as an axis (open Q1 default + `--size` economics).
- Verified from code, not assumed: registry classes (sec/pct/leg-chase tuning,
  naked-leg structural), `__post_init__` bounds, pct-precedence rule,
  `filter_sensitivity_grid` generic prefix, seed-determinism tests.

## Open questions (resolved from code)
1. `quote_shares` as axis? NO — keep `--size` global. `base =
   BacktestParams(quote_shares=size)` and `size = max(5, ...)` scaling mean a
   second axis would fight the global; skip this round per issue default.
2. bool/str axes in `random` too? YES — include in both, sampling registered
   values uniformly, via a separate RNG stream preserving legacy draws.

## Interface contracts (locked before logic)
- New 1D labels: `entry_delay=15s|30s|60s`, `entry_delay=10%|20%`,
  `leg_chase=on|off`, `naked_leg=close|hold`. Baseline row covers the off-point.
- `SENSITIVITY_AXES += ("entry_delay", "leg_chase", "naked_leg")`; `--only`
  choices derive from it (no new special case; `dead_zone` stays the sole one).
- `filter_sensitivity_grid`: unchanged generic `only + "="` prefix path serves
  all three new axes.
- `include_structural` implication extends to `--only naked_leg`.
- `random` labels append sparse segments (e.g. `_ed=..._chase=..._naked=...`)
  only when a new-axis value differs from baseline... (exact suffix fixed in
  build; legacy label prefix untouched).

## Improvement proposal (adopted by default)
Derive tuning/structural gating for the new axes from `param_class_for()` at
grid-build time instead of hard-coding another `if include_structural` branch
per axis, so a future registry reclassification flows through automatically.
Evidence: `backtest/engine.py:331-333` — `def param_class_for(cls, name: str)`
`return cls.spec_for(name)["param_class"]`, with `naked_leg_at_expiry`
registered `"structural"` and `enable_leg_chase` / `entry_delay_*` registered
`"tuning"`. Drop only on explicit operator rejection.

## Tasks (risk-first; atomic vertical slices)
- T1 [Backend/Logic] (M) — sensitivity axes. Add `entry_delay` (single mixed
  axis, inactive field cleared per row), `leg_chase`, `naked_leg` (structural)
  rows after legacy rows; extend `SENSITIVITY_AXES`, `--only` choices,
  structural implication. Depends on: —. Verify:
  `python -m pytest tests/test_sweep_backtest.py -q -k "sensitivity or only"`.
- T2 [Backend/Logic] (M) — random sampler. Sample new axes within registry
  bounds via a second seeded RNG; append sparse label segments; legacy draws
  bit-identical per seed. Depends on: T1. Verify:
  `python -m pytest tests/test_sweep_backtest.py -q -k random`.
- T3 [Backend/Logic] (S) — tests. Exact labels, entry-delay exclusivity
  (sec-rows pct=None and vice versa), real bool/str types, structural opt-in
  (`naked_leg` absent by default, present via opt-in), seeded determinism,
  CLI `--only` end-to-end, legacy-prefix regression. Depends on: T1, T2.
  Verify: `python -m pytest tests/test_sweep_backtest.py -q`.

Checkpoints: after T1 (new 1D axes listable); after T2 (seeded random stable).
