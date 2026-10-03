# Todo — Issue #377

- [x] T1 [Backend/Logic]: engine per-leg fill timing + merge/stop/settle records (`backtest/engine.py` + unit tests)
- [x] T2 [Backend/Logic]: `stops` passthrough in `trades_sample` (`server/osc_dash.py` + integration assertion)
- [x] T3 [Design/UI]: per-row Time/Duration rendering with legacy fallback (`server/osc_dash.py` + browser check)
- [x] Final gate: `tests/test_backtest_engine.py -q` + `tests/test_osc_dash_integration.py -q` green, working tree clean on `i377/*`
