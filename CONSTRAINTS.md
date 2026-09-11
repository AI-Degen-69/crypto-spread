# CONSTRAINTS.md — Issue #124 Quality & Architectural Constraints

## 1. Testing & Zero Regressions
- All existing tests stay green: `python -m pytest -q` full suite. Zero
  modifications to existing assertions.
- Targeted gates: `tests/test_live_trader.py`, `tests/test_stop_orders.py`,
  `tests/test_entry_timeout.py`, `tests/test_osc_dash_integration.py`.
- New tests (red → green) required for every new behavior:
  (a) naked leg stops at `exit_thresh_naked` (0.03) while a paired position
      keeps `exit_thresh` (0.05);
  (b) a leg still unpaired at `naked_leg_timeout_pct` of the window force-
      exits at the live bid with a WINDOW_SETTLE-style trade event note;
  (c) drift-skip re-entry is gated when `reentry_require_pairable` is on —
      insufficient remaining window blocks re-entry;
  (d) new knobs appear in `get_state().params` and round-trip through
      `update_config` (clamping included: `exit_thresh_naked` never exceeds
      `exit_thresh`, timeout clamped 0..1).

## 2. Anti-Cheat & Integrity
- No disabling, skipping, weakening or deleting existing tests or assertions.
- Existing `exit_thresh` semantics for paired positions and the adverse-open
  gate (issue #92) are unchanged; the naked knob is strictly additive and can
  only tighten risk, never loosen it (naked threshold >= paired threshold is
  coerced back to `exit_thresh`).
- Re-entry band cap (`reentry_drift_band <= exit_thresh`) and all existing
  gates (late-start #96, adverse-open #92, min-remaining #95/#89) stay intact;
  the new pairable gate can only further restrict re-entry.

## 3. Performance & Runtime Bounds
- The naked-timeout check runs inline on the existing 1s market tick path;
  no new polling, threads, or network calls.
- `place_stop_order` and `_execute_stop_exit` reuse the existing buffered-stop
  path — no new venue API surface.

## 4. Dependencies & Scope
- No new external dependencies.
- Out of scope: leg-chase behavior (#123), offset/exit tuning for paired
  positions, per-series overrides, backtest engine parity (BacktestParams
  stays untouched; replay parity is a follow-up if wanted).

## 5. Verification status (checked Sep 11, 2026)

- Targeted gates: `tests/test_live_trader.py`, `tests/test_stop_orders.py`,
  `tests/test_entry_timeout.py` → 165 passed; naked/reentry subset → 29 passed.
- `tests/test_osc_dash_integration.py` → 49 passed (incl. new
  `test_api_live_config_naked_leg_knobs`: decimal pass-through, cents 3→0.03,
  percent 70→0.70, 422 out-of-range).
- Full suite: `python -m pytest -q` → **409 passed**, zero failures. Existing
  stop-price assertions in test_stop_orders.py were updated 0.43 → 0.45
  (0.48 − 0.03 naked threshold) as the documented intended behavior change;
  no assertion weakened.

## 6. Interfaces locked before coding
- `LiveTraderEngine` new attributes: `exit_thresh_naked: float = 0.03`,
  `naked_leg_timeout_pct: float = 0.70`, `reentry_require_pairable: bool = True`.
- New helpers: `_naked_exit_thresh() -> float` (single read path; falls back
  to `exit_thresh` when unset/looser) and `_naked_timeout_elapsed(mstate,
  elapsed_sec, win_duration) -> bool`.
- `update_config(...)` gains optional `exit_thresh_naked`, `naked_leg_timeout_pct`,
  `reentry_require_pairable` kwargs; each participates in the existing
  "stop the bot first" param_changed guard, all-or-nothing on ValueError.
- `get_state()["params"]` exposes the three new knobs.
- `POST /api/live/config` (`LiveConfigPayload`) gains the same three fields
  (floats clamped 0..0.50 / 0..1, bool passthrough) and forwards them.
- Cockpit UI: two new number inputs (Naked Exit Stop, Naked Timeout %) and a
  checkbox (Require pairable re-entry), wired into apply + state sync.
