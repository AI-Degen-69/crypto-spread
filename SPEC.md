# SPEC.md — Issue #335: Verify Sweep Axes & Surface Overridden Field

## 1. Overview
The Sweep Visual in the Backtester is designed to hold every Backtester parameter at the operator's configured value while varying only the chosen parameter axis (`queue`, `offset`, `exit_stop`, `exit_rev`). Currently, end-to-end parity is only verified for `queue` at a single point, the `offset` axis's memo-off execution path (`queue_memo=None`) lacks full end-to-end verification, and the `exit_stop` axis replaces all six stop loss thresholds across all market slugs without clear UI indication.

This specification details the comprehensive end-to-end parity testing for all axes and values in `SWEEP_AXES`, and the UI update to display an informative override note in `btSweepMeta`.

---

## 2. Goals & Requirements

### 2.1 Backend / Integration Test Suite (`tests/test_osc_dash_integration.py`)
1. **Multi-Axis End-to-End Parity:**
   - Parameterize tests across all 4 axes (`queue`, `offset`, `exit_stop`, `exit_rev`) and every point in `osc_dash.SWEEP_AXES`.
   - Use a shared `_NON_DEFAULT` query containing non-default parameters across all Backtester controls.
   - For `exit_stop`, set all four HTTP stop loss parameters (`exit_default_5m`, `exit_default_15m`, `exit_btc_5m`, `exit_sol_5m`) to the tested point value so the baseline backtest matches the sweep's 6-key global replacement.
   - Assert exact match on `total_pnl_cents`, `pairs`, and `windows` between `/api/backtest` and the matching point from `/api/backtest/sweep`.
   - Ensure the fixture is non-vacuous (`pairs > 0`).

2. **Offset Memo-Off Path Verification:**
   - Verify that the `offset` sweep path (which sets `queue_memo=None` per `server/osc_dash.py:2012-2015`) produces identical simulation outputs to `/api/backtest`.

3. **Completeness & Axis Surface Guard:**
   - Assert that all values in `SWEEP_AXES` are covered.
   - Guard that the `<select id="btSweepAxis">` options in the served HTML match `SWEEP_AXES.keys()`.

### 2.2 Frontend / Client UI (`server/osc_dash.py`)
1. **Pass Controls Snapshot to Renderer:**
   - In `runSweepVisual`, pass the pre-request captured `v = btControlValues()` to `renderSweepVisual(data, v)`.
2. **Pure Helper `sweepOverrideNote(axis, v, pointValues)`:**
   - Map each axis to its human-readable swept field name and display the operator's submitted value from `v`.
   - For `queue`: e.g., `sweeps Queue depth (submitted: 50)`.
   - For `offset`: e.g., `sweeps Quote offset (submitted: 2.0¢)`.
   - For `exit_rev`: e.g., `sweeps Reversal buffer (submitted: 3.0¢)`.
   - For `exit_stop`: state that the sweep overrides all six stop thresholds (`5m`, `15m`, `BTC`, `SOL`) and list their submitted values.
   - Check if the operator's submitted value matches one of `pointValues` (within float epsilon `1e-6`). For `exit_stop`, require all four submitted values to be equal and match a point.
   - If a point matches, note that it corresponds to "your setting" / the current setting bar. If no point matches, note that no bar equals the current setting.
3. **Display in `btSweepMeta`:**
   - Append or include the override note in the `btSweepMeta` line alongside existing metrics (`best overall`, `best market`, elapsed time).
   - Ensure timing and existing state transitions (`waiting`, `sweeping`, error handling) remain intact.

### 2.3 JavaScript Test Harness (`tests/test_osc_dash_integration.py`)
1. **Served-Text Assertions:**
   - Verify `sweepOverrideNote` exists in `FULL_APP_HTML`.
   - Verify `renderSweepVisual(data, v)` signature and usage.
2. **Node Harness Unit Tests:**
   - Test `sweepOverrideNote` under various scenarios: matching value on axis, non-matching value, `exit_stop` with identical inputs matching a point, and `exit_stop` with differing inputs (e.g. 0.06, 0.07, 0.08, 0.09) stating "all six".

---

## 3. Out of Scope
- Modifying `SWEEP_AXES` definitions or ranges.
- Changing server-side `_sweep_params_for_value` or sweep simulation logic.
- Changing the `exit_stop` 6-key override semantics on the server.
- Altering the API response schema of `/api/backtest/sweep`.
