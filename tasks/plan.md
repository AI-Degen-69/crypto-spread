# Plan: Issue #398 — Fix inconsistent zero line styling across oscillation charts

Branch: `i398/fix-inconsistent-zero-line-styling-across-osc` | Issue: `#398`

## Classification & Routing
- **Size tier**: Small (single file UI styling in `server/osc_dash.py` + tests)
- **Task type**: Design / UI (`frontend-ui-engineering`, `frontend-design`, `test-driven-development`)

## Summary & Problem Analysis
- **Problem**: In the oscillation summary tab (`renderSummaryCharts` in `server/osc_dash.py`), charts have inconsistent zero line styling. The sweep visual charts use `sweepZeroLinePlugin()` and custom dashed gold zero-line scale grid configuration, while `cPerAsset`, `cHist`, `cStart`, and `cPair` rely on default Chart.js gridlines without zero-line highlights.
- **Solution**:
  1. Add `plugins: [sweepZeroLinePlugin()]` to all oscillation tab charts (`cPerAsset`, `cHist`, `cStart`, `cPair`). Note that for `cStart` (doughnut chart), `sweepZeroLinePlugin` safely guards against missing `chart.scales.y` and gracefully no-ops.
  2. Apply consistent y-axis zero-line grid styling (gold color, width 2, borderDash `[6, 4]`) across the Cartesian bar charts (`cPerAsset`, `cHist`, `cPair`) matching the sweep charts standard.
  3. Add regression integration tests in `tests/test_osc_dash_integration.py` ensuring all oscillation tab chart initializations register the plugin and consistent styling.

## CodeRabbit Intake Note
- Adopted: N/A (no CodeRabbit comment on issue #398).
- Rejected: N/A.
- Unverified: N/A.

## Improvement Proposal (Adopted by Default)
- **Proposal**: Standardize both plugin registration (`plugins: [sweepZeroLinePlugin()]`) and y-scale zero-line grid callbacks across `cPerAsset`, `cHist`, and `cPair` so whether canvas post-drawing or Chart.js grid rendering is evaluated, zero-line styling remains identical in appearance and behavior.
- **Evidence**: `server/osc_dash.py:10049-10053` sets gold dashed grid at y=0 for sweep charts, and `server/osc_dash.py:9878-9900` defines `sweepZeroLinePlugin`.

## Tasks

- [x] **Task 1 (S)**: `[Design/UI]` Standardize zero line plugin & grid styling in oscillation tab charts
  - Target files: `server/osc_dash.py`
  - Details:
    - In `renderSummaryCharts()`, update `cPerAsset`, `cHist`, `cStart`, and `cPair` Chart initializations:
      - Add `plugins: [sweepZeroLinePlugin()]` to all 4 charts.
      - Add unified zero-line `grid` styling to `scales.y` for `cPerAsset`, `cHist`, and `cPair`:
        - `color: function(ctx){ return (ctx.tick && ctx.tick.value === 0) ? theme.gold : theme.line; }`
        - `lineWidth: function(ctx){ return (ctx.tick && ctx.tick.value === 0) ? 2 : 1; }`
        - `borderDash: function(ctx){ return (ctx.tick && ctx.tick.value === 0) ? [6, 4] : []; }`
  - Depends on: None
  - Verification: Targeted browser inspection / node script check

- [x] **Task 2 (S)**: `[Testing]` Add regression integration tests for oscillation charts zero line styling
  - Target files: `tests/test_osc_dash_integration.py`
  - Details:
    - Add test checking that `server/osc_dash.py` contains `sweepZeroLinePlugin()` in `cPerAsset`, `cHist`, `cStart`, `cPair` chart configurations.
    - Verify y-axis grid color/dash styling is present on Cartesian oscillation charts.
  - Depends on: Task 1
  - Verification: `python -m pytest tests/test_osc_dash_integration.py -k "oscillation or zero_line" -q`

- [x] **Task 3 (XS)**: `[Verify]` Verify dashboard integration and syntax
  - Target files: `server/osc_dash.py`
  - Details:
    - Run targeted test suite to confirm zero regressions.
  - Depends on: Task 1, Task 2
  - Verification: `python -m pytest tests/test_osc_dash_integration.py -k "summary or oscillation" -q`

## Checkpoints
- Checkpoint 1 (after Task 1): Oscillation charts in `server/osc_dash.py` configure `sweepZeroLinePlugin` and gold dashed zero line.
- Checkpoint 2 (after Task 2 & 3): Targeted tests pass with zero regressions.
