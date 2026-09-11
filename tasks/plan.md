# tasks/plan.md — Issue #124: Naked-leg risk controls (asymmetric stop, naked timeout, reentry gate)

Branch: `feat/issue-124-naked-leg-risk` (off `master`)
Constraints: `CONSTRAINTS.md` (this issue's gates)
Baseline: full suite green on master (367+ tests).
Status: **implementation ~70% done in working tree** (live_trader changes +
config plumbing landed; dashboard plumbing and tests remain).

## Concise spec (spec-driven-development, Standard tier — no SPEC.md change)

**Goal.** Reduce single-leg stop bleed (−$117.96 across 305 stops in the
2026-09-11 run) with three controls:
1. **Asymmetric stop** — a naked (single-leg) position exits at
   `exit_thresh_naked` (default 0.03) instead of the paired `exit_thresh`
   (0.05). Single read path: `_naked_exit_thresh()`; a knob at/above the
   paired stop or ≤ 0 falls back to `exit_thresh` (can only tighten).
2. **Naked timeout** — a leg still unpaired after `naked_leg_timeout_pct`
   (default 0.70) of its window force-exits at the live bid via the existing
   `_execute_stop_exit` path, with a WINDOW_SETTLE-style note. 0 disables.
3. **Reentry gate** — `reentry_require_pairable` (default True) extends
   `_reentry_min_remaining_sec()` so drift-skip re-entry only fires when the
   window has enough life left for a fresh entry to pair before the naked
   timeout horizon. Additive restriction only.

**Out of scope.** Leg-chase (#123), paired-position offset/exit tuning,
per-series overrides, BacktestParams/replay parity.

## Interfaces (api-and-interface-design) — locked

- Engine attrs: `exit_thresh_naked=0.03`, `naked_leg_timeout_pct=0.70`,
  `reentry_require_pairable=True`; helpers `_naked_exit_thresh()`,
  `_naked_timeout_elapsed(...)`.
- `update_config(..., exit_thresh_naked=, naked_leg_timeout_pct=,
  reentry_require_pairable=)` — joins the param_changed guard and all-or-
  nothing ValueError contract; clamping per CONSTRAINTS §5.
- `get_state()["params"]` + `LiveConfigPayload` + cockpit UI fields
  `exitThreshNaked` / `nakedLegTimeoutPct` / `reentryRequirePairable`.

## Tasks

### T1 — Engine: asymmetric stop + timeout + reentry gate (DONE)
Files: `strategy/live_trader.py`
- `_naked_exit_thresh`, `_naked_timeout_elapsed`, stop trigger + stop-staging
  price use the naked threshold, naked-timeout block before the stop-loss
  trigger in `_update_market_strategy`, `_reentry_min_remaining_sec` gate.
Verify: targeted `pytest tests/test_live_trader.py tests/test_stop_orders.py
tests/test_entry_timeout.py -q` — existing tests still green (they pin
`exit_thresh` 0.05 behavior on naked legs only where the naked threshold is
looser; adjust only if a test pins the old single-threshold trigger, see T3).

### T2 — Config plumbing (DONE)
Files: `strategy/live_trader.py`
- `update_config` kwargs + clamping + param_changed guard; `get_state` params.

### T3 — RED→GREEN: engine tests
Files: `tests/test_live_trader.py`, `tests/test_stop_orders.py` (append)
- Naked stop at 0.03 while paired keeps 0.05 (drift 0.04 exits a naked leg,
  not a paired one).
- Naked timeout: fill UP at t, advance clock past 70% of a 300s window →
  stop exit taken, note contains "Naked-leg timeout", exit at live bid.
- Timeout disabled at 0; timeout ignored when both legs filled.
- Reentry gate: adverse-open window reverts inside band but remaining_sec <
  naked timeout horizon → no re-entry when `reentry_require_pairable=True`;
  re-entry happens with the flag False.
- update_config clamps (`exit_thresh_naked=0.10` with exit_thresh 0.05 →
  `_naked_exit_thresh()==0.05`; timeout 1.5 → 1.0) and round-trips state.
Verify: `python -m pytest tests/test_live_trader.py tests/test_stop_orders.py -q`.

### T4 — Dashboard: payload + cockpit UI
Files: `server/osc_dash.py`
- `LiveConfigPayload`: `exit_thresh_naked` (0..0.50, cents-normalized like
  exit_thresh), `naked_leg_timeout_pct` (0..1, whole >1 → /100), 
  `reentry_require_pairable: bool`; forward to `update_config`.
- Cockpit markup: inputs next to Exit Stop Loss Threshold; JS
  `applyCockpitConfig()` posts them; `renderCockpitUI()` syncs from
  `st.params` while running and on first receipt.
Verify: `python -m pytest tests/test_osc_dash_integration.py -q` + manual
uvicorn smoke on :8802 (params round-trip through APPLY PARAMETERS).

### T5 — Dashboard tests + full gate
Files: `tests/test_osc_dash_integration.py` (append)
- POST /api/live/config with the new knobs → state params reflect them;
  cents normalization (3 → 0.03) for `exit_thresh_naked`.
- `python -m pytest -q` full suite green; update CONSTRAINTS.md §5 numbers.
