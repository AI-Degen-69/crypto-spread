# CONSTRAINTS — Issue #307

- **Zero regressions:** `python -m pytest tests/test_sweep_backtest.py -q` must
  pass. Existing axis labels, `Baseline` behavior, and `--only` semantics stay
  byte-identical. Full suite stays with CI on push (never run locally).
- **Determinism:** same `--seed` produces identical legacy draws as before the
  change; new-axis sampling uses a separate RNG stream.
- **Registry is read-only:** no edits to `backtest/engine.py`, `_PARAM_GROUPS`,
  defaults, or `__post_init__` validation. Ladders must sit inside
  `param_spec()` bounds.
- **Structural rule (#233):** tuning axes on by default; structural axes
  (`naked_leg`) only via `--include-structural` or explicit `--only`.
- **Anti-cheat:** no skipped/disabled tests, no deleted assertions, no linter
  suppressions to reach green.
- **Dependencies:** no new external dependencies.
- **Glossary:** use agreed names (`sweep`, `sensitivity`, `tuning` vs
  `structural`) per `docs/glossary.md`.
