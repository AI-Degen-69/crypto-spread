# Plan — Issue #295: List pristine tick files in the dashboard as distinct datasets

Branch: `i295/list-pristine-tick-files-in-the-dashboard` | Issue: #295
Stack: Python 3, FastAPI, pytest · Size: **Small** (one module `server/osc_dash.py` + its test file;
disambiguation design already settled in the CodeRabbit plan on the issue) ·
Task type: **Code** (Backend/Logic + light embedded-frontend touch)

## Resolved inputs (from issue + CodeRabbit plan, no open questions)

- Display label AND request value for a pristine file = the relative path
  `pristine/<basename>` (single string, round-trips via `encodeURIComponent`).
- Verify-cache sidecar mirrors the subdirectory: `.verify_cache/pristine/<basename>.json`,
  parent dirs created on write (collision-free by construction).
- Resolver allow-list contains exactly one approved subdirectory: `pristine`.
  Traversal (`..`, `\`, absolute, leading `/`), >2 segments, and unlisted first segments stay rejected.
- Preferred ranking needs no code change — it keys on dict fields; `is_preferred` is exact `name` equality.
- Delete action for pristine entries is omitted (delete endpoint stays top-level-only; out of scope).
- Only `pristine/` is scanned; `golden/`, `quarantine/`, `.verify_cache/` stay hidden.

## Tasks (atomic slices, dependency-ordered)

### T1 — Shared allow-listed tick-file resolver `[Backend/Logic]` (S)
- File: `server/osc_dash.py` (near the verify-cache helpers).
- Add `_TICKS_SUBDIR_ALLOWLIST = {"pristine"}` and `_resolve_tick_file(file: str)` returning a
  discriminated result: `("ok", Path)` / `("invalid", None)` / `("not_found", None)`.
- Rejects: backslash, `..`, absolute paths, leading `/`, >2 segments, first segment not in allow-list;
  keeps the containment check (`relative_to(TICKS_DIR.resolve())`, `ValueError` → invalid).
- Verification: unit-level asserts in `tests/test_osc_dash_integration.py` via the endpoint tests (T5).
- Depends on: —
- [x] Done

### T2 — Route the three endpoints through the resolver `[Backend/Logic]` (S)
- File: `server/osc_dash.py`.
- `api_backtest` (~:1360), `api_backtest_sweep` (~:1521), `api_ticks_verify` (~:2487):
  replace duplicated inline `"/" in file` guards with `_resolve_tick_file`, mapping each result to the
  endpoint's existing error shape/status code (messages preserved verbatim so current tests pass).
- Verification: existing rejection tests in `tests/test_osc_dash_integration.py` still pass unchanged.
- Depends on: T1
- [x] Done

### T3 — Subpath-aware verify-cache sidecars `[Backend/Logic]` (S)
- File: `server/osc_dash.py`.
- `_verify_sidecar_path` accepts a relative name (`pristine/<basename>`) → `TICKS_DIR / .verify_cache / f"{rel}.json"`.
- `_write_verify_sidecar` derives the relative name from `target.relative_to(TICKS_DIR)` and
  `mkdir(parents=True, exist_ok=True)` on the sidecar parent.
- Update sidecar-path constructions that use `f.name` / `entry["name"]` (`_aggregate_ticks` cache reads
  ~:490–503, manifest read ~:623) to go through `_verify_sidecar_path` with the relative name.
- `_VERIFY_REPORT_CACHE` stays keyed by the relative name the endpoints receive.
- Verification: T5 sidecar-mirroring asserts.
- Depends on: T1
- [x] Done

### T4 — Manifest listing of pristine files + frontend carry-through `[Backend/Logic] + [Frontend]` (M)
- File: `server/osc_dash.py`.
- `api_ticks_manifest` (~:585–662): after the top-level scan, scan `TICKS_DIR / "pristine"` when present;
  same filters (`.jsonl`/`.gz`/`.jsonl.gz`, skip `.idx`, `is_file()`); entry `name = "pristine/<basename>"`;
  stats computed from the real path; add `is_pristine: True`; sidecar/fingerprint read through the relative
  name so `market_breakdown`/`readiness`/`integrity_status`/`capture_state`/`windows_count` populate exactly
  like top-level entries. No recursion into other subdirectories.
- Confirm `pick_preferred`/`_file_rank_key` rank pristine entries unchanged (dict-field keyed);
  `is_preferred` by exact `name` equality (pristine can win and be starred).
- Embedded frontend `loadManifest()` (~:5660): `f.name` stays option label+value (encoder handles `/`);
  DOM ids (`verify_arrow_*` etc.) built from a sanitized token (`/` → `_`); delete action omitted for
  `is_pristine` entries.
- Verification: T5 manifest/ranking tests; dashboard smoke via targeted tests (UI polish is Station IV's browser gate).
- Depends on: T1, T3
- [x] Done

### T5 — Tests with pristine fixtures `[Tests]` (M)
- File: `tests/test_osc_dash_integration.py`.
- Extend `_write_verify_sidecar` helper (:372) to accept a relative name like `pristine/ticks_<day>.jsonl`,
  create the file under `pristine/` and the sidecar at the mirrored path.
- New tests:
  1. **Manifest disambiguation:** same basename at top level and in `pristine/` → both listed with distinct
     `name`s; pristine entry carries its own `readiness`/`integrity_status`.
  2. **Ranking:** pristine entry wins → `preferred_file == "pristine/<basename>"`, `is_preferred is True`.
  3. **Resolution:** `/api/backtest` and `/api/backtest/sweep` with `pristine/<basename>` replay the pristine
     file (not the day file); `..` and backslash values still rejected.
- Verification: `python -m pytest tests/test_osc_dash_integration.py tests/test_theme_tokens.py -q`.
- Depends on: T2, T4
- [x] Done

**Checkpoints:** after T2 (resolver live, all existing tests green) and after T4 (manifest lists pristine).

## Explicitly out of scope (per issue)
Least-bad ranking tiers for pristine, extractor/golden-charter changes, verify-engine changes,
delete-endpoint routing, any collector change.

## Improvement proposal (adopt-by-default, evidence-based)
The CodeRabbit plan's Task 3 says to update "every other sidecar-path construction that currently uses
`f.name` or `entry[\"name\"]`" — evidence from the issue: "the backtest resolves datasets by bare name
(`TICKS_DIR / file`), so listing them needs a disambiguation design". Proposal: implement T1's resolver as the
*single* resolution point and have T4's manifest read sidecars via the same relative-name helper (rather than
patching each `f.name` construction in place), so future subdirectories (e.g. `golden/` for #292) are a
one-line allow-list change. This is simplification within the issue's own scope — adopted.

## Rejections recorded
None — no proposal was rejected this session.
