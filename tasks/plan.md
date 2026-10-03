# Plan — Issue #419: Unify sub-dollar price params to whole-number cents inputs and displays

Branch: i419/unify-sub-dollar-price-params-to-whole-number-cent | Issue: #419
Stack: Python dashboard serving embedded JS (`server/osc_dash.py`) · registry
`backtest/engine.py:342-374` · Node-harness tests (`tests/test_osc_dash_integration.py`) + `tests/test_param_registry.py`
Size: **Standard** — 2 source files + 2 test files, one architectural decision
(UI-edge conversion; engine/API/CLI dollar contract untouched).
Task type: **Code, Design/UI, UX / Copy** (conversion logic + input rendering + label wording)

## Issue in one line
The same cent-denominated knobs are shown and entered in mixed decimal-dollar
formats (`0.105` vs `0.05` vs `0.020`), forcing the operator to count zeros —
every such field should accept and display whole-number cents (`2`, `5`, `10.5c`).

## Embedded spec
See `SPEC.md` (Standard-work spec: goal, locked UI-edge boundary, 5 converted
knobs, out-of-scope list, 5 acceptance checkboxes, edge cases). The 4 open
questions are resolved from code + defaults — nothing to ask the operator.

## CodeRabbit intake (read once; echo ignored)
- Adopted: UI-edge conversion (dollar contract, hashes, templates unchanged);
  registry-owned presentation metadata (`display` block) as the single source;
  shared `centsToDollars`/`dollarsToCents`/`formatCents` helpers + one cents
  validator; step `0.1`, ASCII `c`, cents bounds = dollar bounds × 100;
  remove client whole-number heuristic, keep server `normalize_*` validators;
  `_jk_label()` canonical-label preference; round-trip + validator test list.
- Rejected (over-split merged): the 4-phase/13-subtask layout is collapsed into
  4 atomic tasks below; no new files or abstractions — helpers live next to the
  existing inline-JS readers, metadata next to `_PARAM_GROUPS`.
- Verified seams (`[UNVERIFIED]`: 0 — all spot-checked against the live tree):
  `backtest/engine.py` `_PARAM_GROUPS` (342-374), `param_spec()` (454),
  `bounds_for()` (504); `server/osc_dash.py` `api_params_spec()` (1140,
  `**v` pass-through confirmed), `_jk_label()` (1247), backtest inputs
  (5776-5842), cockpit inputs (6224-6289), `btControlValues()` (8208),
  `applyParamSpec()` (7566); tests `test_osc_dash_integration.py:2735`
  (`0.020` pin), `:4782` (spec endpoint), `test_param_registry.py:52-115`.

## Resolved open questions (from code/plan, not asked)
- Boundary: UI edge only — sweep drivers, templates, `/api/backtest` speak
  dollars; `params_hash()` and saved templates must not shift.
- Sub-cent: one decimal of a cent (`0.105` ↔ `10.5`), venue tick is `0.001`.
- Range limits: `quote_range` + `max_pair_cost` convert too (10/90/99 `c`).
- Labels: ASCII `c`; canonical registry `unit` stays `$` (see proposal below).

## Improvement proposal (adopted by default — simplification/edge-case hardening)
Keep the canonical registry `unit="$"` and add a separate `display` block,
instead of the issue's default of updating the registry unit in place.
Evidence, verbatim — the issue says: "Default: `c` with integer inputs,
registry unit updated." But `param_spec()` emits `"unit": unit` straight from
the 7-tuples into `/api/params/spec`, which templates, sweep drivers, and
`params_hash()` consume as the dollar contract — flipping `unit` risks
re-interpreting stored dollar values as cents. The `display` split (CodeRabbit
Design Choice 2) keeps tuples 7-long and bounds/dollar defaults byte-identical
while giving every tab one cents source. Adopted; recorded so it does not resurface.

## Interfaces (locked)
- Registry `display` block per converted field:
  `{unit: "c", scale: 100, step: 0.1, canonical_label: "<current $ label>"}` +
  dollar `entry_bounds: (0.001, 0.50)` for `exit_thresh_by_slug` only.
  `param_spec()` emits `display` or `None`; tuple length, `unit`, `bounds`,
  `default`, `bounds_for()` unchanged. Venue constants excluded.
- JS helpers (top-level in `server/osc_dash.py`, extractable for node tests):
  `centsToDollars(c)` → dollars (÷100, 3-decimal normalize);
  `dollarsToCents(d)` → cents (×100, 1-decimal iff within 1e-9 else ≤3 decimals);
  `formatCents(d)` → trimmed number + `c`; `validateCentsInput(el)` → bool
  (rejects empty/non-finite/out-of-`min`/`max`/>1-decimal, toggles `input-invalid`).
- `btControlQuery()` output stays dollar-identical (`offset=0.05` for input `5`).
- `LiveConfigPayload`, API `normalize_*` validators, `_clamp_to_spec()`,
  `_build_backtest_params()`, `_sweep_params_for_value()` unchanged.

## Dependency graph & tasks
T1 (registry contract — riskiest, everything reads it) → T2 + T3 (independent of
each other, both pure/convert-then-render) → T4 (test lock-in). T2 is the largest.

- [x] T1 [Backend/Logic] M — `backtest/engine.py`: presentation mapping for the
  5 fields + `param_spec()` `display` emission; relabel 5 registry labels to
  `(c)`; `server/osc_dash.py`: `_jk_label()` prefers `display.canonical_label`.
  Verify: `tests/test_param_registry.py -q` + spec-endpoint tests.
  Depends on: none.
- [ ] T2 [Design/UI] L — `server/osc_dash.py`: shared helpers + validator;
  backtest HTML (~5776-5842) + cockpit HTML (~6224-6289) cents values/steps/
  min-max (offset `0.1–49`, exits/reversal `0.1–50`, quote `0–100`, pair
  `50–100`); `applyParamSpec()` display wiring + exit `entry_bounds` +
  registry unit elements; `btControlValues()`/`applyBacktestTemplate()`/
  `resetBtParams()`/preview conversion; `validateBacktestInputs()` + cockpit
  validator swap + `renderCockpitUI()` hydration; drop client heuristic.
  Verify: node-harness round-trip (`5` → `offset=0.05`) + targeted suites.
  Depends on: T1.
- [ ] T3 [Design/UI] M — `server/osc_dash.py`: `sweepCard()`/tails held rows,
  `formatSweepTickValue()`, sweep anchor, `updateBacktestParamPreview()` all
  through `formatCents()`; `¢` → `c`; sweep dollar state/clamp untouched.
  Verify: node held-card + formatter tests. Depends on: T1.
- [ ] T4 [Code/Logic] S — update label/format/held-card pins, extend registry
  `display` assertions, add conversion + validator tests; final targeted gate
  `python -m pytest tests/test_osc_dash_integration.py tests/test_param_registry.py -q`.
  Depends on: T2, T3.

Checkpoint after T2: inputs convert + requests dollar-identical (prove with
round-trip test) before touching renderings.

Sub-issue fan-out skipped (Standard ceremony waived): 4 tasks run in one branch
session; `Depends on:` above mirrors the tracker order without tracker noise.

## Files NOT to modify
Engine internals beyond the registry (fields, fill rule, `_clamp_to_spec`,
`_build_backtest_params`, `_sweep_params_for_value`), `scripts/backtest.py`,
`scripts/sweep_backtest.py`, `research/jungle-king/` data, venue constants,
non-price inputs, market-result displays, theme, `SPEC-319.md`.
