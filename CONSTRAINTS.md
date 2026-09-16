# CONSTRAINTS.md — Issue #206: Live entry quotes must anchor to the live mid

Binding while `fix/live-entry-anchor-mid-206` is live. These are gates, not
suggestions: a violation blocks the PR.

## 1. Zero regressions

- `python -m pytest -q` stays green (~390 tests, ~50s).
- Gate files first: `tests/test_live_trader.py`, `tests/test_entry_timeout.py`,
  `tests/test_patient_band_preset.py`, `tests/test_fill_telemetry.py`,
  `tests/test_stop_orders.py`.
- Any existing test that asserts `0.48` / `0.47` opening quotes must still pass
  **unchanged**: those fixtures open at mid 0.50, where old and new formulas
  agree. If one of them fails, the formula is wrong — do not edit the test to
  match the code.

## 2. Anti-cheat

- No `@pytest.mark.skip`, `xfail`, deleted assertions, or loosened tolerances.
- No `# noqa` / `# type: ignore` added to make the diff pass a linter.
- Do not rewrite an existing assertion's expected value to match new output
  unless the SPEC explicitly says that value changed. It does not.

## 3. Scope discipline

- Code and tests touched: `strategy/live_trader.py` and `tests/test_live_trader.py`
  only. The Station II planning files — this file, `SPEC.md`, `tasks/plan.md`,
  `tasks/todo.md` — are rewritten per issue by the pipeline and ship in the same
  PR; they carry no behaviour and are not counted against this gate.
- One behavioural change: the round-0 anchor. Issues #207-#214 are separate
  branches; do not fold any of them in, even where the code sits three lines away.
- No new external dependency.
- No new module, class, or helper unless it removes duplication that already
  exists (see the proposed `_anchor_prices` in `tasks/plan.md`), and only with
  operator approval.

## 4. Live-money safety

- Price arithmetic stays inside `[0.01, 0.99]` after clamping — the existing
  `round(min(0.99, max(0.01, x)), 3)` form is reused verbatim, not reinvented.
- `resting_up + resting_down` must equal `1 - 2*offset` for any non-clamped mid.
  A pair that sums above 1.0 is a guaranteed loss and is a blocking defect.
- The round-0 branch must not start latching a price earlier than it does today.
  Recomputing every tick until an order exists is what makes the quote
  placement-time fresh; preserving that is part of the fix, not incidental.
- `mstate.mid` is read under no new lock: it is written under
  `self._book_reconcile_lock` at `:4365` and read plainly at `:4438` today. Match
  the existing pattern; do not introduce a second locking convention.

## 5. Evidence

"Tests pass" is not a report. The PR must show:
- the failing assertion from the red step (mid 0.60 producing 0.47), and
- the green run output for the gate files plus the full suite.
