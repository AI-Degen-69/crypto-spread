# SPEC.md — Issue #137: patient undecided-band maker preset (entry delay + band + no-stop)

## 1. Goal
Wire the EV-research-winning configuration (`docs/ev-research-findings-2026-09-11.md`) into the live trader as a named, cockpit-selectable preset `patient_band_maker`: 60s entry delay, undecided-market entry band (|mid − 0.50| ≤ 0.04), reward offset 0.03, no stop-loss on naked legs (hold to settlement), leg-chase cap `max_pair_cost` 0.98, universe xrp-15m + bnb-15m + eth-5m. Two knobs are new engine state (`entry_delay_sec`, `entry_band`); `stop_loss_enabled` is a new gate on the existing stop paths.

## 2. In Scope
1. **New engine knobs (`strategy/live_trader.py:597` `LiveTraderEngine.__init__`)**:
   - `entry_delay_sec: float = 0.0` (0 = off; preset uses 60). During the delay no quotes are placed.
   - `entry_band: float = 0.0` (0 = off; preset uses 0.04). After delay expiry, the then-current mid is checked: `|mid − 0.50| > band` skips the window (logged like the adverse-open skip).
   - `stop_loss_enabled: bool = True` (default preserves current behavior; preset uses False).
   - All three mutable via `update_config()` (`live_trader.py:1957`) with the same clamp/validate + while-running-guard conventions as the existing knobs, echoed in `get_state()["params"]` (`live_trader.py:1842`).
2. **Delay semantics**: per-window, measured from window `start_ts`; no order placement (live or paper-sim) before `elapsed >= entry_delay_sec`. Must compose with existing gates (late-start #96, adverse-open #92, entry_timeout): delay runs first, then the band check, then the existing open-gate evaluation. Re-entry (#95) keeps its own `reentry_drift_band` and is NOT gated by `entry_band`.
3. **Band semantics**: evaluated once at entry time (after the delay) against the live mid; failure sets the same skip latch family as the adverse-open path (`entry_cancelled_timeout` + status/last_action log) without touching `open_mid/open_drift` telemetry. Band check requires a two-sided book like the open-gate snapshot; if the book is one-sided at expiry, entry waits for the first two-sided tick rather than failing the window.
4. **Stop-loss-off semantics**: when `stop_loss_enabled=false`, `place_stop_order()` (`live_trader.py:877`) becomes a no-op (no STAGED/RESTING stop) and the drift-stop trigger paths skip; naked-timeout (#124) and rollover settlement paths remain authoritative.
5. **Preset + API (`server/osc_dash.py:1172`, `:896`)**:
   - Named preset `patient_band_maker` = offset 0.03, band 0.04, delay 60s, `stop_loss_enabled=false`, `max_pair_cost=0.98`, universe xrp-15m + bnb-15m + eth-5m.
   - Selectable via `POST /api/live/config` (new optional `preset` field and/or the five knob fields; preset application is atomic — all six fields applied together or none).
   - Echoed in `GET /api/live/state` (`active_preset` + knob values in `params`).
6. **Tests**: new unit tests for delay suppression/allowance, band skip without orders, no-stop staging when disabled, chase capped at 0.98 under the preset, defaults preserve behavior.

## 3. Out of Scope
- Queue-position telemetry and dashboard visualization (sibling issues #138, #139).
- Any default-behavior change when the preset is not selected (all new knobs default to current behavior).
- Backtest engine changes (research extensions live in `run/sweeps/sim2.py`, intentionally outside the repo engine).
- `MakerConfig` (`strategy/config.py:17`) changes — this issue targets the live engine knobs, not the hunter-fleet config.

## 4. Acceptance Criteria
- [ ] Knobs `entry_delay_sec`, `entry_band`, `stop_loss_enabled` exist with defaults (0 / 0 / True); enforced: no quotes before delay expiry; no quotes when post-delay |mid − 0.50| > band (band > 0); no stop staged when disabled.
- [ ] Preset `patient_band_maker` sets offset 0.03, band 0.04, delay 60s, `stop_loss_enabled=false`, `max_pair_cost=0.98`; selectable via `POST /api/live/config`, echoed in `GET /api/live/state`.
- [ ] Knobs at defaults → behavior identical to today (all pre-existing tests pass unchanged).
- [ ] New unit tests cover: delay suppresses quoting before 60s and allows after; band failure skips without placing orders; no stop staged when disabled; chase still capped at 0.98 under the preset.
- [ ] `python -m pytest tests/test_live_trader.py tests/test_entry_timeout.py -q` passes, plus the new tests.
