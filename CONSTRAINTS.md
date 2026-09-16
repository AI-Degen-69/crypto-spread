# CONSTRAINTS — Issue #227

Binding while `fix/max-pair-cost-chase-only-227` is live. Per-issue working file
(`docs/git-workflow.md` §5); it stops binding the moment the issue merges.

## Zero regressions

- Targeted gates, run before each commit — never the full suite locally
  (`AGENTS.md` §Testing): `tests/test_backtest_engine.py`,
  `tests/test_live_trader.py`, `tests/test_osc_dash_integration.py`,
  `tests/test_param_registry.py`, `tests/test_backtest_cli.py`,
  `tests/test_sweep_backtest.py`, `tests/test_replay_shadow_check.py`,
  `tests/test_ev_sweep_lab.py`, `tests/test_patient_band_preset.py`, and the two
  parity files.
- GitHub Actions CI on the PR is the merge gate for the full suite.

## Anti-cheat

- No `skip`, `xfail`, deleted assertion, or loosened tolerance to make a test pass.
- A test that asserts the **entry-side** pair-cost gate is testing behaviour this
  issue deletes: it is removed together with the behaviour, and the removal is named
  in the commit body. A test that asserts the **chase ceiling** is behaviour that
  survives: it is rewritten to the new field name and default, never deleted.
- `test_the_registry_does_not_claim_post_init_enforces_every_bound` uses
  `pair_cost_gate` as its worked example of a registry-bounded field with no
  `__post_init__` check. That stops being true here. Retarget it to another field
  that is still unvalidated (`queue_gate`); do not weaken or delete the assertion.
- No lint or type suppression added to route around this change.

## Architectural gates

- **One ceiling, one formula.** The floor-to-cents arithmetic
  `floor((cap - entry) * 100) / 100` appears in exactly one place and is called from
  both engines. It is copied six times today (two in `backtest/engine.py`, four in
  `strategy/live_trader.py`); the change may not leave two copies behind.
- **One name, one default, one range.** `max_pair_cost`, `0.99`, `[0.50, 1.00]`, on
  every surface and in both engines. No per-surface bounds override for this field,
  no alias for the old name, no back-compat shim.
- **No off switch.** No sentinel value, env var, toggle or `enable_*` flag that
  disables the cap. A structural limit that can be switched off is a tuning knob
  (ADR-0003).
- **The cap applies to the chase only.** No new entry-side test of pair cost, on our
  own resting quotes or on the book's asks, in either engine or either research
  simulator.
- **The entry price is latched, never recomputed.** The chase raises the *other*
  leg's quote; the filled leg's entry price is read, never written.
- No new external dependency. `requirements.txt` is unchanged.

## Naming

- `docs/glossary.md` wins. "live" is not a name for the engine; `mode="live"` means
  real money.
- "Gate" is reserved for entry admission (`queue_gate`). The surviving object is a
  **cap** / **ceiling** / **structural limit**; comments and labels say so.

## Numeric discipline

- Cent flooring keeps the existing `+ 1e-9` guard and `round(..., 2)`; no new epsilon
  constant, no bare float equality on prices.
- The range check in `__post_init__` follows the sibling checks in that method —
  `math.isfinite` first, then the bound, then a `ValueError` naming the field and the
  offending value.
