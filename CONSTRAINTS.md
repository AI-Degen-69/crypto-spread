# CONSTRAINTS — Issue #230

Binding while `feat/one-stop-threshold-230` is live. Per-issue working file
(`docs/git-workflow.md` §5); it stops binding the moment the issue merges.

## Zero Regressions

- Targeted gates, run before each commit — never the full suite locally
  (`AGENTS.md` §Testing):
  - `tests/test_backtest_engine.py`
  - `tests/test_live_trader.py`
  - `tests/test_stop_orders.py`
  - `tests/test_param_registry.py`
  - `tests/test_osc_dash_integration.py`
  - `tests/test_backtest_cli.py`
  - `tests/test_sweep_backtest.py`
  - `tests/test_replay_shadow_check.py`
  - `tests/test_dead_zone_parity.py`
  - `tests/test_stop_loss_parity.py` (new parity suite for this issue)
- GitHub Actions CI on the PR is the merge gate for the full suite.

## Anti-Cheat

- No `skip`, `xfail`, deleted assertion, or loosened tolerance to make a test pass.
- Tests that explicitly asserted `exit_thresh_naked` (e.g. tighter stop for naked legs vs paired stop)
  are updated or removed because paired stop never existed in reality (a completed pair cannot lose).
  Every removal/update is explicitly named and rationalized in the commit message.
- Parameter tests and sweep registries that enumerate `BacktestParams` fields must be retargeted
  to the new field set (without `exit_thresh_naked`), never weakened.

## Boundaries

- No new external dependencies.
- No behavior change to `dead_zone` (#229), `quote_range` (#228), `max_pair_cost` (#227),
  `fill_rule` (#226), or `entry_anchor` (#225).
- Leg chase escalation ladder is out of scope and belongs to #231.
- Fresh start / multi-round window loop is out of scope and belongs to #232.
- Stop threshold validation and defaults must remain consistent across both engines, API, and dashboard.
