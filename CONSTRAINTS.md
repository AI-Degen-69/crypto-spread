# CONSTRAINTS — Issue #224

Binding while `fix/window-clock-and-no-invented-numbers-224` is live.

## 1. Test gates

Targeted files first, then the full suite in CI:

```
python -m pytest tests/test_live_trader.py tests/test_entry_timeout.py tests/test_stop_orders.py -q
python -m pytest tests/test_backtest_engine.py tests/test_sweep_backtest.py -q
```

The full suite is CI's job (`docs/git-workflow.md` §4). Do not run `python -m pytest -q` locally
as a gate; it is slow and CI already does it on push.

## 2. No new fabricated value may replace a removed one

Removing `0.40` and `0.50` is the point. Replacing either with a different constant, a clamp to
a plausible range, or a value carried forward from an earlier tick fails this issue outright.
The only permitted outcomes are: a value resolved from the book, or no action.

## 3. Skips are visible

A window skipped for a missing clock logs once at a level the operator sees, and — in the
backtest — is reported as a distinct outcome, not folded into `no_data` or a normal no-fill.
A silent skip trades one invisible failure for another.

## 4. One clock helper, not a repeated expression

The window length and elapsed time are computed in exactly one place per engine. Four copies of
`end_ts - start_ts` with four different fallbacks is how the current divergence happened.

## 5. No behaviour change beyond the invariants

Thresholds, offsets, fill rules, gate ordering and defaults are untouched. Any test that changes
its expected outcome must be explained by a clock that was previously wrong, and that
explanation goes in the commit body.

## 6. Anti-cheat

No `skip`, `xfail`, deleted assertion, loosened tolerance or suppressed warning to make a test
pass. If a test fails because the clock is now correct, fix the test's fixture and say so.

## 7. No new dependencies

Standard library and what is already imported.
