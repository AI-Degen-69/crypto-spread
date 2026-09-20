# SPEC — Issue #270: Flatten and standardize collapsible Backtest sections

## Goal
Make the Backtest tab a flat, scannable workflow of consistently styled peer sections while preserving existing backtest behavior, stable DOM contracts, selected-file state, and chart data. Small Sweep Visual cards must remain legible and open into an accessible, non-mutating detailed chart view.

## User-visible behavior

### Flat collapsible sections
The Backtest tab presents these peer sections in one vertical layout:

1. Backtest Parameters (including the existing parameter groups)
2. Strategy Geometry Preview
3. Overall Execution Results
4. Sweep Visual
5. Per-Series Performance
6. Executed Windows Log

Every major section has the same visual container, heading treatment, spacing, chevron, and collapse behavior. Each heading is a real keyboard-focusable button with:

- `aria-expanded="true|false"` matching visibility;
- `aria-controls` pointing to the unique section body;
- focus-visible styling;
- deterministic initial state and local persistence where the existing parameter accordions use it.

Reopening a section does not lose inputs, selected files, rendered charts, table data, filters, or pagination.

### Sweep Visual charts
The aggregate chart and all ten market charts use the existing Chart.js data and best-result semantics. Small cards:

- use compact, readable tick formatting and density appropriate to their card width;
- do not visibly overlap labels or obscure bars at supported dashboard widths;
- are keyboard focusable and activatable by click, Enter, or Space;
- expose an accessible name identifying the chart and market.

Activation opens one floating detail view with `role="dialog"`, an accessible label, a substantially larger canvas, readable axes, tooltips, the full tested-value set, and the same best-result highlighting. It has a visible close control, Escape support, focus-visible styling, focus moved into the dialog, and focus returned to the triggering card on close. Opening, closing, or resizing the detail view never calls a backtest/sweep endpoint and never changes selected parameters.

### Canonical market labels
All Backtest labels in Per-Series Performance, Executed Windows Log rows, and Executed Windows Log filter options use the shared duration-first format:

`05m BTC`, `05m ETH`, `05m BNB`, `05m SOL`, `05m XRP`, `15m BTC`, `15m ETH`, `15m BNB`, `15m SOL`, `15m XRP`.

The stable series slug/value, sorting, filtering, pagination, and API payloads remain unchanged. The old suffix format (`BTC 5m`, `BTC 15m`) is not rendered in these Backtest areas.

## Existing contracts to preserve

- No changes to `/api/backtest`, `/api/backtest/sweep`, or their response schemas.
- No changes to backtest calculations, sweep axes, timing semantics, chart data, live cockpit layout, or unrelated tabs.
- Preserve existing stable IDs including `btFileSelect`, `btOffset`, `btQueue`, `btOverallCard`, `btSweepCard`, `chartSweepAgg`, `btSweepGrid`, `btSeriesTableWrap`, `btTradesTableWrap`, and all chart/table controls.
- Keep explicit-run behavior: opening the Backtest tab remains read-only; only explicit Run actions invoke simulations.
- Continue using the repository's existing Chart.js and CSS design tokens; add no dependency.

## Edge cases
- Empty and zero-result sweep data still renders an understandable empty state and does not open a broken dialog.
- A detail view can be opened for the aggregate chart and each rendered market card, including a best market card.
- Re-rendering a sweep replaces stale chart instances and does not leave duplicate modal listeners or orphaned canvases.
- Escape, close-button activation, and reopening after collapse all work after repeated open/close cycles.
- Narrow supported widths do not create horizontal overflow; cards remain readable at 320px, 768px, 1024px, and 1440px.
- Missing or unknown series labels fall back safely without changing stable filter values.

## Out of scope
Backtest/replay mathematics, API contracts and timing semantics from Issue #269, sweep axes or tested values, live trading/cockpit layout, unrelated dashboard tabs, new external dependencies, and changes to the selected-file or explicit-run model.

## Verification commands
- `python -m pytest tests/test_osc_dash_integration.py tests/test_theme_tokens.py -q`
- Browser preview at supported widths: inspect collapse/reopen, chart legibility, modal focus/Escape/return-focus, canonical labels, and console/network errors.
