# Plan — Issue #292: Golden dataset card in Tick Files with n/N certification checklist

Branch: `i292/golden-dataset-card-tick-files` | Issue: #292
Stack: Python 3, FastAPI, pytest · Size: **Standard** (new backend endpoint + new card in the
embedded frontend + test coverage; one architectural decision already settled by the issue's
defaults: card at the top of Tick Files, golden-shaped fallback) ·
Task type: **Code** (Backend/Logic + Frontend)

## Resolved inputs (from issue + charter `docs/golden-tick-dataset.md`)

- Card location: top of the Tick Files tab (`tab-ticks`), rendered by a new endpoint
  `GET /api/ticks/golden` reading `run/ticks/golden/` and `golden_manifest.json` (charter §3.1).
- States: `absent` (explicit "no golden dataset yet" card, checklist unchecked, pointer to the
  charter — not an error), `present`, `certified` (every §1.1/§1.2 gate checked).
- Set targets (charter §1.2, n/N): windows total n/500, windows-per-market-pair n/50 (worst
  pair), time blocks n/5, valid ticks n/50,000, sampling gap rate ≤ 0.05, all 10 series present
  (`strategy/series.py:SERIES`).
- Per-day gates (charter §1.1): every golden day PASS + COMPLETE CAPTURE, zero corrupt rows,
  zero collector errors, zero time reversals.
- Certification currency: manifest `policy_version` == installed `READINESS_POLICY_VERSION`
  (`scripts/verify_tick_data.py:32`) — stale → unchecked; per-day `.idx` sidecars fresh
  (charter §3 step 3, `backtest.index.is_fresh`).
- Data sources: cached verify sidecars via `_read_verify_cache` / `_verify_sidecar_path`
  (Issue #295's relative-name keys) — **no full re-stream of day files on dashboard load**.
- Out of scope: creating/certifying the golden set (#281), verify/threshold changes, pristine
  view, backtest engine, collector changes.

## Tasks (atomic slices, dependency-ordered)

### T1 — `GET /api/ticks/golden` endpoint `[Backend/Logic]` (M)
- File: `server/osc_dash.py`.
- Reads `run/ticks/golden/`: absent → `{"state": "absent", ...}` with the checklist shape but
  everything unchecked and `reason: "no golden dataset yet"` (never an error).
- Present → reads `golden_manifest.json` (policy_version, per-day verdicts, sha256s), and each
  golden day's verify verdict from its cached sidecar (relative-name key, e.g.
  `golden/ticks_<day>.jsonl`); computes the §1.1 per-day gates and §1.2 set metrics
  (windows total, worst-pair windows from `market_breakdown`, time blocks, valid ticks,
  sampling gap rate, series coverage vs `strategy/series.py:SERIES`).
- Certification currency: policy version match + `.idx` freshness per day.
- Response carries `state`, `manifest`, `days[]`, `checks[]` (each: name, measured, required,
  ok, n/N formatting fields) — the frontend only renders, never computes.
- Verification: T4 tests (absent / stale-policy / certified).
- Depends on: —
- [x] Done

### T2 — Golden card UI at the top of Tick Files `[Frontend]` (M)
- File: `server/osc_dash.py` (embedded `FULL_APP_HTML`).
- Container `<div id="goldenCardWrap">` inserted at the top of `tab-ticks`, filled by
  `loadGoldenCard()` (fetch `/api/ticks/golden`, called on tab open alongside `loadManifest()`).
- Renders: state badge (absent / present / certified), policy-version currency line (stale →
  explicit unchecked warning), and the checklist — every item with ✓/✗, measured-vs-required,
  and n/N progress format (e.g. `2,750 / 500`), grouped: Set (§1.2) / Per-day (§1.1) /
  Certification currency.
- Absent state: explicit "no golden dataset yet" with the checklist shown unchecked and a
  pointer to `docs/golden-tick-dataset.md` — no error styling.
- Verification: T4 tests assert card HTML/JS invariants; visual check in Station IV browser gate.
- Depends on: T1
- [x] Done

### T3 — Wire into the Tick Files tab lifecycle + docs pointer `[Backend/Logic]` (XS)
- File: `server/osc_dash.py`.
- `loadGoldenCard()` runs when the ticks tab opens (same hook that calls `loadManifest()`),
  and after `executeDeleteFileDirect`/verify rescans that could change golden contents —
  simplest correct trigger: tab-open + the existing `loadManifest()` call sites.
- Verification: targeted tests.
- Depends on: T2
- [x] Done

### T4 — Endpoint + card tests `[Tests]` (M)
- File: `tests/test_osc_dash_integration.py`.
- Three states: **absent-golden** (no dir → `state: absent`, checklist unchecked, 200 OK),
  **stale-policy** (golden manifest with old `policy_version` → currency check unchecked),
  **certified** (fixture manifest + fingerprint-matched sidecars → all §1.1/§1.2 checks ok).
- Assert the frontend ships the card container + `loadGoldenCard` and renders `n/N` progress
  (HTML smoke assertions like the existing SPA tests).
- Verification: `python -m pytest tests/test_osc_dash_integration.py tests/test_theme_tokens.py -q`.
- Depends on: T1, T2
- [x] Done

**Checkpoint:** after T1 (endpoint contract proven by tests-first fixtures) and after T3.

## Explicitly out of scope (per issue)
Golden capture/certification itself (#281); verify logic or threshold changes; pristine view;
backtest engine changes; collector changes; a sixth sidebar tab.

## Improvement proposal (adopt-by-default, evidence-based)
The issue says the endpoint "reads from verify caches (no full re-stream of day files on
dashboard load)" and lists `_read_verify_cache` / `_aggregate_ticks` (374–503) as the pattern.
Proposal: reuse Issue #295's relative-name sidecar keys (`_verify_sidecar_path`) for golden
days too — golden files live under `golden/`, so their sidecars land at
`.verify_cache/golden/<day>.json` with zero new cache machinery, and the certification check
reuses `backtest.index.is_fresh` for sidecar currency. Simplification within the issue's own
scope — adopted.

## Rejections recorded
None — no proposal was rejected this session.
