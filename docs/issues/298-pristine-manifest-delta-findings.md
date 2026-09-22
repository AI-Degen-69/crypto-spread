# Issue #298 Findings: Rebuild of run/ticks/pristine after the bounds_violation Gate (#297)

**Date:** 2026-09-22
**Target:** `run/ticks/pristine/` (golden dataset output of `scripts/build_pristine_dataset.py`)
**Scope:** Operational rebuild + delta analysis only. **Zero code changes** — the extractor,
gate thresholds, and all source modules are byte-identical to master (post-#297).

---

## 1. Executive Summary

The pristine dataset was rebuilt in place with default thresholds (no gate-tuning flags) so
that the `bounds_violation` gate merged in #297 takes effect on the committed-research golden
dataset. Before the rebuild, the git-ignored manifest was snapshotted (hard-gate #5 of
`CONSTRAINTS.md`) so the before/after delta could be computed from real artifacts, not
estimates.

- **Windows passed: 5,042 → 4,910 (−132).**
- **Ticks written: 1,485,319 → 1,428,888 (−56,431).**
- **Every one of the 132 dropped windows carries `bounds_violation` in its new
  `failing_gates` — the drop invariant holds with 0 breaks.** No window was dropped for any
  other reason, and no previously-failing window became passing (0 newly added passed
  windows).
- The 09-18 evidence window from #297 (`0x9f6c…b3b`, `bnb-updown-15m-1789731900`) is now
  failing (`late_start` + `bounds_violation`) and absent from the output files.
- Self-certification: rebuild exit 0 with `output verify: PASS`; new manifest carries
  `output_verify.status == "PASS"` and the bounds-gate `policy_note` clause; the independent
  `python -m scripts.verify_tick_data run/ticks/pristine --json` cross-check also reports
  **PASS** (4,910 windows, 1,428,888 valid ticks, 0 corrupt rows).

### One honest caveat (2026-09-21)

`ticks_2026-09-21.jsonl` was **modified externally before the rebuild** (mtime 2026-09-22
15:09 — a rewrite/backfill pass; see §5), so its SHA-256 differs from the baseline-snapshot
hash. The other five day files are hash-identical. Consequence: drop attribution is
**exact on 5 of 6 days**; on 09-21 the 12 dropped windows still satisfy the invariant (all
carry `bounds_violation`), but they cannot be attributed *solely* to the new gate with
hash-level certainty. The dataset remains research-ready; this caveat is recorded rather
than smoothed over.

---

## 2. Methodology

- **Snapshot before overwrite:** `run/ticks/pristine/pristine_manifest.json` copied to
  `.pristine_baseline_manifest.json` (scratch, deleted after use) **before** the rebuild ran.
- **Rebuild command (exact, default thresholds):**
  ```
  python -m scripts.build_pristine_dataset run/ticks --out run/ticks/pristine
  ```
  Run as a background process, logging to `run/rebuild_298.log`; ended with
  `wrote 4910 pristine windows (1428888 ticks) …; output verify: PASS`.
- **Matching rule:** windows matched on identity tuple
  `(cid, series, slug, duration, start_ts)`. `dropped = passed(old) ∧ ¬passed(new)`.
  Any dropped window lacking `bounds_violation` in the new `failing_gates` would have been
  an invariant break → the analysis script reports `invariant_breaks_total = 0`.
- **Read-only proof:** SHA-256 per-source map compared between the two manifests
  (`totals.source_files`). 5/6 files identical; the 09-21 divergence is itemized in §5.
  Note: the hashes are recorded by the extractor at build start. A future rebuild should
  also re-hash every source file **after both read passes** and reject the build on any
  mismatch, so the bytes actually read are proven identical to the hashed snapshot
  (codified in `CONSTRAINTS.md` gate 2 for this issue).
- **Data sources of truth:** the two manifests only (anti-cheat rule). The 291 baseline doc
  (6,129 / 5,042 / 1,485,319) matched the on-disk baseline manifest exactly and was used as
  corroboration, never as a substitute.

---

## 3. Results

### Totals (before → after)

| Metric | Before (#291 build) | After (#297 gates) | Delta |
| :--- | ---: | ---: | ---: |
| Windows total | 6,129 | 5,791 | −338 |
| Windows passed | 5,042 | 4,910 | **−132** |
| Ticks written | 1,485,319 | 1,428,888 | −56,431 |
| `bounds_violation` windows (new manifest) | — | 158 | — |
| Invariant breaks | — | **0** | — |

### Per day (passed windows)

| Day | Before | After | Dropped | Source file identical? |
| :--- | ---: | ---: | ---: | :--- |
| 2026-09-13 | 425 | 396 | 29 | ✅ yes |
| 2026-09-14 | 1,504 | 1,470 | 34 | ✅ yes |
| 2026-09-15 | 1,438 | 1,385 | 53 | ✅ yes |
| 2026-09-18 | 10 | 10 | 0 | ✅ yes |
| 2026-09-20 | 248 | 244 | 4 | ✅ yes |
| 2026-09-21 | 1,417 | 1,405 | 12 | ⚠️ no (external rewrite, §5) |
| **Total** | **5,042** | **4,910** | **132** | |

`bounds_violation` windows by day in the new manifest: 09-13: 39, 09-14: 37, 09-15: 60,
09-18: 1, 09-20: 9, 09-21: 12 (total 158 — higher than the 132 drops because many
`bounds_violation` windows already failed other gates and were never in the pristine set).

### Interpretation

The −132 delta is exactly the expected footprint of the #297 bounds gate
(`mid ∈ [−0.01, 1.01]`, `touch_pair ∈ [0.50, 1.50]`, mirroring `verify_tick` sane bounds):
windows whose recorded `mid`/`touch_pair` tick values escaped sane bounds — previously
captured *into* the pristine set — are now gated out. Median-range and touch-pair research
conclusions (e.g. `touch_pair` median ≈ 1.01) are computed on in-bounds data going forward.

---

## 4. Gates unchanged — #295 owns dashboard visibility

No gate thresholds were touched in this issue. The rebuild ran with the extractor's default
gate set exactly as merged in #297. Whether (and how) `bounds_violation` counts surface in
the dashboard is **#295's** scope, not this issue's.

## 5. The 2026-09-21 source-file anomaly (NOTICED-BUT-NOT-TOUCHING)

- The baseline manifest hashed `ticks_2026-09-21.jsonl` as `8d1aacdf…61aeb7`; the new
  manifest hashes it `764a5e65…` (matches the on-disk file, mtime 2026-09-22 15:09).
- The current file contains **390,675 lines vs 385,837 parsed by the new manifest**, and the
  338 old-manifest windows absent from the new one are all **previously-failing** windows
  (gates they failed then: `sampling_gap` 320, `collector_error` 8, `early_cutoff` 10) spread
  across the whole day — consistent with a collector rewrite/backfill of that day file before
  this rebuild, not with the rebuild itself (the rebuild ran ~20:58 and only reads).
- Crucially, no passed window was affected by the rewrite: all 1,405 windows that passed
  before and exist now still pass; the 12 drops on that day are all `bounds_violation`.
- **Future-issue candidate:** a "no silent rewrites of captured day files" guard — e.g. the
  collector appending to an existing day file, or any tool that rewrites `run/ticks/*.jsonl`,
  should log loudly / refuse without an explicit flag. This issue records the anomaly but
  does not touch anything outside its scope.

## 5a. Certification addendum (post-review closure)

The source-immutability gap flagged during PR #300 review was closed by a **certified
re-run** of the rebuild against the current source generation (branch
`i298b/pristine-source-certification`):

- **Recovery attempt:** the original capture bytes (`8d1aacdf…`) do **not** survive
  anywhere — the only other checkout (a working copy at commit 8aa1f70) holds a
  byte-identical copy of the *current* generation (`764a5e65…`), and no backup directory or
  archived copy exists. The 338 previously-failing windows of the original capture are
  permanently unrecoverable; provenance is degraded, not restored. **However**, the rewrite
  happened *before* the #298 rebuild, so the **pre-rebuild source state equals the current
  file** — the certification below therefore covers exactly the bytes the #298 build
  consumed.
- **Certified re-run** (`run/cert_298/`): full SHA-256 of all 6 sources + all 6 pristine
  outputs snapshotted before the run; rebuild re-executed with default thresholds →
  `wrote 4910 pristine windows (1428888 ticks) …; output verify: PASS`;
  post-run re-hash: sources **byte-identical** to pre-run (the constraint-#2 post-pass
  requirement, now executed in practice), outputs **byte-identical** to the #298 build
  (determinism), and the manifest's `totals.source_files` map matches both the pre-run
  snapshot and the post-run on-disk state.
- **Result:** the current dataset's integrity is now proven end-to-end on the exact bytes
  it was built from. What remains permanently unverifiable is only the *pre-rewrite*
  generation of `ticks_2026-09-21.jsonl` — a provenance footnote, not a dataset defect:
  every passed window in the certified build was also passing in the baseline, and all 132
  delta drops are attributable to the bounds gate on the 5 hash-identical days plus
  invariant-satisfying on 09-21.
- **Auditability:** the full certified pre/post SHA-256 maps (sources and outputs), the
  exact file lists, and the binary hashing procedure are embedded in the committed raw JSON
  (`docs/measurements/issue-298-pristine-manifest-delta.json` → `certification`), so the
  proof survives even though `run/cert_298/` itself is git-ignored. Anyone can re-verify
  by re-hashing `run/ticks/*.jsonl` and `run/ticks/pristine/ticks_*.jsonl` (raw bytes,
  streamed 1 MiB chunks) and comparing to the embedded maps.

## 6. Artifacts Produced

- `docs/measurements/issue-298-pristine-manifest-delta.json` — raw delta payload (this file's
  numbers tie 1:1 to it).
- `run/rebuild_298.log` — rebuild evidence (git-ignored, not committed).
- `run/verify_298.json` — independent `verify_tick_data` result (git-ignored, not committed).
- No code changes; `python -m pytest tests/test_build_pristine_dataset.py -q` remains 37/37
  (extractor untouched).
