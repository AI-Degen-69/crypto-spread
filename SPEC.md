# SPEC — Issue #229: `dead_zone` governs the end of the window

Binding while `feat/dead-zone-229` is live. Per-issue working file (`docs/git-workflow.md` §5)
— not an architecture document.

The rule itself is agreed: `docs/engine-decision-rules.md` §8 (`dead_zone`) and §14
(`naked_leg_at_expiry`). That file is the definition; this spec is the work that makes
the code match it.

## Goal

One rule governs the end of the window across both engines:
> **In the dead zone: open nothing, and close what is open.**

Delete the obsolete giving-up clocks and redundant timeout gates (`entry_timeout_pct`,
`naked_leg_timeout_pct`, `max_start_elapsed_pct`, `max_start_delay_sec`, and
`stop_loss_enabled`), replacing them with the single switchable dead-zone limit and the
`naked_leg_at_expiry` close/hold policy.

## Acceptance Criteria

1. **One Dead-Zone definition in both engines (`backtest/engine.py` & `strategy/live_trader.py`):**
   - Structural limit with switchable unit: `dead_zone_unit` (`"pct"` [default] or `"sec"`).
   - Default value: `dead_zone_val = 0.10` (10% of window remaining).
   - Validation: `dead_zone_unit in ("pct", "sec")`; `dead_zone_val >= 0.0` (and `<= 1.0` if `unit == "pct"`).
   - Invariant 1 clock compliant: `window_length = end_ts - start_ts`; `remaining = end_ts - now` (live) / `end_ts - snapshot_ts` (backtest).
2. **Dead-Zone Actions on Trigger (`remaining <= dead_zone_cutoff`):**
   - **Open nothing:** No new entry quotes placed. A window whose first observed tick lands inside the dead zone is not entered at all (replaces `max_start_elapsed_pct` / `max_start_delay_sec`).
   - **Cancel unfilled resting quotes:** Any unfilled open buy orders are immediately cancelled.
   - **Handle unpaired leg:** If an unpaired leg is filled, take action per `naked_leg_at_expiry`:
     - `"close"` (default): cancel opposite resting buy quote (OCO) and sell filled leg at best executable bid (cross book, pays taker fee).
     - `"hold"`: cancel opposite resting buy quote, hold filled leg to settlement (pays 1.00 on win or 0.00 on loss; stop loss remains armed during hold if adverse move occurs before settlement).
   - **No fresh start:** Windows in the dead zone are ineligible for re-entry / fresh start.
3. **Five obsolete parameters completely deleted from both engines, parameter registry, schemas, and UI:**
   - `entry_timeout_pct` — deleted.
   - `naked_leg_timeout_pct` — deleted.
   - `max_start_elapsed_pct` — deleted.
   - `max_start_delay_sec` — deleted.
   - `stop_loss_enabled` — deleted, replaced by `naked_leg_at_expiry`.
4. **`naked_leg_at_expiry` parameter introduced in both engines:**
   - Values: `"close"` (default) and `"hold"`.
   - Replaces `stop_loss_enabled` with explicit intent and semantics.
5. **Shared pure calculation in `strategy/book_math.py`:**
   - `dead_zone_cutoff_seconds(window_length: float, dead_zone_val: float, dead_zone_unit: str) -> float`
   - `is_in_dead_zone(remaining_sec: float, window_length: float, dead_zone_val: float, dead_zone_unit: str) -> bool`
   - `dead_zone_start_ts(start_ts: float, end_ts: float, dead_zone_val: float, dead_zone_unit: str) -> float`
6. **Parity test (`tests/test_dead_zone_parity.py`):**
   - Drives a window into the dead zone with an unpaired leg and asserts both engines take the exact same action under both switch values (`"close"` and `"hold"`).
   - Verifies entry is blocked in the dead zone, unfilled quotes are cancelled, and units (`pct` and `sec`) evaluate identically.
7. **Every surface updated:**
   - Parameter registry (`BacktestParams._PARAM_GROUPS`): `dead_zone_val` and `dead_zone_unit` classified as structural limits, `naked_leg_at_expiry` registered.
   - Dashboard (`server/osc_dash.py`): Backtest and Cockpit tab inputs updated, `/api/backtest` and `/api/live/config` schemas updated.
   - Scripts and sweeps: `scripts/backtest.py` and `scripts/sweep_backtest.py` updated to support `--dead-zone-val`, `--dead-zone-unit`, and `--naked-leg-at-expiry`.
8. **Closes #229, closes #211, and closes the second half of #208.**

## Out of Scope

- Leg chase escalation ladder (#231): Will use `dead_zone_start_ts` from `book_math`, but the chase ladder implementation itself belongs to #231.
- Fresh start cleanup (#232): Rule 13 full cleanup belongs to #232.
- Stop threshold cleanup (#230): Deleting `exit_thresh_naked` belongs to #230.
- Structural limits separation in UI (#233): Belongs to #233.

## Edge Cases

- `dead_zone_val == 0.0`: Dead zone is disabled (quotes stand until end of window).
- `remaining_sec <= 0`: Window is expired, dead zone applies.
- `window_length <= 0`: Invalid clock, Invariant 1 forbids trading.
- First tick already in dead zone: Window is not entered (`entered = False`), no orders placed.
- Book has no bid when closing in dead zone: Fallback through `_resolve_exit_bid` ladder; if unpriceable, re-evaluate next tick.
