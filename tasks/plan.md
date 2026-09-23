# tasks/plan.md — Issue #302: forbid silent rewrites of capture files

Branch: `i302/no-silent-rewrites` · Size: Standard · Type: Code + Docs

## Why
During #298's pristine rebuild, `ticks_2026-09-21.jsonl` was silently rewritten
by a collector re-run — the original capture generation is gone forever. Raw day
files are the only non-reproducible artifact in the repo. This issue makes a
silent rewrite impossible: refuse without an explicit flag, back up on rewrite,
log loudly, and cross-check hashes in the verifier.

Route note: CodeRabbit's plan (issue comments) is adopted — 4 phases,
one shared `scripts/tick_safety.py` module, hard refuse scoped to
truncating writes only (append-resume preserved), watchdog restarts explicit.

## Tasks (risk-first)

- [x] **T1 — `scripts/tick_safety.py` (shared safety module).** Day-file path
  convention + `DAY_RE`; reuse `sha256_of` from `scripts/ship_to_drive.py`;
  `guard_day_write(path, mode, allow_rewrite)` — refuse-without-flag raises a
  clear error on truncating rewrite of an existing file, with-flag backup moves
  the old generation to `run/ticks/backup/<name>.<old-sha8>` and records a
  rewrite event (day key, ts, old+new sha, backup path) in
  `run/ticks/rewrite_events.jsonl`; `loud_log()` writes one stderr line
  (path, mode, size, hashes) per create/append-first/rewrite; hash-store
  read/update helpers for `run/ticks/verify_hashes.json`.
  · Validate: new `tests/test_tick_safety.py` (refuse, backup+event, loud line).

- [x] **T2 — Collector integration.** `--allow-rewrite` flag on
  `scripts/collect_ticks.py` (style of `--no-ws`/`--gzip`); `write_snap` calls
  the guard before opening; first-write-per-run detection (create vs append);
  append path stays `"ab"`; loud line on create/first-append/rewrite.
  · Validate: `--help` smoke test + `write_snap` refusal/backup unit tests.

- [x] **T3 — Watchdog explicit restarts.** `collector_cmd()` appends
  `--allow-rewrite`; single-writer PID enforcement and `--once`/`--out`
  refusal untouched.
  · Validate: existing watchdog suite stays green + cmd-shape assertion.

- [x] **T4 — Verifier cross-check.** Peer check in `verify_tick_data.py`:
  current sha vs `verify_hashes.json`; unexplained change (no matching
  rewrite event old→new) → loud `sample_issues` entry + status raised to
  WARN (FAIL for completed past day); store updated after the check; result
  surfaced in report dict + `format_report_text()` + `--json`; exit-code
  contract preserved.
  · Validate: hash-changed-without-event flags; with-event does not flag.

- [x] **T5 — Docs.** `--allow-rewrite`, backup dir, rewrite-event log, loud
  line documented in `docs/collector-hosting-runbook.md`; cross-reference in
  `docs/golden-tick-dataset.md`; pointer added in
  `docs/issues/298-pristine-manifest-delta-findings.md` §5.
  · Validate: docs consistency, no code changes.

## How we verify
- Targeted suites only (`test_tick_safety`, `test_collect_ticks_smoke`,
  `test_collect_ticks_prewarm_align`, `test_verify_tick_data`,
  `test_collector_watchdog`) — full sweep stays CI-only.
- No capture-format/window-gate/pristine-extractor changes.
- Anti-cheat: none of the existing tests weakened.

## Out of scope
- Dashboard display changes (#295 territory), retroactive recovery of the
  lost 09-21 generation (impossible, documented), any gate/threshold change.
