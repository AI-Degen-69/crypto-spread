# TODO — Issue #232: Rule: fresh_start — the engine keeps no memory inside a window

- [x] **T0**: Branch `feat/fresh-start-rule-232` off `master` & lock specs
- [x] **T1**: Remove legacy re-entry knobs (`min_requote_remaining_sec`, `requote_round`, `reentry_stats`) from `strategy/live_trader.py`
- [x] **T2**: Implement `fresh_start` clean-state quoting in `strategy/live_trader.py`
- [x] **T3**: Implement `fresh_start` loop continuation on pair-merge and stop in `backtest/engine.py`
- [x] **T4**: Dedicated fresh_start parity test suite (`tests/test_fresh_start_parity.py`)
- [x] **T5**: Retarget existing test suites (`test_live_trader.py`, `test_backtest_engine.py`, etc.)
- [x] **T6**: Full targeted gates verification & Station IV handoff


