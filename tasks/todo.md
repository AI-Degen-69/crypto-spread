# TODO — Issue #298

- [x] TASK-1: snapshot baseline manifest + record counts (6,129 / 5,042 / 1,485,319)
- [x] TASK-2: background rebuild → exit 0 + `output verify: PASS` in run/rebuild_298.log
- [ ] TASK-3: six verification checks (policy clause, PASS, verify_tick_data PASS,
      bounds_violation ⇒ passed:false, 09-18 window absent, source hashes unchanged;
      09-21 external-rewrite caveat documented in findings §5)
      **REOPENED per CodeRabbit review: the source-hash gate did NOT hold** —
      5/6 day files matched the pre-rebuild SHA-256 map; `ticks_2026-09-21.jsonl` was
      externally rewritten before the rebuild (338 previously-failing windows vanished;
      every passed window still passes; all 12 drops carry bounds_violation).
      Restoring the pre-rebuild source and re-running is tracked on #298.
- [x] TASK-4: delta analysis + docs/issues/298 findings doc + docs/measurements JSON
      (132 drops / 0 invariant breaks / 158 bounds_violation windows)
- [x] TASK-5: commit docs only (e6d21eb), comment delta on #298 (5782239094),
      targeted gate stays 37/37 (regression evidence only)

---

# Archived — Issue #297 (shipped)

- [x] TASK-1: bounds gate in build_pristine_dataset.py
- [x] TASK-2: TestBoundsViolationGate unit + e2e tests
- [x] TASK-3: full targeted gate + diff-stat closeout
