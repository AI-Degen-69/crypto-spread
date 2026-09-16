# TODO — Issue #228: `quote_range` replaces `entry_band` and `adverse_open`

Branch: `fix/quote-range-228`. Plan: `tasks/plan.md`. Spec: `SPEC.md`.

- [x] **T0** Station II — branch + spec lock (no code)
- [ ] **T1** `[Backend/Logic]` Backtest: delete both gates + re-entry, add per-tick `quote_range`
- [ ] **T2** `[Backend/Logic]` Live: delete both gates + re-entry, add per-tick `quote_range`
- [ ] **T3** `[Test/Parity]` `tests/test_quote_range_parity.py` — entry, exit, re-entry to the range, both engines
- [ ] **T4** `[API/Dashboard]` Band inputs → lo/hi range inputs, both tabs + query keys
- [ ] **T5** `[Backend/CLI]` Scripts + sims rename; `patient_band_maker` preset + its test file deleted
- [ ] **T6** `[Test]` Remove band/adverse/re-entry tests with the behaviour (folded per-file into T1/T2/T4/T5)
- [ ] **T7** `[Docs]` Mark the surfaces that still describe the gates
