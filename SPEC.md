# SPEC — Issue #297: pristine dataset — gate out windows with bounds-violation ticks

## Problem (from the issue, confirmed against real data)
`build_pristine_dataset.py` gates windows on continuity only. A window with a tick whose
`mid` or `touch_pair` is out of sane bounds (surfaced on the dashboard as Sample
Discrepancies) is still written into the pristine dataset — contradicting pristine.

**Existence proof on real data (planning-time scan):** `run/ticks/ticks_2026-09-18.jsonl`
(smallest day, 7,180 ticks) contains 1 bounds-violating tick in 1 distinct window — the
bug is real in the current dataset, not theoretical. A full 5.2 GB re-scan is deferred
to build-time verification (optional), not a planning blocker.

## Interface contracts (frozen before logic)
- `_tick_out_of_bounds(tick: dict) -> bool` — new module-level helper in
  `scripts/build_pristine_dataset.py`. Reads `tick.get("mid")` / `tick.get("touch_pair")`;
  None means no violation for that field; non-numeric or out-of-range means True.
  Thresholds hard-coded to [-0.01, 1.01] / [0.50, 1.50] — single definition point,
  mirroring `verify_tick_data.py:205-213`. No params, no CLI knob.
- `_new_window_state(first_tick, source_name)` — adds `bounds_violations: int` seeded
  from the FIRST tick (same pattern as `error_ticks` at line 148). Critical: the first
  tick never flows through `_add_tick_to_state`, so seeding here is the only way a
  first-tick violation is counted.
- `_add_tick_to_state(st, tick, source_name, max_gap_sec)` — increments
  `st["bounds_violations"]` when `_tick_out_of_bounds(tick)`.
- `judge_window(agg, params)` — reads `agg.get("bounds_violations", 0)` (defensive .get:
  the empty-window path at `evaluate_window_gates:95` builds a minimal dict without the
  key and relies on the `no_ticks` early return; .get keeps the gate safe for any
  future caller). Appends `bounds_violation` to `failing` when > 0, after
  `collector_error`, before the snap_density check (gate order in `failing_gates` stays
  deterministic). Adds `bounds_violations: 0` to the `no_ticks` early-return dict AND
  `bounds_violations: <count>` to the normal verdict dict — both return shapes carry
  identical keys.
- `manifest_keys` tuple in `build_pristine_dataset` (line 377) — adds
  `bounds_violations` so the counter reaches `pristine_manifest.json` per window.
- `VERIFY_POLICY_NOTE` (line 47) — extend with one clause noting the bounds gate mirrors
  `verify_tick_data.verify_tick` sane bounds (the note currently claims gates mirror
  `verify_window_continuity` only, which would become a lie).

## Acceptance-criteria to test mapping
| Issue criterion | Test |
|---|---|
| touch_pair > 1.50 fails with bounds_violation | unit: make_tick(touch_pair=1.74) mid-window |
| mid > 1.01 or < -0.01 fails | unit: two cases via make_tick(mid=...) |
| gate name in manifest per-window record | e2e manifest assertion |
| window excluded from pass-2 output / passed_cids | e2e modeled on test_failing_window_absent_from_output |
| byte-identical re-run preserved | existing test_rerun_is_byte_identical must stay green |
| all existing tests pass | full targeted file run |
| first-tick violation counted (code-derived, beyond issue text) | unit: violation at tick index 0 via seeding path |

## Non-goals (hard)
Changing bounds values; touching `verify_tick_data.py`; dashboard changes; source-file
modification; CLI flag; rebuilding the on-disk pristine dataset in this branch.
