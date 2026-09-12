# CONSTRAINTS.md — Issue #132: collector→overview bridge + auto-rebuild

## Quality Gates & Hard Thresholds

### 1. Test Suite Integrity
- **Pass Rate**: 100% — `python -m pytest -q` fully green before and after.
- **New behavior needs tests**: shared module (classify/finalize/summary/
  atomic-write), collector closure path (`tmp_path`, no network), rebuild
  endpoint (mocked subprocess + origin rejection + HTML presence +
  provenance fields), rebuild accuracy (`.jsonl` + `.jsonl.gz` fixtures).
- **Anti-Cheat**: no touching existing fixtures/expectations; no skipped
  tests, no weakened assertions, no linter suppressions, no
  `@ts-ignore`-style silencing. Classification math byte-identical
  (base 0.50, threshold 0.02).

### 2. Behavior & Scope Boundaries
- **Tick path untouched**: snap schema and `write_snap` behavior identical;
  collector change is append-only (`mids`/`touch_pairs` accumulation).
- **Best-effort I/O**: every new collector file op wrapped in
  `try/except` → recorded in existing `errs` list; window-file failure
  must not abort the poll or lose tick data; summary refresh on closure
  only (never per-tick).
- **Legacy freeze**: `scripts/measure_5m_oscillation.py` NOT modified.
- **No new dependencies** (stdlib + existing stack only). No background
  watcher in the dashboard. Every new function gets a docstring
  (`test_docstrings.py` gate).
- **Security**: `/api/rebuild` guarded by `_verify_safe_origin`, same as
  `poll-once`; subprocess `cwd=ROOT`, `timeout=60`, output truncated.

### 3. Perf & Dependencies
- **Perf**: per-poll overhead O(1) appends; summary recompute only on
  closure (file read of `oscillation_windows.jsonl`, rare event).
  Dashboard reuses `_load_all_windows` mtime/size cache.
- **Dependencies**: none new.
