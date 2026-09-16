# TODO — Issue #227: `max_pair_cost` caps the chase only

Branch: `fix/max-pair-cost-chase-only-227`. Plan: `tasks/plan.md`.

- [x] **T1** `[Backend/Logic]` `book_math.chase_cap()` + unit tests
- [x] **T2** `[Backend/Logic]` Backtest: rename to `max_pair_cost`, default 0.99,
      `__post_init__` range check, one registry range, delete the entry-side block
- [x] **T3** `[Backend/Logic]` Live: default 0.99, four inline floorings → `chase_cap`
- [x] **T4** `[Test/Parity]` `tests/test_chase_cap_parity.py` — identical ceiling, both engines
- [x] **T5** `[API/Dashboard]` Delete the ON/OFF toggle, unify the range, rename the control
- [x] **T6** `[Backend/CLI]` `backtest.py`, `sweep_backtest.py` grid, `replay_shadow_check.py` legs
- [x] **T7** `[Research/Logic]` `ev_lab.py` + `sim2.py`: delete the two-ask entry test
- [x] **T8** `[Test]` Remove the four entry-gate tests; rename everywhere else; retarget
      the registry exemplar to `queue_gate` — folded into T2/T5/T6, one pass per file
- [x] **T9** `[Docs]` `AGENTS.md`, optimization results, rule 4's stale line reference
