# CONSTRAINTS.md — Issue #152: tick-file audit (keep today, quarantine, repair)

## Quality Gates & Hard Thresholds

### 1. Test Suite Integrity
- **Pass Rate**: 100% — `python -m pytest -q` fully green before and after
  (data ops must not break code; globs/p szerződések unchanged).
- **No Test Swallowing**: no skipped tests, no weakened assertions to fit the
  change; this issue adds no production code, so no new unit tests required —
  verification is via the three CLI smoke gates below.
- **Anti-Cheat**: no editing tick files to make `verify` green (repair = new
  file, original preserved); no deleting FAIL evidence — quarantine, don't hide.

### 2. Behavior & Scope Boundaries
- **Originals immutable**: never edit `run/ticks/ticks_*.jsonl` in place.
  Repair writes a *copy*; quarantine moves are same-disk renames (reversible).
- **No deletions**: zero file deletions without explicit operator sign-off.
- **Derived data backed up**: `run/oscillation_windows.jsonl` +
  `run/oscillation_summary.json` copied to `run/backup_pre152/` before rebuild.
- **Kept set locked**: today-only = `ticks_2026-09-12.jsonl` (operator decision
  2026-09-13). 08-31 stays in quarantine as reserve, not in the kept set.
- **09-12 is live**: file still growing — window count is a snapshot at rebuild
  time; report the exact rebuild timestamp alongside the count.

### 3. Perf & Dependencies
- **Perf**: full-verify of the 620MB 09-12 file is a single streaming pass
  (existing `verify_tick_file` is O(lines), no full-file buffering); rebuild is
  one pass + sort of window records only.
- **Dependencies**: none new (stdlib + existing scripts only).
