# Technical Implementation Plan: Dashboard UI Controls for Market Selection (Issue #51)

## Overview
Expose market selection by token (`BTC`, `ETH`, `BNB`, `SOL`, `XRP`) and duration (`5m`, `15m`, or `both`) on the FastAPI dashboard API (`server/osc_dash.py`) and Live Cockpit web UI, allowing operators to interactively configure active trading markets and view dynamically rendered status cards and prices.

## Dependency Graph
1. **API Endpoints & Schemas (`server/osc_dash.py`)**:
   - Extend `LiveConfigPayload` with `selected_markets`, `tokens`, and `durations`.
   - Update `api_live_config()` to pass selection to `LiveTraderEngine.update_config()` with `try/except ValueError` returning HTTP 400.
   - Update `api_live_state()` to expose all 10 series in `available_series` and active selections in `selected_series`.
2. **Dashboard UI Components & Logic (`server/osc_dash.py`)**:
   - Add Token filter chips (`BTC`, `ETH`, `BNB`, `SOL`, `XRP`, All, Clear) to Cockpit header/controls.
   - Add Duration filter toggle buttons (`5m`, `15m`, `Both`).
   - Refactor `renderCockpitUI` and `COCKPIT_SERIES` to dynamically render market cards based on `selected_series` / `available_series`.
   - Wire interactive click handlers to call `/api/live/config` and update UI state.
3. **Automated Integration Tests (`tests/test_osc_dash_integration.py`)**:
   - Test `/api/live/config` with token and duration payloads.
   - Test `/api/live/config` rejection with HTTP 400 on invalid tokens or open position deselection.
   - Test `/api/live/state` includes complete `available_series` and active `selected_series`.
4. **Verification & Regression**:
   - Run `python -m pytest -q` across all 160+ tests.
   - Verify 100% docstring coverage.
