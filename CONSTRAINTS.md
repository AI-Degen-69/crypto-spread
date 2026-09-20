# CONSTRAINTS — Issue #283: Host the tick collector on a managed platform

## Scope guard
- Collector hosting only. No dashboard / trading-engine hosting.
- **No capture-logic changes**: `POLL_INTERVAL`, `TICK_BUDGET_MS`, day rotation
  (`write_snap`/`now_day_key`), manifest schema, verify thresholds stay as-is.
- Watchdog edits limited to cross-platform process management
  (`collector_pids` / `kill` / spawn flags) with identical Windows behavior:
  unknown probe state ⇒ take no action, never start a second collector.
- No new Python dependencies without explicit approval (host installs the
  existing `requirements.txt`).

## Measurable boundaries
- Disk on host: >= 10GB free before the proof run.
- Proof run: >= 1 hour continuous; manifest `sampling_interval_s` sane
  (~1.4s warm); zero `WEDGED`/`DUPLICATES` events unexplained in watchdog log.
- `python -m scripts.collect_ticks --once` passes ON the host.
- `python -m scripts.verify_tick_data <downloaded-day-file>` reports no
  structural errors.
- Downloaded day files carry recorded `sha256` checksums.

## Anti-cheat
- No skipping/disabling tests, no deleting assertions, no suppressing linters.
- No editing existing tests to make the work "pass".

## Zero regressions
- Targeted gate: `python -m pytest tests/test_collect_ticks_smoke.py -q`
  plus any watchdog tests covering touched code.
- Full-suite runs stay with CI on push (per AGENTS.md testing policy).
