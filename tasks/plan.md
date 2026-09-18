# Task Plan — Issue #198: feat(backtest-ui): interactive visual parameter preview grid for backtest sweeper

**Size tier:** Standard — 2 files (`server/osc_dash.py` and `tests/test_osc_dash_integration.py`).
**Task type:** Design/UI + Core.

## Context
- The Backtest Parameters card (`#tab-backtest` in `server/osc_dash.py`) presents trading controls as isolated numeric inputs (`btOffset`, `btPairCost`, `btExit5m`, `btExitReversal`, `btEntryDelay`, `btQuoteLo`, `btQuoteHi`, `btDeadZoneVal`, `btDeadZoneUnit`).
- Operators currently cannot visualize how these parameters interact geometrically on the price-time grid prior to running sweeps.
- Adding an interactive 2D SVG preview grid directly inside the Backtest Parameters card provides instant visual feedback on price boundaries, spread offsets, stop loss levels, and time gating as inputs change.

## Tasks

- [x] **TASK-1 [Design/UI]**: Add `#btParamPreviewWrap` container, SVG structure, and CSS styles
  - Target files: `server/osc_dash.py`
  - Build: In `#tab-backtest`, add `#btParamPreviewWrap` containing `#btPreviewMetricsPills`, the `<svg id="btParamPreviewSvg">` element, and legend chips. Add clean CSS matching dashboard palette (`--panel2`, `--line`, `--up`, `--down`, `--gold`, `--cyan`, `--dim`).
  - Helper skill: `frontend-ui-engineering`
  - Verify: HTML structure checks in `tests/test_osc_dash_integration.py`.

- [x] **TASK-2 [Frontend/Logic]**: Implement pure client-side SVG renderer `updateBacktestParamPreview()`
  - Target files: `server/osc_dash.py`
  - Build: Implement `updateBacktestParamPreview()` in the SPA JavaScript block. Render Price Y-axis (0.00 to 1.00, quotable range corridor, 0.50 mid reference, resting long/short bids, stop loss boundary, reversal buffer) and Time X-axis (0 to 300s, entry delay zone, active trading corridor, dead zone tail). Update summary pill chips with calculated values. Clamps edge cases (0 offset, 0 stop, inverted bounds).
  - Helper skill: `frontend-ui-engineering`
  - Verify: Client script checks and DOM structure verification.

- [x] **TASK-3 [Frontend/Logic]**: Wire reactive event listeners in `setupBacktestInputListeners()`
  - Target files: `server/osc_dash.py`
  - Build: Attach `input` and `change` listeners to all relevant input fields (`btOffset`, `btPairCost`, `btExit5m`, `btExit15m`, `btExitBtc`, `btExitSol`, `btExitReversal`, `btEntryDelay`, `btQuoteLo`, `btQuoteHi`, `btDeadZoneVal`, `btDeadZoneUnit`). Call `updateBacktestParamPreview()` on init and tab switch.
  - Helper skill: `frontend-ui-engineering`
  - Verify: Integration test checking JavaScript event bindings in served HTML.

- [x] **TASK-4 [QA/Tests]**: Add integration tests and verify zero regressions
  - Target files: `tests/test_osc_dash_integration.py`
  - Build: Add `test_backtest_param_preview_grid_present()` asserting the container, SVG, metric chips, legend, and update function exist in the served HTML.
  - Helper skill: `test-driven-development`
  - Verify: `python -m pytest tests/test_osc_dash_integration.py -q` passes 100%.

## Verification Matrix
| Task | Method |
|---|---|
| TASK-1 | `pytest tests/test_osc_dash_integration.py -k test_backtest_param_preview` |
| TASK-2 | `pytest tests/test_osc_dash_integration.py -k test_backtest_param_preview` |
| TASK-3 | `pytest tests/test_osc_dash_integration.py -k test_backtest_param_preview` |
| TASK-4 | `python -m pytest tests/test_osc_dash_integration.py -q` (all green) |

