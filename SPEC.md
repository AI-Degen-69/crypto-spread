# Spec: Lock Strategy Parameters While Trading Bot Is Running (Issue #62)

## 1. Objective
Eliminate runtime configuration race conditions and state desynchronization in both the trading engine backend (`strategy/live_trader.py`) and dashboard UI (`server/osc_dash.py`):
1. **Frontend Input Locking**: When the live trading bot is running (`is_running == True`), disable all strategy parameter input fields (`#cockpitOffset`, `#cockpitExit`, `#cockpitShares`, `#cockpitMode`, `#cockpitWallet`, `#cockpitStartBal`) and the `#btnApplyParams` button with visual lock styling (`opacity: 0.4`, `cursor: not-allowed`, tooltip) and a visible lock banner (`#cockpitParamsLockHint`).
2. **Frontend Client Guard**: Short-circuit `applyCockpitConfig()` immediately if `cockpitState && cockpitState.is_running`.
3. **Frontend State Synchronization**: While running, ensure input field values mirror active engine configuration (`st.params`, `st.mode`, `st.starting_balance`, `st.wallet_address`) to prevent stale UI values.
4. **Backend Engine Validation**: In `LiveTraderEngine.update_config()` under `self._engine_lock`, raise `ValueError("Cannot change strategy parameters while the trading bot is running. Stop the bot first.")` if any requested scalar parameter (`offset`, `exit_thresh`, `shares`, `mode`, `wallet_address`, `starting_balance`) differs from the active configuration while `self.is_running == True`.
5. **Idempotency**: Allow calls to `update_config()` where requested parameter values match existing active values (idempotent configuration calls).
6. **REST API Contract**: Ensure `POST /api/live/config` returns HTTP 400 with `{"error": str(e)}` when mid-run mutations are rejected.
7. **Automatic Unlocking**: When the bot is stopped (`is_running == False`), immediately re-enable all parameter inputs and the Apply button.

---

## 2. Tech Stack & Dependencies
- **Runtime**: Python 3.11+
- **Backend Framework**: FastAPI + Uvicorn + Pydantic
- **Frontend Architecture**: Embedded Vanilla ES6 JavaScript + CSS3 SPA in `server/osc_dash.py`
- **Testing Framework**: `pytest`, `pytest-asyncio`, FastAPI `TestClient`

---

## 3. Commands
- Run all tests:
  ```powershell
  python -m pytest -q
  ```
- Run Live Trader engine tests:
  ```powershell
  python -m pytest tests/test_live_trader.py -q
  ```
- Run Dashboard integration tests:
  ```powershell
  python -m pytest tests/test_osc_dash_integration.py -q
  ```
- Start Dashboard server:
  ```powershell
  python -m uvicorn server.osc_dash:app --host 127.0.0.1 --port 8802
  ```

---

## 4. Project Structure
```
strategy/
  live_trader.py               -> LiveTraderEngine.update_config() validation under self._engine_lock
server/
  osc_dash.py                  -> Dashboard UI HTML inputs, #cockpitParamsLockHint, lock helpers & sync
tests/
  test_live_trader.py          -> Unit tests for mid-run parameter rejection, idempotency, and post-stop update
  test_osc_dash_integration.py -> Integration tests for POST /api/live/config HTTP 400 rejection and DOM elements
SPEC.md                        -> This specification document
```

---

## 5. Implementation Contracts & Code Style

### 5.1 Backend Validation Contract (`strategy/live_trader.py`)
In `LiveTraderEngine.update_config()`, within `with self._engine_lock:` before applying any mutations:

```python
with self._engine_lock:
    # 1. Check market selection change while running
    if self.is_running and new_slugs != set(self.markets.keys()):
        raise ValueError("Cannot change market selection while the trading bot is running. Stop the bot first.")

    # 2. Check scalar strategy parameters change while running
    if self.is_running:
        param_changed = False
        if offset is not None and abs(float(offset) - self.offset) > 1e-6:
            param_changed = True
        elif exit_thresh is not None and abs(float(exit_thresh) - self.exit_thresh) > 1e-6:
            param_changed = True
        elif shares is not None and int(shares) != self.shares:
            param_changed = True
        elif mode is not None and mode != self.mode:
            param_changed = True
        elif wallet_address is not None and wallet_address.strip() != (self.wallet_address or ""):
            param_changed = True
        elif starting_balance is not None and self.mode != "live" and abs(float(starting_balance) - self.starting_balance) > 1e-6:
            param_changed = True

        if param_changed:
            raise ValueError("Cannot change strategy parameters while the trading bot is running. Stop the bot first.")
```

### 5.2 Frontend UI & Interactivity Contract (`server/osc_dash.py`)

#### A. HTML Banner Element (`#cockpitParamsLockHint`)
Place directly beside `#btnApplyParams` in the `.form-grid` or form actions container:
```html
<div class="form-group" style="justify-content:flex-end;align-items:center;display:flex;gap:10px">
  <span id="cockpitParamsLockHint" style="display:none;font:700 11px var(--disp);color:var(--down);letter-spacing:0.04em">
    🔒 LOCKED WHILE BOT IS RUNNING — STOP THE BOT TO CHANGE PARAMETERS
  </span>
  <button id="btnApplyParams" class="btn" style="background:rgba(51,201,181,0.15);border-color:var(--up);color:var(--up);font-weight:700;height:35px" onclick="applyCockpitConfig()">💾 APPLY PARAMETERS</button>
</div>
```

#### B. JavaScript Lock Helper Function
```javascript
function updateCockpitParamsLockUI(locked) {
  const paramIds = [
    'cockpitOffset',
    'cockpitExit',
    'cockpitShares',
    'cockpitMode',
    'cockpitWallet',
    'cockpitStartBal',
    'btnApplyParams',
  ];
  paramIds.forEach(id => {
    const el = $(id);
    if (!el) return;
    if (locked) {
      el.disabled = true;
      el.style.opacity = '0.4';
      el.style.cursor = 'not-allowed';
      el.title = 'Stop the bot to change parameters';
    } else {
      // Do not re-enable starting balance if locked by LIVE mode net value
      if (id === 'cockpitStartBal' && $('cockpitMode') && $('cockpitMode').value === 'live') {
        el.disabled = false;
        el.readOnly = true;
        el.style.opacity = '0.7';
        el.style.cursor = 'not-allowed';
        el.title = 'Starting balance is locked to real Polymarket net account value in LIVE mode.';
      } else {
        el.disabled = false;
        el.readOnly = false;
        el.style.opacity = '';
        el.style.cursor = '';
        el.title = '';
      }
    }
  });

  const hint = $('cockpitParamsLockHint');
  if (hint) hint.style.display = locked ? 'inline' : 'none';
}
```

#### C. Guard in `applyCockpitConfig()`
```javascript
async function applyCockpitConfig() {
  if (cockpitState && cockpitState.is_running) {
    return;
  }
  if (isApplyingCockpitConfig) return;
  // ... proceed with config POST
}
```

#### D. Synchronization in `renderCockpitUI(st)`
When `st.is_running == True`:
- Synchronize `#cockpitOffset.value = st.params.offset`.
- Synchronize `#cockpitExit.value = st.params.exit_thresh`.
- Synchronize `#cockpitShares.value = st.params.shares`.
- Call `updateCockpitParamsLockUI(st.is_running)`.

---

## 6. Testing Strategy

### 6.1 Backend Unit Tests (`tests/test_live_trader.py`)
1. **`test_live_trader_mid_run_parameter_lock`**:
   - Instantiate `LiveTraderEngine` with default parameters (`offset=0.02, exit_thresh=0.05, shares=5, mode="paper"`).
   - Set `engine.is_running = True`.
   - Assert `engine.update_config(offset=0.03)` raises `ValueError` with `"Cannot change strategy parameters while the trading bot is running"`.
   - Assert `engine.update_config(exit_thresh=0.08)` raises `ValueError`.
   - Assert `engine.update_config(shares=10)` raises `ValueError`.
   - Assert `engine.update_config(mode="live")` raises `ValueError`.
   - Assert `engine.update_config(wallet_address="0xabc...")` raises `ValueError`.
   - Assert `engine.update_config(starting_balance=5000.0)` raises `ValueError`.
   - Assert engine attributes remain unchanged (`offset == 0.02`, `shares == 5`, etc.).
2. **`test_live_trader_idempotent_config_while_running`**:
   - Set `engine.is_running = True`.
   - Call `engine.update_config(offset=0.02, exit_thresh=0.05, shares=5, mode="paper")`.
   - Assert call succeeds without raising `ValueError`.
3. **`test_live_trader_update_config_after_stopped`**:
   - Set `engine.is_running = False`.
   - Call `engine.update_config(offset=0.03, exit_thresh=0.07, shares=12)`.
   - Assert call succeeds and `engine.offset == 0.03`, `engine.exit_thresh == 0.07`, `engine.shares == 12`.

### 6.2 API Integration Tests (`tests/test_osc_dash_integration.py`)
1. **`test_api_live_config_locked_while_running`**:
   - Start bot via `POST /api/live/control` with `{"action": "start"}`.
   - Send `POST /api/live/config` with `{"offset": 0.04}`.
   - Assert `response.status_code == 400` and `response.json()["error"]` mentions stopping the bot.
   - Send `POST /api/live/config` with identical active parameters (`{"offset": 0.02, "shares": 5}`).
   - Assert `response.status_code == 200`.
   - Stop bot via `POST /api/live/control` with `{"action": "stop"}`.
   - Send `POST /api/live/config` with `{"offset": 0.04}`.
   - Assert `response.status_code == 200` and `response.json()["params"]["offset"] == 0.04`.
   - Reset back to defaults.
2. **`test_cockpit_params_lock_dom_elements`**:
   - Fetch GET `/`.
   - Verify presence of `#cockpitParamsLockHint`.
   - Verify presence of `#btnApplyParams` and all 6 parameter input IDs.

### 6.3 Regression Testing
- Execute full test suite: `python -m pytest -q` (all 195+ tests must pass).

---

## 7. Boundaries

- **Always do:**
  - Execute parameter comparison and validation strictly under `self._engine_lock`.
  - Validate parameters before any state mutation occurs.
  - Allow idempotent calls where values match current engine state.
  - Preserve all element IDs and existing REST API response schema.
  - Run `python -m pytest -q` to ensure 0 regressions.
- **Ask first:**
  - Introducing any new strategy parameters to `LiveConfigPayload` or `LiveTraderEngine`.
  - Changing default parameter values (`offset=0.02`, `shares=5`, etc.).
- **Never do:**
  - Allow mutating strategy parameters while `is_running == True`.
  - Leave inputs editable in the UI when the bot is running.
  - Allow a client-side config submission while `is_running == True`.

---

## 8. Success Criteria
- [ ] `engine.update_config()` raises `ValueError` if `offset`, `exit_thresh`, `shares`, `mode`, `wallet_address`, or `starting_balance` are modified while `is_running == True`.
- [ ] Idempotent `update_config()` calls with unchanged values succeed while `is_running == True`.
- [ ] `/api/live/config` returns HTTP 400 with a descriptive error message when parameter changes are attempted while running.
- [ ] UI inputs (`#cockpitOffset`, `#cockpitExit`, `#cockpitShares`, `#cockpitMode`, `#cockpitWallet`, `#cockpitStartBal`, `#btnApplyParams`) are disabled with visual lock styling when the bot is running.
- [ ] Visible lock notice banner (`#cockpitParamsLockHint`) displays when running and hides when stopped.
- [ ] `applyCockpitConfig()` in JavaScript rejects execution early if `cockpitState.is_running` is true.
- [ ] UI inputs and Apply button are immediately re-enabled when the bot stops.
- [ ] All new unit tests pass in `tests/test_live_trader.py` and `tests/test_osc_dash_integration.py`.
- [ ] Full project test suite passes cleanly (`python -m pytest -q`) with zero regressions.

---

## 9. Open Questions & User Clarifications
- None identified. Requirements and design decisions are fully aligned with Issue #62.
