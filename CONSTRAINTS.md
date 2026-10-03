# CONSTRAINTS.md — Issue #377 (window-detail per-pair Time/Duration)

Locked by Station II (`ii-plan-issue #377`). Standing guardrails for Stations III–V.
Genuinely unresolvable questions: none — both issue open questions were resolved
from code (see `tasks/plan.md` §"Resolved open questions").

## Zero regressions
- `python -m pytest tests/test_backtest_engine.py -q` must pass.
- `python -m pytest tests/test_osc_dash_integration.py -q` must pass (or the
  targeted `-k` subset covering `trades_sample`/window-detail while iterating;
  full file before Station IV handoff).
- New behavior requires new tests: two-pair window timing, stop entry-to-exit
  timing, economics-unchanged assertion (pnl/fees/counts identical with and
  without the new fields).

## Economics freeze
- No change to any fill, merge, stop, settlement, fee, or counting decision in
  `backtest/engine.py`. New code only *records* values the branches already
  compute (`cur_ts`, `elapsed`, booked prices). If a P&L number moves, the task
  is wrong — revert, do not "fix" the expectation.

## Surface freeze
- `tests/test_engine_parity.py::SURFACE_KEYS` is untouched. The change is
  backtest-only telemetry; parity scope does not grow.
- `strategy/live_trader.py`, `research/sweeps/*`, `backtest/index.py`: no
  modifications. Verified: tick-file cids and `BacktestParams.params_hash`
  do not derive from the extended records, so no index/golden rotation.

## Renderer compatibility
- Old payloads (no `pairs` / no `stops` / records without timing keys) must
  render exactly as today: `timeStr1` on the first row, `—` elsewhere.
  New fields are read defensively (`rec.x != null`), never assumed.

## Anti-cheat
- Forbid skipping/disabling tests, deleting assertions, or suppressing linters
  to make red green. Forbid new external dependencies.

## Out of scope (enforced, not deferred)
- Issue #376 cost-vs-legs arithmetic. Engine fill/timing logic beyond exposing
  timestamps. Y-axis/theme/sweep-visual work (#410). Any live-trading change.
