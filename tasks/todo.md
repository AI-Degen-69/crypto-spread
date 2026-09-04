# Tasks: Dashboard UI Controls for Market Selection (Issue #51)

**Status: COMPLETE.** Merged to `master` as `60a5e03` via PR #56 (squash). Issue #51 closed.
Final verification on `master`: `python -m pytest -q` -> **176 passed in 12.81s**.

- [x] Task 1: API schemas and endpoints in `server/osc_dash.py`
  - Acceptance: `LiveConfigPayload` accepts `selected_markets`, `tokens`, `durations`. `api_live_config` safely updates engine and returns HTTP 400 on `ValueError`. `/api/live/state` returns `available_series` and `selected_series`.
  - Verify: `python -m pytest tests/test_osc_dash_integration.py`
  - Files: `server/osc_dash.py`

- [x] Task 2: Cockpit UI filter controls and dynamic card rendering in `server/osc_dash.py`
  - Acceptance: Cockpit UI renders token filter chips and duration toggle buttons. Market grid dynamically displays cards for all active selected series, retaining colors and metadata. User selections sync via `/api/live/config`.
  - Verify: Inspect UI markup and test interactive API flow via TestClient.
  - Files: `server/osc_dash.py`

- [x] Task 3: Integration tests in `tests/test_osc_dash_integration.py`
  - Acceptance: Automated tests cover valid/invalid selection payloads, active position deselection error handling, and state series metadata.
  - Verify: `python -m pytest tests/test_osc_dash_integration.py -v`
  - Files: `tests/test_osc_dash_integration.py`

- [x] Task 4: Full verification gate & docstrings
  - Acceptance: All 160+ tests passing, 100% docstring coverage confirmed.
  - Verify: `python -m pytest -q` and `python -m pytest tests/test_docstrings.py`
  - Files: `tests/test_docstrings.py`

- [x] Task 5: Enforce the selection the bot actually trades
  - Acceptance: The tick loop polls only the selected slugs, window duration is derived per slug (900s for 15m, 300s for 5m), the stream bridge subscribes only to active tokens, and `get_state()` reports the exact active selection the dashboard renders.
  - Verify: `python -m pytest tests/test_live_trader.py -k "ticks_only_selected or state_reports_selection"`
  - Files: `strategy/live_trader.py`, `tests/test_live_trader.py`

- [x] Task 6: Lock market selection while the bot is running
  - Acceptance: `update_config()` raises `ValueError` when a selection change would alter the active market set while `is_running`; offset, exit threshold, shares, mode and wallet stay editable. The API returns HTTP 400. Cockpit filter controls are disabled with a "locked while running" hint, each handler re-checks the lock, the config payload omits selection fields during a run, and a rejected update resyncs the UI from engine state.
  - Verify: `python -m pytest tests/test_live_trader.py tests/test_osc_dash_integration.py -k "running or lock"`
  - Files: `strategy/live_trader.py`, `server/osc_dash.py`, `tests/test_live_trader.py`, `tests/test_osc_dash_integration.py`

- [x] Task 7: Close the start/reselect race and make config updates atomic
  - Acceptance: The running-state guard is evaluated inside the same `_engine_lock` that mutates the market set; `start()`/`stop()` take that lock around the `is_running` flag and `start()` clears `quoting_halted` inside it; the guard compares against `self.markets` rather than `self.selected_series`. Selection is resolved and applied before any scalar field, so a rejected update leaves the whole configuration untouched.
  - Verify: `python -m pytest tests/test_live_trader.py -k "guarded_by_engine_lock or quoting_halted or rejected_config or guard_tracks"`
  - Files: `strategy/live_trader.py`, `tests/test_live_trader.py`

- [x] Task 8: Preserve non-rectangular market selections in the cockpit
  - Acceptance: When the engine's selection is not expressible as a token x duration product (e.g. `BTC 5m` + `ETH 15m`), the cockpit keeps the exact slug set and resubmits it as `selected_markets`, so a parameter apply cannot widen two markets into four. Any explicit chip or duration click returns to the product form.
  - Verify: `python -m pytest tests/test_osc_dash_integration.py -k "non_rectangular"`
  - Files: `server/osc_dash.py`, `tests/test_osc_dash_integration.py`

- [x] Task 9: PR review loop (CodeRabbit)
  - Acceptance: PR #56 opened, reviewed, and merged. Round 1 raised 4 findings (partial config mutation, `quoting_halted` outside the lifecycle lock, collapsed non-rectangular selections, non-hermetic lock test) — all accepted and fixed in `c24d2ea`, each thread replied to inline and resolved. Round 2 returned `APPROVED` with zero new comments; CodeRabbit and GitGuardian checks green.
  - Verify: `gh pr view 56`
  - Files: n/a

## Known limitation (not blocking)

`server/osc_dash.py` is ~3700 lines and embeds the entire cockpit SPA in a Python string literal. This change added ~90 net lines to it without decomposition. Extracting the cockpit JavaScript to a served static file is the real remedy and is out of scope for #51.
