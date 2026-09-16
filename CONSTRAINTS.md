# CONSTRAINTS.md — Issue #214: the engine parity harness

Binding while working on Issue #214. These are gates, not suggestions: a violation blocks the PR.

## 1. Zero Regressions

- Targeted test suites must pass before every commit:
  - `python -m pytest tests/test_engine_parity.py -q` (new, this issue)
  - `python -m pytest tests/test_backtest_engine.py -q`
  - `python -m pytest tests/test_live_trader.py -q`
  - `python -m pytest tests/test_entry_timeout.py -q`
- NEVER run the full test suite locally (`python -m pytest -q` is strictly forbidden per repo
  `AGENTS.md` §Testing & Fast Iteration Policy; the full suite is gated by CI in GitHub Actions).

## 2. No behaviour change — no exceptions

- **This issue changes zero strategy behaviour.** No edit to the decision logic of
  `strategy/live_trader.py` or `backtest/engine.py`. The rule changes agreed on 2026-09-16 are
  issues #224-#233 and land there, one reviewable change at a time. That separation is the
  whole point of splitting them out: ten behaviour changes in one PR is exactly the condition
  under which the divergences being fixed went unnoticed.
- A divergence the harness discovers is **recorded**, not fixed: add it as
  `xfail(strict=True)` with the issue number in the reason, and file the issue.

## 3. The harness must not encode the rules

- The harness compares the two engines against **each other**, never against a hard-coded
  expected value. A scenario that asserts "the quote is 0.47" pins today's tuning; a scenario
  that asserts "both engines quote the same thing" survives every rule change in #224-#233.
- No model or knob name may be hard-coded in a way that makes a rule issue have to edit the
  harness to land. The harness reads the engines' current configuration.

## 4. Anti-Cheat

- No `@pytest.mark.skip`, no deleted assertions, no `pytest.approx` on a field that is exactly
  comparable, no `# noqa` / `# type: ignore` to silence a real finding.
- A parity failure is never resolved by loosening the comparison.

## 5. Dependencies

- No new external dependencies. `dataclasses`, `inspect`, `unittest.mock` and `pytest` cover
  everything this needs.

## 6. Performance and data

- `tests/test_engine_parity.py` must run in **under 5 seconds**.
- No test may read `run/ticks/*.jsonl` (the 700MB capture) or anything under `runs/`.
  Synthetic snaps only, per the precedent in `tests/test_replay_shadow_check.py`.

## 7. Scope discipline

- May create: `tests/test_engine_parity.py`, and a helper module for it if it outgrows one file.
- May modify: `SPEC.md`, `CONSTRAINTS.md`, `tasks/*`, and `AGENTS.md` (one pointer line).
- Anything else needs an explicit operator decision.
