# Plan — Issue #207: An unpriceable leg is substituted with 0.50 instead of skipping the window

- **Issue:** #207 (`ready-for-agent`, assigned)
- **Branch:** `fix/skip-unpriceable-leg-207`
- **Size tier:** Small — 3 files (`strategy/live_trader.py`, `server/osc_dash.py`, `tests/test_live_trader.py`).
- **Task type:** Debug & Code (Trading Engine Correctness & Safety).
- **Stack:** Python 3.12, FastAPI, pytest.
- **Skills routed:** `test-driven-development`, `api-and-interface-design`, `debugging-and-error-recovery`.

## Root Cause

`strategy/live_trader.py:2175` and `:4369` call `book_math.two_sided_mid_with_default`, which substitutes a default of `0.50` when a book is empty or unpriceable.
This fabricated 0.50 causes `adverse_open` and `entry_band` gates to pass unconditionally, corrupts drift tracking by assuming the market is flat at 0.50, allows resting orders to quote blind, and displays a misleading `$0.50` price to the operator.

## Tasks

### T1 — Red Tests for Unpriceable Book Handling `[Debug/Test]`
- In `tests/test_live_trader.py`:
  - Update `test_reconcile_and_strategy_lock_prevents_torn_mid` monkeypatch to target `two_sided_mid`.
  - Add `test_unpriceable_book_yields_none_mid_and_no_drift`: verifies that with an unpriceable book (e.g. missing bid or ask), `mstate.mid` is `None` and `max_up_drift`/`max_down_drift` remain 0.0 without drifting to 0.50.
  - Add `test_unpriceable_book_prevents_quoting`: verifies that `can_place_entry` refuses quoting when `mstate.mid is None`.
  - Add `test_unpriceable_book_timeout_sets_no_book_skipped`: verifies that a window timing out without a priced book receives status `NO_BOOK_SKIPPED` instead of `TIMEOUT_NO_FILL`.
- **Target File:** `tests/test_live_trader.py`
- **Verification:** Run `python -m pytest tests/test_live_trader.py -q -k "unpriceable"` (red before fix).

### T2 — Implement Honest Mid & Safety Guards in `strategy/live_trader.py` `[Backend/Logic]`
- Replace `book_math.two_sided_mid_with_default` with `book_math.two_sided_mid` at `:2175` and `:4369`.
- In `_update_market_strategy`:
  - When `mstate.mid is None`, do not compute anchor quotes or resting prices against 0.50.
  - Guard drift tracking (`if mstate.mid is not None:`) so unpriceable books do not update drift or trigger reversals.
  - Add `and mstate.mid is not None` to `can_place_entry`.
  - In timeout/cancel handling, set `mstate.status = "NO_BOOK_SKIPPED"` when window timed out without ever receiving a priced book.
- **Target File:** `strategy/live_trader.py`
- **Verification:** T1 tests turn green; all tests in `test_live_trader.py` pass.

### T3 — UI Telemetry & Cockpit Badges in `server/osc_dash.py` `[Design/UI]`
- Add `'NO_BOOK': 'No Book'` and `'NO_BOOK_SKIPPED': 'No Book Skipped'` to `OT_STATUS_LABELS`.
- Support `'NO_BOOK_SKIPPED'` in dimmed box styling (`bidsCancelled`) and `cancelReason` tooltip.
- **Target File:** `server/osc_dash.py`
- **Verification:** Run `python -m pytest tests/test_osc_dash_integration.py -q`.

### T4 — Targeted Verification & Quality Gate `[Backend/Logic]`
- Run targeted test suites:
  - `python -m pytest tests/test_book_math.py -q`
  - `python -m pytest tests/test_live_trader.py -q`
  - `python -m pytest tests/test_osc_dash_integration.py -q`
- Verify zero regressions and strict adherence to `CONSTRAINTS.md`.
- **Target Files:** `strategy/live_trader.py`, `server/osc_dash.py`, `tests/test_live_trader.py`
- **Verification:** All targeted tests pass cleanly.

