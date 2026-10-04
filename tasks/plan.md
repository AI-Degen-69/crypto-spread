Branch: i436/feat-dashboard-enable-full-golden-dataset-folder | Issue: #436

# Implementation Plan — Issue #436: Enable Full Golden Dataset Folder Backtest from Dashboard UI

## 1. Context & CodeRabbit Plan Intake
- **Target Issue**: #436 (`feat(dashboard): enable full golden dataset folder backtest from dashboard UI`)
- **CodeRabbit Plan Intake**: None present in issue comments (`@coderabbitai plan` requested, no response generated).
- **Codebase Findings**:
  - `backtest/engine.py` and `backtest/index.py` already support directory paths natively.
  - `server/osc_dash.py:_resolve_tick_file` strictly requires `candidate.is_file()`, blocking `file="golden"`.
  - `server/osc_dash.py:api_ticks_manifest` only inspects individual `.jsonl` files and misses the aggregate `golden/` folder.
  - `#btFileSelect` dropdown and `updateBtRuntimeEstimate` lack representation and breakdown for the full golden dataset.

## 2. One Improvement Proposal
- **Proposal**: Aggregate the 6 days' `market_breakdown` arrays from `run/ticks/golden/golden_manifest.json` into the `golden` manifest entry. This equips the frontend `calculateBtEstimatedRuntime()` with immediate, accurate token (BTC/ETH/BNB/SOL/XRP) and duration (5m/15m) filtering for the full golden dataset rather than only a flat baseline.
- **Classification**: Simplification / edge-case hardening (adopted by default).

## 3. Dependency Graph & Task Breakdown

- **Task 1: Directory Resolution & Golden Manifest Aggregator**
  - **Size**: S
  - **Domain**: `[Backend/Logic]`
  - **Files**: `server/osc_dash.py`
  - **Depends on**: None
  - **Helper skill**: `api-and-interface-design`, `test-driven-development`
  - **Details**:
    - Update `_resolve_tick_file(file: str)` to permit directory candidates when `len(parts) == 1 and parts[0] in _TICKS_SUBDIR_ALLOWLIST` (specifically `"golden"`), returning `("ok", candidate)`. Preserve all traversal and allowlist protections.
    - Update `api_ticks_manifest()`:
      - Read `run/ticks/golden/golden_manifest.json` when present.
      - Extract totals (4,910 windows, 0 gaps, certified) and aggregate `market_breakdown` across all 6 days.
      - Add `out["golden"] = golden_entry` and append `golden_entry` (`name="golden"`) into `out["files"]`.
      - Guard `_aggregate_ticks()` so it aggregates only files (`p.is_file()`), avoiding errors on directory entries.
  - **Verification**: Targeted integration tests in `tests/test_osc_dash_integration.py` for `_resolve_tick_file("golden")` and `GET /api/ticks/manifest`.

- **Task 2: Backtest Scope Dropdown & Runtime Estimator Integration**
  - **Size**: S
  - **Domain**: `[Design/UI]`
  - **Files**: `server/osc_dash.py`
  - **Depends on**: Task 1
  - **Helper skill**: `frontend-ui-engineering`
  - **Details**:
    - In `loadManifest()` in `server/osc_dash.py`, format the `golden` option prominently in `#btFileSelect`:
      `★ Golden Dataset / 4,910 Windows (Certified)`
    - Ensure `#btFileSelect` preserves `golden` selection across refreshes.
    - Confirm `calculateBtEstimatedRuntime()` properly detects `file="golden"` and computes accurate runtime estimates from its `market_breakdown`.
  - **Verification**: Node/integration tests in `tests/test_osc_dash_integration.py` asserting dropdown option generation and runtime calculation with `file="golden"`.

- **Task 3: Backtest Streaming Endpoints & E2E Verification**
  - **Size**: S
  - **Domain**: `[Backend/Logic]`
  - **Files**: `server/osc_dash.py`, `tests/test_osc_dash_integration.py`
  - **Depends on**: Task 1, Task 2
  - **Helper skill**: `test-driven-development`
  - **Details**:
    - Verify that `/api/backtest`, `/api/backtest/stream`, and `/api/backtest/sweep` run seamlessly when `file="golden"` is passed.
    - Add comprehensive test cases in `tests/test_osc_dash_integration.py`:
      - `test_resolve_tick_file_golden_directory()`
      - `test_api_ticks_manifest_surfaces_golden_dataset()`
      - `test_bt_file_select_includes_golden_option()`
      - `test_api_backtest_with_golden_directory()`
  - **Verification**: `python -m pytest tests/test_osc_dash_integration.py -k "golden or backtest or tick_file" -q` passing with 0 errors.

## 4. Checkpoints
- **Checkpoint 1** (after Task 1): `_resolve_tick_file("golden")` returns `("ok", TICKS_DIR / "golden")` and `GET /api/ticks/manifest` returns the certified golden dataset with 4,910 windows.
- **Checkpoint 2** (after Task 2 & 3): Full backtest dropdown and replay endpoints execute with `file="golden"`, all targeted integration tests green.
