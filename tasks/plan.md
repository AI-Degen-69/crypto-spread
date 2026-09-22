# Plan — Issue #281: Capture and certify the golden tick dataset (from the pristine set)

Branch: `i281/golden-from-pristine` | Issue: #281
Stack: Python 3, pytest · Size: **Standard** (one new script + charter note + tests; no
production-code change) · Task type: **Code** (Backend/Logic + Docs)

## Route decision (measured, not assumed)

#281 was chartered as "run the collector for days, verify each day, assemble the golden set".
Measurement on the merged pristine pipeline (PRs #291/#299/#301) shows that route is
superseded: every one of the 6 pristine day files already passes the charter's §1.1 per-day
gates, and the set as a whole clears every §1.2 target with headroom:

- 4,910 windows (≥500), weakest pair 159 (≥50), 6 time blocks (≥5),
  1,428,888 valid ticks (≥50,000), sampling gap rate 0.0 (≤0.05), 10/10 series.
- `verify_ticks_dir(run/ticks/pristine)` → PASS / RESEARCH_READY / COMPLETE CAPTURE.

The charter (§1, "assembled from verified days only") is therefore satisfied by promoting
the pristine days — no collector run, no waiting on wall-clock. Provenance stays complete:
each golden day records its pristine source day and sha256 chain back to the raw capture.

## Tasks (atomic slices, dependency-ordered)

### T1 — `scripts/build_golden_dataset.py` `[Backend/Logic]` (M)
- Filters `run/ticks/pristine/ticks_*.jsonl` through the §1.1 gates using
  `scripts.verify_tick_data.verify_tick_file` (PASS + COMPLETE CAPTURE, zero corrupt, zero
  collector errors, zero time reversals); failing days are excluded and recorded with a
  reason — never silently mixed in (charter §1.1).
- Copies passing days to `run/ticks/golden/` (deterministic, atomic writes via
  `strategy.windows.write_json_atomic` semantics; sources untouched).
- Writes `run/ticks/golden/golden_manifest.json` (charter §3.1): per day —
  `day`, `source` (`pristine/<basename>`), `sha256`, `verify_verdict`
  (status / capture label / readiness level), `windows_count`; set-level —
  totals, `policy_version` (`READINESS_POLICY_VERSION`), certification UTC date, tool version.
- Builds `backtest/index.build_index` sidecars for every golden day so first replay is fast.
- Re-run over unchanged inputs is byte-identical (no wall-clock fields except the explicit
  `certified_utc` date, keyed by content — same content ⇒ same manifest).
- Verification: T3 tests.
- Depends on: —
- [x] Done

### T2 — Charter + runbook note `[Docs]` (S)
- File: `docs/golden-tick-dataset.md`.
- §3 note: the golden set may be assembled from pristine-derived days (#290 pipeline) when
  every §1.1 gate passes per day; collector-route remains the alternative when pristine
  sources are insufficient. Records the measured decision for #281.
- Depends on: T1
- [x] Done

### T3 — Tests `[Tests]` (M)
- File: `tests/test_build_golden_dataset.py`.
- Fixture: tiny fake pristine dir (2 passing days + 1 failing day).
- Asserts: only passing days land in `golden/`; manifest carries source + sha256 +
  verdict + policy version; excluded day recorded with reason; `.idx` sidecars exist and
  `is_fresh`; re-run idempotent (byte-identical manifest); a day promoted to golden passes
  `verify_tick_file` unchanged.
- Verification: `python -m pytest tests/test_build_golden_dataset.py tests/test_verify_tick_data.py -q`.
- Depends on: T1
- [x] Done

**Checkpoint:** after T1 (script runs end-to-end on the real pristine dir, manifest inspectable).

## Explicitly out of scope (per issue + route decision)
Collector/verify-engine changes; new capture runs; bounds-gate re-litigation (#297 closed);
dashboard changes (#292 card already renders golden state); modifying pristine sources.

## Improvement proposal (adopt-by-default, evidence-based)
#281 leaves "concat vs manifest" to the charter; the charter (§1) keeps per-day files with a
manifest because "a concat would bury a bad day inside a good file". Evidence above — adopted:
golden stays per-day files + `golden_manifest.json`, mirroring the pristine layout so the
dashboard card (#292) and verify caches (#295 relative-name sidecars) work with zero changes.

## Rejections recorded
None — no proposal was rejected this session.
