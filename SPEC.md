# SPEC — Issue #266: Sweep Visual sensitivity clarity

## Goal
Make the Backtest Sweep Visual communicate that every X value is a separate replay experiment. Use discrete bar charts, identify the strongest overall result and strongest market, and replace technical axis labels with operator-friendly names. The stop-distance sweep must vary the shared 5m and 15m defaults together rather than implying that `exit_5m` is the whole strategy.

## Current state
Issue #264 delivered `/api/backtest/sweep` with numeric point values, a linear Chart.js line chart, four technical axis names, and ten per-market cards. The UI currently exposes `exit_5m` even though the backtest has separate 5m and 15m stop defaults. Each point is an independent replay; the visualization must not suggest interpolation between untested values.

## Interface contract
- `GET /api/backtest/sweep` remains one-axis only.
- Valid axes become `queue`, `offset`, `exit_stop`, and `exit_rev`. `exit_5m` is retired from the Sweep Visual API and UI.
- The endpoint accepts the current base values for both stop defaults: `exit_default_5m` and `exit_default_15m`.
- `exit_stop` applies the swept value to both `default_5m` and `default_15m` in the sweep copy of the parameters. It does not change production defaults or strategy math. Existing market-specific overrides remain explicit and documented in the response/metadata if they are retained.
- Responses preserve `axis`, ordered `points[]`, numeric `value`, readable `label`, `overall`, `per_series`, `series_order`, `series_labels`, `n_snaps`, and `n_windows`.
- Each point remains an independent run; no response field may imply that intermediate values were simulated.
- Additive metadata identifies:
  - `best_overall`: tested point with the highest aggregate P&L, including its value, label, and P&L.
  - `best_market`: canonical market with the highest per-market P&L across the tested points, including market slug, friendly label, tested point, and P&L.

## UI contract
- The axis selector uses humanized labels:
  - `Queue depth — shares ahead`
  - `Quote offset — distance from anchor`
  - `Stop distance — 5m + 15m markets`
  - `Reversal buffer — distance from anchor`
- The aggregate chart and all ten market charts use bar datasets with a visible zero baseline. They do not use a category or interpolating line scale.
- The aggregate chart marks `best_overall` with a distinct color, point/annotation treatment, or equivalent visible bar emphasis and a text legend.
- The ten-market grid marks `best_market` visibly and shows its friendly label and tested parameter value.
- Best metadata remains safe for an empty `points[]` response: no indexing failure and no false best result.
- Existing DOM IDs remain stable: `btSweepCard`, `btSweepAxis`, `btnRunSweepVisual`, `btSweepMeta`, `chartSweepAgg`, and `btSweepGrid`.

## Acceptance criteria
1. Aggregate and per-market charts render discrete bars from independent tested points, with a zero baseline and no line interpolation.
2. The selected axis menu contains only the four humanized labels and uses `exit_stop` for the shared 5m/15m stop sweep.
3. `exit_stop` changes both 5m and 15m default stop values in every run; tests prove neither duration is left at a fixed unrelated default.
4. The response identifies the best aggregate point and best market, and the UI visibly marks both.
5. Existing response shape, validation, safe basename handling, 404 missing-file behavior, 429 busy behavior, canonical order, and zero-fill behavior remain intact.
6. Focused tests cover bar configuration, humanized labels, shared stop semantics, best-point/best-market metadata, empty/sparse data, and ten-card rendering.
7. Browser verification against `run/ticks/ticks_2026-09-18.jsonl` records the rendered chart count and confirms the best markers and shared stop label.

## Edge cases
- Empty points produce empty bar charts and neutral metadata.
- A market missing from the dataset remains in canonical order with zero-valued bars and cannot become best solely because its value is missing.
- Ties use deterministic first-in-canonical-order selection, documented in tests.
- Negative P&L bars remain visible below the zero baseline.

## Explicit out of scope
Multi-axis, joint-grid, random, structural-limit, scatter, strategy-calculation, tick-data, parameter-default, other-tab, and dependency changes. The existing Chart.js integration remains in place.
