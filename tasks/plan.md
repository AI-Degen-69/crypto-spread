# Plan — Issue #367: Sweep Visual: human market names in token colours, and trim trailing decimals from P&L axis labels

Branch: `i367/sweep-visual-human-market-names` | Issue: #367

## Overview
In the Sweep Visual tab (`server/osc_dash.py`), the ten per-market cards currently render raw slugs during progress runs (e.g. `btc-up-or-down-5m`) and display values on the Y-axis with redundant `.00` decimals (`$-200.00`).
We will introduce 3 pure JavaScript presentation helpers (`sweepMarketName`, `sweepTokenColor`, and `formatSweepMoneyTick`), integrate them across `renderSweepVisual`, `openBtChartDetail`, and `sweepChartOptions`, and add unit test coverage in `tests/test_theme_tokens.py`.

## CodeRabbit Intake Summary
- **Adopted:** Pure JS presentation helpers (`sweepMarketName`, `sweepTokenColor`, `formatSweepMoneyTick`), extracting token brand colors from `ALL_COCKPIT_SERIES`, trimming trailing `.00` from Y ticks, and Node harness test coverage.
- **Rejected:** Modifying backend `series_labels` in Python (preserving API contracts and Issue #270 convention).
- **Status:** Verified and ready.

## Tasks

- [x] **Task 1: Add presentation helpers in `server/osc_dash.py`**
  - **Size:** S
  - **Domain:** `[UI/Frontend]`
  - **Files:** `server/osc_dash.py`
  - **Depends on:** None
  - **Details:** Implement `sweepMarketName(slug)`, `sweepTokenColor(slug)`, and `formatSweepMoneyTick(v)` near `formatSweepTickValue` / `sweepChartColors`. Ensure `sweepTokenColor` reads `ALL_COCKPIT_SERIES` dynamically without hardcoding hex literals.
  - **Verification:** Unit assertions in Node harness.

- [x] **Task 2: Connect helpers to Sweep Visual UI and Chart Options**
  - **Size:** S
  - **Domain:** `[UI/Frontend]`
  - **Files:** `server/osc_dash.py`
  - **Depends on:** Task 1
  - **Details:** In `renderSweepVisual`, format card titles and `aria-label` using `sweepMarketName(seriesKey)` and apply `sweepTokenColor(seriesKey)` to the title style. Pass formatted name and color to `openBtChartDetail`. In `openBtChartDetail`, set dialog heading text and color. In `sweepChartOptions`, use `formatSweepMoneyTick(v)` as the Y-axis tick callback. Update best market display in stats row.
  - **Verification:** Browser preview / integration test checks.

- [x] **Task 3: Update and add tests in `tests/test_theme_tokens.py`**
  - **Size:** S
  - **Domain:** `[Test/Integration]`
  - **Files:** `tests/test_theme_tokens.py`
  - **Depends on:** Task 2
  - **Details:** Update `test_sweep_visual_uses_numeric_axis_and_aligned_market_labels` to match updated title rendering and add tests verifying `sweepMarketName`, `sweepTokenColor`, `formatSweepMoneyTick`, and lack of hardcoded hex values.
  - **Verification:** `python -m pytest tests/test_theme_tokens.py -q`.

- [x] **Task 4: Run targeted integration suite and verify regression safety**
  - **Size:** XS
  - **Domain:** `[Verification]`
  - **Files:** `server/osc_dash.py`, `tests/test_theme_tokens.py`
  - **Depends on:** Task 3
  - **Details:** Run targeted test suites to confirm no regressions in dashboard rendering or API behavior.
  - **Verification:** `python -m pytest tests/test_theme_tokens.py tests/test_osc_dash_integration.py -q`.
