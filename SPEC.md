# SPEC — Issue #294: Rank the least-bad tick file as preferred when no file fully qualifies

## Goal
The live repository holds 6 day files, all `PARTIAL CAPTURE`. Issue #279 shipped
`pick_preferred()` which requires `PASS` + `COMPLETE CAPTURE` to be eligible — a perfectly
correct tier-1 rule, but it means `preferred_file` is always `null` and no file ever gets the
star. The operator wants best-of-available: when no file meets the tier-1 bar, rank the
least-bad available file as a tier-2 fallback so one file is always starred, badged, and
pre-selected as the backtest default.

## Acceptance criteria
1. With only PARTIAL CAPTURE files present, `/api/ticks/manifest` returns the least-bad file
   as `preferred_file` with exactly one `is_preferred: true` row.
2. Tier-1 rule unchanged: PASS + COMPLETE CAPTURE files still outrank any partial file.
   Stale-policy sidecars stay ineligible (no eligibility fields ⇒ no ranking).
3. Badge and dropdown label distinguish **"★ Best available"** (tier 2) from the tier-1
   **"★ Preferred"** star.
4. `preferred_tier` (1 or 2) is returned alongside `preferred_file` so the UI can pick
   the right wording without client-side guessing.
5. `python -m pytest tests/test_osc_dash_integration.py tests/test_theme_tokens.py -q`
   passes.

## Tier-2 total order (defined)
Among files that fail tier-1 eligibility, rank by:
1. `integrity_status`: PASS > WARN > everything else (FAIL, None) — PASS is better even
   without COMPLETE CAPTURE.
2. `capture_state.label`: COMPLETE CAPTURE > PARTIAL CAPTURE > everything else.
3. `readiness.level`: RESEARCH_READY (2) > EXPLORATORY (1) > other/None (0).
4. `windows_count` desc.
5. `mtime` desc.
Files with all eligibility fields null (no cached sidecar, or stale policy) score zero
on every axis — they are the least-bad fallback of last resort, not actively promoted.

## Edge cases
- Empty `run/ticks/` → `files: []`, `preferred_file: null`, `preferred_tier: null`.
- No verify cache for any file → all eligibility fields null; the file with highest
  windows_count/mtime wins tier 2 (0,0,0,windows,mtime) — a valid least-bad pick.
- All files PASS + COMPLETE → tier-1 winner as before; `preferred_tier: 1`.
- Mix of PASS+COMPLETE and PARTIAL → tier-1 wins; `preferred_tier: 1`.
- All files PARTIAL → tier-2 winner; `preferred_tier: 2`, badge says "Best available".
- Single file → it always wins (tier 1 or tier 2 depending on its status).

## Explicit out of scope
Listing pristine files (sibling #295); changing verify logic or thresholds; backtest
math; forced re-verifies; new dependencies.
