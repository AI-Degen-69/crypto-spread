# CONSTRAINTS — Issue #229

Binding while `feat/dead-zone-229` is live. Per-issue working file
(`docs/git-workflow.md` §5); it stops binding the moment the issue merges.

## Zero Regressions

- Targeted gates, run before each commit — never the full suite locally
  (`AGENTS.md` §Testing):
  - `tests/test_backtest_engine.py`
  - `tests/test_live_trader.py`
  - `tests/test_osc_dash_integration.py`
  - `tests/test_param_registry.py`
  - `tests/test_backtest_cli.py`
  - `tests/test_sweep_backtest.py`
  - `tests/test_replay_shadow_check.py`
  - `tests/test_entry_timeout.py`
  - `tests/test_entry_anchor_parity.py`
  - `tests/test_fill_rule_parity.py`
  - `tests/test_chase_cap_parity.py`
  - `tests/test_quote_range_parity.py`
  - `tests/test_dead_zone_parity.py` (new parity file for this issue)
- GitHub Actions CI on the PR is the merge gate for the full suite.

## Anti-Cheat

- No `skip`, `xfail`, deleted assertion, or loosened tolerance to make a test pass.
- Tests that explicitly asserted deleted timeout mechanisms (`entry_timeout_pct` 10% timeout,
  `naked_leg_timeout_pct` elapsed forward from fill, `max_start_elapsed_pct`, `max_start_delay_sec`,
  `stop_loss_enabled`) are converted to assert the corresponding dead zone behaviors or removed
  with the deleted behavior, and every removal/update is named in the commit message.
- `test_sweep_backtest.py` and parameter tests that enumerate `BacktestParams` fields must be
  retargeted to the new field set, never weakened to skip the check.

## Boundaries

- No new external dependencies.
- No behavior change to `quote_range` (#228), `max_pair_cost` (#227), `fill_rule` (#226), or
  `entry_anchor` (#225).
- Stop loss threshold cleanup (`exit_thresh_naked` deletion) is out of scope and belongs to #230.
- Leg chase escalation ladder is out of scope and belongs to #231.
- `dead_zone_val` and `dead_zone_unit` validation and defaults must match across all surfaces
  (both engines, API, dashboard, CLI, and sims).

