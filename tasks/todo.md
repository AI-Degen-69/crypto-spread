# TODO — Issue #298

- [ ] TASK-1: snapshot baseline manifest + record counts (6,129 / 5,042 / 1,485,319)
- [ ] TASK-2: background rebuild → exit 0 + `output verify: PASS` in run/rebuild_298.log
- [ ] TASK-3: six verification checks (policy clause, PASS, verify_tick_data PASS,
      bounds_violation ⇒ passed:false, 09-18 window absent, source hashes unchanged)
- [ ] TASK-4: delta analysis + docs/issues/298 findings doc + docs/measurements JSON
- [ ] TASK-5: commit docs only, comment delta on #298, targeted gate stays 37/37

---

# Archived — Issue #297 (shipped)

- [x] TASK-1: bounds gate in build_pristine_dataset.py
- [x] TASK-2: TestBoundsViolationGate unit + e2e tests
- [x] TASK-3: full targeted gate + diff-stat closeout
