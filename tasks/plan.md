# Plan — Issue #232: Rule: fresh_start — the engine keeps no memory inside a window

**Size**: Standard — both engines (`strategy/live_trader.py`, `backtest/engine.py`),
dedicated parity test (`tests/test_fresh_start_parity.py`), and test suite retargeting.
**Type**: Code.
**Stack**: Python 3.12, FastAPI, pytest. Targeted tests only locally; CI is the merge gate (`AGENTS.md`).
**Spec**: `SPEC.md`. **Gates**: `CONSTRAINTS.md`. **Rule of record**: `docs/engine-decision-rules.md` §13.
**Interview**: Requirements were fully clear from the issue and §13 — interview-me was skipped.

## Tasks

### [x] T0 — Branch + spec lock (done in Station II)
Branch `feat/fresh-start-rule-232` off `master`. `SPEC.md`, `CONSTRAINTS.md`,
`tasks/plan.md`, `tasks/todo.md` written.

### [x] T1 — `[Backend/Clean]` Remove legacy re-entry knobs and special-paths from `strategy/live_trader.py`
**Files**: `strategy/live_trader.py`.
**Do**:
- Remove `min_requote_remaining_sec`, `DEFAULT_MIN_REQUOTE_REMAINING_SEC`, and related config handling in `__init__`, `update_config`, `get_state`.
- Remove `_maybe_requote_after_merge` and `_finalize_requote_telemetry`.
- Remove `requote_round`, `reentry_stats`, `reentry_require_pairable` from `MarketLiveState` and `LiveTraderEngine`.
- Clean up references in `server/osc_dash.py` if any still read these fields.
**Skill**: `incremental-implementation`.
**Verify**: `python -m pytest tests/test_live_trader.py -q`.

### [x] T2 — `[Backend/Logic]` Implement `fresh_start` in `strategy/live_trader.py`
**Files**: `strategy/live_trader.py`.
**Do**:
- Update `_update_market_strategy`:
  - When a pair merge completes: reset round order/fill state (`filled_up = False`, `filled_down = False`, etc.), increment `pairs_count += 1`.
  - When a stop exit completes: reset round order/fill state, increment `stops_count += 1`.
  - Clean condition: `is_clean = not filled_up and not filled_down and not order_id_up and not order_id_down`.
  - Standing conditions: `not in_dead_zone`, `not range_hold`, `not no_book_hold`, `not entry_delay_pending`.
  - Do NOT latch window shut on `pair_captured` or `exit_taken`; allow quoting fresh whenever clean and standing conditions hold!
  - In dead zone, clean market stays idle (no quote placed).
**Skill**: `test-driven-development`.
**Verify**: `python -m pytest tests/test_live_trader.py -q`.

### [x] T3 — `[Backend/Logic]` Implement `fresh_start` in `backtest/engine.py`
**Files**: `backtest/engine.py`.
**Do**:
- In `_simulate_window`:
  - Remove `break` from pair completion branch (`filled_up and filled_down`). Accumulate P&L, set `pair_captured = True`, and reset round state (`orders_live = False`, `resting_up = None`, `resting_down = None`, `filled_up = False`, `filled_down = False`, `entry_price_up = None`, `entry_price_down = None`, `naked_since_elapsed = None`, `chased_leg = ""`, `max_up_drift = 0.0`, `max_down_drift = 0.0`, `reversal_seen_up = False`, `reversal_seen_down = False`).
  - Remove `break` from stop-loss exit branch (`bb_up`/`bb_dn`). Accumulate P&L and fees, set `exit_taken = True`, and reset round state identically.
  - Continue tick loop: next ticks quote fresh at `anchor_mid - offset` whenever clean and standing conditions hold.
  - In dead zone, clean market does not quote.
  - Add `pairs_count: int = 0` and `stops_count: int = 0` to `WindowResult` and aggregate results.
**Skill**: `test-driven-development`.
**Verify**: `python -m pytest tests/test_backtest_engine.py -q`.

### [x] T4 — `[Test/Parity]` Dedicated behavioral parity test suite `tests/test_fresh_start_parity.py`
**Files**: `tests/test_fresh_start_parity.py`.
**Do**:
- Parity 1: Multi-round window completing two pairs at identical prices and ticks in both engines.
- Parity 2: Multi-round window with a stop-loss exit followed by a fresh entry and pair merge.
- Parity 3: Window clean at or inside dead zone does NOT re-enter in either engine.
- Parity 4: Window clean outside quotable range holds until range returns.
**Skill**: `test-driven-development`.
**Verify**: `python -m pytest tests/test_fresh_start_parity.py -q`.

### [x] T5 — `[Tests/Refactor]` Retarget existing tests
**Files**:
- `tests/test_live_trader.py`
- `tests/test_backtest_engine.py`
- `tests/test_osc_dash_integration.py`
**Do**:
- Retarget tests that tested `min_requote_remaining_sec` or `requote_round` to assert `fresh_start` and dead-zone gating.
- Verify zero regressions across all targeted test suites.
**Verify**: Run targeted test gates.

### [x] T6 — `[Review/Ship]` Verification and Station IV handoff
**Do**:
- Run all targeted test gates.
- Verify zero regressions and clean git status.

