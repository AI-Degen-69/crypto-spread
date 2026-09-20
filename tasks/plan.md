# Task Plan — Issue #272: Clarify Tick Files metrics and readiness progress

**Size tier:** Large — changes the Tick Files UI, integrity presentation, readiness target contract, tooltip interaction, and focused regression coverage.
**Task type:** Code + API/Backend + Design/UI + QA.
**Issue:** #272

## Locked decisions
- Keep raw integrity values `PASS/WARN/FAIL` for API compatibility.
- Show capture-state labels `COMPLETE CAPTURE`, `PARTIAL CAPTURE`, and `CORRUPTED DATA` in the UI.
- Show a short reason and safe action for every integrity state.
- Hide the long readiness/profitability explanation from the main layout; expose it through an accessible floating tooltip.
- Use Issue #273's versioned readiness policy as the target source of truth.
- Every readiness metric has explicit exploratory and research targets, measured value, two progress states, and a next milestone.
- Lower-is-better metrics are visibly inverted or labeled `0 is the target`.
- Preserve API compatibility, cache/rescan behavior, Backtest actions, and duration-first market labels.

## Tasks

- [x] **TASK-1 [Backend/API]**: Normalize the readiness target contract.
  - Target: `scripts/verify_tick_data.py`, `server/osc_dash.py`, focused verifier tests.
  - Build: expose versioned target metadata for both levels, including explicit exploratory targets for market pairs, windows per pair, gaps, and collector errors; provide deterministic measured values and next-milestone inputs without changing the readiness gate unexpectedly.
  - Verify: unit tests cover all metrics, both levels, zero values, lower-is-better limits, and policy-version consistency.
  - Helper: `test-driven-development` + `api-and-interface-design`.

- [x] **TASK-2 [Backend/API]**: Add capture-state display metadata without breaking raw integrity values.
  - Target: `scripts/verify_tick_data.py`, `server/osc_dash.py`, integration tests.
  - Build: map `PASS/WARN/FAIL` to customer-facing capture-state metadata with reason categories and safe-action text while retaining raw status fields in verify and manifest responses.
  - Verify: tests assert all mappings, representative reason text, failure precedence, and backwards-compatible raw values.
  - Helper: `api-and-interface-design` + `test-driven-development`.

- [x] **TASK-3 [Design/UI]**: Replace ambiguous Tick Files labels and status rendering.
  - Target: `server/osc_dash.py`, `tests/test_osc_dash_integration.py`, `tests/test_theme_tokens.py`.
  - Build: render `COMPLETE CAPTURE`, `PARTIAL CAPTURE`, or `CORRUPTED DATA`, show a short reason/action, keep metric definitions visible, and preserve Backtest/rescan controls and `05m BTC`/`15m BTC` labels.
  - Verify: served-HTML assertions and browser inspection of aggregate and expanded per-file views at desktop and narrow widths.
  - Helper: `frontend-ui-engineering`.

- [x] **TASK-4 [Design/UI]**: Build per-file two-target progress rows.
  - Target: `server/osc_dash.py`, UI/integration tests.
  - Build: add independent progress meters for snapshots, windows, tape entries, market-duration coverage, windows per pair, time blocks, corrupt rows, schema errors, gaps, and collector errors. Each row shows measured value, exploratory target/progress, research target/progress, reached state, and next milestone. Use reversed semantics for lower-is-better metrics and retain text accessible to screen readers.
  - Verify: browser checks for empty/tiny, exploratory, and near-research reports; tests cover target text, progress clamping, next milestone, and zero-target metrics.
  - Helper: `frontend-ui-engineering` + `test-driven-development`.

- [x] **TASK-5 [Design/UI]**: Move the readiness explanation into an accessible floating tooltip.
  - Target: `server/osc_dash.py`, UI tests.
  - Build: add an `ⓘ` trigger beside Research Readiness, keyboard/focus support, Escape/outside close behavior, and concise copy explaining that targets measure data coverage rather than profitability and require later untouched-period checking.
  - Verify: browser interaction and DOM assertions confirm the copy is not in the normal visible layout, the tooltip is reachable, and no console errors occur.
  - Helper: `frontend-ui-engineering`.

- [x] **TASK-6 [QA/Regression]**: Validate real files and simplify the final implementation.
  - Target: changed verifier/dashboard/tests/docs.
  - Build: inspect current files including the September 18 example, confirm measured-vs-target displays, remove duplicate helpers or dead styling, and preserve cache-first loading.
  - Verify: targeted pytest suite, compile/diff checks, API response inspection, and browser verification in the real Tick Files tab.
  - Helper: `code-simplification` + `verification-before-completion`.

## Evidence-based improvement proposal
Use one shared serializable target definition for the verifier, API, and UI instead of duplicating thresholds in JavaScript. This follows the existing versioned `READINESS_POLICIES` contract in `scripts/verify_tick_data.py` and prevents the displayed progress bars from drifting away from the actual readiness gate.

## Out of scope
Collector protocol changes, replay/fill/exit math, window grouping changes, live trading behavior, new dependencies, and profitability claims.
