# Plan — Issue #290: pristine-window dataset extractor for the existing capture days

Stack: Python 3, pytest · Size: **Standard** (new CLI script + tests; touches no existing module)
Type: **Code** (data tooling) · Branch: `feat/pristine-window-dataset`

## Resolved inputs (planning record)
- Issue supplies exact file:line map; no `needs-answers` label, no open questions → nothing to
  resolve from the operator.
- `code-explorer` persona skipped: not Large/unfamiliar code — the issue names every reuse point.
- `type-design-analyzer` persona applied to the frozen contract (findings in "Interface
  contracts" below).
- Sub-issue mapping skipped: skill reference `references/issue-tracker.md` does not exist on
  disk; 4 linear tasks stay tracked here + `tasks/todo.md` only.
- `err` is a top-level snap field (`scripts/collect_ticks.py:870`: `ub_err or db_err or tape_err`),
  counted by `verify_tick_data.py:511` → the collector-error gate reads truthy `tick["err"]`.
- Day files on disk: 2026-09-13/14/15/18/20/21 (six `.jsonl` days, as the issue states).

## Spec
See `SPEC.md` (Standard size). One-line spec: stream six day files, verdict every captured
window against per-window continuity gates, write whole surviving windows keyed to their
**start** day + a byte-stable `pristine_manifest.json`; sources are never modified.

## Interface contracts (frozen before logic)
- `PristineGateParams` — frozen dataclass: `max_gap_sec=6.0`, `max_start_delay_sec=5.0`
  (drives both late-start and early-cutoff, mirroring `verify_window_continuity` semantics),
  `max_snap_interval_sec=3.0` (density floor: `min_snaps = ceil(duration / max_snap_interval_sec)`).
  Frozen so thresholds cannot drift between windows → determinism invariant enforced.
- `evaluate_window_gates(ticks, params) -> dict` — single constructor path computing
  `passed` and `failing_gates` together, so they cannot disagree. Gate math mirrors
  `verify_window_continuity`: `start_delay = max(0, first_ts − start_ts)`,
  `end_cutoff = max(0, end_ts − last_ts)`, gaps `> max_gap_sec`, reversals `delta < 0`,
  error ticks `truthy tick["err"]`, density `tick_count ≥ ceil(duration / max_snap_interval_sec)`
  (issue's literal "≥1 snap per 3s of window duration").
- `WindowVerdict` / manifest records — plain dicts at the JSON boundary (every repo artifact —
  window records, verify reports, golden manifest — is a plain dict; a dataclass adds a
  conversion layer with no invariant gain at the file boundary). Invariants enforced by
  construction + tests, recorded as the accepted escape hatch.
- Manifest ordering invariant: source hashes computed first, tick files written next,
  `pristine_manifest.json` written **last**, atomically (`write_lines_atomic`/`write_json_atomic`
  from `strategy.windows`) — a manifest never references a missing output file.
- Manifest is byte-stable: **no wall-clock fields anywhere** (a build timestamp would break the
  issue's byte-identical re-run requirement). Provenance = source sha256s + policy version.
- CLI: `python -m scripts.build_pristine_dataset <ticks_dir> --out <dir>`; exit 0 success,
  2 config error (`out == ticks_dir` resolved → refused: outputs share the `ticks_<day>.jsonl`
  naming and would clobber sources; also no input day files found).
- Two-pass streaming (memory): pass 1 gates per merged cid (scalar aggregates only),
  pass 2 re-streams and copies raw lines of surviving cids to per-start-day files.
  Memory stays O(windows), never O(ticks) — ~1.87M tick dicts would be ~2GB held at once.
- Reuse, no copies: `scripts.rebuild_windows.iter_ticks` (jsonl/gz streaming),
  `scripts.ship_to_drive.sha256_of` + its `DAY_RE` day-key pattern,
  `scripts.verify_tick_data.verify_ticks_dir` (embedded output re-verify),
  `strategy.windows.write_lines_atomic` / `write_json_atomic`,
  window record fields per `strategy.windows.finalize_window` schema.

## Dependency graph
TASK-1 → TASK-2 → TASK-3 → TASK-4 (linear: gate engine unblocks writer; real-data run needs
both; closeout needs the run). Risk-first: cross-file window merge + gate math land before any
output writing.

## Tasks

| ID | Size | Tag | Target files | What is built | Helper skill | Depends on | Verification |
|---|---|---|---|---|---|---|---|
| TASK-1 | M | [Backend/Logic] | `scripts/build_pristine_dataset.py`, `tests/test_build_pristine_dataset.py` | `PristineGateParams`, `evaluate_window_gates`, cross-file per-cid aggregation (midnight-spanning windows merged into one stream keyed to start day) | test-driven-development | — | `python -m pytest tests/test_build_pristine_dataset.py -k gate -q` |
| TASK-2 | M | [Backend/Logic] | same | Two-pass writer: whole surviving windows grouped by start day → `run/ticks/pristine/ticks_<day>.jsonl`; `pristine_manifest.json` (per-window verdict + sources + sha256, per-pair counts, gates block, embedded output re-verify); CLI + guards; byte-stable output | test-driven-development, incremental-implementation | TASK-1 | `python -m pytest tests/test_build_pristine_dataset.py -q` (synthetic fixture: byte-identical re-run, source hashes unchanged, re-verify clean, overwrite guard, exit codes) |
| TASK-3 | S | [Backend/Logic] | — (run/ is gitignored) | Real run over the six days; re-verify output dir; compare per-pair pristine counts to the issue's ~799–802 (5m) / ~208–209 (15m); source hash invariance before/after | verification-before-completion | TASK-2 | `python -m scripts.build_pristine_dataset run/ticks --out run/ticks/pristine` + `python -m scripts.verify_tick_data run/ticks/pristine` |
| TASK-4 | XS | [Backend/Logic] | `tasks/todo.md` | Closeout: targeted suites green (`test_build_pristine_dataset`, `test_verify_tick_data`, `test_ship_to_drive` — sha256_of reuse), todos ticked | — | TASK-3 | targeted pytest runs |

Checkpoints: after TASK-2 (gates + writer proven on synthetic data) and after TASK-3 (real six-day
extraction certified) — one-line progress reports in Mode A, no approval pauses.

## Improvement proposal (adopt by default)
Embed a compact `verify_ticks_dir(out_dir)` verdict of the freshly written output inside
`pristine_manifest.json` (`output_verify` key), mirroring the golden-manifest pattern of carrying
each day's verify verdict (charter §3.1) — turns acceptance criterion #2 into a machine-checked,
self-certifying artifact. Evidence: AC #2 ("Re-verifying the output with `scripts/verify_tick_data`
reports zero…") + `docs/golden-tick-dataset.md` §3.1. Adopted into TASK-2. Rejected proposals: none.

## Explicit out of scope (from the issue)
No modification of any existing day file; no collector changes (PR #289 territory); no promotion
into `run/ticks/golden/` and no golden-charter amendment; no backtest engine changes.
