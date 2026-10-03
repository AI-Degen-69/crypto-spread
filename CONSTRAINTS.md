# CONSTRAINTS.md — Issue #421 (cockpit `max_pair_cost` hydration)

Locked by Station II (`ii-plan-issue #421`). Branch:
`i421/cockpit-max-pair-cost-hydration`. Guardrails for Stations III–V.

## Zero regressions

- `python -m pytest tests/test_osc_dash_integration.py tests/test_param_registry.py -q`
  must pass before handoff. Targeted suites only locally — the full suite is CI's
  merge gate (`docs/git-workflow.md` §4).
- The nine sibling hydrations in both branches keep their exact current text —
  only one guarded line is added per branch.
- `applyCockpitConfig()` keeps posting `centsToDollars(pairCostEl.value)`
  (`server/osc_dash.py:12733-12736`) — the dollars-on-the-wire contract from #419
  is not reopened.
- `validateCockpitInputs()` keeps validating the field through the shared
  `validateCentsInput()` (`server/osc_dash.py:12597`) — untouched.
- `updateCockpitParamsLockUI()` keeps `cockpitPairCost` in `paramIds`
  (`server/osc_dash.py:12103`) — the field stays locked while the trading engine runs.

## Scope freeze

- Two guarded assignments, one per hydration branch, both calling the existing
  `dollarsToCents()` helper.
- Tests in `tests/test_osc_dash_integration.py` only.
- One status cell in `docs/issues/419-noticed-but-not-touching.md` (row N1).
- **No** engine, API, CLI, registry, conversion-helper, HTML-attribute, or
  lock-UI change. **No** new external dependencies.

## Facts locked against the code (do not re-derive)

- `LiveTraderEngine.max_pair_cost` defaults to **`0.99`**
  (`strategy/live_trader.py:920`), **not** `MakerConfig`'s `0.995`
  (`strategy/config.py:649`). The two are different objects; the issue body names
  the wrong one. Hydration displays whatever the trading engine holds — the
  cockpit is now truthful about `0.99` too.
- `/api/live/state` carries `params.max_pair_cost` (`strategy/live_trader.py:2629`),
  so the field is hydratable — no API change needed.
- `update_config()` clamps to `[0.50, 1.00]` (`strategy/live_trader.py:2934`) and
  the payload field enforces the same bounds (`server/osc_dash.py:4665`).

## Anti-cheat

- Forbid skipping/disabling tests, deleting assertions, suppressing linters.
- The new hydration assertions must fail if the line is deleted **or** reverted to
  a literal (e.g. `.value = 99`). A whole-file search for `cockpitPairCost` is
  **not** an acceptable substitute — it passes even when one branch is missing.
- The existing `cockpitPairCost` pins (HTML `value="99"`, `min=50`/`max=100`/
  `step=0.1` at `server/osc_dash.py:6300`, and the validator pin at
  `tests/test_osc_dash_integration.py:10416`) must stay byte-identical.

## Performance thresholds

- Two property reads plus one arithmetic call inside an existing state-render
  branch that already runs ten of them. No measurable latency change.

## Out of scope

Engine internals, `/api/live/*` endpoint shapes, CLI flags, `params_hash()`,
sweep code, theme, and any other cockpit input. Changing the hardcoded HTML
`value="99"` fallback is out of scope — an empty field fails
`validateCentsInput()` and would block Apply.