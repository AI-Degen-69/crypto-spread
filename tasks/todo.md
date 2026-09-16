# TODO — Issue #229: `dead_zone` governs the end of the window

Branch: `feat/dead-zone-229`. Plan: `tasks/plan.md`. Spec: `SPEC.md`.

- [x] **T0** Station II — branch + spec lock (no code)
- [x] **T1** `[Backend/Logic]` Shared dead-zone calculation in `strategy/book_math.py`
- [x] **T2** `[Backend/Logic]` Backtest: delete timeout/start knobs, add Dead Zone & `naked_leg_at_expiry`
- [ ] **T3** `[Backend/Logic]` Live: delete timeout/start knobs, add Dead Zone & `naked_leg_at_expiry`
- [ ] **T4** `[Test/Parity]` `tests/test_dead_zone_parity.py` — entry blocked, orders cancelled, close vs hold
- [ ] **T5** `[API/Dashboard]` Update API schemas, parameter registry, and Cockpit/Backtest UI controls
- [ ] **T6** `[CLI/Research]` Retarget CLI, sweeps, and existing test suites
- [ ] **T7** `[Review/Ship]` Review verification, docs update, and PR presentation

