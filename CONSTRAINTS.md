# CONSTRAINTS.md — Issue #209: stop loss must be measured from the entry price, not from 0.50

Binding while working on Issue #209. These are gates, not suggestions: a violation blocks the PR.

## 1. Zero Regressions

- Targeted test suites must pass:
  - `python -m pytest tests/test_live_trader.py -q`
  - `python -m pytest tests/test_backtest_engine.py -q`
- NEVER run the full test suite locally (`python -m pytest -q` is strictly forbidden per repo AGENTS.md; full suite is gated by CI in GitHub Actions).
- All 141 existing tests in `test_live_trader.py` and all 136 tests in `test_backtest_engine.py` must remain green.

## 2. Anti-Cheat & Quality Gates

- No `@pytest.mark.skip`, `xfail`, deleted assertions, or loosened tolerances.
- No `# noqa` / `# type: ignore` added to mask typing or linting errors.
- Never hardcode 0.50 as the stop loss anchor for open positions.

## 3. Scope Discipline

- Files touched:
  - `strategy/live_trader.py` (measure adverse excursion & reversal from entry price per leg)
  - `backtest/engine.py` (measure adverse excursion & reversal from entry price per leg, while keeping window max_up/max_down metrics intact for oscillation stats)
  - `tests/test_live_trader.py` (add regression tests for entry-anchored stop loss and reversal)
  - `tests/test_backtest_engine.py` (add parity & regression tests for entry-anchored stop loss and reversal)
  - Station II planning files: `CONSTRAINTS.md`, `SPEC.md`, `tasks/plan.md`, `tasks/todo.md`
- Out of scope:
  - Entry gate / decided market fixes (reserved for #208)
  - Leg chase timing adjustments (reserved for #210)
  - Naked leg timeout percentage calculation (reserved for #211)
  - Re-entry mid updates (reserved for #212)
  - Entry band anchoring (reserved for #213)
  - General parity harness (reserved for #214)
  - No new external dependencies

## 4. Mathematical & Engine Correctness

- For UP positions (`filled_up and not filled_down`):
  - Reference price `entry_up = fill_price_up if fill_price_up is not None else resting_up`.
  - Adverse excursion is `max(0.0, entry_up - mid)` when mid drops below entry.
  - Stop loss triggers when `max_down_drift >= naked_thr` (or `exit_thresh`).
  - Reversal detection triggers when `max_down_drift >= naked_thr` and `(entry_up - mid) < exit_reversal`.
- For DOWN positions (`filled_down and not filled_up`):
  - Reference price `entry_down = fill_price_down if fill_price_down is not None else resting_down`.
  - Implied mid entry is `1.0 - entry_down`.
  - Adverse excursion is `max(0.0, mid - (1.0 - entry_down))` when mid rallies above implied entry.
  - Stop loss triggers when `max_up_drift >= naked_thr` (or `exit_thresh`).
  - Reversal detection triggers when `max_up_drift >= naked_thr` and `(mid - (1.0 - entry_down)) < exit_reversal`.
- While 0 legs are filled (resting orders):
  - Position adverse drift is 0.0 — no open position exists to stop out.
- Regression test requirement:
  - A leg filled at 0.45 with `exit_thresh = 0.05` must NOT trigger stop loss until mid reaches 0.40 (UP) or 0.60 (DOWN).
