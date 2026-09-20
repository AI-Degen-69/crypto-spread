# CONSTRAINTS — Issue #285: Railway trial + Google Drive file store

## Scope guard
- Capture path untouched: no edits to `collect_ticks` poll/rotation/manifest
  logic, no verify threshold changes, no backtest changes.
- Watchdog edits limited to: optional shipper pass behind `DRIVE_REMOTE`
  (default off, Windows behavior identical) — no changes to probe/kill/spawn
  semantics merged in #284.
- No new pip dependencies (`requirements.txt` stays at 5). `rclone` comes
  from the platform image (nixpacks apt), configured purely by env.
- No credential in the repo: OAuth refresh token travels via env only; tests
  use fake tokens/subprocesses.

## Measurable boundaries
- Shipper uploads only closed days (`now_day_key()` past the file's day);
  the live day file is never touched.
- Every Drive day has a matching `.sha256`; pulled file checksum matches and
  `verify_tick_data` reports no structural errors.
- 500MB volume holds ≥ 2 closed gz days as buffer (shipper keeps up daily).
- A failed upload never crashes capture (logged, retried, state in
  `shipped.json`).

## Anti-cheat
- No skipping/disabling tests, no deleting assertions, no suppressing linters.
- No editing existing tests to make the work "pass".

## Zero regressions
- Targeted gate: `python -m pytest tests/test_collector_watchdog.py tests/test_collect_ticks_smoke.py -q` plus new shipper tests.
- Full-suite runs stay with CI on push (per AGENTS.md testing policy).
