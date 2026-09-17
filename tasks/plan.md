# Plan — Issue #230: Rule: one stop threshold — delete `exit_thresh_naked`

**Size**: Standard — both engines (`backtest/engine.py`, `strategy/live_trader.py`),
dashboard API & UI cleanup (`server/osc_dash.py`), sweeps and scripts, ~7 targeted test files,
and a dedicated parity test (`tests/test_stop_loss_parity.py`).
**Type**: Code (+ UI cleanup).
**Stack**: Python 3.12, FastAPI, pytest. Targeted tests only locally; CI is the merge gate (`AGENTS.md`).
**Spec**: `SPEC.md`. **Gates**: `CONSTRAINTS.md`. **Rule of record**: `docs/engine-decision-rules.md` §2 & §10.
**Interview**: Requirements were fully clear from the issue — interview-me was skipped.

## Contract Changes

```python
# backtest/engine.py & strategy/live_trader.py
- exit_thresh_naked: float = 0.0 / 0.05
# Single surviving parameter:
# exit_thresh / exit_thresh_by_slug (governs all stop-loss exits)
```

## Tasks

### [x] T0 — Branch + spec lock (done in Station II)
Branch `feat/one-stop-threshold-230` off `master`. `SPEC.md`, `CONSTRAINTS.md`,
`tasks/plan.md`, `tasks/todo.md` written.

### [x] T1 — `[Backend/Logic]` Backtest: delete `exit_thresh_naked` and unify on `exit_thr`
**Files**: `backtest/engine.py`.
**Do**:
- Remove `exit_thresh_naked` from `BacktestParams` dataclass fields and `__post_init__` validation.
- Remove `exit_thresh_naked` from `param_spec` and `_PARAM_GROUPS`.
- Remove helper `naked_exit_thresh()`.
- In `replay()`: replace all `naked_thr` usages with single `exit_thr`.
- Unify the pre-fill check (line 1028) and main check (line 1146) to both use `exit_thr`.
**Skill**: `test-driven-development`.
**Verify**: `python -m pytest tests/test_backtest_engine.py -q`.

### [ ] T2 — `[Backend/Logic]` Live engine: delete `exit_thresh_naked` and unify on `self.exit_thresh`
**Files**: `strategy/live_trader.py`.
**Do**:
- Remove `self.exit_thresh_naked` and helper `_naked_exit_thresh()`.
- In `_stage_stop_order()`: calculate stop price using `self.exit_thresh`.
- In drift tracking: evaluate adverse excursion against `self.exit_thresh`.
- In stop trigger evaluation: trigger stop when adverse drift reaches `self.exit_thresh`.
- In `get_state()`: remove `exit_thresh_naked` from returned state dictionary.
- In `update_config()`: remove `exit_thresh_naked` parameter.
**Skill**: `test-driven-development`.
**Verify**: `python -m pytest tests/test_live_trader.py -q`.

### [ ] T3 — `[Test/Parity]` Stop Loss parity test suite
**Files**: `tests/test_stop_loss_parity.py`.
**Do**:
- Implement parity test suite driving both engines through identical snapshots:
  - Parity 1: Identical stop arming calculation (`round(fill_price - exit_thresh, 2)` clamped to `[0.01, 0.99]`).
  - Parity 2: UP leg fill, mid drifts down -> triggers stop at the identical tick and cancels DOWN order (OCO).
  - Parity 3: DOWN leg fill, mid drifts up -> triggers stop at the identical tick and cancels UP order (OCO).
  - Parity 4: Reversal detection suppresses stop identically when price retraces within `exit_reversal`.
  - Parity 5: Second leg fill completes pair and cancels staged stop identically.
**Skill**: `test-driven-development`.
**Verify**: `python -m pytest tests/test_stop_loss_parity.py -q`.

### [ ] T4 — `[API/Dashboard]` Remove `exit_thresh_naked` from dashboard API, schemas, and UI
**Files**: `server/osc_dash.py`.
**Do**:
- Remove `exit_thresh_naked` query parameter from `/api/backtest`.
- Remove `exit_thresh_naked` from `LiveConfigPayload` / `CockpitConfigPayload` and validation models.
- Remove HTML controls `#btExitNaked` and `#cockpitExitNaked` and associated label bindings.
- Remove from JavaScript query builder and configuration synchronization.
**Skills**: `frontend-ui-engineering`, `api-and-interface-design`.
**Verify**: `python -m pytest tests/test_osc_dash_integration.py -q`.

### [ ] T5 — `[Tests/Refactor]` Retarget existing test suites, sweeps, and scripts
**Files**:
- `tests/test_param_registry.py`
- `tests/test_backtest_engine.py`
- `tests/test_live_trader.py`
- `tests/test_stop_orders.py`
- `tests/test_replay_shadow_check.py`
- `tests/test_ev_sweep_lab.py`
- `research/sweeps/ev_lab.py`
- `scripts/replay_shadow_check.py`
**Do**:
- Update parameter lists and fixtures to remove `exit_thresh_naked`.
- Retarget tests that asserted naked vs paired stop differences to assert single `exit_thresh` behavior.
**Verify**: Run all targeted test files.

### [ ] T6 — `[Review/Ship]` Verification and Station IV handoff
**Do**:
- Run all targeted test gates.
- Verify zero regressions and clean git status.
