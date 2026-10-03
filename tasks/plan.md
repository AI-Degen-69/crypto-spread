# Plan: Issue #411 - Move the Backtest action row to the card bottom-right and give the stop-loss thresholds their own row

Branch: `i411/backtest-action-row-bottom-right` | Issue: `#411`

## Classification & Routing
- **Size tier**: Small (markup position + two CSS declarations in one file, plus test updates; no behaviour change)
- **Task type**: Design / UI (layout only, zero semantic change)
- **Routing skills**: `frontend-ui-engineering`, `test-driven-development`, `incremental-implementation`
- **Verification mode**: targeted pytest + one live browser check of the Backtest tab

## Resolved Open Questions (resolved from code, not asked)
1. **`display:contents` or a full-width wrapper?** Full-width wrapper. Verified: `.form-grid` is `repeat(4,1fr)` (`osc_dash.py:5179`) with a 2-column `max-width:900px` fallback (`:5180`). A plain wrapper would collapse to one cell; `display:contents` is exactly what prevents the group from ever occupying its own row. The wrapper becomes a `grid-column:1/-1` item with its own `repeat(4,1fr)` inner grid plus its own 900px rule, so "one row of four" holds at both breakpoints. The two `display:contents` rules are therefore deleted and `tests/test_osc_dash_integration.py:5379` is rewritten to explain why.
2. **Single line or wrap?** Wrap right-aligned. `flex-wrap:wrap` is kept and `justify-content:flex-end` added. Verified precedent: the Sweep Visual (`:5689`) and Log (`:5765`) toolbars already use exactly `display:flex;justify-content:flex-end;align-items:center;flex-wrap:wrap;gap:8px`. Reusing that declaration set means no new breakpoint and visual consistency with the two existing right-aligned toolbars.
3. **Does `btHash` move too?** No - it stays the first child of `#btSecParametersBody` as a dataset caption, per the issue's default. Verified it is a standalone `<div class="mono" id="btHash">` (`:5454`) with no coupling to the action row.

## CodeRabbit Intake Note (issue comment, 2026-10-03)
- **Adopted**: move the five controls unchanged into a right-aligned wrapping footer after the `.bt-accordion` close; full-width `grid-column:1/-1` wrapper replacing `display:contents`; Share Size directly before the group; separate DOM-order and CSS assertions in the tests.
- **Rejected**: `class="form-grid"` on `#btStopLossFields`. Verified no JS queries `.form-grid` (a repo-wide search for `querySelector('.form-grid')` returns nothing), so nesting is safe - but the existing 900px media rule targets `.form-grid` and would then apply to the inner grid too, making "one row of four" impossible to guarantee. An explicit id-scoped inner grid plus its own media rule is clearer and testable.
- **Deviation from the CodeRabbit sketch**: it leaves the toolbar as an inline-styled `div`. `CONSTRAINTS.md` forbids new inline `style=` for the moved row, so the alignment goes in the `<style>` block next to the `.bt-peer-layout` rules.
- **Unverified**: none. Every seam cited below was read in `server/osc_dash.py` at the current HEAD (`d6501a1`).

## Improvement Proposal (Adopted by Default)
- **Proposal**: give the moved footer a real id (`btSetupActions`) and assert its `justify-content:flex-end` in a dedicated CSS test, so a future "tidy the markup" edit cannot silently drop the right-alignment that this whole issue exists to deliver.
- **Evidence**: `server/osc_dash.py:5689` and `:5765` are inline-styled right-aligned toolbars with no id and no test - the pattern this change copies is currently untestable. Adding the id + assertion costs two lines and is the only way the acceptance criterion "renders in a right-aligned container" can be verified mechanically.

## Tasks

- [x] **Task 1 (S)**: `[Design/UI]` Add the failing layout tests
  - Target files: `tests/test_osc_dash_integration.py`
  - Details:
    - Add `from html.parser import HTMLParser` and a small DOM-order helper that collects `id` attributes in document order (stdlib only, no new dependency).
    - Add a style-text helper that extracts the rule block for a selector, so CSS assertions are separate from DOM assertions.
    - Rewrite `test_backtest_run_buttons_sit_at_setup_top_level` (`:4961`) - it currently asserts the buttons sit BETWEEN `btSecParametersBody` and `bt-accordion`, which is exactly what this issue reverses. New assertion: the five controls are inside `#btSecParametersBody`, appear AFTER the `.bt-accordion` element, and are NOT inside `#btSecGeometry`.
    - Rewrite `test_stop_loss_thresholds_live_inside_one_grid_group` (`:5367`) - it slices from `<div id="btStopLossFields">` to `data-param-label="quote_shares"`, which stops working once Share Size moves above the group. New assertion: all four ids are descendants of `#btStopLossFields`, in source order 5m, 15m, BTC 5m, SOL 5m, and `btSize` precedes `#btStopLossFields`.
    - Rewrite `test_stop_loss_group_survives_the_form_grid` (`:5379`) - replace the `display:contents` assertion with: the wrapper is `grid-column:1/-1`, has its own `repeat(4,1fr)` inner grid, has its own 900px rule, and `[hidden]` still resolves to `display:none`. Keep the `cockpitStopLossFields` absence assertion (#229).
    - Add a new test asserting `#btSetupActions` exists and its rule carries `justify-content:flex-end`.
    - Strengthen the reset coverage so `resetBtParams()` still writes all four moved thresholds plus `btSize` (`:9116-9120`).
  - Depends on: None
  - Verification: `python -m pytest tests/test_osc_dash_integration.py -q -k "backtest or stop_loss or run_buttons"` must be **RED** on the untouched `server/osc_dash.py`, and each failure must name the layout it pins.

- [ ] **Task 2 (S)**: `[Design/UI]` Move the action row and rebuild the stop-loss row
  - Target files: `server/osc_dash.py`
  - Details:
    - Delete the toolbar `div` at `:5455-5464` from between `#btHash` and `.bt-accordion`. Keep `#btHash` as the first body child.
    - Insert the same five controls **unchanged** into `<div id="btSetupActions">` after the `.bt-accordion` close (`:5635`) and before the `#btSecParametersBody` close (`:5636`). Preserve every attribute: `id="btnRunSweepIcon"`, `id="btnRunSweepText"`, `hidden` on `btnStopBacktest`, both `onclick` handlers, both `aria-live` spans, and the `bt-runtime-badge` class.
    - Add one CSS rule beside the `.bt-peer-layout` rules (near `:5295`): `display:flex;justify-content:flex-end;align-items:center;flex-wrap:wrap;gap:8px;margin-top:14px` (14px matches the accordion's own gap at `:5279`).
    - Move the `btSize` `.form-group` (`:5554-5557`) directly before `#btStopLossFields` in the Quote Placement `.form-grid`, keeping its label and `data-param="quote_shares"`.
    - Replace `#btStopLossFields{display:contents}` (`:5186`) with `#btStopLossFields{grid-column:1/-1;display:grid;grid-template-columns:repeat(4,1fr);gap:12px}` plus `@media(max-width:900px){#btStopLossFields{grid-template-columns:repeat(2,1fr)}}`. Keep `#btStopLossFields[hidden]{display:none}`.
    - Rewrite the `:5181-5185` comment to record that the group now owns a full-width row and why `display:contents` was removed (it made the group share the outer flow, which is the defect #411 fixes).
    - Must NOT change: `btControlValues()`, `btControlQuery()`, `resetBtParams()`, `updateBtStopVisibility()`, `stopBacktestRun()`, the `.bt-stop[hidden]` rule, any `id` or `data-param`, the Cockpit mirror, or the Sweep Visual / Per-Series / Log sections.
  - Depends on: Task 1
  - Verification: `python -m pytest tests/test_osc_dash_integration.py -q -k "backtest or stop_loss or run_buttons"` turns GREEN.

- [ ] **Task 3 (XS)**: `[Verify]` Green run of the targeted dashboard suites
  - Target files: none (verification only)
  - Details: targeted runs only - the full suite is forbidden locally (`AGENTS.md` Testing & Fast Iteration Policy); CI is the merge gate.
  - Depends on: Task 1, Task 2
  - Verification:
    - `python -m pytest tests/test_osc_dash_integration.py -q -k "backtest or stop_loss or run_buttons"`
    - `python -m pytest tests/test_osc_dash_integration.py -q -k "stop or reset"`
    - `python -m pytest tests/test_osc_dash_integration.py tests/test_theme_tokens.py -q`
    - Live browser check of `:5515` Backtest tab at desktop and <900px widths: controls bottom-right, thresholds one row of four, Share Size directly above.

## Checkpoints
- **Checkpoint 1** (after Task 1): the new layout tests are RED against the untouched file, each failure naming the layout it pins.
- **Checkpoint 2** (after Task 3): all targeted suites green; a browser check confirms the visual outcome at both breakpoints.

## Notes
- `SPEC.md` is intentionally not created: this is a Small, layout-only change with the spec fully captured in `CONSTRAINTS.md` and the acceptance criteria of #411.
- `SPEC.md` does not exist in this repo at HEAD; `CONSTRAINTS.md` previously held #399 and was rewritten for #411 (per-issue working state, per `AGENTS.md`).
