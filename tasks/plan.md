# Task Plan — Issue #270: Flatten and standardize collapsible Backtest sections

**Issue:** #270 — Flatten and standardize collapsible Backtest sections
**Size tier:** Large — one server-served UI contains several independently testable layout, accessibility, chart, and label contracts, with focused integration and browser verification.
**Task type:** Code + Design/UI + API/Interface compatibility + QA.
**Stack:** Python/FastAPI/uvicorn serving a vanilla JavaScript dashboard with existing Chart.js; pytest is the focused test runner. No package manifest or frontend build step is present.

## Issue-derived interface contract

- **Rendered DOM:** preserve existing stable IDs and add unique body IDs for the six peer sections. Each heading is a button whose `aria-controls` names its body and whose `aria-expanded` matches `hidden`.
- **Chart interaction:** existing sweep payload (`points`, `series_order`, `series_labels`, `best_overall`, `best_market`) remains the source. Expansion is client-only and takes a chart/card descriptor; it does not fetch or rerun anything.
- **Labels:** `marketName(series)` remains the single helper, but its output contract becomes duration-first (`05m BTC`). Slug values and filter comparisons stay unchanged.
- **Accessibility:** the chart detail surface is a dialog with accessible label, close button, focus-in/focus-out behavior, and Escape handling.
- **API:** no endpoint, query parameter, response field, calculation, or timing contract changes.

## Dependency order and tasks

### Phase 1 — contracts and flat section shell

- [x] **TASK-1 [Design/UI] Section inventory and flat peer layout**
  - **Files:** `server/osc_dash.py`, `tests/test_osc_dash_integration.py`.
  - **Build:** wrap Backtest Parameters, Strategy Geometry Preview, Overall Execution Results, Sweep Visual, Per-Series Performance, and Executed Windows Log in consistent peer section markup; retain all current controls and stable IDs.
  - **Acceptance:** six clear headings exist at one vertical hierarchy; no result card remains visually/nestingly unrelated; responsive layout has no new horizontal overflow.
  - **Helper:** `frontend-ui-engineering` + `incremental-implementation`.
  - **Verify:** focused served-HTML assertions for section count, headings, stable IDs; browser inspection at 320/768/1024/1440px.
  - **Dependencies:** None.

- [x] **TASK-2 [Design/UI/Accessibility] Generalize collapse semantics and state**
  - **Files:** `server/osc_dash.py`, `tests/test_osc_dash_integration.py`, `tests/test_theme_tokens.py`.
  - **Build:** extend the existing `toggleBtSection`/initialization pattern to all six peer sections, with deterministic defaults, `aria-expanded`, `aria-controls`, hidden-body synchronization, focus-visible styling, and safe local persistence.
  - **Acceptance:** every section is keyboard operable; collapse/reopen preserves child DOM and rendered state; opening the Backtest tab remains read-only.
  - **Helper:** `frontend-ui-engineering` + `test-driven-development`.
  - **Verify:** HTML/Node contract tests plus browser keyboard toggle and reopen checks with zero console errors and no backtest requests.
  - **Dependencies:** TASK-1.

### Checkpoint A

- [x] Flat six-section DOM and collapse behavior pass focused tests.
- [x] Existing Backtest IDs, explicit Run behavior, and parameter controls remain intact.

### Phase 2 — labels and chart readability

- [x] **TASK-3 [Design/UI/Compatibility] Canonical market labels everywhere in Backtest results**
  - **Files:** `server/osc_dash.py`, `tests/test_osc_dash_integration.py`, `tests/test_theme_tokens.py`.
  - **Build:** update the shared market-label helper/order and all Per-Series Performance, Executed Windows Log row, and filter-option rendering to `05m BTC` / `15m BTC` without changing slug values, ordering, filtering, sorting, or API data.
  - **Acceptance:** all ten canonical labels are represented; `BTC 5m`/`BTC 15m` suffix forms do not appear in the targeted Backtest areas.
  - **Helper:** `api-and-interface-design` + `test-driven-development`.
  - **Verify:** focused HTML/API assertions and browser checks for table rows and filter options.
  - **Dependencies:** TASK-1.

- [x] **TASK-4 [Design/UI] Make small Sweep Visual cards legible**
  - **Files:** `server/osc_dash.py`, `tests/test_theme_tokens.py`, `tests/test_osc_dash_integration.py`.
  - **Build:** adjust the existing shared Chart.js options for compact cards: readable duration/asset tick formatting, bounded tick density/rotation, responsive chart sizing, and preserved full-value tooltips/best highlighting.
  - **Acceptance:** no visible label overlap or bar obstruction at supported widths; aggregate and all ten market charts retain numeric X/Y values, tooltips, and best highlighting.
  - **Helper:** `frontend-ui-engineering` + `test-driven-development`.
  - **Verify:** served-HTML chart-option assertions and browser screenshots/inspection at narrow and desktop widths.
  - **Dependencies:** TASK-1.

### Checkpoint B

- [x] Canonical labels and compact chart rendering pass focused tests.
- [x] Browser confirms cards remain legible without changing sweep data or triggering requests.

### Phase 3 — expanded chart view

- [x] **TASK-5 [Design/UI/Accessibility] Add view-only expanded chart dialog**
  - **Files:** `server/osc_dash.py`, `tests/test_osc_dash_integration.py`, `tests/test_theme_tokens.py`.
  - **Build:** add one reusable detail surface for aggregate and per-series cards. Make each card focusable/activatable by click/Enter/Space; render a larger chart from the existing sweep payload with full tested-value set, readable axes/tooltips, and the same best-result highlighting.
  - **Acceptance:** dialog has `role="dialog"`, accessible name, close button, Escape support, focus-visible styling, focus entry and return to the triggering card; no endpoint is called and parameters/file selection are unchanged.
  - **Helper:** `frontend-ui-engineering` + `api-and-interface-design` + `test-driven-development`.
  - **Verify:** Node/HTML interaction contract tests and browser checks for aggregate + market card, close button, Escape, focus return, repeated open/close, and console/network cleanliness.
  - **Dependencies:** TASK-4.

- [x] **TASK-6 [QA/Regression] End-to-end Backtest acceptance review and cleanup**
  - **Files:** `server/osc_dash.py`, `tests/test_osc_dash_integration.py`, `tests/test_theme_tokens.py`.
  - **Build:** exercise explicit Run, selected-file flow, parameter edits, collapse/reopen after results, filter/pagination behavior, all chart expansion paths, and narrow layout; remove duplicate listeners/styles or dead code without altering unrelated tabs.
  - **Acceptance:** all Issue #270 criteria are covered, no API/data behavior changed, and the implementation remains within the locked constraints.
  - **Helper:** `verification-before-completion` + `code-simplification`.
  - **Verify:** `python -m pytest tests/test_osc_dash_integration.py tests/test_theme_tokens.py -q`, compile/syntax checks as applicable, and browser preview with zero console errors/network failures.
  - **Dependencies:** TASK-2, TASK-3, TASK-5.

### Checkpoint C — ready for Station IV

- [x] Targeted test command passes; no test was skipped or weakened.
- [x] Browser confirms flat sections, collapse semantics, chart legibility, dialog focus behavior, canonical labels, and no unintended simulation.
- [x] `git diff` contains only Issue #270 files plus intentional focused tests.

## One evidence-based improvement proposal (adopted by default)

Use one shared chart-card/detail renderer and one Chart.js option factory for both the small cards and dialog: the current `renderSweepVisual()` duplicates chart construction while forcing `autoSkip: false` and only `maxTicksLimit: 7` in a small-card context (`server/osc_dash.py:5198-5210`), which is direct evidence for centralizing tick policy and preventing card/detail behavior from drifting.

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Existing scripts/tests depend on result IDs or card DOM | High | Preserve IDs and assert them before/after markup changes. |
| Chart.js canvas instances leak during re-render/modal use | Medium | Keep one registry/destroy path; test repeated render/open/close. |
| Label helper change affects unrelated tabs | Medium | Scope assertions to Backtest and verify existing shared usages explicitly. |
| Focus behavior fails only after repeated interactions | High | Browser-test click, keyboard activation, Escape, close, and return focus in cycles. |
| Collapse hides a chart whose size is measured before render | Medium | Resize/update chart after reopening and verify visually. |

## Out of scope
Backtest/replay math, API contracts, timing semantics, sweep axes/data, live cockpit, unrelated tabs, and new dependencies.
