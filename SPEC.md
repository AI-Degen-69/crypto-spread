# SPEC: Part 2 — Dashboard UI Controls for Market Selection by Token and Duration

## Objective
Provide operators with live interactive controls in the Cockpit dashboard (`server/osc_dash.py`) to select which cryptocurrency markets and window durations (`5m`, `15m`, or `both`) to quote and trade on, updating the live engine dynamically via `/api/live/config` and filtering the Cockpit status cards and charts accordingly.

## Tech Stack
- Backend: Python 3.10+, FastAPI, Pydantic, Uvicorn
- Frontend: Vanilla HTML5 / CSS3 / ES6 JavaScript (embedded in `server/osc_dash.py`), Chart.js
- Testing: `pytest`, `httpx` / FastAPI `TestClient`

## Commands
- Run test suite: `python -m pytest -q`
- Run dashboard integration tests: `python -m pytest tests/test_osc_dash_integration.py -v`
- Start dashboard server: `python -m uvicorn server.osc_dash:app --host 127.0.0.1 --port 8802`
- Verify docstrings: `python -m pytest tests/test_docstrings.py`

## Project Structure
- `server/osc_dash.py`:
  - `LiveConfigPayload`: Add `selected_markets`, `tokens`, `durations`.
  - `/api/live/config`: Handle and validate selection parameters, returning HTTP 400 on `ValueError`.
  - `/api/live/state`: Return `available_series` (all 10 markets with token and duration metadata) and `selected_series`.
  - HTML/JS Cockpit UI:
    - Token filter toggles (`BTC`, `ETH`, `BNB`, `SOL`, `XRP`) + All / Clear.
    - Duration filter toggles (`5m`, `15m`, `Both`).
    - Dynamic market cards grid rendering only currently selected series.
    - Persist user UI selection in `localStorage` and synchronize with backend `/api/live/state`.
- `tests/test_osc_dash_integration.py`:
  - Test `/api/live/config` with valid and invalid `selected_markets`, `tokens`, and `durations`.
  - Test `/api/live/state` includes `available_series` and active `selected_series`.
  - Test error handling when deselecting an active position returns HTTP 400 with explanation.

## Code Style & Architecture
- **Pydantic Schema Extension**:
```python
class LiveConfigPayload(BaseModel):
    offset: Optional[float] = Field(default=None, ge=0.001, le=0.49)
    exit_thresh: Optional[float] = Field(default=None, ge=0.001, le=0.50)
    shares: Optional[int] = Field(default=None, ge=1, le=10000)
    mode: Optional[str] = Field(default=None, pattern="^(paper|live)$")
    wallet_address: Optional[str] = None
    starting_balance: Optional[float] = Field(default=None, ge=0.0)
    selected_markets: Optional[List[str]] = None
    tokens: Optional[List[str]] = None
    durations: Optional[List[int]] = None
```
- **Error Propagation**: Catch `ValueError` in `api_live_config` and return `JSONResponse(status_code=400, content={"error": str(e)})`.
- **UI State Synchronization**:
  - `renderCockpitUI()` renders token pills and duration buttons reflecting active selections.
  - Changing token or duration triggers an asynchronous `POST /api/live/config` call with updated selection.
  - If server rejects (e.g. market has open position), UI alerts the operator and rolls back the toggle.

## Testing Strategy
- Unit & integration tests in `tests/test_osc_dash_integration.py`:
  - `test_api_live_config_market_selection()`: POST tokens and durations, assert 200 and updated state.
  - `test_api_live_config_invalid_token()`: POST invalid token (e.g. `DOGE`), assert 400 error.
  - `test_api_live_config_open_position_rejection()`: With an open leg in market, assert deselecting returns 400.
  - `test_api_live_state_series_metadata()`: Assert available series list contains all 10 markets with slug, token, duration, label, and color.
- Regression testing: `python -m pytest -q` (160+ passing).
- Docstring coverage: 100% on non-test source files.

## Boundaries
- **Always**:
  - Return HTTP 400 with clean error message when payload fails validation or position guard triggers.
  - Preserve backward compatibility for existing `/api/live/config` callers who do not provide selection fields.
  - Sync UI state seamlessly with backend engine state on initial load.
- **Ask first**:
  - Removing or deprecating existing `/api/live/*` routes.
- **Never**:
  - Hardcode the 5m markets in frontend card rendering loops; derive cards dynamically from selected series.
  - Silently ignore market selection failures in the UI.

## Success Criteria
1. `/api/live/state` returns all 10 series in `available_series` and active selections in `selected_series`.
2. `/api/live/config` accepts `selected_markets`, `tokens`, and `durations`, updates `LiveTraderEngine`, and returns updated state.
3. `/api/live/config` returns HTTP 400 with descriptive error if tokens/durations are invalid or if an active market with open position cannot be deselected.
4. Live Cockpit UI displays interactive token chips (`BTC`, `ETH`, `BNB`, `SOL`, `XRP`) and duration toggles (`5m`, `15m`, `Both`).
5. Cockpit market card matrix renders only active selected markets (e.g. 1 card for BTC 15m or 10 cards for all).
6. 100% docstring coverage preserved and all tests pass (`python -m pytest -q`).
