# SPEC.md — Issue #152: Tick-file audit — keep today, quarantine fragments, repair 09-11

## 1. Goal
Curate `run/ticks/` into one trusted backtest set (today-only) so every
backtest and the #132 auto-refresh build on known-good data. Fragments go to
quarantine, nothing is deleted, originals are never edited in place.

## 2. Background (observed 2026-09-12/13)
- `run/ticks/` holds 6 files: 08-31 (580MB, full day), 09-07 (37MB, 1.7h),
  09-08 (177MB, 8.6h), 09-09 (19MB, 35min), 09-11 (271MB, 7.4h, 12 corrupt
  lines), 09-12 (620MB+, still growing — collector live). 09-10 missing.
- The 12 corrupt lines in 09-11 (0.013%) are truncation/interleave fragments
  (mid-JSON starts/ends, empty lines) — each JSONL line is one independent
  tick, so dropping them is tail-safe. Pattern matches the dual-writer race
  from #151.
- `run/oscillation_windows.jsonl` is stale (built 09-01, pre-today data).
- Both `verify_ticks_dir` (`scripts/verify_tick_data.py:511`) and
  `rebuild_windows` (`scripts/rebuild_windows.py:251`) use non-recursive
  `Path.glob`, so a `run/ticks/quarantine/` subdir is automatically excluded
  from verification and rebuilds — no code change needed.

## 3. In Scope
1. Backup derived `run/oscillation_windows.jsonl` + `run/oscillation_summary.json`
   before rebuild (cheap insurance; both are regenerable + gitignored).
2. Move 08-31, 09-07, 09-08, 09-09 to `run/ticks/quarantine/` (reversible,
   same disk); 09-11 quarantined as-is pending the repair call.
3. Rebuild windows from today-only (`ticks_2026-09-12.jsonl`) and report the
   window count vs the ~1,900/day max and the 2,430 ev-research sample.
4. Backtest smoke on the kept set proves it replays green.
5. OPTIONAL (needs operator sign-off): repair 09-11 by writing a repaired
   *copy* (drop 12 bad lines), original stays in quarantine, re-verify green.

## 4. Out of Scope
- Deleting anything (one-way door — proposal + approval first, per issue).
- Moving `ticks/` to `data/` (~40 refs — separate issue).
- The oscillation-page auto-refresh itself (#132 consumes this decision).
- Editing originals in place — live-captured ticks are irreplaceable.

## 5. Interfaces (locked before logic)
- No production code changes. CLI contracts used as-is:
  - `python -m scripts.verify_tick_data run/ticks [--json]` → per-file
    PASS/WARN/FAIL; kept set must show no FAIL.
  - `python -m scripts.rebuild_windows --pattern ticks_2026-09-12.jsonl`
    → `(num_files, num_windows)`; `--pattern` selects the kept set explicitly
    so the count is auditable even if quarantine layout changes.
  - `python -m scripts.backtest run/ticks/ticks_2026-09-12.jsonl --offset 0.02 --queue 50`
    → exit 0 with per-series + overall stats (source may be a single file).
- Quarantine contract: `run/ticks/quarantine/<original-name>`; excluded from
  all globs by non-recursive `Path.glob` (verified, not assumed).

## 6. Acceptance Criteria
- [ ] Window count from today-only reported (vs ~1,900 max / 2,430 sample).
- [ ] Quarantine done; kept set replays green; verify badges on kept files not FAIL.
- [ ] `python -m pytest -q` green.
