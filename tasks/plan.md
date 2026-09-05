# Implementation Plan: Lock Strategy Parameters While Trading Bot Is Running (Issue #62)

## Overview
Lock strategy configuration parameters (`Spread Offset`, `Exit Stop Loss`, `Share Size`, `Execution Mode`, `Starting Balance`, `Wallet Address`) and the `APPLY PARAMETERS` button from being modified while the live trading bot is running (`is_running == True`). Enforce this in both backend engine validation (`LiveTraderEngine.update_config()` under `self._engine_lock`) and dashboard UI (`server/osc_dash.py`).

## Architecture Decisions
1. **Thread-Safe Backend Validation**: Check parameters under `self._engine_lock` in `update_config()` before any mutation occurs. If `is_running == True` and any incoming value differs from active configuration, raise `ValueError("Cannot change strategy parameters while the trading bot is running. Stop the bot first.")`. Idempotent calls (where values match active settings) succeed.
2. **Synchronized Frontend Lock**: Disable `#cockpitOffset`, `#cockpitExit`, `#cockpitShares`, `#cockpitMode`, `#cockpitWallet`, `#cockpitStartBal`, and `#btnApplyParams` when `st.is_running` is true with visual lock styling (`opacity: 0.4`, `cursor: not-allowed`, tooltip).
3. **Clear Operator Feedback**: Display `#cockpitParamsLockHint` banner (`🔒 LOCKED WHILE BOT IS RUNNING — STOP THE BOT TO CHANGE PARAMETERS`) adjacent to the Apply button.
4. **Client-Side Guard**: Short-circuit `applyCockpitConfig()` early if `cockpitState && cockpitState.is_running`.

## Task List

### Phase 1: Backend Validation & Unit Tests
- [x] Task 1: Backend parameter locking under `_engine_lock` in `strategy/live_trader.py` and unit tests in `tests/test_live_trader.py`
  - Acceptance: `engine.update_config()` raises `ValueError` if any strategy parameter (`offset`, `exit_thresh`, `shares`, `mode`, `wallet_address`, `starting_balance`) changes while `is_running=True`. Idempotent calls succeed. Modification succeeds when stopped.
  - Verify: `python -m pytest tests/test_live_trader.py -q`
  - Files: `strategy/live_trader.py`, `tests/test_live_trader.py`

### Phase 2: Frontend Cockpit Locking & Banner
- [x] Task 2: Cockpit form inputs lock, notice banner, and client guard in `server/osc_dash.py`
  - Acceptance: Inputs and `#btnApplyParams` are disabled with lock styling and tooltip when `st.is_running` is true. `#cockpitParamsLockHint` is displayed. `applyCockpitConfig()` rejects early while running. Interactivity restores when bot stops.
  - Verify: Browser / DOM inspection and integration tests.
  - Files: `server/osc_dash.py`

### Phase 3: Integration Tests & Full Suite Verification
- [ ] Task 3: Integration tests in `tests/test_osc_dash_integration.py` and regression test run
  - Acceptance: `POST /api/live/config` returns 400 when bot runs and parameters change, returns 200 for idempotent requests, returns 200 when bot is stopped. DOM asserts `#cockpitParamsLockHint`. Full test suite passes.
  - Verify: `python -m pytest -q`
  - Files: `tests/test_osc_dash_integration.py`
