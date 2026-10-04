# Specification — Issue #436: Enable Full Golden Dataset Folder Backtest from Dashboard UI

## 1. Overview & Problem Statement
Currently, the CLI backtest engine supports replaying the entire `run/ticks/golden` folder across all 6 certified days (4,910 windows). Furthermore, `backtest/engine.py` and `backtest/index.py` natively support directory sources.
However, in the dashboard UI (`server/osc_dash.py`):
1. `_resolve_tick_file()` strictly rejects directories (`candidate.is_file()` required), returning `("not_found", None)` for `file="golden"`.
2. `api_ticks_manifest()` only lists individual daily files (`golden/ticks_YYYY-MM-DD.jsonl`) under `golden/`, with no entry representing the full certified folder.
3. The `#btFileSelect` dropdown only allows individual files or uncertified `All Files (Default)` (which scans root `run/ticks/`).
4. Replay runtime estimation lacks aggregated window and market breakdown data for the full golden dataset.

## 2. Goals
- Allow `_resolve_tick_file("golden")` to resolve cleanly to `(TICKS_DIR / "golden")`.
- Expose the aggregated `golden` dataset in `api_ticks_manifest()` using certified totals and aggregated market breakdowns from `run/ticks/golden/golden_manifest.json`.
- Surface `★ Golden Dataset / 4,910 Windows (Certified)` in the `#btFileSelect` dropdown on the Backtest page.
- Enable running single and streaming backtests across the full golden dataset from `/api/backtest`, `/api/backtest/stream`, and `/api/backtest/sweep` via `file=golden`.
- Maintain strict security: allow-list validation and path-traversal prevention remain fully enforced.

## 3. Explicitly Out of Scope
- Modifying offline CLI backtest behavior or replay engine core logic (`backtest/engine.py`).
- Creating or re-certifying golden dataset files (the certified dataset in `run/ticks/golden/` is already authoritative).

## 4. Technical Specifications & Interface Changes

### 4.1. File Resolver: `server/osc_dash.py:_resolve_tick_file`
- If `candidate.is_dir()`:
  - Allow if `len(parts) == 1 and parts[0] in _TICKS_SUBDIR_ALLOWLIST` (specifically `"golden"`).
  - Return `("ok", candidate)`.
  - Disallow arbitrary or sub-nested directories.
- If not a directory:
  - Must exist and be a file (`candidate.is_file()`).

### 4.2. Manifest Endpoint: `server/osc_dash.py:api_ticks_manifest`
- Read `run/ticks/golden/golden_manifest.json` when present.
- Extract certified totals (`windows_count: 4910`, `status: "certified"`).
- Aggregate `market_breakdown` across the 6 days to support series/duration filtering.
- Populate `out["golden"]` object and include `{"name": "golden", ...}` in `out["files"]`.
- Guard `_aggregate_ticks` against attempting file-only stats on directory entries.

### 4.3. Dashboard UI: `#btFileSelect` & Runtime Estimation
- In `loadManifest()`, render the `golden` option prominently:
  `★ Golden Dataset / 4,910 Windows (Certified)`
- When `file=golden` is selected, `calculateBtEstimatedRuntime` uses `market_breakdown` / `windows_count` to compute interactive replay runtime estimates.

## 5. Acceptance Criteria
- [ ] `_resolve_tick_file("golden")` resolves cleanly to `(TICKS_DIR / "golden")` without `404` / `not_found`.
- [ ] `GET /api/ticks/manifest` surfaces the aggregated `golden/` dataset with certified window counts from `golden_manifest.json`.
- [ ] The dashboard Backtest UI `#btFileSelect` dropdown contains an entry for the full Golden dataset (e.g. `★ Golden Dataset / 4,910 Windows (Certified)`).
- [ ] Running a backtest with `file=golden` via `/api/backtest` and `/api/backtest/stream` executes across all 6 golden days and returns aggregate results.
- [ ] `python -m pytest tests/test_osc_dash_integration.py -k "golden or backtest or tick_file" -q` passes.
