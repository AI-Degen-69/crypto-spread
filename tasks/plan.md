# Plan — Issue #410: Widen sweep-visual bars, space queue ticks by pixel

Branch: i410/widen-sweep-visual-bars-and-space-queue-depth-tick | Issue: #410
Stack: Python dashboard serving embedded Chart.js 4.4.0 UI (`server/osc_dash.py`) · Node-harness tests (`tests/test_osc_dash_integration.py`)
Size: **Small** — one source file; mechanism de-risked by the CodeRabbit plan + the #390 planner precedent to reuse.
Task type: **Code, Design/UI** (algorithm + visual rendering)

## Issue in one line
On geometric sweep axes (queue depth) every bar is a 1–2px hairline and tick
labels pile onto the low end, because thickness follows the smallest gap and
ticks are chosen by index, not pixel distance.

## Embedded spec
- Goal: bars visibly wider than hairlines, never overlapping; adjacent tick
  labels ≥ `SWEEP_CARD_TICK_GAP_PX` apart on aggregate card + detail dialog.
- Acceptance: the issue's 6 checkboxes (wider capped bars, gap-separated ticks,
  maxTicks budget kept, uniform axes unchanged, 3 new test cases, both suites).
- Edge cases: 2-point axes, categorical axes (index coordinates — legacy path),
  single-value/empty sweeps, hidden-tab zero-width canvases (legacy fallback),
  window resize (planner runs in `afterBuildTicks`, so it re-plans for free).
- Out of scope: y axis, theme, sweep engine/maths, data, small-card planner.

## CodeRabbit intake (read once; echo ignored)
- Adopted: per-bar widths (target `0.72 × median` adjacent gap, cap each bar at
  `0.72 × nearest-neighbor` gap — non-overlap by construction, identical output
  on uniform axes); public `beforeDatasetsDraw` plugin hook (no private
  overrides); pixel planner only for non-uniform axes (uniform keeps legacy);
  linear value→pixel estimate with conservative span at `afterBuildTicks`;
  width plugin on all three charts, tick change on agg + detail only.
- Rejected: anything inventing new files/abstractions — all helpers live next
  to `sweepTickStep`/`sweepTickIndices`; dataset-level `barThickness` and
  `barThickness: 'flex'` (both overlap or shift centers on uneven gaps).
- Verified seams (`[UNVERIFIED]`: 0): `sweepChartOptions`/`afterBuildTicks`
  (`osc_dash.py:10467-10544`), legacy branch (`10527-10529`), planner
  (`10442-10464`), grids (`sweepAxisValues`, queue geometric), 3 chart
  constructions (detail `10604`, agg `10737`, cards `10787`), node tests
  (`test_osc_dash_integration.py:9075-9250`).

## Resolved open questions (from code/plan, not asked)
- Bar width: median-gap rule above (deviation from nothing — issue proposed it).
- Every axis: same rule everywhere; uniform detection makes it a no-op where
  gaps are equal — no per-axis special-casing.
- Tick budget: `maxTicks` untouched; only *which* ticks change.

## Improvement proposal (adopted by default — simplification)
Reuse `sweepTickStep`/`sweepTickIndices` + `sweepLabelWidthPx` for the agg/detail
pixel branch instead of a second planner. Evidence, verbatim:
`const step = sweepTickStep(xVals.length, plotWidthPx, widestPx, SWEEP_CARD_TICK_GAP_PX);`
(`osc_dash.py:10522`) and
`scale.ticks = sweepTickIndices(xVals.length, step)` (`osc_dash.py:10524`).
The small-card path already proves the idiom; the agg branch needs only a wider
plot width and a non-uniform site selector. Adopted.

## Interfaces (locked)
- `sweepIsUniformAxis(xVals)` → bool (all adjacent gaps equal within 1e-9;
  categorical axes count as uniform — index coordinates).
- `sweepPixelTickIndices(xVals, plotWidthPx, labels, maxTicks, gapPx)` → kept
  indices: value→pixel via linear map on conservative span, greedy
  widest-separation pick within `maxTicks`, first+last anchored when they clear
  by a full gap (mirrors `sweepTickIndices` last-value rule).
- `sweepBarWidthsPx(xVals, plotWidthPx)` → per-bar px widths (median target,
  neighbor cap, `0.72` fill).
- `sweepBarWidthPlugin` (`beforeDatasetsDraw`): assigns `element.width` from
  precomputed widths; registered on detail + agg + card charts.

## Dependency graph & tasks
T1 (widths, riskiest Chart.js mechanics) → T2 (ticks) → T3 (wiring + proof).
T1/T2 independent of each other; both pure + node-tested before any wiring.

- [x] T1 [Code/Logic] M — `server/osc_dash.py`: `sweepBarWidthsPx` helper +
  `sweepBarWidthPlugin`; uniform-axis output equals today's fit width.
  Verify: node-harness tests (geometric queue widths widen + capped,
  uniform axis unchanged). Depends on: none.
- [ ] T2 [Code/Logic] M — `server/osc_dash.py`: uniformity detector +
  pixel tick selector; agg/detail `afterBuildTicks` takes the pixel branch
  only for non-uniform axes. Verify: node tests (non-uniform selection,
  budget honored, uniform/categorical byte-identical). Depends on: none.
- [x] T3 [Design/UI] S — wire plugin into the 3 chart constructions; live
  browser proof (queue sweep: bars + spaced labels). Verify: full
  `test_osc_dash_integration.py -q` + `test_theme_tokens.py -q` + gate
  screenshot. Depends on: T1, T2.

Checkpoint after T2: pure logic + node tests green.

## Files NOT to modify
`backtest/*`, `strategy/*`, `research/sweeps/*`, small-card planner body,
theme tokens, `tests/test_engine_parity.py`.
