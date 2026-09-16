# CONSTRAINTS — Issue #226

Binding while `fix/one-fill-rule-226` is live. Per-issue working file
(`docs/git-workflow.md` §5); it stops binding the moment the issue merges.

## Zero regressions

- Targeted gates, run before each commit — never the full suite locally
  (`AGENTS.md` §Testing): `tests/test_backtest_engine.py`,
  `tests/test_live_trader.py`, `tests/test_osc_dash_integration.py`,
  `tests/test_param_registry.py`, `tests/test_backtest_cli.py`,
  `tests/test_sweep_backtest.py`, `tests/test_replay_shadow_check.py`,
  `tests/test_ev_sweep_lab.py`, `tests/test_selection_bias.py`,
  `tests/test_fill_telemetry.py`, `tests/test_clob_ws_collector.py`,
  `tests/test_docstrings.py`.
- GitHub Actions CI on push is the sole full-suite merge gate.
- Every behavioural change lands with a test that fails before it.

## Anti-cheat

- No `skip`, `xfail`, deleted assertion, or loosened tolerance to make a test
  pass. A test that now asserts the old fill rule is **rewritten to the new rule**,
  with its docstring saying what changed — not deleted.
- No lint or type suppression added to route around this change.
- A test may not be deleted merely because it exercised `fill_model`. Only the
  model-comparison tests (`tape` vs `book` vs `both` vs `cross` as alternatives)
  disappear; everything they incidentally covered keeps a test under the one rule.

## Architectural gates

- **One fill decision, one place.** The tape-print and fully-through checks and
  the maker/taker price choice live in a single shared helper that both
  `backtest/engine.py` and `strategy/live_trader.py` call. No second copy of the
  predicate anywhere, research simulators included.
- **No new knob replaces the old one.** No `fill_mode`, no `use_taker_price`, no
  env var, no per-call override. The rule is not configurable.
- **The entry price is latched, never recomputed.** Once a leg fills, nothing may
  rewrite its entry price — the chase raises the *other* leg's quote only.
- No new external dependency. `requirements.txt` is unchanged.

## Naming

- `docs/glossary.md` wins. "live" is not a name for the engine; `mode="live"` means
  real money. New comments say "maker fill" / "taker fill", not "book fill".

## Numeric discipline

- Price comparisons use the existing `± tick_size + 1e-6` idiom. No bare float
  equality, no new epsilon constant.
- Fees go through `_taker_fee(price, rate)`; no fee arithmetic inlined at a call
  site.
