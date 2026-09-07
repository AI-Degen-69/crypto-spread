# CONSTRAINTS.md — Issue #95 Quality & Architectural Constraints

## 1. Testing & Zero Regressions
- All existing tests stay green: `python -m pytest -q` (317 tests today, plus the
  new ones). Zero modifications to existing assertions.
- The #92 gate tests (`tests/test_live_trader.py:1653-1733`) and the #92/#96 parity
  tests (`tests/test_entry_timeout.py:419-455` and below) must pass unchanged —
  the gate itself is not being weakened.
- New tests (red -> green) required, one per behavior:
  1. drift-skipped window whose mid reverts inside the band re-enters and rests orders
  2. drift-skipped window whose mid stays outside the band does not re-enter (BTC 15m case)
  3. entry-timeout-cancelled window (`is_late_start`) never re-enters, even at mid 0.50
  4. #96 late-start-skipped window never re-enters
  5. re-entry blocked when remaining window time < `min_requote_remaining_sec`
  6. per-window re-entry cap enforced (second revert does not re-enter)
  7. `open_mid` / `open_drift` still readable after re-entry; `last_action` names the drift
  8. `reentry_count` cleared by `_handle_window_rollover()` and by `reset_pnl()`
  9. backtest: adverse-skipped window re-enters; timeout-cancelled window does not
- Targeted gates: `python -m pytest tests/test_live_trader.py -q`,
  `python -m pytest tests/test_entry_timeout.py -q`,
  `python -m pytest tests/test_backtest_engine.py -q`,
  `python -m pytest tests/test_osc_dash_integration.py -q`.

## 2. Anti-Cheating & Integrity
- No disabling, skipping, weakening or deleting existing tests or assertions.
- Re-entry is conditioned on `mstate.adverse_open`, never on
  `entry_cancelled_timeout` alone. `is_late_start` and `late_start_skip` are never
  cleared, bypassed or removed from `can_place_entry`.
- The adverse-open gate threshold (`exit_thresh`) is not lowered, and
  `open_gate_evaluated` is not used to re-run the gate against a friendlier mid.
- No widening of the band to make a test pass; tests adapt to the documented
  default, not the other way round.

## 3. Parameters & Parity
- `reentry_drift_band`, `min_requote_remaining_sec` and `max_reentries_per_window`
  carry byte-identical defaults in `LiveTraderEngine.__init__` and `BacktestParams`
  (`0.015`, `60.0`, `1`). A change to one without the other is a defect.
- `exit_reversal`, `exit_thresh`, `entry_timeout_pct`, `max_start_elapsed_pct` and
  the `0.50 - offset` resting anchor are untouched by this issue.
- `BacktestParams.__post_init__` validates the new fields in the existing style
  (finite, `0.0 <= reentry_drift_band <= 0.5`, `min_requote_remaining_sec >= 0`,
  `max_reentries_per_window >= 0`).
- `update_config()` treats `reentry_drift_band` like the other scalar knobs: it
  participates in the "cannot change parameters while running" guard.

## 4. Performance & Integration Guardrails
- No new dependencies in `requirements.txt`.
- The re-entry check is O(1) per market per tick, no network calls, no extra CLOB
  round-trips beyond the order placement that already follows `can_place_entry`.
- All `mstate` mutations on the re-entry path happen under `self._engine_lock`,
  matching the surrounding cancellation and rollover blocks.
- Contract change is additive only: three new params-payload/config fields and new
  `MarketLiveState` fields with defaults. No existing API shape changes.
- `reentry_count` / `reentry_mid` / `reentry_drift` reset on window rollover and on
  `reset_pnl()`, alongside the other per-window state.
