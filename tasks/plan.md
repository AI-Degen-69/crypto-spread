# Plan: Dynamic Symmetric Quoting Alignment Across Both Pages and Backtest Engine

Task Type: Code
Size Tier: Standard
Target Files: server/osc_dash.py, backtest/engine.py, tests/test_backtest_engine.py, eli5_backtest_params.html

## Task Breakdown

### Task 1: Update Dashboard UI Across Both Pages and Documentation
- **Files**: `server/osc_dash.py`, `eli5_backtest_params.html`
- **Description**:
  1. On the Backtest Replay page (`server/osc_dash.py`), update the Fill Model option label from `Cross (Guaranteed if crossing ≤47¢)` to `Cross (Strict Through-Price Fill — Ask ≤ Resting Bid - 1¢)`.
  2. Update empirical explanation text in `server/osc_dash.py` to clarify quotes are at `mid - offset` (e.g. 48¢ when mid is 50¢).
  3. On the Live Cockpit page (`server/osc_dash.py`), remove hardcoded `0.48` fallbacks in toast alerts, market cards, and bid text strings.
  4. In `eli5_backtest_params.html`, update text to explain initial quotes at `mid - offset`.
- **Status**: [x]
- **Verification**: `python -m pytest tests/test_osc_dash_integration.py` (49 passed)

### Task 2: Implement Dynamic Mid-Anchored Quoting in Backtest Engine
- **Files**: `backtest/engine.py`
- **Description**:
  1. In `_simulate_window`, dynamically read initial `up_mid` and `down_mid` from the first valid snapshot with orderbook data.
  2. Compute `resting_up = round(min(0.99, max(0.01, init_mid - params.offset)), 3)` and `resting_down = round(min(0.99, max(0.01, (1.0 - init_mid) - params.offset)), 3)`.
  3. On drift-skip re-entry (`reentry_mid`), re-anchor resting quotes to current snapshot mids if orders have not yet been filled.
- **Status**: [x]
- **Verification**: `python -m pytest tests/test_backtest_engine.py` (50 passed)

### Task 3: Add Unit Tests for Dynamic Backtest Quoting
- **Files**: `tests/test_backtest_engine.py`
- **Description**: Add unit tests verifying:
  1. Off-center window (e.g. up_mid=0.35, down_mid=0.65) quotes `resting_up = 0.33` and `resting_down = 0.63` in replay instead of static 0.48.
  2. Replay fill logic strictly tests against these dynamic quotes.
- **Status**: [x]
- **Verification**: `python -m pytest tests/test_backtest_engine.py -k test_backtest_dynamic_symmetric_quoting_off_center_open` (passed)

### Task 4: Regression Gate & Verification
- **Files**: All test files
- **Description**: Execute complete pytest suite across all test files.
- **Status**: [x]
- **Verification**: `python -m pytest -q` (415 passed in 39.35s)
