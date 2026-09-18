# CONSTRAINTS — Issue #198: interactive visual parameter preview grid for backtest sweeper

## Scope Lock (read first)
1. **Backtest engine untouched:** `backtest/engine.py` and its core simulation logic must not be modified. This is strictly a frontend UI/visualization addition.
2. **Backtest tab only:** Scope is strictly `#tab-backtest` and `setupBacktestInputListeners()` in `server/osc_dash.py`. Do not alter Live Cockpit, collector controls, or market data tabs.
3. **Pure client-side SVG rendering:** Rendered using lightweight vanilla DOM/SVG inside `server/osc_dash.py`. No external charting libraries, no npm packages, no server-side image generation, no new pip dependencies.
4. **No backend API or query parameter changes:** `/api/backtest` endpoints, parameters, and signatures remain 100% unchanged.

## Quality Guardrails
5. **Zero regressions:** Targeted test suite covering modified files must pass:
   `python -m pytest tests/test_osc_dash_integration.py -q`.
   Full-repo sweeps stay with CI on push (AGENTS.md testing policy).
6. **Zero runtime errors / Degenerate case safety:** Degenerate parameter inputs (`btOffset=0`, disabled stops, `btEntryDelay=0`, inverted quote range) must clamp gracefully without throwing JavaScript exceptions, NaN SVG coordinates, or broken layout.
7. **Performance budget:** Client-side preview redraw execution time must remain < 16ms (60 FPS smooth interaction on standard browser event loop) without triggering network activity.
8. **No suppression:** Strictly forbid skipping, disabling, weakening existing tests, or suppressing linters.

