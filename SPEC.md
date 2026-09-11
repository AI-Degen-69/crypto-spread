# SPEC.md — Issue #139: cockpit queue-telemetry panel + PnL histogram

## 1. Goal
Two cockpit visuals answering the study's open question live: (1) a queue-telemetry panel (fill_ratio buckets + verdict line) fed by `run/live_fill_telemetry.jsonl`, and (2) a per-position PnL histogram from `st.trades`. Inline SVG, no new dependencies, dark theme, table fallbacks.

## 2. In Scope
1. **Endpoint `GET /api/live/queue_telemetry`** (`server/osc_dash.py`): reads `run/live_fill_telemetry.jsonl` + settlement PnL, reuses `scripts/bucket_fills.bucketize` (single source of truth, no duplicated bucket logic), returns per-bucket {count, mean PnL}, chased-fill {count, mean PnL} separately, total fills, and a verdict string. Missing/empty file → explicit `{"empty": true, ...}` state, HTTP 200.
2. **Verdict rule** (deterministic, documented): `awaiting fills` when empty; `tape-like` when the [0,0.5) buckets' mean PnL > 0 and the [0.5,+) mean ≤ 0; `queue-toxic` for the mirror; else `mixed/unclear`.
3. **Queue panel UI**: 4 bucket bars (count) + mean-PnL annotations + chased row + verdict line; empty → explicit message, never a blank box.
4. **PnL histogram UI**: client-side from `st.trades` (same 50-trade session window the table shows — count must match the table; subtitle says so). Freedman–Diaconis binning capped to 12–20 bins, exact-zero PnL as its own bin, profit/loss-colored bars, mean + bootstrap CI-lo annotation (2,000 resamples, matching the study's statistic), `<title>` tooltips, `<details>` table fallback.
5. **Wiring**: queue panel fetched in `fetchCockpitState` alongside `/api/live/state` (5s cadence preserved); render functions beside the existing SVG chart code; `esc()` used for all interpolated strings.
6. **Tests**: endpoint aggregation math on a synthetic telemetry file (incl. empty-file state + verdict transitions); integration asserts for new panel IDs/JS hooks in cockpit HTML.

## 3. Out of Scope
- Trading-behavior changes; backtest/`/analysis` visuals; new persistence; new JS dependencies.

## 4. Acceptance Criteria
- [ ] Endpoint returns buckets + means + chased count from a synthetic file; explicit empty state when absent.
- [ ] Queue panel renders bars + verdict; empty data shows a message.
- [ ] Histogram renders from `st.trades` with colors, zero bin, mean + CI-lo; count matches the trades table.
- [ ] Inline SVG, responsive, table fallback, dark theme.
- [ ] `python -m pytest tests/test_osc_dash_integration.py -q` passes, plus endpoint math test.
