# Branch: i332/compact-sweep-visual-charts | Issue: #332

## Scope & Classification
- **Issue:** #332 — feat(dash): compact Sweep Visual charts with a zero line and sign-coloured bars (profit green / loss red / best gold)
- **Size Tier:** Small (touches `server/osc_dash.py` and `tests/test_theme_tokens.py`)
- **Task Type:** Design/UI + Code
- **Stack:** Python 3.12, FastAPI, Vanilla JS / HTML / CSS in `osc_dash.py`, Chart.js 4.4.0, pytest

## CodeRabbit Plan Consultation
- **Adopted:**
  - Scriptable callbacks in `scales.y.grid` for `ctx.tick && ctx.tick.value === 0`.
  - Node-based test harness pattern in `tests/test_theme_tokens.py` following `tests/test_recent_windows_table.py`.
  - Compact dimensions (~90px aggregate, ~70px per-market, `minmax(160px,1fr)` grid).
  - Precedence order: Gold (best) > Sign (Profit `theme.up`, Loss `theme.down`, Neutral/Zero `theme.dim`).
- **Rejected:**
  - Separate CSS wrapper divs with extra classes: not needed, setting canvas height and `maintainAspectRatio: false` with tighter padding achieves exact same result with fewer lines and zero extra DOM boilerplate.
- **Unverified items checked:**
  - Confirmed no tests in `tests/` assert `height="140"` or `minmax(220px`. Existing assertions in `test_theme_tokens.py` check IDs which will be preserved.

## Improvement Proposal (Adopted by default)
- **Edge-case hardening:** In `sweepChartColors`, handle neutral/zero or non-finite values by returning `theme.dim`, ensuring break-even (0 P&L) or missing points are neither falsely marked as profit (`theme.up`) nor loss (`theme.down`).

---

## Tasks

### Task 1: [Design/UI] [Code] Shared Sweep Chart Helpers — Sign-based Bar Colors & Zero Reference Line
- **Target files:** `server/osc_dash.py`
- **Helper skill:** `frontend-ui-engineering`
- **Depends on:** None
- **Details:**
  - Update `sweepChartColors(data, seriesKey, theme)`:
    - Compute P&L value for point (aggregate via `point.overall.total_pnl_cents` or market via `point.per_series[seriesKey]`).
    - Best point returns `theme.gold`.
    - Positive P&L returns `theme.up`.
    - Negative P&L returns `theme.down`.
    - Zero/neutral or non-finite returns `theme.dim`.
  - Update `sweepChartOptions(data, detail)`:
    - Add `maintainAspectRatio: false` for `!detail`.
    - In `scales.y.grid`:
      - `color: ctx => (ctx.tick && ctx.tick.value === 0) ? theme.dim : theme.line`
      - `lineWidth: ctx => (ctx.tick && ctx.tick.value === 0) ? 1.5 : 1`
- **Verification:** Targeted tests + syntax check.

### Task 2: [Design/UI] Compact Sweep Visual Cards & Responsive Grid
- **Target files:** `server/osc_dash.py`
- **Helper skill:** `frontend-ui-engineering`
- **Depends on:** Task 1
- **Details:**
  - In markup (`#btSweepCard` / `#btSweepAggCard` / `#btSweepGrid`):
    - Update aggregate canvas `#chartSweepAgg` height from `140` to `90`.
    - Reduce `#btSweepAggCard` padding to `8px 10px` and margin to `8px`.
    - Update `#btSweepGrid` template columns from `minmax(220px,1fr)` with `gap:10px` to `minmax(160px,1fr)` with `gap:6px`.
  - In `renderSweepVisual`:
    - Set per-market canvas `cv.height = 70` (down from 110).
    - Reduce card padding to `6px 8px`.
    - Compact title text font/margins while preserving the exact `title.textContent` format expected by tests (`★ BEST MARKET`).
- **Verification:** DOM inspection and test suite passes.

### Task 3: [Logic/TDD] Unit & Regression Tests in `tests/test_theme_tokens.py`
- **Target files:** `tests/test_theme_tokens.py`
- **Helper skill:** `test-driven-development`
- **Depends on:** Task 1, Task 2
- **Details:**
  - Add test asserting `sweepChartOptions` contains y-grid callback testing `tick.value === 0`.
  - Add Node test verifying `sweepChartColors` outputs:
    - Mixed aggregate (positive -> `theme.up`, negative -> `theme.down`, zero -> `theme.dim`, best -> `theme.gold`).
    - All-negative sweep (best -> `theme.gold`, others -> `theme.down`).
    - Per-market series with matching and non-matching `best_market`.
- **Verification:** `python -m pytest tests/test_theme_tokens.py tests/test_osc_dash_integration.py -q`.

---

## Checkpoints
- **Checkpoint 1 (after Task 1 & 2):** Sweep Visual charts render with compact dimensions, color-coded bars, and a clear zero reference line.
- **Checkpoint 2 (after Task 3):** Full targeted test suite passes with zero regressions.
