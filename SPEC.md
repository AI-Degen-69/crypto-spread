# SPEC — Issue #307: sweepable engine knobs (entry delay, leg chase, naked leg)

## Goal
The sweep engine (`scripts/sweep_backtest.py`) exposes three more registered
`BacktestParams` knobs as sweep options — `entry_delay`, `leg_chase`,
`naked_leg` — reading ladders/bounds from the `param_spec()` registry instead
of hand-coding values. No engine change; read-only consumption.

## Acceptance criteria (from the issue)
- `--only` accepts `entry_delay`, `leg_chase`, `naked_leg` in the sensitivity preset.
- Classification respected: `naked_leg_at_expiry` (structural) only behind
  `--include-structural` / explicit `--only`; `entry_delay` + `leg_chase`
  (tuning) swept by default.
- `random` preset samples the new axes within registered bounds; fixed `--seed`
  stays deterministic (legacy draws unchanged).
- Existing presets/outputs unchanged for prior axis names (labels, baseline).
- `python -m pytest tests/test_sweep_backtest.py -q` passes with new tests per axis.

## Scope
- `SENSITIVITY_AXES`, `generate_sensitivity_grid`, `generate_random_grid`,
  `--only` choices in `scripts/sweep_backtest.py`.
- Joint `grid` preset unchanged.
- `quote_shares` stays global via `--size` (open Q1 resolved: skip as axis).
- Dashboard/UI untouched; `--only` semantics unchanged (generic `axis=` prefix).

## Edge cases
- Single `entry_delay` axis mixes sec + pct values; the inactive field is
  cleared per row (sec rows set `entry_delay_pct=None`, pct rows set
  `entry_delay_sec=0.0`) because pct takes precedence when set
  (`backtest/engine.py:139-141`). Baseline (off) is the `Baseline` row.
- Ladders checked against registry bounds; `__post_init__` rejects
  out-of-range values (sec 0–3600, pct 0–1.0, naked_leg close|hold).
- `random` uses a second seeded RNG for new axes so legacy draws for a fixed
  seed are bit-identical.

## Out of scope
Dashboard/UI changes, new presets, `--only` semantics changes, engine
parameter definitions or thresholds, golden dataset changes.
