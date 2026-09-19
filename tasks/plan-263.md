# Task Plan — Issue #263: Redesign Strategy Geometry Preview

**Size tier:** Standard — the change is concentrated in the dashboard preview but spans the SVG layout, CSS/card shell, and integration tests, with one layout decision for collision-free labels.
**Task type:** Design/UI + Code.
**Issue:** https://github.com/AI-Degen-69/crypto-spread/issues/263

## Context and scope

The Backtest tab's Strategy Geometry Preview currently renders all right-side labels at the same x-coordinate, uses `preserveAspectRatio="none"`, wraps seven legend entries, and mixes typography without a clear hierarchy. The issue requires a readable price × time chart while preserving the existing strategy calculations and all other backtest surfaces.

In scope:
- `server/osc_dash.py` preview card CSS/HTML and `updateBacktestParamPreview()`.
- `tests/test_osc_dash_integration.py` preview contract and regression checks.

Out of scope:
- Strategy math, backtest engine, parameters, defaults, or other charts/tabs.
- New dependencies.
- Changes to the unrelated uncommitted Issue #174 work already present in the checkout.

## Locked interface contracts

- Keep the DOM IDs `btParamPreviewWrap`, `btParamPreviewSvg`, `btPreviewMetricsPills`, and `btParamPreviewLegend`.
- Keep the function signature `updateBacktestParamPreview()` and its reactive wiring through `setupBacktestInputListeners()`.
- Preserve the computed values and inputs: `offset`, `pairCostMax`, `exitStop`, `exitReversal`, `entryDelay`, `quoteLo`, `quoteHi`, `deadZoneVal`, and `deadZoneUnit`.
- Preserve the default computed levels: `longBid`, `shortComp`, `pairCost`, `stopPrice`, and `revPrice`.
- Render within the existing theme tokens (`var(--disp)`, `var(--mono)`, `var(--tx)`, `var(--dim)`, `var(--cyan)`, `var(--up)`, `var(--down)`, `var(--gold)`).
- Keep the preview self-contained: no network calls and no external charting library.

## Improvement proposal (adopted by default)

Use a data-driven label layout pass that sorts right-side labels by their computed y-coordinate and enforces a minimum vertical gap before drawing leader lines; this is grounded in the current implementation where every label uses `x="${padL + plotW + 6}"` and therefore collides when levels are close.

## Tasks

- [x] **TASK-1 [Design/UI]**: Establish the preview layout and visual token contract.
  - Target: `server/osc_dash.py` around the preview CSS/card shell.
  - Build: Define the chart/gutter dimensions, responsive container rules, title/pill spacing, and compact legend structure without changing the existing DOM IDs.
  - Helper skill: `frontend-ui-engineering`.
  - Verify: `python -m pytest tests/test_osc_dash_integration.py -q` plus browser inspection at 1200px and 1920px.

- [x] **TASK-2 [Design/UI]**: Replace distorted SVG sizing with a readable price-time plot.
  - Target: `server/osc_dash.py:updateBacktestParamPreview()`.
  - Build: Use a stable viewBox and non-distorting aspect-ratio behavior; keep visible horizontal price grid, vertical time grid, axis labels, corridor, delay zone, dead zone, and active span.
  - Helper skill: `frontend-ui-engineering`.
  - Verify: browser inspection confirms geometry remains proportional at 1200–1920px and existing Node preview test remains green.

- [x] **TASK-3 [Design/UI]**: Implement collision-free right-side level labels.
  - Target: `server/osc_dash.py:updateBacktestParamPreview()`.
  - Build: Represent Long Bid, Short Complement, Stop Loss, Reversal, and Mid as label records; sort and space them with a minimum gap, clamp within the gutter, and draw leader lines from each level to its final label.
  - Helper skill: `frontend-ui-engineering`.
  - Verify: browser inspection with default values and tightly clustered values; labels remain legible and no two label boxes overlap.

- [x] **TASK-4 [Design/UI]**: Simplify the header pills and legend.
  - Target: `server/osc_dash.py` preview card and render function.
  - Build: Keep the four metric pills with consistent 9–12px hierarchy and reduce the legend to at most five grouped entries while retaining distinguishable corridor, time zones, quote levels, and exit levels.
  - Helper skill: `frontend-ui-engineering`.
  - Verify: static test asserts the compact legend contract; browser inspection confirms no cramped wrapping.

- [x] **TASK-5 [Code/Regression]**: Preserve preview calculations and reactive behavior.
  - Target: `tests/test_osc_dash_integration.py`, `server/osc_dash.py` only if required for testable markup.
  - Build: Extend focused assertions for stable function/DOM markers, non-distorting preview SVG, compact legend markers, and unchanged zero-value behavior; do not weaken or remove existing assertions.
  - Helper skill: `test-driven-development`.
  - Verify: `python -m pytest tests/test_osc_dash_integration.py -q`.

- [x] **TASK-6 [QA]**: Final issue-specific UI verification and simplification.
  - Target: modified Issue #263 files only.
  - Build: Remove dead styling or duplicate SVG fragments, check accessibility/contrast of labels and zones, and document any browser-only observations for Station IV.
  - Helper skill: `code-simplification`.
  - Verify: targeted pytest suite plus browser check at default parameters and the acceptance-case parameter combinations.

## Verification matrix

| Requirement | Proof |
|---|---|
| Right-side labels do not overlap | Browser check at default and clustered levels; deterministic label layout code |
| Typography is hierarchical | Theme-token/static assertions plus browser check |
| Chart is not distorted | `preserveAspectRatio` contract and browser check at 1200/1920px |
| Zones and corridor remain distinguishable | SVG marker assertions plus browser check |
| Legend has at most five entries | HTML/static assertion and browser check |
| Strategy math is unchanged | Existing Node zero-value test and integration suite |

## Safety constraints

- Do not overwrite or reformat unrelated changes in `server/osc_dash.py` or any Issue #174 artifacts.
- No new packages or dependencies.
- Do not skip tests, delete assertions, or suppress failures.
- Full repository pytest is not run locally; targeted tests are the development gate and CI owns the full suite.

## Handoff

This dedicated plan is used because the standard `tasks/plan.md` currently contains active uncommitted Issue #174 work. Before Build, invoke the builder with this plan explicitly or promote it only after the #174 owner confirms the shared plan file is free.
