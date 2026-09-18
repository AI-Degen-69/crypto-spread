# Task Plan — Issue #213: Quoting around current price across tradeable range & replacing entry_band veto

**Size tier:** Standard — Multi-module verification across `strategy/live_trader.py`, `backtest/engine.py`, `server/osc_dash.py`, and test suites.
**Task type:** Code / Verification.

## Context
- Issue #213 requested removing the hard 0.50-anchored `entry_band` veto (`abs(mid - 0.50) > band`) that permanently latched out windows, allowing quoting around the current mid across tradeable price ranges.
- This was addressed in architecture rule 6 (`docs/engine-decision-rules.md` §6) and implemented via Issue #228 / PR #241 (`0e8eebb`).
- PR #241 merged to `master` with commit message `Closes #213`, but GitHub did not close Issue #213 automatically.
- This plan validates all acceptance criteria of #213, verifies zero regressions across all parity suites, and establishes formal resolution documentation.

## Tasks

- [x] **TASK-1 [Backend/Logic]**: Verify live engine decision loop compliance in `strategy/live_trader.py`
  - Target files: `strategy/live_trader.py`
  - Build/Verify: Verify that `quote_range` (default `(0.10, 0.90)`) is evaluated per-tick on `mstate.mid`, `entry_band` permanent latch is gone, and resting quotes anchor to `mstate.mid - offset`.
  - Helper skill: `test-driven-development`
  - Verify: Run `python -m pytest tests/test_live_trader.py -q`.

- [x] **TASK-2 [Backend/Logic]**: Verify backtest engine parity compliance in `backtest/engine.py`
  - Target files: `backtest/engine.py`
  - Build/Verify: Verify `BacktestParams.quote_range` logic in `_simulate_window`: mid outside range holds placement for that tick only without setting latches; mid re-entering quotes normally.
  - Helper skill: `test-driven-development`
  - Verify: Run `python -m pytest tests/test_backtest_engine.py -q`.

- [x] **TASK-3 [QA/Parity]**: Run targeted parity and dashboard integration test suites proving behavioral parity and UI controls
  - Target files: `tests/test_quote_range_parity.py`, `tests/test_engine_parity.py`, `tests/test_osc_dash_integration.py`
  - Build/Verify: Execute all parity scenarios (outside-at-open, leave-and-return, boundary mids 0.10/0.90, resting quote stability, custom range limits) and verify dashboard `quote_lo`/`quote_hi` controls.
  - Helper skill: `verification-loop`
  - Verify: `python -m pytest tests/test_quote_range_parity.py tests/test_engine_parity.py tests/test_osc_dash_integration.py -q`.

- [x] **TASK-4 [Documentation & Closure]**: Generate verification evidence artifact and prepare issue closure
  - Target files: `docs/issues/213-verification-entry-band-quote-range.md` (or artifact)
  - Build/Verify: Document test results, link PR #241 and Commit `0e8eebb`, confirm all acceptance criteria met, and provide closure instructions.
  - Helper skill: `documentation-and-adrs`
  - Verify: Validate artifact completeness against SPEC.md.

## Verification Matrix
| Task | Method |
|---|---|
| TASK-1 | `python -m pytest tests/test_live_trader.py -q` |
| TASK-2 | `python -m pytest tests/test_backtest_engine.py -q` |
| TASK-3 | `python -m pytest tests/test_quote_range_parity.py tests/test_engine_parity.py tests/test_osc_dash_integration.py -q` |
| TASK-4 | Review verification findings against SPEC.md & GitHub PR #241 |


