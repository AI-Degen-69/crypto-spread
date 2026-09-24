Branch: i319/dashboard-jungle-king-tab-present-the-ofat | Issue: #319

# Plan — Jungle King tab (visual quick reference for the OFAT manifest)

## Size & type
- **Tier: Standard** — 2 surfaces in one file (`server/osc_dash.py` endpoint +
  SPA markup/JS), one test file, no schema/dependency change. Rationale: the
  dashboard is a single-file monolith, so "2–5 files" collapses to one file and
  its tests; scope is still a new tab + new API = Standard.
- **Task type: Code** (Backend/Logic + a Design/UI slice).

## Stack
Python 3 / FastAPI, pytest (TestClient), vanilla JS inside `FULL_APP_HTML`.
No new dependencies (CONSTRAINTS-319.md).

## Open questions → resolved from code (issue #319 had one)
- **Static HTML vs served tab → SERVED TAB.** Evidence: every dashboard surface
  is a `switchTab()` pane in the SPA (`server/osc_dash.py:4845`), the four tab
  routes all serve `FULL_APP_HTML` (`server/osc_dash.py:9016-9022`), the repo
  unified the dashboard onto one canonical port 5515 (commit fdee9bb, #316), and
  `docs/issue-workflow.md` Station II prescribes "resolve open questions from
  code". A static page would fork the operator's entrypoint for zero gain.
  Recorded on #319 as a resolution comment. No operator questions remain.

## Improvement proposal (Step 5, evidence-based)
- **Proposal: server-side registry join.** Folded into the plan (simplification
  — adopt-by-default). Evidence, verbatim from `docs/glossary.md`:
  > **a tuning knob** is swept and tuned freely: offset, stop loss, entry
  > delay, share size.
  and from the issue body:
  > Labels for shared parameters come from the parameter registry
  > (`BacktestParams.param_spec()`), not a second hand-written copy — the drift
  > defect issue #164 fixed must not be reintroduced.
  `server/osc_dash.py:164` issue-comment ("Issue #164: this is the single
  source of truth") confirms the registry is where labels live. Joining
  manifest ↔ registry in Python (one place) instead of fetching
  `/api/params/spec` from the tab's JS and joining client-side keeps the label
  in exactly one code path and makes the contract testable with TestClient.

## Baseline provenance guard (do NOT "fix" during build)
`param_ranges.json` baselines `queue_gate=50.0`, `quote_shares=120` mirror the
operator-replicable CLI defaults (`scripts/backtest.py:51` `default=50.0`,
`:58` `default=120`) and the README checklist — **not** the engine defaults
(`backtest/engine.py:116-117`: `queue_gate=0.0`, `quote_shares=5`). The viewer
presents the manifest as-is; "aligning" baselines to engine defaults is a
defect, not a cleanup. Locked in SPEC-319.md.

## Sub-issues (tracker mirrors the plan)
- T1 → #320 (endpoint; no blockers)
- T2 → #321 (tab skeleton; blocked-by #320)
- T3 → #322 (presentation; blocked-by #320, #321)
Family cross-references posted on each; #319 is the parent.

## Tasks (dependency graph first, risk-first)

### TASK-1 (T1, #320) — `GET /api/jungle-king` endpoint [x]
- Size: **M** · Tag: **[Backend/Logic]** · Verification: **pytest (TestClient)**
- Target files: `server/osc_dash.py` (endpoint near `/api/params/spec`,
  ~line 991), `tests/test_osc_dash_integration.py` (new tests, RED first).
- Build: read `research/jungle-king/param_ranges.json` per request (path
  resolved from the repo root the server runs from); group into the manifest's
  four sections in manifest order; join each param with the registry
  (`param_spec()`): label/unit/param_class (per-slug exit keys inherit the
  parent `exit_thresh_by_slug` class = tuning, label derived from that single
  entry); `baseline` + `values` verbatim from the manifest; `baseline_in_values`
  computed; `registry` object for shared params (label/why/default/bounds) or
  null. Missing/corrupt file → JSON error body + non-200, never a traceback.
- Depends on: none (root).
- Done when: new tests green; `python -m pytest tests/test_osc_dash_integration.py tests/test_param_registry.py -q` green.

### TASK-2 (T2, #321) — tab skeleton: sidebar button + container + hook
- Size: **S** · Tag: **[Backend/Logic]** (markup wiring) · Verification: **pytest + curl-free TestClient HTML assertions**
- Target files: `server/osc_dash.py` (sidebar nav block ~line 3208-3218, tab
  content ~line 3624, `switchTab()` ~line 4845).
- Build: sidebar button `tab-btn-jungleking` ("Jungle King", after Backtest
  Sweeper, before Stats Summary); `<div id="tab-jungleking" class="tab-content">`
  card layout with four group sections; `switchTab()` hook
  `if(name==='jungleking') loadJungleKing();`; stub `loadJungleKing()` that
  fetches `/api/jungle-king` once and renders into the container.
- Depends on: TASK-1 (the hook fetches the new endpoint).

### TASK-3 (T3, #322) — presentation: baseline, chips, class badges, empty state [x]
- Size: **M** · Tag: **[Design/UI]** · Verification: **pytest (render-level) + browser check on :5515**
- Target files: `server/osc_dash.py` (JS + CSS inside `FULL_APP_HTML`).
- Build: one card per parameter; baseline value prominent + highlighted chip in
  the values row; candidate values as chips/pills (scannable, manifest order);
  class badge per parameter (tuning knob / structural limit / execution
  assumption per ADR-0003); graceful inline notice on fetch error/empty payload
  (empty-state convention, not a blank screen); read-only by construction — no
  buttons, no POSTs. Labels rendered only from the served payload.
- Depends on: TASK-1, TASK-2.

## Checkpoints
- After TASK-1: one-line progress report (Mode A) — endpoint JSON verified by tests.
- After TASK-3: browser check on :5515 (tab reachable, chips scannable, no console errors) — then Station IV.

## Verification (issue-level)
- `python -m pytest tests/test_osc_dash_integration.py -q` — covers the SPA
  anchors, the endpoint contract, and render-level assertions (new tests).
- `python -m pytest tests/test_param_registry.py -q` — guards the untouched
  `/api/params/spec` / `BacktestParams` contract (zero-regression gate).
- Full suite stays with CI on push (AGENTS.md policy).
