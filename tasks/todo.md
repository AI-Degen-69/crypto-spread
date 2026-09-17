# TODO — Issue #231: Rule: the leg chase escalates with time instead of firing on the first tick

- [x] **T0**: Branch `feat/leg-chase-ladder-231` off `master` & lock specs
- [x] **T1**: Shared chase escalation math in `strategy/book_math.py` (`chase_progress`, `chase_ceiling`)
- [x] **T2**: Backtest engine: time-proportional leg chase ladder anchored on dead zone
- [x] **T3**: Live engine: time-proportional leg chase ladder & cleanup of legacy stepped knobs
- [x] **T4**: Dedicated leg chase parity test suite (`tests/test_leg_chase_parity.py`)
- [x] **T5**: Retarget existing test suites (`test_stop_orders.py`, `test_live_trader.py`, `test_backtest_engine.py`)
- [x] **T6**: Full targeted gates verification & Station IV handoff
