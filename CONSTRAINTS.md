# CONSTRAINTS — Issue #232

Binding while `feat/fresh-start-rule-232` is live. Per-issue working file
(`docs/git-workflow.md` §5); it stops binding the moment the issue merges.

## Zero Regressions

- Targeted gates, run before each commit — never the full suite locally
  (`AGENTS.md` §Testing):
  - `tests/test_backtest_engine.py`
  - `tests/test_live_trader.py`
  - `tests/test_stop_orders.py`
  - `tests/test_param_registry.py`
  - `tests/test_osc_dash_integration.py`
  - `tests/test_stop_loss_parity.py`
  - `tests/test_dead_zone_parity.py`
  - `tests/test_leg_chase_parity.py`
  - `tests/test_fresh_start_parity.py` (new parity suite for this issue)
- GitHub Actions CI on the PR is the merge gate for the full suite.

## Anti-Cheat

- No `skip`, `xfail`, deleted assertion, or loosened tolerance to make a test pass.
- Tests that asserted legacy knobs (`min_requote_remaining_sec`, `requote_round`, etc.)
  must be retargeted to the `fresh_start` rule, never silently dropped without replacement.
- Parity tests must assert exact tick-by-tick and round-by-round equivalence between backtest and live engines.

## Boundaries

- No new external dependencies.
- No new tuning knobs. `fresh_start` eliminates knobs, it does not add them.
- Deletion of legacy knobs: `min_requote_remaining_sec`, `_maybe_requote_after_merge`, `requote_round`, `reentry_stats`.
- The dead zone (rule §8) is the sole time gate for new entries or fresh starts.
- A market that becomes clean inside the dead zone MUST NOT re-enter.
- Both engines must behave identically when a market is clean: no memory of past rounds in the window.

