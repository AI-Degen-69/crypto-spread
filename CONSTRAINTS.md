# CONSTRAINTS — Issue #290: pristine-window dataset extractor

## Scope guard
- New `scripts/build_pristine_dataset.py` + `tests/test_build_pristine_dataset.py` only.
  No edits to `collect_ticks.py`, `rebuild_windows.py`, `verify_tick_data.py`,
  `strategy/*`, or any existing day file.
- Outputs live only under `run/ticks/pristine/` (gitignored); sources are read-only.
- `out == ticks_dir` (resolved) is refused with exit 2 — outputs share the
  `ticks_<day>.jsonl` naming and would clobber sources.

## Zero regressions
- Targeted gate: `python -m pytest tests/test_build_pristine_dataset.py -q` passes.
- Reused-module suites stay green: `tests/test_verify_tick_data.py`,
  `tests/test_ship_to_drive.py` (sha256_of reuse). No existing test is modified.
- Full-repo sweep stays with CI on push (repo policy — never run locally).

## Determinism (the core invariant)
- Re-run over unchanged inputs → byte-identical output files. **No wall-clock fields
  anywhere in the manifest.** Provenance = source sha256s + verify policy version only.
- Deterministic ordering: source files sorted by name; windows sorted by (start_ts, cid);
  per-pair counts sorted by key. All thresholds flow through one frozen
  `PristineGateParams` dataclass (no per-window drift).

## Anti-cheat
- No skipping, disabling, deleting, or weakening of any test or assertion.
- No new external dependency without explicit operator approval (`requirements.txt` stays at 5).

## Performance
- Streaming two-pass over day files; memory O(windows), never O(ticks).
- No per-tick dict copies: pass 2 writes surviving windows' raw lines, re-parses nothing.

## Gates (mirroring verify_tick_data defaults, overridable via CLI flags)
- late start ≤5s · early cutoff ≤5s · zero gaps >6s · zero time reversals ·
  zero collector-error ticks (`err` field) · snap density ≥1 snap per 3s of window duration
  (`tick_count ≥ ceil(duration / max_snap_interval_sec)`).
