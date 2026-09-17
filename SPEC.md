# SPEC — Issue #231: Rule: the leg chase escalates with time instead of firing on the first tick

Binding while `feat/leg-chase-ladder-231` is live. Per-issue working file (`docs/git-workflow.md` §5)
— not an architecture document.

The rule itself is agreed: `docs/engine-decision-rules.md` §12 (`leg_chase`).
That file is the definition; this spec is the work that makes the code match it.

## Goal

Replace the crude, instant-fire leg chase with a time-proportional escalation ladder across both engines:
> **The chase ceiling walks from our original quote up to the cap as the window runs out:**
> ```python
> max_affordable = floor((max_pair_cost - entry_price_of_filled_leg) * 100) / 100
> progress       = clamp((now - went_naked_at) / (dead_zone_start - went_naked_at), 0, 1)
> ceiling        = original_resting + progress * (max_affordable - original_resting)
> target         = min(other_leg_ask, ceiling)
> ```
> The quote is only ever raised, never lowered. At `progress = 0` the chase offers nothing beyond
> the original quote; at the dead zone the full cap is available, which is the last chance to
> complete a pair before the dead zone cuts the leg.

## Acceptance Criteria

1. **Shared Math (`strategy/book_math.py`):**
   - Helper `chase_progress(now, went_naked_at, dead_zone_start) -> float` clamped to `[0.0, 1.0]`.
   - Helper `chase_ceiling(original_resting, max_affordable, progress) -> float` floored to 2dp cents precision.
2. **Backtest Engine Integration (`backtest/engine.py`):**
   - Record `went_naked_elapsed` at the tick exactly one leg fills (`filled_up != filled_down`).
   - Derive `dead_zone_start_sec` from window length and dead zone configuration.
   - On each tick while naked, calculate `progress`, `ceiling`, and `target = min(ask, ceiling)`.
   - Quote only raised, never lowered (`if target > resting: resting = target; chased_now = True`).
   - Tick immediately after fill with unmoved market does NOT raise quote (`progress == 0 -> ceiling == original_resting`).
3. **Live Engine Integration (`strategy/live_trader.py`):**
   - Use `mstate.naked_since_ts` (already tracked upon one-sided fill).
   - Compute `dead_zone_start_ts` from window bounds and `dead_zone_val`/`dead_zone_unit`.
   - Update `resting` quote according to the same `chase_progress` and `chase_ceiling`.
   - Clean up vestigial, unused config knobs in `LiveTraderEngine` (`chase_step_pct`, `chase_max_steps`, `chase_interval_sec`) that belonged to the unadopted stepped chase.
4. **Behavioral Parity Test Suite (`tests/test_leg_chase_parity.py`):**
   - Test 1: First tick after fill with unmoved market leaves opposite quote unchanged.
   - Test 2: Progress advances proportionally with time toward the dead zone.
   - Test 3: Quote never exceeds `max_pair_cost - filled_entry` (strict arithmetic cap).
   - Test 4: Quote is never lowered when ask drops below previously chased bid.
   - Test 5: Backtest and Live engines produce identical chased prices at identical timestamps across simulated ticks.
5. **Zero Regressions:**
   - All targeted test suites pass cleanly.

## Out of Scope

- Multi-round fresh start (#232): Handling window rollover without memory belongs to #232.
- Stop threshold modifications (already completed in #230).

## Edge Cases

- `went_naked_at >= dead_zone_start`: `progress = 1.0`, full `max_affordable` available immediately.
- `max_affordable <= original_resting`: `ceiling = original_resting`, quote never walks backward.
- `ask is None`: Quote cannot anchor; remains at current resting price.
