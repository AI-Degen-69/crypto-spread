# CONSTRAINTS.md — Issue #308 (locked for this issue only)

Per-issue working file. Regenerate for the next issue; never carry over.

## Zero regressions

- Targeted suites must pass after every task:
  `tests/test_backtest_engine.py`, `tests/test_backtest_cli.py`,
  `tests/test_osc_dash_integration.py`, plus the new
  `tests/test_backtest_selection.py`.
- Full suite (`python -m pytest -q`) stays with CI on push. Never run locally.
- New behavior (selection, per-duration, coverage, CLI flags, API params)
  requires tests. No untested branch.

## Anti-cheat

- No skipping/disabling tests, no deleted assertions, no linter suppressions.
- No silent empty results: unknown tokens/durations raise, they never return
  zero windows quietly.
- `replay()` simulation path untouched: same windows in → same per-window out
  for empty selection.

## Contracts that must not move

- `BacktestParams` fields and `params_hash()` output unchanged (selection is
  not a strategy param).
- `aggregate.per_series` and `aggregate.overall` shapes unchanged.
- Tick/cid grouping (`group_by_cid`) untouched; selection filters raw ticks
  before grouping so whole windows are kept or dropped.
- Tick dict shape untouched; dashboard worker response keeps its existing row
  conventions, extended only.

## Performance

- Golden full run (4,910 windows) must complete in the same order of time as
  today — selection is a linear pre-filter, no per-tick API/IO.
- No new external dependencies. Standard library + repo code only.

## Language

- Glossary (`docs/glossary.md`) wins on names: "backtest engine", "window",
  "tick". No new invented terms in comments or commit messages.
