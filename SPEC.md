# SPEC — Issue #279: auto-pick healthiest tick file as the default backtest dataset

## Goal
Every backtest/sweep replay silently mixes partial or corrupted captures with good ones,
because the Backtest tab always defaults to "All Files (Default)". The health data to avoid
that already exists in the verify cache (`integrity_status`, `capture_state`,
`readiness.level`, `windows_count` per file). Make the dashboard act on it: compute a
server-side "preferred" ranking in `/api/ticks/manifest`, badge the winner, and pre-select it
as the Backtest dataset on first load.

## Acceptance criteria
1. `/api/ticks/manifest` returns `preferred_file` (name or `null`) and per-file
   `is_preferred`, derived **only** from cached verify data, with the deterministic tie-break
   `readiness.level → windows_count desc → mtime desc`.
2. Eligibility = `integrity_status == "PASS"` AND `capture_state.label == "COMPLETE CAPTURE"`.
   WARN/FAIL, uncached, and stale-policy files are never eligible.
3. Tick Files table shows exactly one ★ Preferred badge when a winner exists, none otherwise.
4. Backtest dropdown marks the preferred option with `★` and pre-selects it on fresh load
   (also setting `window.selectedBacktestFile`); a manual selection — including All Files —
   persists across `loadManifest()` refreshes; when nothing qualifies, default stays
   "All Files (Default)".
5. No change to `/api/ticks/verify`, the verify engine, readiness thresholds, backtest math,
   or other tabs.
6. `python -m pytest tests/test_osc_dash_integration.py tests/test_theme_tokens.py -q` passes.

## Edge cases
- Empty `run/ticks/` → `files: []`, `preferred_file: null`, UI unchanged.
- No verify cache for any file → `preferred_file: null` (no re-verify is ever triggered).
- All files WARN/FAIL → `preferred_file: null`.
- Multiple PASS+COMPLETE files → total order by (level, windows_count, mtime); the files
  list is name-sorted upstream, so equal keys resolve stably by name.
- Stale cache: `integrity_status`, `capture_state`, and `readiness` are all gated on
  `cache_current` (`_readiness_cache_is_current`); a stale sidecar leaves every
  eligibility field null — ineligible is the safe default.

## Explicit out of scope
Forcing re-verify of uncached files; changing verify logic or thresholds; backtest execution
math; the golden/pristine dataset views (issues #281/#292); new dependencies.
