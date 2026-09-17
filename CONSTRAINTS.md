# CONSTRAINTS — Issue #214

Binding while `feat/parity-harness-214` is live. Per-issue working file
(`docs/git-workflow.md` §5); it stops binding the moment the issue merges.

## Zero Regressions

- Targeted gates, run before each commit — never the full suite locally
  (`AGENTS.md` §Testing):
  - `tests/test_engine_parity.py` (new parity suite for this issue, must run in < 5.0s)
  - `tests/test_backtest_engine.py`
  - `tests/test_live_trader.py`
  - `tests/test_fresh_start_parity.py`
  - `tests/test_leg_chase_parity.py`
  - `tests/test_stop_loss_parity.py`
- GitHub Actions CI on the PR is the merge gate for the full suite.

## Anti-Cheat

- No `skip`, `xfail`, deleted assertion, or loosened tolerance to make tests pass.
- Any legitimate divergence discovered by the harness must be declared as a known exception or strict xfail with an issue number, not swept under the rug.
- Parity tests must compare actual execution state surfaces between live and backtest engines.

## Performance Threshold

- `tests/test_engine_parity.py` execution must complete in under 5.0 seconds total on synthetic test snaps.

## Boundaries

- No new external dependencies (pure Python 3 stdlib + pytest).
- Harness-only scope: no modifications to production trading logic or decision rules unless adapting harness parameters.
- Reusable harness design: must easily accommodate future scenarios added by other issues.
