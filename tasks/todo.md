# Tasks Checklist — Issue #214: Engine Parity Test Harness

- [x] **T0 — Branch + spec lock**: `feat/parity-harness-214` checked out, `SPEC.md`, `CONSTRAINTS.md`, `tasks/plan.md`, `tasks/todo.md` created.
- [x] **T1 — [Test/Harness] Snap-to-poll adapter**: Implement `snaps_to_polls` in `tests/test_engine_parity.py`.
- [x] **T2 — [Test/Harness] Headless live driver**: Implement `live_outcome` in `tests/test_engine_parity.py`.
- [x] **T3 — [Test/Harness] Backtest extractor & diff assertion**: Implement `backtest_outcome` and `assert_parity`.
- [x] **T4 — [Test/Scenarios] Seed scenarios**: Add tests for balanced open, mid anchor, pair-cost cap, unpriceable leg, stop loss, fresh start, and parameter matrix.
- [x] **T5 — [Docs] Documentation updates**: Update `AGENTS.md` with parity harness gate documentation.
- [x] **T6 — [Review/Ship] Verification**: Run targeted test suites and prepare for Station IV.
