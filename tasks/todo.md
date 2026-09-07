# tasks/todo.md — Issue #95

- [ ] T1 live state fields + three knobs + rollover/reset clearing
- [ ] T2 RED: 8 live re-entry tests in tests/test_live_trader.py
- [ ] T3 GREEN: `_maybe_reenter_drift_skipped()` wired before `can_place_entry`
- [ ] T4 params payload + `update_config()` + `ConfigPayload` plumbing
- [ ] T5 RED: 4 backtest parity tests in tests/test_entry_timeout.py
- [ ] T6 GREEN: `adverse_skipped` split + backtest re-entry rule
- [ ] T7 `python -m pytest -q` fully green, docstring gate passes
