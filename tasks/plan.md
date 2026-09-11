# Plan: Dynamic Symmetric Mid-Anchored Quoting & Stop Loss Trigger (0.05$)

Task Type: Code
Size Tier: Standard
Target Files: strategy/live_trader.py, server/osc_dash.py, tests/test_live_trader.py

## Task Breakdown

### Task 1: Update UI Label to "Stop Loss Trigger ($)" and Default Threshold to 0.05
- **Files**: `server/osc_dash.py`, `strategy/live_trader.py`
- **Description**: Update UI form label in `server/osc_dash.py:2307` to `Stop Loss Trigger ($)` and set default `exit_thresh_naked` to `0.05`.
- **Verification**: Run `python -m pytest tests/test_osc_dash_integration.py`

### Task 2: Implement Dynamic Symmetric Mid-Offset Calculation for Round 0
- **Files**: `strategy/live_trader.py`
- **Description**: Update initial quote calculation in `_evaluate_monitored_window` to compute `resting_up` and `resting_down` dynamically from `up_mid - self.offset` and `down_mid - self.offset`.
- **Verification**: Run `python -m pytest tests/test_live_trader.py`

### Task 3: Add Unit Tests for Dynamic Symmetric Quoting & Cancellation
- **Files**: `tests/test_live_trader.py`
- **Description**: Add explicit unit tests verifying:
  1. 50/51 market produces 0.49 UP and 0.48 DOWN limit orders.
  2. 30/71 market produces 0.28 UP and 0.69 DOWN limit orders.
  3. Single leg stop exit cancels opposite resting order.
- **Verification**: Run `python -m pytest tests/test_live_trader.py`

### Task 4: Full Suite Verification & Regression Gate
- **Files**: All test files
- **Description**: Execute complete pytest suite across all test files.
- **Verification**: Run `python -m pytest -q` (Expect: 414 passed).
