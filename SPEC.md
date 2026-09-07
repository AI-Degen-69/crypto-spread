# SPEC — Issue #95: re-entry into a drift-skipped window when the mid reverts

## Goal
A window skipped by the adverse-open drift gate (`status = "DRIFT_SKIPPED"`) is
currently dead for its whole duration. Allow one bounded re-entry per window: when
the live mid has reverted to within a tight configurable band of 0.50 and enough
window time remains to pair two legs, the engine quotes that market again. Live
(`strategy/live_trader.py`) and backtest (`backtest/engine.py`) must agree on which
windows are entered.

## Status (verified Sep 7, 2026)

- Live path (Behaviors 1-3 below) **implemented** in `c0f7264`; 8 tests in
  `tests/test_live_trader.py:1736-1926`, all green.
- `reentry_drift_band` params/config API **implemented** in `750fd2e`
  (covered by `tests/test_osc_dash_integration.py:1204`).
- Backtest parity (Behavior 4 below) **implemented** — `BacktestParams` fields
  + `__post_init__` validation (`backtest/engine.py:141-168`), the
  `adverse_skipped` split (`:314-315`, `:378`) and the per-tick re-entry rule
  (`:381-401`); 6 parity tests in `tests/test_entry_timeout.py:663-734`.

## Background (current behavior)
- The gate is a one-shot snapshot taken from the first two-sided tick
  (`strategy/live_trader.py:3145-3150`), stored as `open_mid` / `open_drift` /
  `adverse_open` / `open_gate_evaluated`.
- On `adverse_open`, the cancellation block (`live_trader.py:3168-3267`) sets the
  shared latch `entry_cancelled_timeout = True` and `status = "DRIFT_SKIPPED"`.
- `can_place_entry` (`live_trader.py:3283-3290`) then fails on two independent
  terms — `entry_cancelled_timeout` and `is_adverse_open` — until
  `_handle_window_rollover()` clears them (`live_trader.py:3577`; re-entry fields
  reset at `:3691-3693`).
- The same latch is shared with the entry-timeout cancel (`is_late_start`) and with
  the issue #96 late-start skip (`late_start_skip`). Only `mstate.adverse_open`
  distinguishes a drift skip from the other two.
- The backtest (`backtest/engine.py:344-346`) sets the *same* `entry_cancelled`
  flag for the adverse-open gate, the entry timeout and the late start, so it
  cannot currently tell the three reasons apart at all.

## Behavior to implement
1. **Re-entry condition (live).** On a tick where all of the following hold, the
   window is re-entered:
   - `mstate.adverse_open is True` (drift skip, never a timeout/late-start cancel)
   - `mstate.entry_cancelled_timeout is True` (the skip already applied)
   - `mstate.late_start_skip is False` and `is_late_start is False`
   - no fills yet, not `pair_captured`, not `exit_taken`
   - both legs quote two sides (same `book_two_sided` precondition as the gate)
   - `abs(mid - 0.50) <= self.reentry_drift_band`
   - `remaining_sec >= self.min_requote_remaining_sec`
   - `mstate.reentry_count < self.max_reentries_per_window`
2. **Re-entry effect.** Under `_engine_lock`: clear `entry_cancelled_timeout`,
   clear `adverse_open`, reset `order_status_up` / `order_status_down` to `"NONE"`,
   clear `order_id_up` / `order_id_down` and their times, increment
   `reentry_count`, record `reentry_mid` / `reentry_drift`, set
   `status = "QUOTING"` and a `last_action` naming the original open and the
   reverted drift. Existing `cancelled_orders` rows are kept as history.
3. **Snapshot preservation.** `open_mid` / `open_drift` / `open_gate_evaluated` are
   **not** reset. Re-entry is itself the new, stricter evaluation of the live mid
   (band 0.015 vs `exit_thresh` 0.05); re-snapshotting would clobber the
   telemetry the acceptance criteria require to stay visible, and the gate is by
   design an *open* gate — post-entry protection is the exit/stop path.
4. **Backtest parity.** `_simulate_window()` gains a separate `adverse_skipped`
   flag alongside `entry_cancelled`, set only by the adverse-open gate, and applies
   the same re-entry rule per tick (`remaining = duration - elapsed`). Timeout and
   late-start cancels never set it, so they are never re-entered.

## New parameters (live + backtest, identical defaults)
| Name | Default | Meaning |
|---|---|---|
| `reentry_drift_band` | `0.015` | max `abs(mid - 0.50)` allowed for re-entry |
| `reentry_min_remaining_pct` | `0.30` | re-entry needs this fraction of the window left; the effective gate is the tighter of it and the knob below (90s on 5m, 270s on 15m) |
| `min_requote_remaining_sec` | `300.0` | min window seconds left; the shared knob #89 adopts |
| `max_reentries_per_window` | `1` | per-window re-entry cap |

`reentry_drift_band` is exposed in the params payload (`live_trader.py:1749-1758`)
and settable through `update_config()` plus the `/api/live/config` payload.

### Assumptions (deviations from the issue text, deliberate)
- **`reentry_drift_band` is a new knob, not a reuse of `exit_reversal`.** The issue
  notes live `exit_reversal = 0.015` vs backtest `0.02`. Unifying `exit_reversal`
  itself would change exit behavior and existing backtest expectations, which is
  out of scope; a dedicated knob defaulting to `0.015` in both engines meets the
  live/backtest parity requirement without touching exit semantics.
- **`min_requote_remaining_sec` is issue #89's knob at its shipped `300.0`
  default, not a new 60s one.** #89 landed this attribute for post-merge
  re-quoting while this issue was in progress; the acceptance criteria call for
  one shared knob, so re-entry reads #89's and does not redefine it. The
  consequence is deliberate and tested
  (`test_reentry_blocked_by_default_requote_gate_on_5m`): a 5m window is 300s
  long, so at the default **no 5m market can ever re-enter** — only 15m windows,
  and only in their first half. Two of the four reverting markets in the issue's
  evidence table are 5m, so an operator who wants those recovered must lower the
  knob (60s is the smallest value in which two legs can realistically pair).
  Raising it back is the one-line `update_config(min_requote_remaining_sec=...)`
  call, which also moves #89's post-merge re-quoting.
- **Superseded: the gate now scales with the window.** An absolute floor cannot
  mean the same thing on a 5m and a 15m market, and at 300s it blocked exactly
  the 5m markets the evidence table shows reverting. Re-entry requires
  `min(min_requote_remaining_sec, reentry_min_remaining_pct * window)` -- 90s on
  5m, 270s on 15m. The shared knob stays the ceiling, so this can only tighten
  #89's value and never loosens the post-merge re-quoting it also governs.
- **The static `0.50 - offset` anchor stays.** #89 has not landed. The tight
  default band is the mitigation, and the limitation is documented in code.

## Out of scope
- #89 dynamic mid-anchored quoting and post-merge re-quoting.
- Changing `exit_reversal`, `exit_thresh`, PnL math, fill detection or rollover.
- Re-entering windows cancelled by the entry timeout or the #96 late-start guard.

## Acceptance criteria
See issue #95; each maps to a test in `tests/test_live_trader.py` (live, landed)
and `tests/test_entry_timeout.py` (backtest parity, landed at `:663-734`).
`python -m pytest -q` passes (334 tests collected; the four targeted gates are
189 passed).
