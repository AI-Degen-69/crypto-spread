# CONSTRAINTS.md — Issue #207: An unpriceable leg is substituted with 0.50 instead of skipping the window

Binding while working on Issue #207. These are gates, not suggestions: a violation blocks the PR.

## 1. Zero regressions

- Targeted test suites must pass:
  - `python -m pytest tests/test_book_math.py -q`
  - `python -m pytest tests/test_live_trader.py -q`
- NEVER run the full test suite locally (`python -m pytest -q` is strictly forbidden per repo AGENTS.md; CI gates full suite).
- All existing live_trader tests must remain green.

## 2. Anti-cheat

- No `@pytest.mark.skip`, `xfail`, deleted assertions, or loosened tolerances.
- No `# noqa` / `# type: ignore` added to mask bugs.
- Do not keep dead code or fallback 0.50 defaults for unpriceable books.

## 3. Scope discipline

- Files touched:
  - `strategy/live_trader.py` (call `two_sided_mid`, guard `mstate.mid is not None` in quoting, drift, and skip status)
  - `server/osc_dash.py` (recognize `NO_BOOK_SKIPPED` and `NO_BOOK` badges/labels)
  - `tests/test_live_trader.py` (update monkeypatch in lock test, add new tests for unpriceable book skip & drift safety)
  - Station II planning files: `CONSTRAINTS.md`, `SPEC.md`, `tasks/plan.md`, `tasks/todo.md`
- Out of scope:
  - Premature changes to other open issues (#208, #209, #210, #211, #212, #213, #214)
  - Modifying backtest engine (backtest parity will be handled under #214)
  - No new external dependencies

## 4. Mathematical & Engine Correctness

- `mstate.mid` must be `None` when either leg cannot be priced.
- Zero drift tracking must occur when `mstate.mid is None` — unpriceable books must not be treated as "flat 0.50".
- Quote placement must require `mstate.mid is not None` — the engine must never quote blind into an unpriceable market.
- Windows that never formed a two-sided book before timeout must be marked `NO_BOOK_SKIPPED` rather than `TIMEOUT_NO_FILL`.
