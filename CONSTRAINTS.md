# CONSTRAINTS.md — Issue #302: forbid silent rewrites of capture files

Branch: `i302/no-silent-rewrites` · Size: Standard · Type: Code + Docs

## Hard boundaries
1. **Zero regressions:** targeted suites green —
   `python -m pytest tests/test_tick_safety.py tests/test_collect_ticks_smoke.py tests/test_collect_ticks_prewarm_align.py tests/test_verify_tick_data.py tests/test_collector_watchdog.py -q`.
   Full suite stays with CI.
2. **Capture format is sacred:** no change to the capture format, window gates,
   thresholds, or the pristine extractor. This is data-safety plumbing only.
3. **Append path untouched:** normal per-snap writes stay `"ab"` append —
   crash-recovery resume must keep working byte-for-byte.
4. **No silent destruction:** a superseded generation is moved to
   `run/ticks/backup/`, never destroyed in place; every rewrite leaves a
   rewrite event + both hashes on disk.
5. **Watchdog semantics preserved:** single-writer PID enforcement unchanged;
   `--once`/`--out` refusal logic unchanged; watchdog adds `--allow-rewrite`
   explicitly so respawns stay logged, not blocked.
6. **Exit-code contract:** verifier keeps `1` on FAIL and on WARN with
   `--strict`; the cross-check may only raise status, never lower it.
7. **No new dependencies.** stdlib + existing project modules only.
8. **Anti-cheat:** no skipping/disabling tests, no deleted assertions,
   no suppressed linters. Existing refusal-test idiom
   (`pytest.raises(..., match=...)`) is followed, not bypassed.

## Interfaces (locked)
- `scripts/tick_safety.py` (new): day-path helper, `guard_day_write()`,
  `loud_log()`, rewrite-event append/read, hash-store read/update.
  Reuses `sha256_of` from `scripts/ship_to_drive.py`.
- `collect_ticks.write_snap(..., allow_rewrite: bool)` — refuses via the guard
  on truncating rewrite without the flag; appends stay silent-fast.
- `collector_watchdog.collector_cmd()` — appends `--allow-rewrite` explicitly.
- `verify_tick_data` cross-check: peer check function, `sample_issues` entry +
  status raise on unexplained hash change; store updated after the check.
- Paths: `run/ticks/backup/`, `run/ticks/rewrite_events.jsonl`,
  `run/ticks/verify_hashes.json`.

## Where this plan and the issue disagree, the issue wins.
