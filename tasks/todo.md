# tasks/todo.md — Issue #110 (prior #95 plan completed; preserved in git history)

- [x] T1 RED: isolated-axis tests in tests/test_sweep_backtest.py (0.025 entry, --only filter, bad value errors)
- [x] T2 GREEN: reversals += 0.025 + --only flag in scripts/sweep_backtest.py; sweep tests green (17387f0)
- [x] T3 Run isolated sweep on run/ticks/ticks_2026-09-08.jsonl → run/sweeps/exit_reversal_110.json (48,766 snaps, 515 windows, 5 runs)
- [x] T4 Results table + unify recommendation (docs §6 + gh issue comment 110)
- [x] T5 python -m pytest -q fully green (386 passed); CONSTRAINTS.md §6 verification dated
