# Plan — Issue #216: Dashboard resting-price fallback reads up_mid/down_mid

- **Issue:** #216 (`ready-for-agent`, assigned)
- **Branch:** `fix/dash-resting-price-fallback-216`
- **Size tier:** Small — 2 files (`server/osc_dash.py`, `tests/test_osc_dash_integration.py`).
- **Task type:** UI & Design / Debug.
- **Stack:** Python 3.12, FastAPI, pytest, Vanilla JS client.
- **Skills routed:** `frontend-ui-engineering`, `debugging-and-error-recovery`, `test-driven-development`.

## Root Cause

In `server/osc_dash.py` (lines 3327, 3337, 6016, 6017, 6066, 6067), fallback expressions check `m.up_mid` and `m.down_mid`. Neither property is ever emitted by `live_trader.py` (which emits `mid` as the single authoritative mid). Because `m.up_mid` is always undefined, the fallback unconditionally evaluates to `0.48`, which hardcodes `offset = 0.02`. If an operator runs with a different offset (e.g. 0.03), the dashboard presents a quote price that the engine never traded or placed.

## Tasks

### T1 — Red Tests for Dashboard Resting Fallback `[Debug/Test]`
- Add tests to `tests/test_osc_dash_integration.py`:
  - `test_dash_script_contains_no_phantom_up_down_mid_keys`: asserts `up_mid` and `down_mid` are completely absent from the served client script.
  - `test_dash_resting_price_helper_respects_custom_offset_and_mid`: asserts that when resting quotes are null and offset is 0.03, the helper does not evaluate to 0.48, but correctly yields 0.47 (flat) or anchored prices from `mid` (e.g. mid=0.60 -> up=0.57, down=0.37).
- **Target File:** `tests/test_osc_dash_integration.py`
- **Verification:** Run `python -m pytest tests/test_osc_dash_integration.py -q -k "phantom_up_down_mid or resting_price_helper"` (must fail before fix).

### T2 — Implement Helpers and Replace 6 Call Sites in `server/osc_dash.py` `[Design/UI]`
- In `server/osc_dash.py`, define `cockpitRestingPrice(m, leg, fallbackOffset)` and `cockpitLegPrice(m, leg, fallbackOffset)`.
- Replace the 6 dead ternary sites:
  - Fill toasts: lines 3327, 3337
  - Position status string: lines 6016, 6017
  - Card bids html: lines 6066, 6067
- Pass `st?.params?.offset` as the fallback offset at all sites.
- **Target File:** `server/osc_dash.py`
- **Verification:** T1 tests turn green; all existing tests in `test_osc_dash_integration.py` pass.

### T3 — Targeted Verification & Quality Gate `[Backend/Logic]`
- Run targeted test suite:
  - `python -m pytest tests/test_osc_dash_integration.py -q`
- Verify zero regressions, clean syntax, no console errors, and strict adherence to CONSTRAINTS.md.
- **Target Files:** `tests/test_osc_dash_integration.py`, `server/osc_dash.py`
- **Verification:** All tests in `tests/test_osc_dash_integration.py` pass cleanly.
