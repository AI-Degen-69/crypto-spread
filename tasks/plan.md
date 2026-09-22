# Plan — Issue #298: Rebuild run/ticks/pristine after the bounds_violation gate lands

Branch: `i298/rebuild-pristine-after-bounds-gate` | Issue: #298
Stack: Python 3, pytest · Size: **Standard** (operational long-running rebuild + analysis +
2 committed docs; no production code change, but cross-artifact evidence work) ·
Type: **Research + Docs** (quantitative delta analysis + durable findings record)

## Resolved inputs (planning record)

- Issue supplies 4 acceptance criteria and the exact rebuild command — adopted as-is.
- CodeRabbit plan (comment 5780981530, fetched this session): reviewed against the actual
  code and adopted as the task skeleton. Its key insight — snapshot the git-ignored manifest
  BEFORE the rebuild overwrites it — is CONSTRAINTS hard-gate #5. Its findings-doc decision
  (commit `docs/issues/298-...md` since `run/` can't be committed) matches repo convention
  (`221-gil-contention-findings.md` template exists).
- This plan adds what CodeRabbit lacked: (a) the SHA-256 read-only proof via
  `totals.source_files` old-vs-new; (b) the drop-invariant assertion (every dropped window
  carries `bounds_violation`); (c) baseline numbers verified on disk at planning time
  (6,129 / 5,042 / 1,485,319 — match the 291 baseline doc, so no fallback needed);
  (d) background-process execution for the ~5.2 GB rebuild so the session doesn't block.
- Sub-issue mapping skipped: 5 linear tasks stay tracked here + `tasks/todo.md`.

## Evidence gathered at planning time

- On-disk manifest inspected: keys `policy_note, gates, totals, per_pair_pristine_counts,
  windows, output_verify`; 6,129 window rows with identity fields
  `cid, series, slug, duration, start_ts, start_day` — sufficient for delta matching.
- `build_pristine_dataset.py:145` discovers only top-level `ticks_*.jsonl*` → in-place
  `--out run/ticks/pristine` cannot self-scan; `:371-374` already deletes stale output.
- Sources: 6 day files, ~5.2 GB total; existing pristine ~4.9 GB. Rebuild is streaming;
  expect tens of minutes → run as background process writing `run/rebuild_298.log`.

## Spec

See `SPEC.md` — verified code facts, acceptance criteria, and the full method (snapshot →
rebuild → verify → delta → findings doc → issue comment).

## Tasks

- **TASK-1** [Ops/Baseline] · Size S · scratch: `.pristine_baseline_manifest.json`
  Copy `run/ticks/pristine/pristine_manifest.json` to the scratch path. Record top-line
  counts (`totals.windows_total / windows_passed / ticks_written`) and per-day passed counts
  (group windows by `start_day`, passed only). Assert counts equal the 291 baseline doc
  (6,129 / 5,042 / 1,485,319).
  · Depends on: — · Verify: printed counts match baseline doc; snapshot file exists.

- **TASK-2** [Ops/Rebuild] · Size M · runs `scripts/build_pristine_dataset.py`
  Launch `python -m scripts.build_pristine_dataset run/ticks --out run/ticks/pristine` as a
  background process logging to `run/rebuild_298.log` (no gate flags). Wait for exit code 0
  and the `output verify: PASS` success line. On `RuntimeError` or exit≠0 → stop, report.
  · Depends on: TASK-1 (snapshot must exist first) · Verify: exit 0 + PASS line in log.

- **TASK-3** [Verification] · Size S · read-only manifest checks
  On the new manifest: (a) `policy_note` contains the bounds-gate clause; (b)
  `output_verify.status == PASS`; (c) independent `python -m scripts.verify_tick_data
  run/ticks/pristine --json` → PASS; (d) every window with `bounds_violation` in
  `failing_gates` has `passed: false`; (e) the 09-18 evidence window is failing and its cid
  is absent from output files; (f) `totals.source_files` == baseline map (read-only proof).
  · Depends on: TASK-2 · Verify: all six checks green (scripted, output recorded).
  **Acceptance record:** five of six checks green. The source-hash check did NOT hold:
  `ticks_2026-09-21.jsonl` was externally rewritten before the rebuild, so the new manifest's
  hash for it differs from the baseline map (5/6 matched). Every passed window still passes
  and all 12 drops on that day carry `bounds_violation`, but the per-constraint proof of
  source immutability is incomplete for 09-21 — recorded as a deviation, not silently
  passed. Restoration + re-run tracked on #298.

- **TASK-4** [Research/Delta] · Size M · `docs/issues/298-pristine-manifest-delta-findings.md`,
  `docs/measurements/issue-298-pristine-manifest-delta.json`
  One-off read-only Python: match old/new windows on `(cid, series, slug, duration,
  start_ts)`; dropped = passed-before ∧ ¬passed-now. Assert every dropped window carries
  `bounds_violation` in new `failing_gates`. Group drops by `start_day`; record
  `windows_passed`/`ticks_written` deltas + total `bounds_violation` window count. Write the
  findings doc (221 template: Executive Summary / Methodology w/ exact CLI + matching rule /
  Results table before-after-delta per day / git-ignored `run/` caveat / gates unchanged,
  #295 owns dashboard) and the raw JSON measurement. Delete the scratch snapshot.
  · Depends on: TASK-3 · Verify: doc + JSON exist; numbers tie to the two manifests.

- **TASK-5** [Closeout] · Size XS · git + issue
  Commit the two docs files only (`git diff --stat` shows nothing else); comment the delta
  summary on #298; tick todos; confirm `python -m pytest tests/test_build_pristine_dataset.py
  -q` still 37/37 (extractor untouched).
  · Depends on: TASK-4 · Verify: clean diff-stat + issue comment posted.

Checkpoints: after TASK-2 (rebuild PASS), after TASK-4 (delta recorded).

## Improvement proposals (recorded)

- **Adopted (evidence):** SHA-256 map comparison (`totals.source_files` old-vs-new) as the
  read-only proof — the manifest already carries per-source hashes, so the check is free and
  stronger than "we didn't touch the files".
- **Adopted (execution):** background rebuild with log file — 5.2 GB streaming is too slow
  for a blocking call; log doubles as evidence for the findings doc.

---

# Archived — Issue #297 (shipped as PR #299, merged 41a6997)

TASK-1/2/3 all completed `[x]`: bounds_violation gate (helper + seed + judge + manifest +
policy note), 10 new tests, full targeted gate 37/37, 2 clean commits (313a69a + c0fa46a).
