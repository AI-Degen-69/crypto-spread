# Plan: Issue #335 — Verify Sweep Axes Hold Backtester Parameters & Surface Overridden Field

Branch: `i335/verify-every-axis-holds-the-operators-backtester` | Issue: `#335`
Size Tier: Standard (2 files, targeted UI note + comprehensive backend/frontend integration tests)
Task Type: Code / Debug / Design

---

## CodeRabbit Intake Note
- **Adopted from CodeRabbit plan:**
  - Parametrized test structure for all 4 axes across all `SWEEP_AXES` values with `_NON_DEFAULT` controls.
  - Setting all 4 HTTP stop parameters for `exit_stop` parity verification.
  - Verification of `queue_memo=None` memo-off execution path for `offset`.
  - Adding pure helper `sweepOverrideNote(axis, v, pointValues)` and passing `v` snapshot to `renderSweepVisual`.
  - Served-text assertions and Node harness unit tests in `tests/test_osc_dash_integration.py`.
- **Adapted / Simplified:**
  - Kept tasks unified in 3 atomic, cohesive tasks instead of over-splitting into 5 micro-tasks.
  - Reused existing `_sweep_fixture` structure while ensuring 5m and 15m markets are present if needed for multi-market verification.
- **Unverified items checked against code:**
  - Verified `SWEEP_AXES` definitions at `server/osc_dash.py:1854-1859`.
  - Verified `_sweep_params_for_value` at `server/osc_dash.py:1862-1879`.
  - Verified `runSweepVisual` and `renderSweepVisual` at `server/osc_dash.py:6763-7050`.

---

## Improvement Proposal (Adopted by default)
- **Proposal:** When displaying `sweepOverrideNote` for `exit_stop`, detect whether all four stop inputs (`btExit5m`, `btExit15m`, `btExitBtc`, `btExitSol`) are identical and equal to a sweep point. If identical and matching a point value, indicate that this specific bar equals the operator's current uniform stop loss; if inputs are mixed or differ from all points, explicitly clarify that the sweep tests uniform thresholds across all markets while the operator configured mixed/custom per-market thresholds.
- **Evidence:** `server/osc_dash.py:1868-1878` overrides all six keys uniformly (`default_5m`, `default_15m`, `btc-5m`, `btc-15m`, `sol-5m`, `sol-15m`).

---

## Task Decomposition

### Task 1: [x] [Backend/Logic] [TDD] End-to-End Multi-Axis Parity & Offset Memo-Off Test Suite
- **Size:** M
- **Target Files:** `tests/test_osc_dash_integration.py`
- **Assigned Skill:** `test-driven-development`
- **Depends on:** None
- **Description:**
  1. Add a shared query builder / test helper that runs `/api/backtest` and `/api/backtest/sweep` with non-default parameters (`_NON_DEFAULT`).
  2. Implement parametrized tests covering every `(axis, value)` in `osc_dash.SWEEP_AXES` (all 4 axes: `queue`, `offset`, `exit_stop`, `exit_rev`), asserting exact equality of `total_pnl_cents`, `pairs`, and `windows` between `/api/backtest` and the matching sweep point.
  3. For `exit_stop`, set all four stop inputs (`exit_default_5m`, `exit_default_15m`, `exit_btc_5m`, `exit_sol_5m`) to the sweep point value to match the server's 6-key override.
  4. Ensure non-vacuous execution (`pairs > 0`).
  5. Add test verifying the `offset` axis memo-off path (`queue_memo=None`), confirming exact simulation parity against a plain backtest.
  6. Add test verifying that every `SWEEP_AXES` axis is represented in the HTML select options.
- **Verification:** `python -m pytest tests/test_osc_dash_integration.py -k "sweep" -q`

---

### Task 2: [x] [Design/UI] [Frontend] Sweep Visual Client Override Note & Renderer Update
- **Size:** S
- **Target Files:** `server/osc_dash.py`
- **Assigned Skill:** `frontend-ui-engineering`
- **Depends on:** None
- **Description:**
  1. In `runSweepVisual`, pass the captured `v = btControlValues()` snapshot to `renderSweepVisual(data, v)`.
  2. Add pure `sweepOverrideNote(axis, v, pointValues)` helper in client JavaScript:
     - Formats the name of the swept field and displays the submitted value from `v`.
     - For `exit_stop`, states that the sweep overrides all six stop thresholds and lists the submitted values for 5m, 15m, BTC, and SOL.
     - Uses float epsilon tolerance (`1e-6`) to check whether the submitted value matches one of `pointValues` ("your setting" / current setting bar). For `exit_stop`, requires all four inputs to be equal to match a point.
     - If no point matches, states that no bar equals the current setting.
  3. In `renderSweepVisual`, append the override note to the `btSweepMeta` text line, preserving all existing metrics and elapsed time displays.
- **Verification:** Verified by served-text checks and Node harness in Task 3.

---

### Task 3: [x] [Backend/Logic] [TDD] Served-Text Assertions & Node Harness Tests
- **Size:** S
- **Target Files:** `tests/test_osc_dash_integration.py`
- **Assigned Skill:** `test-driven-development`
- **Depends on:** Task 1, Task 2
- **Description:**
  1. Add served-text assertions in `test_osc_dash_integration.py` ensuring `sweepOverrideNote` is present in `FULL_APP_HTML` and `runSweepVisual` passes `v` to `renderSweepVisual`.
  2. Add Node.js harness tests for `sweepOverrideNote`:
     - Test matching value on axis (e.g., `queue=50`).
     - Test non-matching value on axis (e.g., `offset=0.022`).
     - Test `exit_stop` with mixed inputs (0.06, 0.07, 0.08, 0.09) asserting "all six" and listing all values.
     - Test `exit_stop` with uniform inputs equal to an axis point (0.08) asserting that the matching point is marked.
  3. Run full targeted test suite and theme token tests.
- **Verification:**
  - `python -m pytest tests/test_osc_dash_integration.py -k "sweep" -q`
  - `python -m pytest tests/test_theme_tokens.py -q`

---

## Checkpoints
- **Checkpoint 1 (after Task 1):** Backend end-to-end parity tests pass across all 4 axes and all points.
- **Checkpoint 2 (after Task 2 & 3):** UI and Node tests pass, all targeted integration tests pass cleanly.
