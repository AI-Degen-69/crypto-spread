# TODO — Issue #298

- [x] TASK-1: snapshot baseline manifest + record counts (6,129 / 5,042 / 1,485,319)
- [x] TASK-2: background rebuild → exit 0 + `output verify: PASS` in run/rebuild_298.log
- [x] TASK-3: six verification checks (policy clause, PASS, verify_tick_data PASS,
      bounds_violation ⇒ passed:false, 09-18 window absent, source hashes unchanged;
      09-21 external-rewrite caveat documented in findings §5)
      Was **REOPENED per CodeRabbit review** (source-hash gate did not hold: 5/6 day
      files matched; `ticks_2026-09-21.jsonl` externally rewritten pre-rebuild).
      **Closed by certified re-run** (`i298b/pristine-source-certification`, findings
      §5a): original capture bytes unrecoverable (provenance permanently degraded), but
      pre-rebuild source state == current file, and the re-run proved end-to-end —
      sources byte-identical pre→post build, outputs byte-identical to the #298 build,
      manifest map matches both. Original-generation recovery N/A; dataset certified.
- [x] TASK-4: delta analysis + docs/issues/298 findings doc + docs/measurements JSON
      (132 drops / 0 invariant breaks / 158 bounds_violation windows)
- [x] TASK-5: commit docs only (e6d21eb), comment delta on #298 (5782239094),
      targeted gate stays 37/37 (regression evidence only)

---

# Archived — Issue #297 (shipped)

- [x] TASK-1: bounds gate in build_pristine_dataset.py
- [x] TASK-2: TestBoundsViolationGate unit + e2e tests
- [x] TASK-3: full targeted gate + diff-stat closeout
