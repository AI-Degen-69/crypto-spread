# CONSTRAINTS.md — Dynamic Symmetric Mid-Anchored Quoting Quality Gates

## Quality Gates & Hard Thresholds

### 1. Test Suite Integrity
- **Pass Rate**: 100% (414/414 passing tests in `pytest`).
- **No Test Swallowing**: Strictly forbidden to skip, comment out, or mock-pass failing assertions.

### 2. Risk & Pair Cost Boundaries
- **Pair Cost Ceiling**: Initial pair cost ($\text{resting\_up} + \text{resting\_down}$) must strictly satisfy $\text{pair\_cost} < 1.00$ for all quoted windows.
- **Symmetric Distance**: Distance from `up_mid` to `resting_up` and `down_mid` to `resting_down` must both equal `offset` (0.02).

### 3. Execution & Safety Guards
- **Opposite Cancellation**: Stop exit on one leg must immediately issue a cancel request for the opposite leg and set its status to `CANCELLED`.
- **Paired Position Immunity**: Paired positions (`filled_up` and `filled_down`) must never be stop-lossed; they must proceed to `PAIR_MERGE` redemption for $1.00.
