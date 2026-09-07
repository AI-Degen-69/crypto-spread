# tasks/todo.md — Issue #95

- [x] T1 live state fields + three knobs + rollover/reset clearing (45f02cc)
- [x] T2 RED→GREEN: 8 live re-entry tests in tests/test_live_trader.py (c0f7264; tests at :1760-1926)
- [x] T3 GREEN: `_maybe_reenter_drift_skipped()` wired before `can_place_entry` (c0f7264; live_trader.py:3278)
- [x] T4 params payload + `update_config()` + `ConfigPayload` plumbing (750fd2e; test_osc_dash_integration.py:1204)
- [x] T5 RED: 6 backtest parity tests in tests/test_entry_timeout.py (:663-734) — landed in working tree
- [x] T6 GREEN: `adverse_skipped` split + backtest re-entry rule — landed (backtest/engine.py:141-168, :314-315, :378, :381-401)
- [x] T7 `python -m pytest -q` fully green, docstring gate passes — 334 passed / 18 files

Verified Sep 7, 2026: four targeted gates = 189 passed; full collection = 334.
CONSTRAINTS.md §5 and SPEC.md Status carry the per-behavior breakdown.
