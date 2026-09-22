# SPEC — Issue #298: Rebuild run/ticks/pristine after the bounds_violation gate lands

Per-issue specification. Source: issue #298 body + CodeRabbit coding plan
(comment 5780981530), code-verified against `scripts/build_pristine_dataset.py`.

## Context

PR #299 (issue #297) added the always-on `bounds_violation` gate to the pristine extractor,
but the derived dataset on disk (`run/ticks/pristine/`, built by PR #291) predates the gate.
Known evidence: `run/ticks/ticks_2026-09-18.jsonl` holds 1 bounds-violating tick in 1 window
currently inside the pristine dataset. This issue re-runs the extractor and produces a durable
delta record for #295 dashboard consumers.

## Verified Code Facts (checked, not assumed)

- File discovery (`build_pristine_dataset.py:145`) globs `ticks_dir.glob("ticks_*.jsonl*")`
  top-level only → the `pristine/` subdir is never scanned. In-place `--out run/ticks/pristine`
  is safe.
- Rebuild hygiene (`:371-374`): old manifest unlinked, stale day files deleted before writing.
  Clean-rebuild semantics already exist — no helper script needed.
- Self-certification (`:431-445`): `verify_ticks_dir(out_dir)` runs on fresh output; a non-PASS
  aborts with `RuntimeError` and writes no manifest.
- Baseline manifest verified on disk: `totals = {windows_total: 6129, windows_passed: 5042,
  ticks_written: 1485319}`, six day files with `source_files` SHA-256 map; window identity
  fields `cid, series, slug, duration, start_ts, start_day` all present. Old window rows do
  NOT carry `bounds_violations` (pre-gate build) — delta matching uses identity fields only.
- Template `docs/issues/221-gil-contention-findings.md` and dir `docs/measurements/` exist.

## Acceptance Criteria (from the issue)

1. Rebuild completes; `pristine_manifest.json` policy note includes the bounds-gate clause.
2. Windows containing bounds-violation ticks are gone from the output and carry
   `bounds_violation` in `failing_gates` in the manifest (`passed: false`).
3. `output_verify` status is PASS.
4. Delta vs the pre-rebuild manifest (windows dropped per day) is recorded — committed
   findings doc + issue comment.

## Method

1. Snapshot baseline manifest to `.pristine_baseline_manifest.json` (repo root, scratch,
   deleted after TASK-4; never committed).
2. Run `python -m scripts.build_pristine_dataset run/ticks --out run/ticks/pristine`
   (no flags; long-running over ~5.2 GB → background process with log file).
3. Verify: exit 0, `output verify: PASS`, policy clause, independent
   `verify_tick_data run/ticks/pristine` PASS, every `bounds_violation` gate entry has
   `passed: false`, the 09-18 evidence window is failing and absent from output, and
   `totals.source_files` hashes equal the baseline (read-only proof).
4. Delta: match old/new windows on `(cid, series, slug, duration, start_ts)`; dropped =
   passed before, not passed now. Invariant to assert: every dropped window carries
   `bounds_violation` in its new `failing_gates` (gates otherwise unchanged, same inputs).
   Group drops by `start_day`; record `windows_passed` / `ticks_written` deltas and the
   `bounds_violation` window count.
5. Write `docs/issues/298-pristine-manifest-delta-findings.md` (Executive Summary /
   Methodology with exact CLI + matching rule / Results table before-after-delta per day /
   note that `run/` is git-ignored so this doc is the durable record / gates unchanged,
   dashboard owned by #295) and `docs/measurements/issue-298-pristine-manifest-delta.json`.
6. Comment the delta summary on issue #298.

## Out of Scope

Gate/threshold changes; dashboard work (#295); any `strategy/`, `server/`, `backtest/` edits;
committing `run/` artifacts.
