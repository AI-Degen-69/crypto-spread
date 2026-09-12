# Plan: Issue #152 — Tick-file audit: keep today, quarantine fragments, repair 09-11

Task Type: Code
Size Tier: Small
Target Files: (data ops only) `run/ticks/`, `run/oscillation_windows.jsonl`, `run/oscillation_summary.json`; docs `SPEC.md`, `CONSTRAINTS.md`, `tasks/plan.md`, `tasks/todo.md`

Decisions locked with user: kept set = today-only (`ticks_2026-09-12.jsonl`) ·
  08-31 to quarantine as reserve (not in kept set) ·
  09-11 repair still open — quarantined as-is; repair is Task 5 OPTIONAL ·
  originals never edited in place.

## Task Breakdown

### Task 1: Backup derived dataset (safety first)
- **Files**: `run/oscillation_windows.jsonl`, `run/oscillation_summary.json` → `run/backup_pre152/`
- **Type**: Code
- **Description**:
  1. `mkdir run/backup_pre152`; copy both files (Copy-Item).
  2. Record byte sizes + line count of `oscillation_windows.jsonl` in the
     build log (baseline to compare post-rebuild counts against).
- **Status**: [x] (backup 2940 baseline windows, 1,484,752 bytes)
- **Verification**: `Get-ChildItem run/backup_pre152` shows both files, sizes match originals

### Task 2: Quarantine fragments + 08-31 reserve
- **Files**: `run/ticks/ticks_2026-08-31.jsonl`, `ticks_2026-09-07.jsonl`, `ticks_2026-09-08.jsonl`, `ticks_2026-09-09.jsonl` → `run/ticks/quarantine/`
- **Type**: Code
- **Description**:
  1. `mkdir run/ticks/quarantine`; move the 4 files (same-disk rename, reversible).
  2. Confirm non-recursive globs exclude quarantine: `verify_ticks_dir` must
     list only the remaining files (check `--json` `files_checked`).
- **Status**: [x] (files_checked == 2 confirmed; quarantine auto-excluded)
- **Verification**: `python -m scripts.verify_tick_data run/ticks --json` shows `files_checked == 2` (09-11 + 09-12 only)

### Task 3: Quarantine 09-11 as-is (repair decision pending)
- **Files**: `run/ticks/ticks_2026-09-11.jsonl` → `run/ticks/quarantine/`
- **Type**: Code
- **Description**:
  1. Move 09-11 to quarantine unmodified (12 corrupt lines intact as evidence).
  2. Kept set is now exactly `ticks_2026-09-12.jsonl`; verify kept file is not FAIL.
- **Status**: [x] (09-12: WARN, 0 corrupt, 219,594 valid ticks)
- **Verification**: `python -m scripts.verify_tick_data run/ticks/ticks_2026-09-12.jsonl` exits 0 (status PASS or WARN, not FAIL)

### Task 4: Rebuild windows from today-only + report count
- **Files**: `run/oscillation_windows.jsonl`, `run/oscillation_summary.json` (regenerated)
- **Type**: Code
- **Description**:
  1. `python -m scripts.rebuild_windows --pattern ticks_2026-09-12.jsonl`
     (explicit pattern = auditable kept set).
  2. Report: `(num_files, num_windows)`, rebuild timestamp (09-12 still live —
     count is a snapshot), vs ~1,900/day max and 2,430 ev-research sample.
     Post the numbers on #152.
- **Status**: [x] (1730 windows @ 2026-09-13 00:30 snapshot; posted on #152)
- **Verification**: command prints `Wrote N windows`; `python -m scripts.verify_tick_data run/ticks --json` still clean on the kept set

### Task 5 (OPTIONAL — needs operator sign-off): repair 09-11 as reserve
- **Files**: `run/ticks/quarantine/ticks_2026-09-11.jsonl` (read-only source) → new repaired copy
- **Type**: Code
- **Description**:
  1. Stream 09-11, drop the 12 unparseable lines (0.013%; truncation fragments,
     each JSONL line independent — tail-safe), write repaired copy
     (original untouched in quarantine).
  2. Re-verify repaired copy to green (no FAIL); keep it as reserve OUTSIDE the
     today-only kept set unless operator says otherwise.
  3. NOT part of base scope — implement only on operator approval of the
     improvement below.
- **Status**: [ ] (blocked on approval)
- **Verification**: `python -m scripts.verify_tick_data <repaired-copy>` → status PASS/WARN, `corrupt_lines == 0`

### Task 6: Backtest smoke on the kept set
- **Files**: `run/ticks/ticks_2026-09-12.jsonl` (read-only)
- **Type**: Code
- **Description**:
  1. `python -m scripts.backtest run/ticks/ticks_2026-09-12.jsonl --offset 0.02 --queue 50`
     → exit 0, per-series + overall stats printed (= replays green).
- **Status**: [x] (exit 0, 1730 windows replayed, params baseline offset=0.02 queue=50)
- **Verification**: exit code 0 + stats table in stdout

### Task 7: Full regression gate
- **Files**: —
- **Type**: Code
- **Description**:
  1. `python -m pytest -q` (0 failures).
  2. `git status --short` shows only `SPEC.md`, `CONSTRAINTS.md`,
     `tasks/plan.md`, `tasks/todo.md` (run/ is gitignored — data moves invisible to git, by design).
- **Status**: [x] (494 passed; git shows only SPEC/CONSTRAINTS/plan/todo)
- **Verification**: `python -m pytest -q` + `git status --short`
