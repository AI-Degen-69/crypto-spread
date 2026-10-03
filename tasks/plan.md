# Plan — Issue #421: Cockpit max pair cost input is never hydrated from trading engine state

Branch: `i421/cockpit-max-pair-cost-hydration` | Issue: #421
Stack: Python dashboard serving embedded JS (`server/osc_dash.py`) ·
FastAPI `/api/live/*` · pytest + node harness (`tests/test_osc_dash_integration.py`)
Size: **Small** — one source function (`renderCockpitUI`), two added lines, one
test file, one ledger cell.
Task type: **Code, Design/UI** (UI hydration + regression pin)

## Issue in one line

The trading platform's `max pair cost` input is never filled from the trading
engine's state, so it shows a hardcoded `99` and — when the operator presses
Apply — writes that stale number back into the engine's config.

## Embedded spec (Small work — no `SPEC.md` ceremony)

**Goal.** `renderCockpitUI()` hydrates `#cockpitPairCost` from
`st.params.max_pair_cost` through the shared `dollarsToCents()` helper, in both
hydration branches, symmetric with `cockpitOffset` and `cockpitQuoteLo`.

**Scope.** Two guarded assignments. Tests. One ledger cell.

**Out of scope.** Engine defaults/bounds, the dollars-on-the-wire contract, other
cockpit inputs, `/api/live/*` shapes, the HTML `value="99"` fallback, the lock UI.

**Edge cases.**
- `st.params.max_pair_cost == null` (older payload) → guard skips, the HTML
  fallback `99` stays. Same shape as the nine siblings.
- `dollarsToCents(0.995) === 99.5` — one decimal of a cent, allowed by the
  field's `step="0.1"` and `validateCentsInput()`'s one-decimal regex.
- Engine clamps to `[0.50, 1.00]` (`strategy/live_trader.py:2934`) so the
  hydrated value is always inside the input's `min=50`/`max=100` — hydration can
  never render an input that `validateCockpitInputs()` would reject.

## CodeRabbit intake (read once; echo ignored)

- **Adopted:** one guarded `dollarsToCents(st.params.max_pair_cost)` per branch,
  copying the exact `cockpitOffset` guard shape; no conversion logic, loop, or
  shared hydration abstraction; keep the HTML `value="99"`; three tests (branch
  source pin, `0.995 ↔ 99.5` helper assertion, stopped-engine state contract).
- **Rejected:** harmonising `LiveTraderEngine`'s default to `MakerConfig`'s
  `0.995` (Design Choice 1 option 1) — the issue forbids default changes, and the
  fix is truthful either way. Its 3-phase / 4-task layout collapsed to 3 tasks.
- **Verified seams** (spot-checked against the live tree; `[UNVERIFIED]`: 0):
  `renderCockpitUI()` (`server/osc_dash.py:12799`), running branch
  (`12834-12852`, assignments `12838-12847`), first-init branch
  (`12854-12875`, assignments `12860-12869`), `dollarsToCents()` (`8250`),
  `validateCockpitInputs()` (`12592`), `applyCockpitConfig()` pair-cost write
  (`12733-12736`), HTML input (`6300`), lock list (`12103`);
  `strategy/live_trader.py:920` (default), `:2629` (state payload), `:2934`
  (clamp); `server/osc_dash.py:4665` (payload bound); tests
  `tests/test_osc_dash_integration.py:3477` (endpoint pattern), `:7876`
  (slicing pattern), `:10206` (cents-helper node test), `:10416` (input pin).

## Corrected fact — the issue body names the wrong default

The issue states the live default is `0.995` (`strategy/config.py:649`). That
line is `MakerConfig` — a different object. The trading engine the cockpit drives
is `LiveTraderEngine`, whose `self.max_pair_cost: float = 0.99`
(`strategy/live_trader.py:920`, with the comment "this is what an unconfigured
engine starts at"). So the fresh-engine cockpit shows `99` today *by coincidence*,
and the real defect is the silent config write-back, not a `99`-vs-`99.5` display
gap. Hydration fixes both: the field now reads whatever the trading engine holds.
This caveat goes in the PR body, verbatim as recorded here.

## Resolved open questions (from code, not asked)

- **Hydration or literal?** Hydration — matches the nine siblings and the issue.
- **Keep the HTML `value="99"`?** Yes — it is the pre-hydration fallback for a
  payload with no `params.max_pair_cost`; removing it leaves the field empty,
  which fails `validateCentsInput()` and blocks Apply.
- **Sync while stopped too?** No — the issue's scope is the two existing branches.
  Adding unconditional stopped-state sync would overwrite operator edits on a
  1s poll.

## Improvement proposal (adopted by default — edge-case hardening)

Widen the source assertion from "pair cost is hydrated" to "both branches
hydrate the same key set". Evidence, verbatim — the issue's own words: *"A
regression test asserts the hydration for `cockpitPairCost` and fails if the line
is removed or reverted to a hardcoded value."* A pin that only names pair cost
passes if `cockpitShares` silently drops out of one branch tomorrow. The test
will therefore assert both slices hydrate the identical ordered key list, with
pair cost in it. Same cost (one extra `assert`), strictly stronger guard. Adopted;
recorded so it does not resurface.

## Interfaces (locked — no change)

- `st.params.max_pair_cost` (float dollars, `null`-able) → `dollarsToCents()` →
  `input.value` (cents string). Identical contract to `offset` / `quote_range`.
- Nothing else. No new JS function, no signature change, no payload change.

## Dependency graph & tasks

T1 (hydration lines — the defect) → T2 (source pin reads the lines) →
T3 (helper + endpoint contract + ledger, depends on T1 and T2).

- [x] T1 `[Design/UI]` **XS** — `server/osc_dash.py`: add the guarded assignment
  in the running branch (next to the `cockpitExitReversal` line, `:12840`) and the
  identical line in the first-init branch (next to `:12862`). Copy the
  `cockpitOffset` guard shape verbatim. No other line changes.
  **Verify:** `python -m pytest tests/test_osc_dash_integration.py -q -k cockpit`
  **Depends on:** none.

- [x] T2 `[Code/Logic]` **S** — `tests/test_osc_dash_integration.py`: new test
  that fetches the served page and slices both hydration blocks (anchors: the
  `st.is_running` comment at `:12834` → `}` before `:12854`; the
  `!hasInitializedCockpitFilters && st.selected_series` block at `:12856` →
  `else if`). Assert in **each** slice: (a) `cockpitPairCost` is assigned from
  `dollarsToCents(st.params.max_pair_cost)`; (b) neither slice assigns a literal
  to `cockpitPairCost` (rejects `.value = 99`); (c) both slices hydrate the same
  ordered key list — the nine siblings plus pair cost (improvement proposal).
  Runs without Node.
  **Verify:** the new test, plus the existing `test_cents_helpers_convert_exactly_at_the_ui_edge_node`.
  **Depends on:** T1.

- [x] T3 `[Code/Logic]` **S** — extend
  `test_cents_helpers_convert_exactly_at_the_ui_edge_node`
  (`tests/test_osc_dash_integration.py:10206`) with `dollarsToCents(0.995) === 99.5`
  and `centsToDollars(99.5) === 0.995`; add a stopped-engine endpoint contract
  test modelled on `test_api_live_cockpit_endpoints` (`:3477`) that POSTs
  `max_pair_cost = 0.995` to `/api/live/config` and asserts `/api/live/state`
  returns `is_running == false` with `params.max_pair_cost == 0.995` (monkeypatch
  `_poll_single_market`, reset the singleton as the neighbours do); flip row N1 in
  `docs/issues/419-noticed-but-not-touching.md` from `published` to `resolved`
  with the PR reference. Final gate:
  `python -m pytest tests/test_osc_dash_integration.py tests/test_param_registry.py -q`.
  **Depends on:** T1, T2.

Checkpoint after T1: the field is hydrated; before the tests exist, prove it by
hand (`Select-String` on both branches) rather than by trusting the plan.

Sub-issue fan-out skipped (Small work): 3 tasks in one branch session; the
`Depends on:` above mirrors tracker order without tracker noise.

## Files NOT to modify

`strategy/config.py`, `strategy/live_trader.py`, `backtest/engine.py`,
`server/osc_dash.py` outside `renderCockpitUI`'s two hydration branches (the HTML
input at `6300`, `validateCockpitInputs()`, `applyCockpitConfig()`,
`updateCockpitParamsLockUI()`, `/api/live/*` and `LiveConfigPayload` at `4665` are
all off-limits), `tests/test_param_registry.py`, `scripts/*`, `research/*`.