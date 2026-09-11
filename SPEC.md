# SPEC.md — Dynamic Symmetric Mid-Anchored Quoting & Stop Loss Trigger (0.05$)

## 1. Goal
Implement dynamic symmetric mid-anchored limit order pricing based on each leg's live mid (`u_mid - offset` and `d_mid - offset`) for initial window quoting (round 0), update UI label to `Stop Loss Trigger ($)` with default 0.05$, and confirm instant cancellation of opposite resting limit orders upon single-leg stop exit.

---

## 2. In Scope
1. **Dynamic Symmetric Mid-Anchored Initial Quotes (`strategy/live_trader.py`)**:
   - Calculate initial limit order prices dynamically per leg:
     $$\text{resting\_up} = \text{round}(\max(0.01, \min(0.99, \text{up\_mid} - \text{offset})), 3)$$
     $$\text{resting\_down} = \text{round}(\max(0.01, \min(0.99, \text{down\_mid} - \text{offset})), 3)$$
   - Ensure symmetric distance (`offset`) from each leg's respective mid price (e.g. 50/51 -> 0.49 / 0.48; 30/71 -> 0.28 / 0.69).
2. **Stop Loss Trigger ($) Naming & Default**:
   - Update Cockpit UI label in `server/osc_dash.py` to `Stop Loss Trigger ($)` with default `0.05`.
   - Set `exit_thresh_naked = 0.05` in `strategy/live_trader.py` to match `exit_thresh = 0.05`.
3. **Opposite Order Cancellation**:
   - Verify that single-leg stop exit in `_execute_stop_exit` immediately cancels the un-filled resting order on the opposite leg (`mstate.order_status = "CANCELLED"`).
4. **Unit & Integration Tests (`tests/test_live_trader.py`)**:
   - Add/update tests verifying dynamic symmetric initial quotes, UI labels, and opposite order cancellation.

---

## 3. Out of Scope
- Modifying `PAIR_MERGE` CTF redemption logic.
- Modifying Leg Chase logic (issue #123).
- Modifying post-merge Re-quoting logic (issue #89).

---

## 4. Acceptance Criteria
- [ ] Round 0 initial quotes place UP at `up_mid - offset` and DOWN at `down_mid - offset`.
- [ ] For 50/51 market, UP limit order is 0.49 and DOWN limit order is 0.48 (pair cost 0.97, 3¢ profit).
- [ ] UI label in Cockpit is updated to `Stop Loss Trigger ($)` with default 0.05$.
- [ ] Single leg stop exit cancels opposite resting order immediately.
- [ ] All 414 tests pass (`python -m pytest -q`).
