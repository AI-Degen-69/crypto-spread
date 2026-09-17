# Plan — Issue #229: `dead_zone` governs the end of the window

**Size**: Standard — both engines (`backtest/engine.py`, `strategy/live_trader.py`),
a shared calculation in `strategy/book_math.py`, dashboard API & UI controls,
CLI flags and sweeps, ~8 targeted test files, and a dedicated parity test.
Five parameter deletions plus two new parameters.
**Type**: Code (+ Design/UI for dashboard controls).
**Stack**: Python 3.12, FastAPI, pytest. Targeted tests only locally; CI is the
merge gate (`AGENTS.md`).
**Spec**: `SPEC.md`. **Gates**: `CONSTRAINTS.md`. **Rule of record**:
`docs/engine-decision-rules.md` §8 & §14.
**Interview**: Requirements were fully clear from the issue — interview-me was skipped.

## Contract Changes

```python
# backtest/engine.py & strategy/live_trader.py
- entry_timeout_pct: float = 0.10
- naked_leg_timeout_pct: float = 0.0 / 0.70
- max_start_elapsed_pct: float = 0.10
- max_start_delay_sec: float = 0.0
- stop_loss_enabled: bool = True
+ dead_zone_unit: str = "pct"             # "pct" | "sec"
+ dead_zone_val: float = 0.10             # validated: >= 0.0 (and <= 1.0 if pct)
+ naked_leg_at_expiry: str = "close"      # "close" | "hold"
```

Shared helpers in `strategy/book_math.py`:
- `dead_zone_cutoff_seconds(window_length: float, dead_zone_val: float, dead_zone_unit: str) -> float`
- `is_in_dead_zone(remaining_sec: float, window_length: float, dead_zone_val: float, dead_zone_unit: str) -> bool`
- `dead_zone_start_ts(start_ts: float, end_ts: float, dead_zone_val: float, dead_zone_unit: str) -> float`

## Tasks

### [x] T0 — Branch + spec lock (done in Station II)
Branch `feat/dead-zone-229` off `master`. `SPEC.md`, `CONSTRAINTS.md`,
`tasks/plan.md`, `tasks/todo.md` written.

### [x] T1 — `[Backend/Logic]` Shared dead-zone calculation in `strategy/book_math.py`
**Files**: `strategy/book_math.py`, `tests/test_book_math.py`.
**Do**: Implement `dead_zone_cutoff_seconds`, `is_in_dead_zone`, and `dead_zone_start_ts`.
Handle clock boundaries (`window_length <= 0`, negative remaining time, unit validation).
**Skill**: `test-driven-development`.
**Verify**: Targeted test in `test_book_math.py`.

### [x] T2 — `[Backend/Logic]` Backtest: delete timeout knobs, add Dead Zone & `naked_leg_at_expiry`
**Files**: `backtest/engine.py`.
**Do**: Delete `entry_timeout_pct`, `naked_leg_timeout_pct`, `max_start_elapsed_pct`,
`max_start_delay_sec`, and `stop_loss_enabled`.
Add `dead_zone_unit` (`"pct"`), `dead_zone_val` (`0.10`), and `naked_leg_at_expiry` (`"close"`).
Implement dead-zone trigger:
- Open nothing (if first tick or current tick in dead zone).
- Cancel unfilled resting buy quotes when entering dead zone.
- For an unpaired filled leg: if `"close"`, cancel opposite order and sell filled leg at best executable bid (cross book, pays fee); if `"hold"`, cancel opposite order and carry filled leg to window settlement.
Update `_PARAM_GROUPS` to register `dead_zone_val` and `dead_zone_unit` as structural limits.
**Skill**: `test-driven-development`.
**Verify**: `python -m pytest tests/test_backtest_engine.py -q`.

### [x] T3 — `[Backend/Logic]` Live: delete timeout knobs, add Dead Zone & `naked_leg_at_expiry`
**Files**: `strategy/live_trader.py`.
**Do**: Delete `entry_timeout_pct`, `naked_leg_timeout_pct`, `max_start_elapsed_pct`,
and `stop_loss_enabled`.
Add `dead_zone_unit`, `dead_zone_val`, and `naked_leg_at_expiry`.
Update `update_config()` validation and `engine_state()` dictionary.
Implement dead-zone trigger:
- Skip entry for windows whose current tick is in the dead zone.
- Cancel unfilled resting buy orders upon entering dead zone.
- For an unpaired filled leg: if `"close"`, trigger market exit at book bid; if `"hold"`, cancel opposite quote and hold to settlement.
**Skill**: `test-driven-development`.
**Verify**: `python -m pytest tests/test_live_trader.py -q`.

### [x] T4 — `[Test/Parity]` Dead Zone parity test
**Files**: `tests/test_dead_zone_parity.py`.
**Do**: Drive both engines through shared snapshot streams:
- Outside dead zone: quotes sit and wait, no early cancellation.
- Enter dead zone with unfilled quotes: both cancel orders.
- Enter dead zone with unpaired leg: assert identical exit tick, price, and fee under `"close"`.
- Enter dead zone with unpaired leg under `"hold"`: assert both hold to settlement.
- Unit parity: assert `pct=0.10` on 300s window behaves identically to `sec=30.0`.
**Skill**: `test-driven-development`.
**Verify**: `python -m pytest tests/test_dead_zone_parity.py -q`.

### [x] T5 — `[API/Dashboard]` Update API schemas, registry, and Cockpit/Backtest UI
**Files**: `server/osc_dash.py`.
**Do**: Update `BacktestRequest` and `LiveConfigPayload` models.
Replace timeout / late-start inputs in Backtest & Cockpit tabs with Dead Zone controls:
`dead_zone_val`, `dead_zone_unit`, `naked_leg_at_expiry`.
Update `/api/backtest` query parameter mapping.
**Skills**: `frontend-ui-engineering`, `api-and-interface-design`.
**Verify**: `python -m pytest tests/test_osc_dash_integration.py tests/test_param_registry.py -q`.

### [x] T6 — `[CLI/Research]` Retarget CLI, sweeps, and existing test suites
**Files**: `scripts/backtest.py`, `scripts/sweep_backtest.py`, `research/sweeps/sim2.py`,
`research/sweeps/ev_lab.py`, `tests/test_entry_timeout.py`, `tests/test_backtest_cli.py`,
`tests/test_sweep_backtest.py`.
**Do**: Update CLI flags (`--dead-zone-val`, `--dead-zone-unit`, `--naked-leg-at-expiry`),
retarget `tests/test_entry_timeout.py` to assert dead-zone rules instead of obsolete 10% timeout.
**Verify**: Run all targeted test files.

### [x] T7 — `[Review/Ship]` Review verification, docs update, and PR presentation
**Do**: Ensure all targeted tests pass. Hand off to Station IV.



## Order and Commits

T0 → T1 → T2 → T3 → T4 (parity proves T2–T3) → T5 → T6 → T7.
One atomic commit per task, conventional, scoped. Feature branch: `feat/dead-zone-229`.

## Improvement Proposed (Under Operator Review)

**Shared pure dead-zone calculation in `strategy/book_math.py`.**
Instead of calculating the dead-zone window cutoff separately in `strategy/live_trader.py`
and `backtest/engine.py`, extract pure functions into `strategy/book_math.py`:
- `dead_zone_cutoff_seconds(window_length: float, dead_zone_val: float, dead_zone_unit: str) -> float`
- `is_in_dead_zone(remaining_sec: float, window_length: float, dead_zone_val: float, dead_zone_unit: str) -> bool`
- `dead_zone_start_ts(start_ts: float, end_ts: float, dead_zone_val: float, dead_zone_unit: str) -> float`

This guarantees exact arithmetic parity (no floating point or boundary drift between engines)
and directly prepares the `dead_zone_start_ts` needed by the next issue (#231 leg chase escalation).

