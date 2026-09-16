# CONSTRAINTS — Issue #225

Binding while `fix/entry-anchor-repriced-two-sided-225` is live.

## 1. Test gates

```
python -m pytest tests/test_backtest_engine.py tests/test_entry_timeout.py tests/test_entry_anchor_parity.py -q
python -m pytest tests/test_osc_dash_integration.py tests/test_sweep_backtest.py -q
```

The full suite is CI's job (`docs/git-workflow.md` §4).

## 2. A changed expectation needs a cause, not an adjustment

This issue changes what the backtest fills, so test expectations will move. Every one that does
must be explained by the rule, in the commit body or the test's own docstring. "The number is
different now" is not an explanation, and quietly re-baselining a number is the failure mode
this constraint exists to catch.

## 3. Fixtures describe real books

A binary pair's DOWN leg is the complement of its UP leg. Several fixtures centred DOWN on an
unrelated `down_ask`, which nothing noticed while the anchor read the one-sided `s["mid"]`. Where
a fixture is corrected, it is corrected to a coherent book — not to whatever makes the assertion
pass. A test whose subject **is** a leg-imbalanced book opts out explicitly and says why.

## 4. No expected price on both sides of a parity assertion

A parity test compares the two engines against each other. Writing `0.51` into the live
assertion *and* the backtest assertion tests neither engine against the other; the backtest side
reads the live engine's own resting price.

## 5. Nothing else moves

No change to gates, thresholds, the fill rule, or `BacktestParams` defaults. The one behaviour
change is where the anchor comes from and when it stops being recomputed.

## 6. Anti-cheat

No `skip`, `xfail`, deleted assertion or loosened tolerance. A test that can no longer be reached
through `_simulate_window` is moved to the function it covers and says so — it is not deleted.

## 7. No new dependencies
