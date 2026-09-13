# Entry-Fill Validation & Settle Mark Fidelity (Issue #160)

## 1. Overview
This document records the formal entry-fill validation and settlement fidelity analysis required by Issue #160 following the cross-check in #146 (`runs/paper/2026-09-11_22-10_IDT/replay_comparison/comparison.md`).

---

## 2. Settlement Mark-to-Book Resolution (Finding 1 Resolution)
In the 11h shadow run (`runs/paper/2026-09-11_22-10_IDT`), all 10 expired naked positions were settled at exactly `0.50` due to `mstate.up_bid or 0.50` falling back unconditionally when boundary polls cleared book handles. At true executable marks, these 10 settles produced -$24.49 rather than the booked +$0.24.

### Fix Implemented in `strategy/live_trader.py`:
1. **Latched Book Prices**: `MarketLiveState` maintains `last_valid_up_bid`, `last_valid_down_bid`, `last_valid_up_ask`, `last_valid_down_ask`. Book clearing during window rollover no longer wipes market history.
2. **Binary Complement Cascade (`_resolve_exit_bid`)**:
   - Priority 1: Direct executable book bid (`up_bid` / `down_bid`).
   - Priority 2: Binary complement of opposite ask (`1.0 - down_ask` / `1.0 - up_ask`).
   - Priority 3: Latched book bid from the most recent valid tick.
   - Priority 4: Latched binary complement ask (`1.0 - last_valid_down_ask` / `1.0 - last_valid_up_ask`).
   - Priority 5: Derived synthetic mid (non-default).
   - **Fail-Loud Safety Guard**: If zero book data or history exists, the engine logs a CRITICAL error and raises a `RuntimeError`. Silent fallback to `0.50` is strictly forbidden.
3. **Telemetry**:
   - `scripts/shadow_ev_pilot.py:snapshot()` now exports `up_bid`, `down_bid`, `up_ask`, `down_ask`, `last_valid_up_bid`, `last_valid_down_bid`, `last_valid_up_ask`, and `last_valid_down_ask` in the per-minute `snapshots.jsonl`.
   - Every `TradeEvent` with action `WINDOW_SETTLE` records the exact resolution source in its notes (e.g., `UP=0.0100 (complement_ask)`).

---

## 3. Entry-Fill Validation of the 45 Scoped Pairs
During the scoped 11h shadow run, 45 pairs filled under paper simulation.
- **Paper Sim Entry Mechanism**: Orders were matched passively whenever the market ask crossed the resting price (`mstate.up_ask <= resting_up`).
- **Toxicity & Queue Position**:
  - In real CLOB markets, passive limit orders sit in a price-time priority queue. When an adverse price move occurs, resting orders are filled by aggressive flow ("toxic fills").
  - EV Research Phase 6 demonstrated that under a strict queue-ahead simulation (`tapeq`), every winning configuration turned negative.
- **Decision & Deferred Validation**:
  - Offline backtests and paper simulations cannot reconstruct real queue-ahead priority without assuming queue models.
  - Therefore, entry-fill queue validation is **formally deferred to Issue #143 (Live micro-pilot with queue telemetry)**.
  - Issue #143 executes minimum-size (5 shares/leg) orders on Polymarket CLOB with real money to capture measured `queue_ahead_at_rest` telemetry and deliver a data-grounded go/no-go verdict before capital sizing.

---

## 4. Engine 1-Pair/Window vs Paper Re-Quoting
- **Backtest Engine Behavior**: `backtest/engine.py:704` breaks the window loop after the first pair is captured (`pair_captured = True`), capping backtest outcomes at 1 pair per window.
- **Paper / Live Engine Behavior**: In `strategy/live_trader.py`, when a pair is merged or exited and sufficient window duration remains (`min_requote_remaining_sec`), the engine can initiate a second or third re-quote round (`requote_round`), capturing up to 4+ pairs in a volatile window (e.g. BNB 09:00Z).
- **Architectural Decision**:
  - Documented as a known asymmetric structural delta.
  - The backtest engine remains intentionally conservative (under-counting high-frequency multi-pair windows) to avoid over-optimistic churn assumptions.
  - Live and paper engines retain re-quoting capabilities governed by `min_requote_remaining_sec` and `max_reentries_per_window`.
