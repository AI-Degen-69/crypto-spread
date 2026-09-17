# SPEC — Issue #232: Rule: fresh_start — the engine keeps no memory inside a window

Binding while `feat/fresh-start-rule-232` is live. Per-issue working file (`docs/git-workflow.md` §5)
— not an architecture document.

The rule itself is agreed: `docs/engine-decision-rules.md` §13 (`fresh_start`).
That file is the definition; this spec is the work that makes the code match it.

## Goal

Whenever the market is clean, the engine evaluates the window exactly as if it had just opened.
No record of what happened earlier in it, and no special path back in.

**Clean** means: no open position **and** no resting orders.
That is true in four situations, and the engine does not distinguish between them:
1. Nothing has been tried yet;
2. A pair merged successfully;
3. An unpaired leg was stopped out (or closed at dead zone);
4. The orders were cancelled.

**Trigger:** The market is clean and all three standing conditions hold:
- The mid is inside `quote_range` (rule §6)
- The window has not reached the dead zone (rule §8)
- Both legs are priceable (rule §5 / Invariant 0)

**Action:** Quote, exactly as a window that has just opened.

## What is Replaced / Deleted

1. `reentry_drift_band` — deleted.
2. `min_requote_remaining_sec` — deleted (the dead zone is the sole time gate).
3. `reentry_min_remaining_pct` — deleted.
4. `max_reentries_per_window` — deleted.
5. `_maybe_requote_after_merge` and `requote_round` — deleted (subsumed by standard clean-market evaluation).
6. `_maybe_reenter_drift_skipped` — already deleted in #228.
7. `reentry_require_pairable` & `reentry_stats` — removed from active logic and state dicts.

## Engine Divergence Fix (Backtest vs. Live)

- Previously in `backtest/engine.py`, both pair-completion (`filled_up and filled_down`) and stop-loss (`bb_up`/`bb_dn`) exited the tick loop via `break`.
- Under `fresh_start`, neither branch breaks out of the tick loop.
- When a pair completes or an exit triggers:
  - Account P&L / fees / telemetry for that round.
  - Reset round execution state (`orders_live = False`, `resting_up = None`, `resting_down = None`, `filled_up = False`, `filled_down = False`, `entry_price_up = None`, `entry_price_down = None`, `naked_since_elapsed = None`, `chased_leg = ""`, `max_up_drift = 0.0`, `max_down_drift = 0.0`, `reversal_seen_up = False`, `reversal_seen_down = False`).
  - Continue tick loop: if the market is clean and standing conditions hold, place fresh quotes at the current mid minus offset!
  - If a clean market enters the dead zone, no new quotes are placed.

## Acceptance Criteria

1. All re-entry knobs (`min_requote_remaining_sec`, `reentry_stats`, `reentry_require_pairable`) and special-path methods (`_maybe_requote_after_merge`, `requote_round`) are removed from `LiveTraderEngine` and `MarketLiveState`.
2. `backtest/engine.py` continues the tick loop after pair merge and after stop exit, subject only to standing conditions.
3. Dedicated parity test suite `tests/test_fresh_start_parity.py` runs multi-round windows (e.g. 2 pairs completed in one 15m window, or 1 stop followed by a completed pair) and verifies both engines place identical quotes and fills at identical ticks.
4. A window that becomes clean inside the dead zone is **never** re-entered in either engine.
5. Zero regressions across all targeted test gates.

## Out of Scope

- Parity test harness for remaining knobs (belongs to #214).
- Dead-zone empirical duration measurements (#222, #223).

