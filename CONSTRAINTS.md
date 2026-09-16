# CONSTRAINTS — Issue #228

Binding while `fix/quote-range-228` is live. Per-issue working file
(`docs/git-workflow.md` §5); it stops binding the moment the issue merges.

## Zero regressions

- Targeted gates, run before each commit — never the full suite locally
  (`AGENTS.md` §Testing): `tests/test_backtest_engine.py`,
  `tests/test_live_trader.py`, `tests/test_osc_dash_integration.py`,
  `tests/test_param_registry.py`, `tests/test_backtest_cli.py`,
  `tests/test_sweep_backtest.py`, `tests/test_replay_shadow_check.py`,
  `tests/test_ev_sweep_lab.py`, `tests/test_entry_timeout.py`,
  `tests/test_entry_anchor_parity.py`, `tests/test_fill_rule_parity.py`,
  and the new parity file for this issue.
- `tests/test_patient_band_preset.py` is deleted with the preset it covers —
  the one file this issue is allowed to delete (operator decision 2026-09-16).
- GitHub Actions CI on the PR is the merge gate for the full suite.

## Anti-cheat

- No `skip`, `xfail`, deleted assertion, or loosened tolerance to make a test pass.
- A test that asserts the **band gate, the adverse-open gate, or drift-skip
  re-entry** is testing behaviour this issue deletes: it is removed together
  with the behaviour, and the removal is named in the commit body
  (`CONSTRAINTS.md` permits exactly this and nothing wider). A test that
  asserts the **timeout / late-start skip** stays — those gates belong to
  #229/#230, and weakening them here is forbidden.
- `test_sweep_backtest.py` / `test_selection_bias`-adjacent scans that
  enumerate `BacktestParams` fields must be retargeted to the new field set,
  never weakened to skip the check.

## Boundaries

- No new external dependencies.
- No behaviour change to the chase, the exits, the dead zone (none yet), the
  entry timeout, or the fill rule. If a diff hunk touches those paths, it is
  out of scope and reverted.
- `quote_range` defaults, validation and registry classification must match on
  every surface (API, CLI, dashboard, both engines, sims). One range, like the
  #227 one-cap rule.
- `params_hash()` will change for every config (field set changes). Accepted —
  old hashes are not comparable across this issue.
