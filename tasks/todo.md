# Issue #270 Planning Checklist

- [x] Add the six flat peer Backtest sections while preserving stable IDs and controls
- [x] Generalize accessible collapse semantics and persisted state to every section
- [x] Standardize Per-Series Performance and Executed Windows Log labels to `05m BTC` / `15m BTC`
- [x] Make aggregate and ten per-series Sweep Visual cards legible at supported widths
- [x] Add reusable view-only expanded chart dialog with focus management and Escape close
- [x] Verify selected-file state, explicit Run behavior, filters, pagination, and collapse/reopen after results
- [x] Run focused tests and browser verification with zero console/network errors
- [x] Confirm no API, calculations, sweep data, timing, or unrelated tab changes

## Targeted verification

`python -m pytest tests/test_osc_dash_integration.py tests/test_theme_tokens.py -q`

## Browser verification

- Backtest tab at 320px, 768px, 1024px, and 1440px: flat alignment, no horizontal overflow, no small-chart text overlap.
- Toggle each section with mouse and keyboard; verify `aria-expanded`/visibility and preserved controls/results.
- Run one explicit backtest and one Sweep Visual; verify opening the tab or chart does not create a new request.
- Open aggregate and market charts; verify larger axes/tooltips/full tested values, best highlighting, close button, Escape, focus return, and repeated open/close.
- Verify ten canonical labels in the series table, log rows, and filter options; confirm old suffix labels are absent.
- Inspect console and network logs for zero feature-caused errors.
