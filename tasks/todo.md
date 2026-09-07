# tasks/todo.md — Issue #95

- [x] T1 live state fields + three knobs + rollover/reset clearing (45f02cc)
- [x] T2 RED→GREEN: 8 live re-entry tests in tests/test_live_trader.py (c0f7264; tests at :1760-1926)
- [x] T3 GREEN: `_maybe_reenter_drift_skipped()` wired before `can_place_entry` (c0f7264; live_trader.py:3278)
- [x] T4 params payload + `update_config()` + `ConfigPayload` plumbing (750fd2e; test_osc_dash_integration.py:1204)
- [x] T5 RED: 6 backtest parity tests in tests/test_entry_timeout.py (:663-734) — landed in working tree
- [x] T6 GREEN: `adverse_skipped` split + backtest re-entry rule — landed (backtest/engine.py:141-168, :314-315, :378, :381-401)
- [x] T7 `python -m pytest -q` fully green, docstring gate passes — 334 passed / 18 files

- [x] T8 merge origin/master (#89) and share its `min_requote_remaining_sec` knob (305bceb, 9542c3b)

Verified Sep 7, 2026 on a clean detached worktree at 9542c3b: `python -m pytest -q`
= 346 passed. After the #89 merge the re-entry time gate defaults to 300s, so only
15m windows re-enter until an operator lowers it; the tests set it explicitly.
CONSTRAINTS.md §5 and SPEC.md Status carry the per-behavior breakdown.
