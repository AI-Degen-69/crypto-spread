# Plan — Issue #279: auto-pick healthiest tick file as the default backtest dataset

Stack: Python 3 + FastAPI, pytest · Size: **Standard** (one module + its endpoint/UI tests;
2–3 files, one ranking decision) · Type: **Code** (Backend/Logic + light UI) ·
Branch: `feat/preferred-tick-file-279`

## Resolved inputs (planning record)
- Issue supplies an exact eligibility formula and tie-break chain; no `needs-answers` label,
  no open questions → nothing to resolve from the operator.
- `code-explorer` persona skipped: not Large/unfamiliar code — the issue names every reuse
  point (`api_ticks_manifest()`, `loadManifest()`, `btFileSelect`) with file:line.
- `type-design-analyzer` persona applied to the frozen ranking contract (findings in
  "Interface contracts" below): ranking must be pure and derived-only-from-arguments so the
  endpoint cannot disagree with itself between files entries.
- Sub-issue mapping skipped: skill reference `references/issue-tracker.md` does not exist on
  disk; 4 linear tasks stay tracked here + `tasks/todo.md` only (same as plan #290).
- Verified in code: `api_ticks_manifest()` (`server/osc_dash.py:533`) already enriches every
  file with `integrity_status`, `capture_state`, `readiness.level`, `windows_count` from the
  verify cache (`:566-580`); the `All Files (Default)` option is the empty-value option of
  `btFileSelect` (`:2930`); `window.selectedBacktestFile` mirrors the dropdown on manual
  change (`:8277-8281`) and `loadManifest()` restores `sel.value || selectedBacktestFile`
  across refreshes (`:5602`, `:5622-5624`) — so a manual "All Files" pick already survives
  reloads via the empty-string value; only the **first** load (both empty) needs the
  preferred pre-select.

## Spec
See `SPEC.md` (Standard size). One-line spec: rank verify-cache-healthy files in
`api_ticks_manifest()`, expose `preferred_file` + per-file `is_preferred`, badge the winning
Tick Files row with ★, and pre-select the winner in the Backtest dropdown on first load —
falling back to today's "All Files (Default)" behavior when nothing qualifies.

## Interface contracts (frozen before logic)
- `pick_preferred(files) -> dict | None` — **pure** function over the `out["files"]` entries.
  Eligible = `integrity_status == "PASS"` AND `capture_state.label == "COMPLETE CAPTURE"`
  (string compare on the exact labels `scripts/verify_tick_data.capture_state()` emits).
  Rank: `readiness.level` (RESEARCH_READY=2 > EXPLORATORY=1 > INSUFFICIENT/other=0) →
  `windows_count` desc → `mtime` desc (deterministic total order; files list is already
  name-sorted upstream so equal keys keep stable name order). Returns the winning entry
  reference or `None` when zero files qualify. No I/O, no globals.
- Endpoint payload additions (additive only, no field renamed or removed):
  `out["preferred_file"] = <name> | None`; each file entry gains `is_preferred: bool`
  (exactly one `true` when a winner exists, all `false` otherwise).
- UI contract: the ★ lives in the file-name cell of the Tick Files row
  (`.loadManifest()` table, `:5673-5720+`) and in the dropdown option label; the badge is a
  `<span title="…">` styled with existing theme tokens (`--gold`) — no new CSS file, no new
  dependency.
- Pre-select contract: on `loadManifest()`, when neither an existing `sel.value` nor
  `window.selectedBacktestFile` is set (`currentVal` falsy) AND `d.preferred_file` names an
  option, set `sel.value` and `window.selectedBacktestFile` to it; any manual choice —
  including re-picking All Files — keeps persisting through `currentVal` exactly as today.
  Backtest request path (`:4855`) reads the dropdown first, so no execution-path change.

## Tasks
- **TASK-1** [Backend/Logic] · Size M · `server/osc_dash.py`, `tests/test_osc_dash_integration.py`
  Add pure `pick_preferred()` + wire `preferred_file` / `is_preferred` into
  `api_ticks_manifest()`. Endpoint tests: PASS+COMPLETE winner picked; WARN/FAIL mixes never
  eligible; level tie broken by `windows_count`, then `mtime`; all-unverified (no cache)
  → `preferred_file: null`, every `is_preferred` false; empty `run/ticks/` → `files: []` +
  `preferred_file: null`. · Depends on: — · Verify: targeted pytest (new tests + existing
  manifest endpoint tests at `tests/test_osc_dash_integration.py:208-330` stay green).
- **TASK-2** [Design/UI] · Size S · `server/osc_dash.py` (Tick Files row builder)
  Render exactly one ★ Preferred badge on the winning row's file-name cell (span with tooltip
  naming why: integrity PASS, complete capture, readiness level, window count); zero badges
  when no winner; colors/spacing via existing theme tokens. · Depends on: TASK-1 ·
  Verify: targeted pytest HTML assertions + browser preview of the Tick Files tab.
- **TASK-3** [Design/UI] · Size S · `server/osc_dash.py` (`loadManifest()` `:5595-5625`,
  `btFileSelect` markup `:2928-2931`)
  Mark the preferred option `★` in its label; on first load pre-select `preferred_file` and
  set `window.selectedBacktestFile` (only when nothing stored yet); manual selection —
  including All Files — must keep persisting across `loadManifest()` refreshes; no qualifier
  → default stays "All Files". · Depends on: TASK-1 · Verify: targeted pytest (extend
  `tests/test_theme_tokens.py:127-132` dropdown assertions + inline-script fixtures in
  `tests/test_osc_dash_integration.py`) + browser preview.
- **TASK-4** [Backend/Logic] · Size XS · closeout
  Run the full targeted gate `python -m pytest tests/test_osc_dash_integration.py
  tests/test_theme_tokens.py -q`; tick todos; confirm nothing else changed
  (`git diff --stat`). · Depends on: TASK-1..3 · Verify: targeted pytest green.

Checkpoints: after TASK-1 (endpoint contract proven by tests) and after TASK-3 (UI visible) —
one-line progress reports in Mode A, not approval pauses.

## Improvement proposal (recorded)
- **Adopted (simplification/hardening):** implement the ranking as a pure
  `pick_preferred(files)` helper instead of inlining the comparison in the endpoint —
  evidence, issue verbatim: *"Compute a per-file "preferred" ranking server-side in
  `api_ticks_manifest()` … with a deterministic tie-break (level → windows_count → mtime);
  covered by endpoint tests"*. A pure helper makes that determinism directly unit-testable
  without HTTP fixtures and keeps the endpoint a thin serializer, matching how the file
  already extracts `_aggregate_ticks`.
- *(Rejected proposals would be recorded here with their reason — none so far this session.)*
