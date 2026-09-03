# Todo List: Issue #50 Market Selection Engine and CLI Filtering

- [x] Task 1: Implement `filter_series()` and helpers in `strategy/series.py`
  - Acceptance: Pure function returning matching subset of `SERIES`; validates tokens and durations; raises `ValueError` on invalid values; 100% docstrings.
  - Verify: Unit tests in `tests/test_series.py` and `python -m pytest tests/test_docstrings.py`.
  - Files: `strategy/series.py`, `tests/test_series.py`

- [ ] Task 2: Spot tick fan-out and 15m aliases in `strategy/streaming.py`
  - Acceptance: `series_for_symbol(symbol)` returns all active matching slugs (5m & 15m); 15m aliases added to `SYMBOL_TO_SERIES` & `SERIES_TO_SYMBOL`; `UnifiedStreamBridge._handle_spot_tick` broadcasts with fan-out.
  - Verify: Unit tests in `tests/test_streaming.py`.
  - Files: `strategy/streaming.py`, `tests/test_streaming.py`

- [ ] Task 3: Multi-market selection and dynamic configuration in `LiveTraderEngine`
  - Acceptance: Engine accepts market selection during `__init__` and `update_config()`; `SERIES_COLORS` includes 15m; deselected markets have resting orders cancelled; single snapshot in `_tick_all_markets`; `on_spot_tick` fans out to all active markets sharing the token.
  - Verify: `tests/test_live_trader.py`.
  - Files: `strategy/live_trader.py`, `tests/test_live_trader.py`

- [ ] Task 4: CLI flags in `scripts/run_single_window_test.py`
  - Acceptance: Accepts `--tokens` (comma-separated) and `--duration` (`5m`, `15m`, `both`); resolves via `filter_series()`; maintains backward compatibility.
  - Verify: Smoke test via `--help` and CLI test.
  - Files: `scripts/run_single_window_test.py`

- [ ] Task 5: End-to-end verification and docstring audit
  - Acceptance: All test suites green: `python -m pytest -q`.
  - Verify: `python -m pytest -q`.
  - Files: Whole repository.
