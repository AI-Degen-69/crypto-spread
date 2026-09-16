# Plan — Issue #209: Stop loss is measured from 0.50, not from the entry price

- **Issue:** #209 (`ready-for-agent`, assigned)
- **Branch:** `fix/stop-loss-from-entry-price-209`
- **Size tier:** Standard — 4 files (`strategy/live_trader.py`, `backtest/engine.py`, `tests/test_live_trader.py`, `tests/test_backtest_engine.py`).
- **Task type:** Debug & Code (Risk Management & Trading Engine Correctness).
- **Stack:** Python 3.12, FastAPI, pytest.
- **Skills routed:** `debugging-and-error-recovery`, `test-driven-development`, `api-and-interface-design`, `planning-and-task-breakdown`.

## Root Cause

`strategy/live_trader.py:4455-4469` and `backtest/engine.py:794-816` measure adverse price excursion against a hardcoded base of `0.50` (`mid - 0.50` / `0.50 - mid`).
When a leg fills away from 0.50 (e.g. UP filled at 0.45), the position carries the distance from 0.50 as immediate artificial drift from tick 1. With `exit_thresh = 0.05`, the leg is immediately stopped out with zero actual market move against the entry price.

## Tasks

### T1 — Failing Tests for Live Engine Entry-Anchored Stop Loss `[Debug/Test]`
- In `tests/test_live_trader.py`:
  - Add `test_stop_loss_anchored_to_fill_price_up`:
    - UP leg filled at 0.45 with `exit_thresh = 0.05`.
    - Mid moves: 0.45 -> 0.43 -> 0.41 -> 0.40 -> 0.39.
    - Assert no stop loss triggers at 0.45, 0.43, or 0.41 (in old code it triggers immediately).
    - Assert stop loss triggers only at <= 0.40.
  - Add `test_stop_loss_anchored_to_fill_price_down`:
    - DOWN leg filled at 0.45 (implied mid 0.55) with `exit_thresh = 0.05`.
    - Mid moves: 0.55 -> 0.57 -> 0.59 -> 0.60 -> 0.61.
    - Assert no stop loss triggers until mid >= 0.60.
  - Add `test_reversal_anchored_to_entry_price`:
    - Adverse excursion occurs, mid bounces back to within `exit_reversal` of entry.
    - Assert reversal flag arms and suppresses the exit.
- **Target File:** `tests/test_live_trader.py`
- **Verification:** Run `python -m pytest tests/test_live_trader.py -q -k "anchored"` (fails before implementation).

### T2 — Implement Entry-Anchored Drift & Reversal in `strategy/live_trader.py` `[Backend/Logic]`
- In `strategy/live_trader.py`:
  - When `mstate.filled_up and not mstate.filled_down`:
    - Reference: `entry_up = mstate.fill_price_up if mstate.fill_price_up is not None else mstate.resting_up`.
    - If `mstate.mid is not None` and `entry_up is not None`:
      - `mstate.max_down_drift = max(mstate.max_down_drift, max(0.0, entry_up - mstate.mid))`.
      - Check reversal: `if mstate.max_down_drift >= thresh and (entry_up - mstate.mid) < self.exit_reversal: mstate.reversal_seen_down = True`.
  - When `mstate.filled_down and not mstate.filled_up`:
    - Reference: `entry_dn = mstate.fill_price_down if mstate.fill_price_down is not None else mstate.resting_down`.
    - If `mstate.mid is not None` and `entry_dn is not None`:
      - `mstate.max_up_drift = max(mstate.max_up_drift, max(0.0, mstate.mid - (1.0 - entry_dn)))`.
      - Check reversal: `if mstate.max_up_drift >= thresh and (mstate.mid - (1.0 - entry_dn)) < self.exit_reversal: mstate.reversal_seen_up = True`.
  - While neither leg is filled, position drift remains 0.0.
- **Target File:** `strategy/live_trader.py`
- **Verification:** T1 tests turn green; all existing tests in `test_live_trader.py` pass.

### T3 — Failing Tests for Backtest Replay Entry-Anchored Stop Loss `[Debug/Test]`
- In `tests/test_backtest_engine.py`:
  - Add unit tests verifying that `simulate_window` / `replay`:
    - When UP fills at 0.45 with `exit_thresh = 0.05`, does NOT exit at mid 0.45 or 0.42.
    - Exits at mid <= 0.40.
    - Keeps `WindowResult.max_up` and `max_down` measuring window range from 0.50 for classification.
- **Target File:** `tests/test_backtest_engine.py`
- **Verification:** Run `python -m pytest tests/test_backtest_engine.py -q -k "anchored"` (fails before implementation).

### T4 — Implement Entry-Anchored Stop Loss & Parity in `backtest/engine.py` `[Backend/Logic]`
- In `backtest/engine.py`:
  - Preserve `max_up = max(mids) - 0.50` and `max_down = 0.50 - min(mids)` for `WindowResult` and `_classify`.
  - Introduce dedicated position excursion tracking:
    - `adverse_drift_up: float = 0.0`
    - `adverse_drift_down: float = 0.0`
  - When holding UP alone (`filled_up and not filled_down`):
    - Track `entry = entry_price_up or resting_up`.
    - Update `adverse_drift_up = max(adverse_drift_up, max(0.0, entry - mid))`.
    - Check reversal against `(entry - mid) < params.exit_reversal`.
  - When holding DOWN alone (`filled_down and not filled_up`):
    - Track `entry = entry_price_down or resting_down`.
    - Update `adverse_drift_down = max(adverse_drift_down, max(0.0, mid - (1.0 - entry)))`.
    - Check reversal against `(mid - (1.0 - entry)) < params.exit_reversal`.
  - Use `adverse_drift_up` / `adverse_drift_down` in stop loss exit conditions.
- **Target File:** `backtest/engine.py`
- **Verification:** T3 tests turn green; all tests in `test_backtest_engine.py` pass.

### T5 — Targeted Verification & Quality Gate `[Quality Gate]`
- Run targeted test suites:
  - `python -m pytest tests/test_live_trader.py -q`
  - `python -m pytest tests/test_backtest_engine.py -q`
  - `python -m pytest tests/test_entry_timeout.py -q`
- Ensure zero regressions and strict adherence to `CONSTRAINTS.md`.
- **Target Files:** `strategy/live_trader.py`, `backtest/engine.py`, `tests/test_live_trader.py`, `tests/test_backtest_engine.py`
- **Verification:** All tests pass with zero warnings/errors.
