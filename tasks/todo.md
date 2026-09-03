# Tasks: Dashboard UI Controls for Market Selection (Issue #51)

- [ ] Task 1: API schemas and endpoints in `server/osc_dash.py`
  - Acceptance: `LiveConfigPayload` accepts `selected_markets`, `tokens`, `durations`. `api_live_config` safely updates engine and returns HTTP 400 on `ValueError`. `/api/live/state` returns `available_series` and `selected_series`.
  - Verify: `python -m pytest tests/test_osc_dash_integration.py`
  - Files: `server/osc_dash.py`

- [ ] Task 2: Cockpit UI filter controls and dynamic card rendering in `server/osc_dash.py`
  - Acceptance: Cockpit UI renders token filter chips and duration toggle buttons. Market grid dynamically displays cards for all active selected series, retaining colors and metadata. User selections sync via `/api/live/config`.
  - Verify: Inspect UI markup and test interactive API flow via TestClient.
  - Files: `server/osc_dash.py`

- [ ] Task 3: Integration tests in `tests/test_osc_dash_integration.py`
  - Acceptance: Automated tests cover valid/invalid selection payloads, active position deselection error handling, and state series metadata.
  - Verify: `python -m pytest tests/test_osc_dash_integration.py -v`
  - Files: `tests/test_osc_dash_integration.py`

- [ ] Task 4: Full verification gate & docstrings
  - Acceptance: All 160+ tests passing, 100% docstring coverage confirmed.
  - Verify: `python -m pytest -q` and `python -m pytest tests/test_docstrings.py`
  - Files: `tests/test_docstrings.py`
