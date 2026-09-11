# CONSTRAINTS.md — Issue #137: patient undecided-band maker preset

## Quality Gates & Hard Thresholds

### 1. Test Suite Integrity
- **Pass Rate**: 100% — `python -m pytest -q` fully green; targeted gate `python -m pytest tests/test_live_trader.py tests/test_entry_timeout.py -q` green plus new tests.
- **No Test Swallowing**: strictly forbidden to skip, comment out, or mock-pass failing assertions; new behavior must be covered by new tests, existing tests must pass UNCHANGED at default knob values.
- **Anti-Cheat**: no silencing of stop-loss/drift assertions, no weakening of thresholds to get green, no stub implementations.

### 2. Behavior-Preservation Boundaries
- **Defaults = today**: `entry_delay_sec=0` (off), `entry_band=0` (off), `stop_loss_enabled=True`. With defaults, quote placement, adverse-open skip (#92), re-entry (#95), leg chase (#123), and stop paths (#87/#124) behave byte-for-byte as before.
- **Band independence**: `entry_band` (0.03–0.04 scale) is independent of `exit_thresh`; it must NOT gate re-entry (re-entry keeps `reentry_drift_band`). `reentry_drift_band <= exit_thresh` clamp (live_trader.py:2165) stays untouched.
- **Stop-off scope**: `stop_loss_enabled=false` disables stop staging/triggering ONLY; naked-timeout and rollover settlement paths remain authoritative and must still close/settle naked legs.
- **Pair cost ceiling**: leg chase under the preset stays capped so pair cost ≤ `max_pair_cost` = 0.98 (cent-floored, same math as today).

### 3. Execution & Safety Guards
- **Atomic preset application**: selecting `patient_band_maker` applies all six fields together; a rejected config change leaves the whole configuration untouched (existing `update_config` contract).
- **While-running guard**: knob/preset changes while `is_running` follow the existing "stop the bot first" `ValueError` convention — no silent mutation of a live engine.
- **Pilot safety**: preset universe is xrp-15m + bnb-15m + eth-5m at minimum size (5 shares/leg); no change to live-vs-paper mode handling or wallet-balance fetch paths.

### 4. Performance & Dependencies
- **Latency**: no additional network I/O in the per-tick quote path; delay/band checks are pure arithmetic on already-available `now`, `start_ts`, `mid`.
- **Dependencies**: no new external libraries without explicit approval.
