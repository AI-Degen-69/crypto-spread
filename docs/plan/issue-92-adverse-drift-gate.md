# Plan — Issue #92: resting entry orders cancelled seconds into a window by the adverse-drift gate

Source: https://github.com/<owner>/<repo>/issues/92 (labels: bug, ready-for-agent)

## Problem

The adverse-open drift gate cancels both resting entry bids within a second or two of a window
opening and latches the market out for the rest of that window, so live runs place orders and
immediately kill them.

Root causes identified in the issue:

- `initial_drift = abs(mid - 0.50)` and `is_adverse_open = initial_drift >= self.exit_thresh`
  (default `0.05`) are computed on **every** 1s tick of `_run_loop`, not once at window open,
  despite the variable name and the "Pre-entry drift check" comment.
- The first tick where the gate is true cancels both resting bids and sets
  `entry_cancelled_timeout = True`, reset only on window rollover — no re-entry when the mid
  returns toward 0.50.
- `mid` is derived from the live book with one-sided fallbacks: a missing bid or ask makes
  `up_mid` / `down_mid` collapse to the single available side, so a thin book at window open can
  report a >= 0.05 synthetic drift that does not reflect a real skew.
- `backtest/engine.py` has no equivalent adverse gate (only the entry timeout), so backtest and
  live disagree on which windows are entered.

`entry_timeout_pct` now defaults to `1.0` (full-window, commit `197bf34`), so the 10%-elapsed
timeout is not the cause. The drift gate added in `c504e09` (#63) is.

## Out of scope

The post-fill stop-loss logic, the entry-timeout feature itself, dashboard rendering of cancelled
orders, and multi-merge re-quoting (tracked in #89).

## Relevant files

- `strategy/live_trader.py:2665-2676` — `mid` computation with one-sided book fallbacks
- `strategy/live_trader.py:2707-2719` — window duration, `is_late_start`, `initial_drift`, `is_adverse_open`
- `strategy/live_trader.py:2720-2804` — cancellation block, live and paper branches, `entry_cancelled_timeout` latch
- `strategy/live_trader.py:2806-2815` — `DRIFT_SKIPPED` / `TIMEOUT_NO_FILL` status and `last_action` text
- `strategy/live_trader.py:2817-2825` — `can_place_entry` gating on `is_adverse_open`
- `strategy/live_trader.py:2510-2518` — 1s `_run_loop` cadence
- `strategy/live_trader.py:488-494` — `offset`, `exit_thresh`, `entry_timeout_pct` defaults
- `backtest/engine.py:252-276` — backtest entry gate with no adverse-drift equivalent
- `tests/test_entry_timeout.py` — existing entry-gate tests to extend
- `tests/test_live_trader.py` — adverse-drift tests added in #63

## Step 1. Snapshot window-open state and evaluate the drift gate once

Capture the opening mid for each market exactly once per window (on rollover / first usable tick)
into dedicated `mstate` fields, and evaluate `initial_drift` / `is_adverse_open` against that
snapshot instead of recomputing from the live mid on every 1s `_run_loop` tick. The snapshot
resets on window rollover alongside the existing per-window state.

Acceptance: the gate decision for a window is computed from the window-open snapshot, not the
current tick's mid; orders placed in a normal window survive past the first tick.

## Step 2. Require a two-sided book before trusting mid for the gate

Add a book-quality guard so the one-sided fallbacks in the `mid` computation cannot by themselves
fire the gate: the opening snapshot is only taken, and the gate only evaluated, when both bid and
ask are present on both the UP and DOWN books. Until a usable two-sided book is seen, the gate
stays undecided rather than defaulting to adverse.

Acceptance: a one-sided or empty book at window open does not cancel resting bids; the snapshot is
deferred until a two-sided book is available.

## Step 3. Unlatch the gate and report the measured drift

Stop `entry_cancelled_timeout` from permanently latching a market out on a transient book artifact:
allow re-quoting when the drift condition is no longer true and window time remains under the
default `entry_timeout_pct = 1.0`. When entry is genuinely skipped, `last_action` must state the
measured drift and the threshold that rejected it, and `DRIFT_SKIPPED` / `TIMEOUT_NO_FILL` status
must distinguish the two causes.

Acceptance: a market skipped by a transient artifact re-quotes when the condition clears; a genuine
skip reports the measured drift and the threshold in `last_action`.

## Step 4. Restore live/backtest parity for the entry gate

Port the corrected adverse-open drift gate into `backtest/engine.py:252-276`, which currently
applies only the entry timeout. Both engines must share the same gate semantics — same threshold
source (`exit_thresh`), same window-open evaluation, same two-sided-book requirement — so backtest
and live agree on which windows are entered.

Acceptance: backtest and live enter the same set of windows for identical book input; the gate
threshold and evaluation point are shared, not duplicated with drift.

## Step 5. Regression tests and full-suite verification

Extend `tests/test_entry_timeout.py` and `tests/test_live_trader.py` with regression coverage for
the three failure modes named in the issue, plus a backtest-parity test for step 4.

Acceptance: new tests cover one-sided book at open does not cancel, genuine `>= exit_thresh` open
drift does cancel, and orders survive past the first tick in a normal window;
`python -m pytest tests/ -q` passes.
