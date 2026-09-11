# Plan: Issue #137 — Wire research-winning 'patient undecided-band maker' preset into live trader

Task Type: Code
Size Tier: Standard
Target Files: strategy/live_trader.py, server/osc_dash.py, tests/test_live_trader.py, tests/test_entry_timeout.py (+ new tests/test_patient_band_preset.py)

## Task Breakdown

### Task 1: Engine knobs — init + update_config + state echo
- **Files**: `strategy/live_trader.py` (`__init__` :600, `update_config` :1945, `get_state` :1842)
- **Type**: Code
- **Description**:
  1. Add `entry_delay_sec: float = 0.0`, `entry_band: float = 0.0`, `stop_loss_enabled: bool = True` to `__init__` (defaults = current behavior).
  2. Extend `update_config()` signature + while-running change detection + clamped mutation (delay ≥ 0, band 0..0.50, bool cast) following the existing knob conventions.
  3. Echo all three + `active_preset` in `get_state()["params"]`.
  4. Track `active_preset: str | None` (None = custom/manual); set on preset application, cleared on any manual knob divergence.
- **Status**: [x]
- **Verification**: `python -m pytest tests/test_live_trader.py -q`

### Task 2: Entry-delay gate in the quote path
- **Files**: `strategy/live_trader.py` (quote placement block :3418-3470, pre-entry section :3565+)
- **Type**: Code
- **Description**:
  1. Compute `elapsed = now - start_ts` per tick; when `entry_delay_sec > 0` and `elapsed < entry_delay_sec` and no leg filled yet: place no orders (live or paper-sim), set informative `last_action` (e.g. `entry delayed Xs/Ys`), do NOT latch any skip flag.
  2. Compose with existing gates: delay evaluated before adverse-open (#92) snapshot consumption and entry_timeout/late-start (#96) handling; a window that fills nothing during the delay keeps all existing skip paths intact after expiry.
  3. Reset per-window state on rollover (same reset family as `entry_cancelled_timeout`, `open_gate_evaluated`).
   (Implemented statelessly as a pure function of `elapsed_sec`, so no rollover reset is required.)
- **Status**: [x]
- **Verification**: `python -m pytest tests/test_entry_timeout.py -q` + new delay tests

### Task 3: Post-delay entry-band gate (undecided-market filter)
- **Files**: `strategy/live_trader.py` (open-gate snapshot :3542-3548 as pattern, pre-entry skip :3565+)
- **Type**: Code
- **Description**:
  1. After delay expiry, on the first tick with a two-sided book: check `abs(mid - 0.50) <= entry_band` (only when `entry_band > 0`); on failure latch the window skipped via the `entry_cancelled_timeout` family + `last_action` log mirroring the adverse-drift skip wording, without touching `open_mid/open_drift` telemetry.
  2. One-sided book at expiry: wait for the first two-sided tick instead of failing the window (same `book_two_sided` guard as the open gate).
  3. Explicitly NOT applied to re-entry (#95): re-entry keeps `reentry_drift_band` only.
  4. Distinct telemetry (adopted improvement): per-window `band_skip` flag + session-level `band_skip_stats` counter (follows the `reentry_stats` precedent), echoed in `get_state()`, so the pilot can tell band-filter skips apart from adverse-open skips.
- **Status**: [ ]
- **Verification**: `python -m pytest tests/test_live_trader.py tests/test_entry_timeout.py -q` + new band tests

### Task 4: stop_loss_enabled gate on stop paths
- **Files**: `strategy/live_trader.py` (`place_stop_order` :877, fill→stage call sites :3766/3799/3821/3853, drift-stop triggers :4013/4039)
- **Type**: Code
- **Description**:
  1. `place_stop_order()` early-returns (no-op, no id/staged status) when `stop_loss_enabled` is False — single choke point covering live-buffered and paper-RESTING paths.
  2. Drift-stop trigger evaluation skips when disabled; naked-timeout (`_naked_timeout_elapsed`) and rollover settlement paths stay authoritative.
  3. Defaults (`True`) leave every existing stop test green unchanged.
- **Status**: [ ]
- **Verification**: `python -m pytest tests/test_live_trader.py -q` + new no-stop tests

### Task 5: Preset + API wiring (config + state)
- **Files**: `server/osc_dash.py` (`LiveConfigPayload` :1062, `POST /api/live/config` :1172, `GET /api/live/state` :896), `strategy/live_trader.py` (`update_config`, `get_state`)
- **Type**: Code
- **Description**:
  1. Define preset table `patient_band_maker` = offset 0.03, entry_band 0.04, entry_delay_sec 60, stop_loss_enabled False, max_pair_cost 0.98, universe (xrp-15m, bnb-15m, eth-5m).
  2. `LiveConfigPayload`: add optional `preset`, `entry_delay_sec`, `entry_band`, `stop_loss_enabled`, `enable_leg_chase`, `max_pair_cost` fields with matching validators/ranges.
  3. `POST /api/live/config`: `preset="patient_band_maker"` atomically applies all six fields (or returns 400 leaving config untouched, per existing contract); individual knobs remain settable without a preset.
  4. `GET /api/live/state`: echo `active_preset` + knob values.
- **Status**: [ ]
- **Verification**: `python -m pytest tests/test_osc_dash_integration.py -q` + new preset API tests

### Task 6: Tests + regression gate
- **Files**: `tests/test_patient_band_preset.py` (new), `tests/test_live_trader.py`, `tests/test_entry_timeout.py`
- **Type**: Code
- **Description**:
  1. New tests: delay suppresses quoting before 60s and allows after; band failure skips without placing orders; no stop staged when disabled; chase still capped at 0.98 under the preset; defaults preserve behavior.
  2. Run targeted gate, then the full suite.
- **Status**: [ ]
- **Verification**: `python -m pytest tests/test_live_trader.py tests/test_entry_timeout.py -q` then `python -m pytest -q`
