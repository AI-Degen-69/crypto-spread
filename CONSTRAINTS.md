# CONSTRAINTS.md — Quality Bar & Boundaries for Issue #459

## 1. Zero Regressions
- `python -m pytest tests/test_live_trader.py -q` must pass — it holds both the new regression tests and the engine's existing stop/dead-zone behavior.
- `python -m pytest tests/test_dead_zone_parity.py -q` must pass — the live/backtest dead-zone parity is a standing contract (#229) and this fix lands inside that branch.
- `python -m pytest tests/test_backtest_engine.py tests/test_docstrings.py -q` must pass — the mirrored dead-zone behavior and the docstring gate that covers any new function.
- Green at the final verification gate is the PR condition. A red test inside Task 1 is the intended TDD step (repro first), never a commit that reaches the PR.

## 2. Performance
- No new I/O, network call, or allocation in the tick path. The fix swaps one malformed call for a correctly-shaped one inside a branch that already exists; markets outside the dead zone must do identical work to before.

## 3. Anti-Cheat
- No skipping, `xfail`-ing, or deleting assertions; the new tests must not be weakened to pass.
- No lint/type suppressions and no `except Exception` — `except RuntimeError` is the only catch permitted, matching the stop-exit precedent at `strategy/live_trader.py:1668`.
- The regression test must genuinely fail before the fix (`TypeError`) and pass after it. If it passes before the fix, the repro is wrong and must be rebuilt.

## 4. Boundaries — do not touch
- `_resolve_exit_bid` itself, its resolution ladder, and its `RuntimeError` semantics.
- `_execute_stop_exit`, `_tick_all_markets`, `_run_loop`, and their exception handling.
- The backtest engine's parity path (`backtest/engine.py`), `naked_leg_at_expiry`, and every config knob.
- No new dependencies; stdlib and existing imports only.

## 5. Invariants
- **Invariant 0 (#224):** never invent a price. A held exit is strictly better than an exit at a fabricated mark.
- `naked_leg_at_expiry="hold"` behavior is unchanged.
- One exit per naked leg (the #451 contract) is preserved.
