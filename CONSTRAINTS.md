# CONSTRAINTS.md — Issue #145: delay/band knobs + winning preset

## Quality Gates & Hard Thresholds

### 1. Test Suite Integrity
- **Pass Rate**: 100% — `python -m pytest tests/test_backtest_engine.py
  tests/test_osc_dash_integration.py -q` fully green before and after.
- **New behavior needs tests**: every new knob gets ≥1 engine test (delay
  holds quotes; band skip/admit; post-delay quote anchoring) + API
  passthrough/clamp test; dashboard ids covered by a UI presence test.
- **Anti-Cheat**: no touching existing fixtures/expectations to fit the change
  (defaults-unchanged proven by untouched hashes); no skipped tests, no
  weakened assertions, no linter suppressions.

### 2. Behavior & Scope Boundaries
- **Defaults byte-identical**: `entry_delay_sec=0, entry_band=0` must replay
  exactly as today — same resting anchor (first snapshot), same fills, same
  `params_hash` for old param sets (new fields default into the hash payload
  deterministically; old hashes only stable when new fields are default —
  document, don't chase).
- **Live-identical semantics**: delay = observe-only (classification uses full
  path); band once, latched, two-sided mid, bypass for adverse-owned windows;
  re-entry path untouched.
- **No live-trader changes, no sweep changes, no new dependencies.**
- **Clamps at the boundary**: API clamps invalid values (no 400s, no silent
  inf/NaN acceptance) mirroring `LiveConfigPayload`.

### 3. Perf & Dependencies
- **Perf**: per-window loop stays O(snaps); no extra passes, no buffering.
- **Dependencies**: none new (stdlib + existing stack only).
