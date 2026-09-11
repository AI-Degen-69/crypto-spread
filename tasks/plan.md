# tasks/plan.md — Issue #123: Chase the second leg after a one-sided fill

Branch: `feat/issue-123-leg-chase` (off `master`)
Constraints: `CONSTRAINTS.md`
Baseline: full suite green on master (409+ tests).

## Concise Spec (spec-driven-development, Standard tier)

**Goal:** When one leg fills, step up the opposite leg's quote towards the best ask (bounded by `max_pair_cost`, default 0.98) instead of resting passively, converting stop-outs into pair merges.

1. **Trigger Condition:** Exactly one leg is filled (`filled_up XOR filled_down`), not paired, not stopped, and `enable_leg_chase` is True.
2. **Quote Logic:**
   - For unfilled DOWN: `target = min(down_ask, max_pair_cost - fill_price_up)`. Quote steps up to `max(resting_down, target)`.
   - For unfilled UP: `target = min(up_ask, max_pair_cost - fill_price_down)`. Quote steps up to `max(resting_up, target)`.
   - If stepped up above initial base resting quote, flag `chased_leg`.
3. **Fill & Telemetry:**
   - If opposite leg fills while chased, set `chased_fill = True`.
   - Telemetry and re-entry stats record chased fills vs passive fills.
   - When pair completes or window exits, chase state clears and quotes normalize.
4. **Config:**
   - `enable_leg_chase: bool = True`
   - `max_pair_cost: float = 0.98`
   - Clamped in `update_config` (0.50..1.00).

## Tasks

### T1 — Config and State attributes
Files: `strategy/config.py`, `strategy/live_trader.py`
- Add `enable_leg_chase: bool = True` and `max_pair_cost: float = 0.98` to `LiveTraderEngine.__init__`.
- Add `chased_leg` and `chased_fill` to `MarketLiveState`.
- Add `enable_leg_chase` and `max_pair_cost` to `update_config` and `get_state()["params"]`.

### T2 — Quoting & Fill Logic in `update_market`
Files: `strategy/live_trader.py`
- When one leg is filled, calculate opposite leg chase bid respecting `max_pair_cost`.
- If chased quote is placed and fills, record `chased_fill = True`.
- Normalize quotes on merge or window exit.
- Integrate into re-entry telemetry events (`tel["chased_fill"]`) and `self.reentry_stats`.

### T3 — Unit Tests (TDD)
Files: `tests/test_live_trader.py`
- Test 1: Single fill triggers chase up to opposite ask when under cap.
- Test 2: When opposite ask exceeds `max_pair_cost - fill_price`, bid is clamped to cap.
- Test 3: When both legs filled, chase does not trigger.
- Test 4: Chased fill is distinguished from passive fill in telemetry and state.
- Test 5: `update_config` updates `max_pair_cost` and `enable_leg_chase` with validation.

### T4 — Verification & Regression Suite
- Run `pytest tests/test_live_trader.py tests/test_entry_timeout.py -q`.
- Run full suite: `pytest -q`.
