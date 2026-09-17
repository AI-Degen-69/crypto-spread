# CONSTRAINTS — Issue #231

Binding while `feat/leg-chase-ladder-231` is live. Per-issue working file
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
  - `tests/test_leg_chase_parity.py` (new parity suite for this issue)
- GitHub Actions CI on the PR is the merge gate for the full suite.

## Anti-Cheat

- No `skip`, `xfail`, deleted assertion, or loosened tolerance to make a test pass.
- Tests that asserted instant-fire leg chase (e.g. quote raised on the first tick after fill without market move)
  must be retargeted to the escalation ladder rules, never deleted.
- Parity tests must assert exact numerical equality (within cent precision) on both engines.

## Boundaries

- No new external dependencies.
- No new tuning knobs. The escalation ladder is strictly defined by `max_pair_cost` (§4) and the dead zone (§8).
- Vestigial knobs in `LiveTraderEngine` (`chase_step_pct`, `chase_max_steps`, `chase_interval_sec`) that belonged
  to the unadopted stepped chase must be cleaned up or safely phased out.
- The chase ceiling can only increase, never decrease.
- The chase ceiling floored to whole cents (`floor(...) / 100`) so `chased_bid + entry <= max_pair_cost` is never breached.
- Fresh start / multi-round window loop is out of scope and belongs to #232.
