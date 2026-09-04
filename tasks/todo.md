# Tasks: Issue #62 Lock Strategy Parameters While Trading Bot Is Running

- [x] Task 1: Backend parameter locking under `_engine_lock` in `strategy/live_trader.py` and unit tests in `tests/test_live_trader.py`
  - Acceptance: `engine.update_config()` raises `ValueError` if any strategy parameter (`offset`, `exit_thresh`, `shares`, `mode`, `wallet_address`, `starting_balance`) changes while `is_running=True`. Idempotent calls succeed. Modification succeeds when stopped.
  - Verify: `python -m pytest tests/test_live_trader.py -q`
  - Files: `strategy/live_trader.py`, `tests/test_live_trader.py`

- [ ] Task 2: Cockpit form inputs lock, notice banner, and client guard in `server/osc_dash.py`
  - Acceptance: Inputs and `#btnApplyParams` are disabled with lock styling and tooltip when `st.is_running` is true. `#cockpitParamsLockHint` is displayed. `applyCockpitConfig()` rejects early while running. Interactivity restores when bot stops.
  - Verify: DOM inspection and integration tests.
  - Files: `server/osc_dash.py`

- [ ] Task 3: Integration tests in `tests/test_osc_dash_integration.py` and regression test run
  - Acceptance: `POST /api/live/config` returns 400 when bot runs and parameters change, returns 200 for idempotent requests, returns 200 when bot is stopped. DOM asserts `#cockpitParamsLockHint`. Full test suite passes.
  - Verify: `python -m pytest -q`
  - Files: `tests/test_osc_dash_integration.py`
