# Todo: Issue #152 — tick-file audit (keep today, quarantine, repair)

- [x] Task 1: Backup derived dataset to `run/backup_pre152/` (2940 baseline windows)
- [x] Task 2: Quarantine 08-31/09-07/09-08/09-09 → `run/ticks/quarantine/` (`verify --json` shows `files_checked == 2`)
- [x] Task 3: Quarantine 09-11 as-is; kept set = 09-12 only, WARN 0-corrupt (`verify run/ticks/ticks_2026-09-12.jsonl`, exit 0)
- [x] Task 4: Rebuild windows today-only → 1730 windows @ 2026-09-13 00:30 (`rebuild_windows --pattern ticks_2026-09-12.jsonl`)
- [ ] Task 5 (OPTIONAL, needs approval): repair 09-11 → repaired copy, re-verify green, reserve only
- [x] Task 6: Backtest smoke on kept set (`scripts.backtest ... --offset 0.02 --queue 50`, exit 0, 1730 windows replayed)
- [x] Task 7: Full regression gate (494 passed, `git status --short` clean except docs/plan)
