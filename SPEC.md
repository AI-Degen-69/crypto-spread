# SPEC — Issue #198: interactive visual parameter preview grid for backtest sweeper

## Goal
Add an interactive, real-time 2D visualization preview widget to the Backtest Parameters card (`#tab-backtest` in `server/osc_dash.py`) that renders active operator metrics (Mid, Spread Offset, Quotable Range/Entry Band, Stop Loss, Reversal Buffer, and Window Timing Gates) as a unified 2D price-time coordinate grid in pure SVG.

## Background & Rationale
Currently, the Backtest Parameters card presents trading controls as isolated numeric inputs (`btOffset`, `btPairCost`, `btExit5m`, `btExitReversal`, `btEntryDelay`, `btQuoteLo`, `btQuoteHi`, `btDeadZoneVal`, etc.). Operators cannot easily visualize how these parameters interact geometrically on the price grid — where resting bids sit relative to 0.50 mid, how wide the pair cost and spread are, where adverse price excursion triggers the stop loss, where reversal cancels the stop, or how much of the window lifecycle is guarded by entry delay and the end-of-window dead zone.

A live 2D coordinate preview grid updates instantly as users adjust input values, providing immediate visual feedback on the strategy's geometry before running sweeps.

## UI / UX Architecture & Layout
- **Preview Panel Location:** Inside `#tab-backtest` in the Backtest Parameters card, directly below the tuning knobs / operator accordion sections and above the execution buttons (`#btnRunSweep`).
- **Container Structure:**
  - `#btParamPreviewWrap`: Styled card/panel (`background: var(--panel2); border: 1px solid var(--line); border-radius: 10px; padding: 12px; margin-top: 14px;`).
  - Header with title `📐 Strategy Geometry Preview (2D Price-Time Grid)` and live metric summary chips (`#btPreviewMetricsPills`).
  - SVG Container: `<svg id="btParamPreviewSvg" viewBox="0 0 760 260" preserveAspectRatio="none" style="width:100%;height:260px;display:block;overflow:visible">`.
  - Color-coded legend matching dashboard theme tokens (`--up`, `--down`, `--gold`, `--cyan`, `--dim`, `--faint`).

## Coordinate System & Visual Elements
### 1. Price Axis (Y-Axis, Vertical: 0.00 to 1.00)
- **Margin & Dimensions:** `padLeft: 55px, padRight: 25px, padTop: 20px, padBottom: 35px`.
- **Quotable Range / Admission Zone:**
  - Shaded horizontal corridor `[quoteLo, quoteHi]` (default 0.10 to 0.90) with subtle pattern/tint indicating the admissible market region.
  - If `entry_band` is specified, also highlights the neutral corridor `[0.50 - entry_band, 0.50 + entry_band]`.
- **Center Mid Line:**
  - Horizontal reference line at $0.50 with dashed styling and label `Mid $0.50`.
- **Quoted Bid Levels & Effective Spread:**
  - Long resting bid line at `0.50 - offset` (e.g. $0.48) labeled `Long Bid: $0.48` in `--cyan`.
  - Short complement bid line at `0.50 + offset` (or complementary leg at `0.50 - offset`, with pair cost = `1.0 - 2*offset` = $0.96) labeled `Short Bid: $0.48` in `--up`.
  - Effective spread bracket between `[0.50 - offset, 0.50 + offset]` labeled `Spread: 2 × offset`.
- **Stop Loss Boundary:**
  - Horizontal line at `0.50 - offset - exit_stop` (e.g. $0.43) in `--down` labeled `Stop Loss: $0.43 (-5¢)`.
- **Reversal Buffer:**
  - Shaded band or dashed line offset by `exit_reversal` above the stop line (e.g. $0.45) labeled `Reversal Buffer (+2¢)` in `--gold`.

### 2. Time Axis (X-Axis, Horizontal: 0% to 100% / 0s to 300s/900s)
- **Window Progress:** 0 to 300s (5m reference window duration).
- **Entry Delay Zone:** Shaded lead-in rectangle `[0, entry_delay_sec]` with warning tint and label `Entry Delay (No Quoting)`.
- **Active Trading Zone:** Middle region `[entry_delay, window_end - dead_zone]` where quotes rest.
- **Dead Zone Tail:** Shaded end rectangle `[window_end - dead_zone, window_end]` in `--dim`/neutral tint labeled `Dead Zone (Exit Only)`.

### 3. Metric Summary Pills (Top Bar)
- Spread Width: `2 × offset`
- Pair Cost: `1.00 - 2*offset` (with comparison to `max_pair_cost`)
- Stop Distance: `-exit_stop`
- Quoting Window: Active trading duration in seconds and percentage.

## Reactivity & Client-Side Execution
- `updateBacktestParamPreview()` function:
  - Reads values from inputs: `#btOffset`, `#btPairCost`, `#btExit5m`, `#btExitReversal`, `#btEntryDelay`, `#btQuoteLo`, `#btQuoteHi`, `#btDeadZoneVal`, `#btDeadZoneUnit`.
  - Pure SVG generation; no DOM repainting bottlenecks; execution time < 5ms.
- Input listeners:
  - Wired in `setupBacktestInputListeners()` to fire on `input` and `change` across all relevant form elements.
  - Automatically invoked on initial page load and when tab 2 is activated.
- Graceful Edge-Case Guards:
  - Handles `offset = 0`, `exit_stop = 0`, `exit_reversal = 0`, `entry_delay = 0`, `dead_zone = 0`, `quoteLo >= quoteHi`.
  - Clamps visual coordinates within bounds to avoid SVG clipping or NaN attribute errors.

## Acceptance Criteria
- [ ] A dedicated 2D visualization preview container (`#btParamPreviewWrap` containing `#btParamPreviewSvg`) renders in `#tab-backtest`.
- [ ] The SVG grid accurately visualizes the Price axis (0.50 mid, quotable range corridor, spread offset bids, stop loss threshold, reversal buffer) and Time axis (entry delay, active zone, dead zone tail).
- [ ] Modifying any operator input (`btOffset`, `btPairCost`, `btExit5m`, `btExitReversal`, `btEntryDelay`, `btQuoteLo`, `btQuoteHi`, `btDeadZoneVal`, `btDeadZoneUnit`) updates the preview diagram in real time without backend network calls.
- [ ] Diagram handles edge cases gracefully (zero offset, disabled stops, zero delay) with clear visual cues and no JavaScript console errors.
- [ ] Integration test in `tests/test_osc_dash_integration.py` validates that `#btParamPreviewSvg`, container elements, and event listener bindings are present in the served SPA HTML.
- [ ] Targeted test suite `python -m pytest tests/test_osc_dash_integration.py -q` passes 100%.

## Out of Scope
- Modifying the underlying Python backtest engine (`backtest/engine.py`).
- Adding server-side chart rendering or new REST API endpoints.
- Modifying Live Cockpit charts or tabs 1/3/4.
