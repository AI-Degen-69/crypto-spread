# Plan — Issue #231: Rule: the leg chase escalates with time instead of firing on the first tick

**Size**: Standard — shared math in `strategy/book_math.py`, both engines (`backtest/engine.py`, `strategy/live_trader.py`),
dedicated parity test (`tests/test_leg_chase_parity.py`), and test suite retargeting.
**Type**: Code.
**Stack**: Python 3.12, FastAPI, pytest. Targeted tests only locally; CI is the merge gate (`AGENTS.md`).
**Spec**: `SPEC.md`. **Gates**: `CONSTRAINTS.md`. **Rule of record**: `docs/engine-decision-rules.md` §12.
**Interview**: Requirements were fully clear from the issue and §12 — interview-me was skipped.

## Contract Changes

```python
# strategy/book_math.py
def chase_progress(now: float, went_naked_at: float, dead_zone_start: float) -> float: ...
def chase_ceiling(original_resting: float, max_affordable: float, progress: float) -> float: ...
```

## Tasks

### [ ] T0 — Branch + spec lock (done in Station II)
Branch `feat/leg-chase-ladder-231` off `master`. `SPEC.md`, `CONSTRAINTS.md`,
`tasks/plan.md`, `tasks/todo.md` written.

### [ ] T1 — `[Backend/Math]` Shared chase escalation math in `strategy/book_math.py`
**Files**: `strategy/book_math.py`.
**Do**:
- Implement `chase_progress(now, went_naked_at, dead_zone_start) -> float` clamped to `[0.0, 1.0]`.
- Implement `chase_ceiling(original_resting, max_affordable, progress) -> float` floored to 2dp cents precision.
- Unit test coverage for progress and ceiling math.
**Skill**: `test-driven-development`.
**Verify**: `python -m pytest tests/test_book_math.py -q`.

### [ ] T2 — `[Backend/Logic]` Backtest engine escalation ladder
**Files**: `backtest/engine.py`.
**Do**:
- When one leg fills (`filled_up != filled_down`), record `went_naked_elapsed`.
- Derive `dead_zone_start_sec` using existing dead zone cutoff math.
- In leg chase block: compute `progress = chase_progress(elapsed, went_naked_elapsed, dead_zone_start_sec)`.
- Compute `ceiling = chase_ceiling(original_resting, max_affordable, progress)`.
- Target: `target = min(other_leg_ask, ceiling)`. Only raise if `target > resting`.
- At tick immediately after fill with unmoved market, quote remains at original resting price.
**Skill**: `test-driven-development`.
**Verify**: `python -m pytest tests/test_backtest_engine.py -q`.

### [ ] T3 — `[Backend/Logic]` Live engine escalation ladder & cleanup
**Files**: `strategy/live_trader.py`.
**Do**:
- In `_update_market_strategy`: calculate `dead_zone_start_ts` from window bounds.
- Use `mstate.naked_since_ts` as `went_naked_at`.
- Compute `progress` and `ceiling` via `book_math.chase_progress` and `book_math.chase_ceiling`.
- Clean up unused legacy attributes (`chase_step_pct`, `chase_max_steps`, `chase_interval_sec`) that belonged to stepped chase.
- Ensure consistent escalation across paper and live execution blocks.
**Skill**: `test-driven-development`.
**Verify**: `python -m pytest tests/test_live_trader.py -q`.

### [ ] T4 — `[Test/Parity]` Leg chase dedicated parity test suite
**Files**: `tests/test_leg_chase_parity.py`.
**Do**:
- Implement dedicated parity test suite driving both engines through identical snapshots:
  - Parity 1: First tick after fill with unmoved market does NOT raise quote.
  - Parity 2: Time progress toward dead zone scales ceiling smoothly.
  - Parity 3: Ceiling at dead zone equals full `max_affordable`.
  - Parity 4: Quote is never lowered when ask fluctuates downward.
  - Parity 5: Exact tick-by-tick parity between backtest and live engine.
**Skill**: `test-driven-development`.
**Verify**: `python -m pytest tests/test_leg_chase_parity.py -q`.

### [ ] T5 — `[Tests/Refactor]` Retarget existing test suites
**Files**:
- `tests/test_stop_orders.py`
- `tests/test_live_trader.py`
- `tests/test_backtest_engine.py`
**Do**:
- Retarget tests that assumed instant quote lifting on the first tick after fill.
- Validate zero regressions across all targeted test gates.
**Verify**: Run all targeted test files.

### [ ] T6 — `[Review/Ship]` Verification and Station IV handoff
**Do**:
- Run all targeted test gates.
- Verify zero regressions and clean git status.
