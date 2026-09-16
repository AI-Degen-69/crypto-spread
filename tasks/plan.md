# tasks/plan.md — Issue #201: Group backtest stop-loss params under an on/off toggle

- **Issue:** #201 — https://github.com/AI-Degen/crypto-spread/issues/201
- **Branch:** `feat/backtest-stop-loss-toggle-201` off `master`
- **Size tier:** **Small** — one file (`server/osc_dash.py`), one HTML block +
  one new JS function + two JS lines. No contract change, no engine change.
- **Task type:** `[Design/UI]` (front-end grouping/toggle). Not Debug, not
  Backend — `stop_loss_enabled=false` already flows into `BacktestParams`.
- **Stack detected:** Python 3 / FastAPI, dashboard HTML+vanilla JS served as a
  string from `server/osc_dash.py`. Tests: `pytest`, HTML-string assertions via
  `TestClient` in `tests/test_osc_dash_integration.py`.

## Spec (embedded — Small tier, no separate SPEC.md)

### Goal
One stop-loss on/off switch in the backtest Operator Controls that owns all four
threshold inputs (`btExit5m`, `btExit15m`, `btExitBtc`, `btExitSol`).

### Acceptance criteria
1. Backtest Parameters shows a single stop-loss on/off toggle grouping all four
   threshold fields.
2. Off → fields hidden + `disabled`; `/api/backtest` URL carries
   `stop_loss_enabled=0`.
3. On → fields visible + enabled; URL carries `stop_loss_enabled=1`.
4. Toggle styling/behavior match the existing pair-cost toggle in the same
   section (`toggle-wrap` / `toggle-switch` / `toggle-slider`, ON/OFF span).
5. `python -m pytest tests/test_osc_dash_integration.py -q` passes.

### Edge cases
- Default page load = **On** (today's select defaults to `value="1"`).
- Hidden fields must also be `disabled`, so a stale value cannot be read back by
  any future form-serialization path.
- `runBacktest()` must not read `.value` off a checkbox — `.checked ? '1' : '0'`.
- `resetBtParams()` / `applyWinningConfig()` must call the toggle handler if they
  touch the checkbox (mirrors existing `btPairCostEnabled` handling).
- `applyParamSpec()` walks `[data-param]` and sets `min`/`max` only for
  `INPUT[type=number]`, so keeping `data-param="stop_loss_enabled"` on a checkbox
  is safe and preserves the `bt` surface-prefix test.

### Out of scope
Cockpit tab stop-loss control, engine semantics, threshold defaults/bounds, any
other parameter group.

## API / interface contract (locked)

| Surface | Before | After |
|---|---|---|
| DOM | `<select id="btStopLossEnabled" data-param="stop_loss_enabled">` with `1`/`0` options | `<input type="checkbox" id="btStopLossEnabled" data-param="stop_loss_enabled" checked onchange="toggleStopLossInputs()">` |
| DOM | four loose `.form-group` blocks | same four, wrapped in `<div id="btStopLossFields">` |
| JS | `const stopLoss = $('btStopLossEnabled').value` | `const stopLoss = $('btStopLossEnabled') ? ($('btStopLossEnabled').checked ? '1' : '0') : '1'` |
| JS | — | `function toggleStopLossInputs()` — mirror of `togglePairCostInput()` |
| HTTP | `/api/backtest?...&stop_loss_enabled=1\|0` | **unchanged** |
| Python | `server/osc_dash.py:651,718` | **unchanged** |

## Tasks

### T1 `[Design/UI]` — Restructure the stop-loss markup
- **File:** `server/osc_dash.py` (~2404-2419 thresholds, ~2461-2467 select)
- **Do:** Delete the standalone `btStopLossEnabled` select `.form-group`. In its
  place at the threshold block, add a header row reusing the pair-cost pattern:
  `<label data-param-label="stop_loss_enabled">` + `toggle-wrap` containing
  `<span id="btStopLossToggleLabel">ON</span>` and the `toggle-switch` checkbox
  `btStopLossEnabled` (`checked`, `data-param="stop_loss_enabled"`,
  `onchange="toggleStopLossInputs()"`). Wrap the four existing threshold
  `.form-group` blocks in `<div id="btStopLossFields">`.
- **Skill:** `frontend-ui-engineering`
- **Verify:** `python -m pytest tests/test_osc_dash_integration.py -q` still
  green (registry/surface tests must not regress).

### T2 `[Design/UI]` — `toggleStopLossInputs()` + request wiring
- **File:** `server/osc_dash.py` (next to `togglePairCostInput()` ~3897; caller
  ~3976; `resetBtParams()` ~4222; `applyWinningConfig()` ~4255)
- **Do:** Add `toggleStopLossInputs()`: read `.checked`, set `hidden` on
  `btStopLossFields`, set `disabled` + `opacity` on the four inputs, flip the
  `ON`/`OFF` label text and `var(--up)`/`var(--dim)` color — same shape as
  `togglePairCostInput()`. Change the `stopLoss` read in `runBacktest()` to
  `.checked ? '1' : '0'`. In `resetBtParams()`, restore the checkbox to `checked`
  and call the handler.
- **Skill:** `frontend-ui-engineering`
- **Verify:** page loads with fields visible; toggling Off hides/disables them.

### T3 `[Test]` — Lock the behavior
- **File:** `tests/test_osc_dash_integration.py`
- **Do:** Add tests in the existing HTML-string style:
  (a) `btStopLossFields` wrapper exists and the four ids live inside it;
  (b) `btStopLossEnabled` is a `checkbox` with `toggle-switch` markup and no
      leftover `<select id="btStopLossEnabled"`;
  (c) `toggleStopLossInputs` is defined and wired via `onchange`;
  (d) `runBacktest` sends `stop_loss_enabled=${stopLoss}` derived from `.checked`.
- **Skill:** `test-driven-development`
- **Verify:** `python -m pytest tests/test_osc_dash_integration.py -q`, then full
  `python -m pytest -q` (~50s, ~390 tests).

## Improvement pass (operator decides — NOT folded in)

**Proposal:** the four threshold labels are hard-coded strings
(`Exit Stop Loss 5m ($)` …) while the registry already owns
`exit_thresh_by_slug` → `"Exit Stop Loss ($)"` (`backtest/engine.py:228`), and
`test_shared_labels_are_not_hard_coded_in_the_page` exists precisely to catch
that class of drift. Since T1 already rewrites this block, using
`data-param-label="exit_thresh_by_slug"` on the new group header would put the
group title under registry control for free.
**Cost:** touches wording the issue declared out of scope.
**Recommendation:** adopt only the group *header* label from the registry; leave
the four per-slug labels hard-coded. Defer if you want the diff minimal.
