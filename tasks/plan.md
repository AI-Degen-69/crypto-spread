# Plan: Issue #160 — Paper sim settles mark naked legs to 0.50 (bids never maintained) — fix expiry marking

Task Type: Code + Debug
Size Tier: Standard
Target Files:
- `strategy/live_trader.py`
- `scripts/shadow_ev_pilot.py`
- `tests/test_live_trader.py`
- `docs/ev-paper-settle-validation.md` (new documentation on entry fill validation & 1-pair delta)

Decisions locked with user: Requirements fully clear from the issue and PR #162 findings — `interview-me` was skipped.

---

## 1. Spec Summary (Standard Tier)

### Problem Statement
In `strategy/live_trader.py:_handle_window_rollover`:
```python
if mstate.filled_up:
    bid = mstate.up_bid or 0.50
    settle_pnl += (bid - fill_up) * self.shares
if mstate.filled_down:
    bid = mstate.down_bid or 0.50
    settle_pnl += (bid - fill_dn) * self.shares
```
At window expiration, when CLOB order books clear or boundary polls return empty/swapped books, `mstate.up_bid` and `mstate.down_bid` are `None`. This triggered the unconditional `or 0.50` fallback on 10/10 shadow settles in #146, turning an actual -$24.49 true-mark loss into a booked +$0.24 profit (+$6.74 shadow night distorted to appear profitable when it was actually -$19.5).

### Core Changes
1. **Bid Maintenance & Latching**:
   - Add `last_valid_up_bid`, `last_valid_down_bid`, `last_valid_up_ask`, `last_valid_down_ask` to `MarketLiveState`.
   - Latch bids whenever non-empty book responses or websocket book updates are received.
   - Do not wipe latched bids when an expiring book temporarily returns empty.
2. **Mark-to-Book & Binary Complement Settlement**:
   - For an expired naked UP leg:
     1. Priority 1: `mstate.up_bid`
     2. Priority 2: Binary complement `round(1.0 - mstate.down_ask, 4)` (since UP + DOWN = 1.0)
     3. Priority 3: Latched `mstate.last_valid_up_bid`
     4. Priority 4: Latched complement `round(1.0 - mstate.last_valid_down_ask, 4)`
   - For an expired naked DOWN leg:
     1. Priority 1: `mstate.down_bid`
     2. Priority 2: Binary complement `round(1.0 - mstate.up_ask, 4)`
     3. Priority 3: Latched `mstate.last_valid_down_bid`
     4. Priority 4: Latched complement `round(1.0 - mstate.last_valid_up_ask, 4)`
   - Fail Loud: If no bid or complement can be derived, log CRITICAL error and raise a `ValueError`/mark with explicit `MARK_UNAVAILABLE` error note; never silently use 0.50.
3. **Telemetry & Auditability**:
   - Add `settle_source` and `mark_bid` in `TradeEvent.notes`.
   - Export `up_bid`, `down_bid`, `up_ask`, `down_ask` in `scripts/shadow_ev_pilot.py:snapshot()`.
4. **Entry-Fill & Model Delta Documentation**:
   - Document the 45 scoped entry fills (touch vs queue toxicity, deferred to #143) and backtest 1-pair/window vs paper re-quoting.

---

## 2. Think Outside the Box — Proposed Improvement
**💡 Dual-Sided Complement Mark with Bid Latching & Settle Confidence Flag**:
In binary prediction markets, books near expiry are often one-sided or drained of resting bids on the losing outcome. By computing `mark_bid = up_bid or (1.0 - down_ask) or last_valid_up_bid`, we guarantee execution-grade valuation without requiring an active bid on the dead leg. Furthermore, recording `settle_source: "direct_bid" | "complement_ask" | "latched_bid" | "emergency_mid"` on the `TradeEvent` provides 100% auditability for every settlement in paper and live modes.

---

## 3. Tasks Breakdown

### Task 0: Baseline & Red Test Setup `[Debug]`
- **Target File**: `tests/test_live_trader.py`
- **Helper Skill**: `python-testing` / `tdd-workflow`
- **Verification**: Run `python -m pytest tests/test_live_trader.py -k "settle" -q`
- Write failing tests that demonstrate the bug:
  - Mock a naked leg with empty book at rollover: assert it currently yields 0.50 settlement (the defective behavior).

### Task 1: Bid Maintenance & Latching in `MarketLiveState` `[Backend/Logic]`
- **Target File**: `strategy/live_trader.py`
- **Helper Skill**: `api-and-interface-design`
- **Verification**: `python -m pytest tests/test_live_trader.py -q`
- Add latched bid/ask attributes to `MarketLiveState`:
  - `last_valid_up_bid: Optional[float] = None`
  - `last_valid_down_bid: Optional[float] = None`
  - `last_valid_up_ask: Optional[float] = None`
  - `last_valid_down_ask: Optional[float] = None`
- In `_update_market_strategy` and `on_book_update`:
  - Update `last_valid_*` whenever incoming values are non-null and > 0.
  - Do not overwrite `last_valid_*` with None.

### Task 2: Mark-to-Book & Binary Complement Settle Logic `[Backend/Logic]`
- **Target File**: `strategy/live_trader.py`
- **Helper Skill**: `test-driven-development`
- **Verification**: `python -m pytest tests/test_live_trader.py -q`
- Implement helper method `_resolve_exit_bid(mstate, side) -> Tuple[float, str]` in `LiveTraderEngine`:
  - Implements the 4-stage resolution cascade: direct bid -> complement ask -> latched bid -> latched complement.
  - Returns `(resolved_bid, source_name)`.
- Update `_handle_window_rollover` to use `_resolve_exit_bid`.
- Include `source_name` and `resolved_bid` in `TradeEvent.notes`.

### Task 3: Fail-Loud Safety Guard `[Backend/Logic]`
- **Target File**: `strategy/live_trader.py`
- **Helper Skill**: `debugging-and-error-recovery`
- **Verification**: `python -m pytest tests/test_live_trader.py -q`
- If all 4 resolution stages fail to find a valid market quote:
  - Log `log.critical("[%s] Window Rollover FAILED: No valid market book or latched bid for %s leg", mstate.slug, side)`
  - Mark position with an explicit `MARK_UNAVAILABLE` status/action and raise or record a non-zero audit penalty; NEVER silently substitute 0.50.

### Task 4: Snapshot Telemetry Export `[Backend/Logic]`
- **Target File**: `scripts/shadow_ev_pilot.py`
- **Helper Skill**: `incremental-implementation`
- **Verification**: `python -m scripts.shadow_ev_pilot --hours 0.02 --snap-every 10`
- Add `up_bid`, `down_bid`, `up_ask`, `down_ask` to `snapshot()` in `scripts/shadow_ev_pilot.py`.

### Task 5: Comprehensive Unit & Regression Tests `[Test]`
- **Target File**: `tests/test_live_trader.py`
- **Helper Skill**: `python-testing`
- **Verification**: `python -m pytest tests/test_live_trader.py -q`
- Tests to add:
  - `test_rollover_settle_direct_bid`: verifies settle at 0.01 / 0.99 with live book bids.
  - `test_rollover_settle_binary_complement`: verifies settle at `1.0 - down_ask` when `up_bid` is missing.
  - `test_rollover_settle_latched_bid`: verifies settle uses last valid bid when book clears at boundary.
  - `test_rollover_settle_fails_loud_on_empty_books`: verifies no silent 0.50 fallback.

### Task 6: Entry-Fill Validation & Model Delta Documentation `[Docs]`
- **Target File**: `docs/ev-paper-settle-validation.md`
- **Helper Skill**: `documentation-and-adrs`
- **Verification**: Inspect generated doc and verify all acceptance criteria are satisfied.
- Document entry fill validation:
  - State that the 45 scoped pairs filled at touch/mid in paper mode.
  - Explicitly document that live queue-position toxicity cannot be simulated without real prints and is formally deferred to #143 queue telemetry.
  - Document the structural delta of engine 1-pair/window vs paper re-quoting.
