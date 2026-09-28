Branch: i303/dashboardtick-files-rework-summary-cards-table-dro | Issue: #303

# Plan — Issue #303: Tick Files summary cards and table layout

## Classification
- **Size:** Standard — one dashboard/API module plus its integration tests; includes a small API contract addition and several dependent UI changes.
- **Task type:** Code + Design/UI.
- **Stack:** Python / FastAPI; inline HTML/JavaScript in `server/osc_dash.py`; pytest integration tests in `tests/test_osc_dash_integration.py`.
- **Execution verification:** focused API integration tests and HTML contract assertions; preview the Tick Files UI during build if practical.

## Issue requirements
1. Remove the Tick Snapshots and Tape Entries summary cards; retain Files, Market Windows, Tape Empty, and Research Readiness.
2. Split Market Windows into exact 5m and 15m counts, with unknown/partial coverage shown accurately.
3. Replace the estimated Tick Snapshots table column with per-file 5m / 15m counts.
4. Reorder the table to Last Modified | File Name | 5m / 15m | Integrity | Research Readiness | Actions | Size.
5. Keep the change scoped to the Tick Files screen and `/api/ticks/manifest` mapping.

## Resolved planning questions
- Duration mapping is explicit in the issue and current sidecar shape: 300 seconds = 5m; 900 seconds = 15m. Ignore other durations in these split counts.
- A missing accepted per-file sidecar means per-file split counts are unknown (`null`), not zero. A verified file with no entries for a duration has an exact zero.
- Aggregate counts use the existing basename deduplication and merged `(series, duration)` breakdown. Preserve `windows_source`: `none` (no files) uses null split totals; `partial` reports the known subtotal, zero if no files are yet verified; complete cache data reports exact totals.
- Issue text says UI-only, while exact per-file/dataset split counts are not currently fields of the payload. A minimal additive mapping in `/api/ticks/manifest` is necessary to feed the requested screen; verification and sidecar logic remain unchanged.
- Stale readiness policy: code currently withholds readiness, integrity, and ranking fields for old-policy sidecars. Counts are facts from an accepted, fingerprint-matched sidecar; per-file duration counts can be exposed without changing readiness policy behavior.
- #295 is closed. Its raw/pristine split, PRISTINE badge, and delete protection remain outside this issue.

## CodeRabbit plan intake (read once)
- **Adopted:** nullable per-file counts and aggregate split totals, reuse of `windows_source`, basename deduplication, the Research Readiness class hook instead of a positional selector, corrected empty-state span, and focused assertions for exact/partial/stale-policy behavior.
- **Rejected/adjusted:** its API mapping changes are retained only because the issue requires exact counts not present in the current payload; this is the smallest payload addition and does not touch the verification algorithm or acceptance policy. Its line numbers were treated as pointers and checked against current code.
- **Unverified:** no uncertain CodeRabbit assumption remains in the scoped requirements after checking current code/tests. A running service preview could not serve the current branch; visual changes on the current branch remain for operator/review inspection.

## Improvement proposal
Adopt a small edge-case clarification: represent zero verified windows as `0` but absent per-file verification as `null`/`—`, preventing the display from implying that an unchecked file has no windows. Evidence: issue #303 requires “exact per-file counts (real counts per file, not estimates)” and describes counts “available from per-file window data by `duration` 300 vs 900”; current `api_ticks_manifest()` only sets `entry["market_breakdown"] = (cached or {}).get("market_breakdown", [])`, which collapses absent cache into an empty list unless explicit availability is preserved.

## Scope boundaries
- Product files: `server/osc_dash.py`, `tests/test_osc_dash_integration.py`.
- Per-issue planning artifacts: `SPEC.md`, `CONSTRAINTS.md`, `tasks/plan.md`, `tasks/todo.md`.
- Do not change collector, gates, verification implementation, sidecar acceptance, `run/`, Golden Dataset card, backtest dropdown, or #295 features.

## Dependency graph and tasks

### T1 — Add exact split-count manifest contract (M, [Backend/Logic]) — [x]
- **Targets:** `server/osc_dash.py`; `tests/test_osc_dash_integration.py`.
- **Build:** expose nullable per-file `windows_5m` / `windows_15m` from accepted `market_breakdown`; expose aggregate `total_windows_5m` / `total_windows_15m` from the existing deduplicated `(series, duration)` totals. Preserve old fields and all existing source semantics.
- **Helper skill:** `test-driven-development` was followed test-first; the requested installed skill/persona files were not available in the checkout/environment and were not simulated.
- **Depends on:** none.
- **Verify:** exact duration split, real zero vs unknown, partial coverage, tier-copy deduplication, and old-policy counts while readiness remains withheld. The focused integration selection passes.

### T2 — Rework Tick Files cards, captions, table, and column-dependent update (M, [Design/UI]) — [x]
- **Targets:** `server/osc_dash.py`.
- **Build:** render the four requested cards in order; explain split durations, verification source, and `≥`; reorder seven headers and row cells; replace estimated snapshot cell with counts and unknown tooltip; refresh the exact window-count cell after its verify request completes; preserve the preferred badge/actions; use a Research Readiness class hook and `colspan=7`; add the minimum responsive card/table styles.
- **Helper skill:** frontend skill files were not available in the checkout/environment and were not simulated. Followed current inline HTML/JS conventions and test assertions.
- **Depends on:** T1.
- **Verify:** targeted HTML contract assertion passes. A horizontally scrollable table wrapper and responsive card grid were added. Browser preview could not verify this build: `127.0.0.1:5515` was already serving the pre-change version, and it was not stopped or replaced. A visual check against the updated branch remains for the operator/review stage.

### Checkpoint 1 (after T1–T2)
- Manifest retains existing fields and exposes exact split counts. Confirmed changes leave Golden Dataset card and backtest dropdown source code untouched. Browser visual inspection is pending.

### T3 — Complete regression coverage and targeted verification (S, [Backend/Logic] + [Design/UI]) — [x]
- **Targets:** `tests/test_osc_dash_integration.py`.
- **Build:** extend manifest/API and Tick Files HTML assertions for absent sidecar, verified zero, partial coverage, deduplication, old policy, labels/order and hook.
- **Helper skill:** TDD was used; incremental-implementation helper files were not available in the checkout/environment and were not simulated.
- **Depends on:** T1, T2.
- **Verify:** `python -m pytest tests/test_osc_dash_integration.py -q` → 196 passed, 4 existing FastAPI `on_event` deprecation warnings. No full local suite.

## Checkpoint 2 (after T3)
- Focused tests pass. Scope excludes collector, verification logic, Golden Dataset card, and backtest dropdown.
- `observability-and-instrumentation` follow-up: this additive cached manifest mapping does not perform work beyond existing sidecar aggregation and needs no new instrumentation; no new logs/metrics added.

## Station III implementation notes
- The branch was shortened to satisfy the issue skill's 50-character slug rule: `i303/dashboardtick-files-rework-summary-cards-table-dro`.
- Searchable installed helper-skill files/personas were not present in the shared checkout. Implemented directly with repository conventions; no unavailable persona was simulated.
- One unrelated test fixture initially failed because its handcrafted sidecar omitted the cache-required `series_counts` field; adding the required fixture field made the intended stale-policy test pass.
- A running dashboard preview on port 5515 served the pre-change version; left that service running and unmodified. The automated HTML assertions and API tests validate the current source, but a live visual check against this branch remains for the next review.
