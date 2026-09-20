# Task Plan — Issue #269: Duration-aware backtest timing percentages

**Size tier:** Large — changes cross the Backtest UI, FastAPI parameter boundary, frozen BacktestParams contract, timestamp replay behavior, geometry rendering, and parity tests.
**Task type:** Code + Design/UI + API/Backend + QA/Regression.
**Issue:** #269

## Decisions locked by the issue
- Operator-facing Backtest timing values are percentages from 0 to 100.
- One percentage applies relative to each window's actual timestamp duration.
- 10% means 30s on 5m/300s and 90s on 15m/900s.
- Geometry's primary time axis is normalized 0%–100%; seconds may appear only as secondary explanatory text.
- Live cockpit behavior is out of scope; preserve internal timestamp-based replay and parity.

## Tasks

- [x] **TASK-1 [Backend/Logic]**: Define one duration-aware percentage conversion contract.
  - Target: `backtest/engine.py`, `strategy/book_math.py`, focused engine tests.
  - Build: tested conversion from `window_length` and percentage to seconds; exact boundaries and invalid-window behavior; percentage input carried without breaking existing callers.
  - Verify: unit tests prove 10%→30s/90s and boundaries.

- [x] **TASK-2 [Backend/Logic]**: Apply percentage timing independently to every replay window.
  - Target: `backtest/engine.py`, parity tests.
  - Build: entry delay uses each window's actual timestamp duration; dead-zone percentage remains duration-aware; existing timestamp and parity behavior retained.
  - Verify: focused engine, dead-zone, and parity tests.

- [x] **TASK-3 [API/Backend]**: Update the Backtest request and response contract.
  - Target: `server/osc_dash.py`, integration tests.
  - Build: accept validated 0–100% `entry_delay_pct` and `dead_zone_pct`, explicitly translate them at the simulation boundary, and preserve compatibility for internal seconds callers.
  - Verify: API validation, safe file, guard, and response tests.

- [x] **TASK-4 [Design/UI]**: Convert Backtest timing controls to percentages.
  - Target: `server/osc_dash.py` served HTML/JavaScript, theme/integration tests.
  - Build: Entry Delay and Dead Zone controls use 0–100% bounds, clear labels, percentage query parameters, and no seconds-unit selector on the Backtest surface.
  - Verify: served-HTML tests and no-auto-run behavior.

- [x] **TASK-5 [Design/UI]**: Normalize Strategy Geometry Preview to 0%–100%.
  - Target: `server/osc_dash.py`, tests.
  - Build: replace the hardcoded 300s timeline with a normalized percentage axis and place delay/dead-zone shading using percentages.
  - Verify: focused UI tests and source inspection of normalized labels/zones.

- [x] **TASK-6 [QA/Regression]**: Run the focused gate and real-data browser verification.
  - Target: focused timing, parity, API, and UI suites.
  - Build: verify the changed semantics against the available test fixtures and preserve the real-data browser gate for Station IV.
  - Verify: `python -m pytest tests/test_book_math.py tests/test_backtest_engine.py tests/test_dead_zone_parity.py tests/test_engine_parity.py tests/test_osc_dash_integration.py tests/test_theme_tokens.py -q` — 348 passed.

## Out of scope
Live cockpit timing controls, market-data collection, sweep axes, P&L/strategy rules, chart expansion, and unrelated dashboard tabs.
