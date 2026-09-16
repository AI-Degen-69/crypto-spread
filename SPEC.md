# SPEC.md — Issue #209: stop loss must be measured from the entry price, not from 0.50

## 1. Problem Statement

In both `strategy/live_trader.py` and `backtest/engine.py`, adverse drift (the excursion that triggers the stop loss) is currently calculated against a hardcoded constant `0.50` base:
```python
if mid > 0.50:
    mstate.max_up_drift = max(mstate.max_up_drift, mid - 0.50)
elif mid < 0.50:
    mstate.max_down_drift = max(mstate.max_down_drift, 0.50 - mid)
```

This leads to a catastrophic risk-management defect:
1. **Premature stop exits**: If an UP leg fills at `0.45`, `0.50 - mid` is already `0.05` at the moment of fill. With the default `exit_thresh = 0.05` (or `exit_thresh_naked`), `max_down_drift >= 0.05` evaluates to `True` immediately on tick 1, exiting the leg before the market has moved a single cent against our entry!
2. **Asymmetric risk allocation**: A leg filled at `0.50` receives the intended 5 cents of adverse room, while a leg filled at `0.45` gets 0 cents of room, and a leg filled at `0.55` gets 10 cents. The risk per trade becomes an arbitrary artifact of fill location.
3. **Distorted reversal detection**: Reversal detection checks whether `(0.50 - mid) < exit_reversal` instead of checking whether the market retraced back towards the entry price.

## 2. Technical Specification & Mathematical Contracts

### 2.1 Entry-Anchored Adverse Excursion for Live Engine (`strategy/live_trader.py`)

- When an UP leg is filled alone (`mstate.filled_up and not mstate.filled_down`):
  - Reference entry price: `entry_up = mstate.fill_price_up if mstate.fill_price_up is not None else mstate.resting_up`.
  - If `mstate.mid is not None` and `entry_up is not None`:
    - `adverse_drift = max(0.0, entry_up - mstate.mid)`
    - `mstate.max_down_drift = max(mstate.max_down_drift, adverse_drift)`
    - Reversal detection:
      `if mstate.max_down_drift >= thresh and (entry_up - mstate.mid) < self.exit_reversal:`
          `mstate.reversal_seen_down = True`

- When a DOWN leg is filled alone (`mstate.filled_down and not mstate.filled_up`):
  - Reference entry price: `entry_dn = mstate.fill_price_down if mstate.fill_price_down is not None else mstate.resting_down`.
  - Implied UP mid at entry: `1.0 - entry_dn`.
  - If `mstate.mid is not None` and `entry_dn is not None`:
    - `adverse_drift = max(0.0, mstate.mid - (1.0 - entry_dn))`
    - `mstate.max_up_drift = max(mstate.max_up_drift, adverse_drift)`
    - Reversal detection:
      `if mstate.max_up_drift >= thresh and (mstate.mid - (1.0 - entry_dn)) < self.exit_reversal:`
          `mstate.reversal_seen_up = True`

- When neither leg is filled (`not mstate.filled_up and not mstate.filled_down`):
  - No position is held; `max_up_drift` and `max_down_drift` remain `0.0`.

- When both legs are filled (`mstate.filled_up and mstate.filled_down`):
  - The pair is captured; stop loss does not monitor paired positions.

### 2.2 Entry-Anchored Adverse Excursion for Backtest Replay (`backtest/engine.py`)

- Maintain `max_up` (`max(mids) - 0.50`) and `max_down` (`0.50 - min(mids)`) purely as window-level oscillation metrics returned in `WindowResult`.
- Track dedicated position adverse drift:
  - For UP: `adverse_drift_up` measured from `entry_price_up or resting_up`.
  - For DOWN: `adverse_drift_down` measured from `1.0 - (entry_price_down or resting_down)`.
- Trigger stop loss exits when:
  - `filled_up and not filled_down and adverse_drift_up >= naked_thr`
  - `filled_down and not filled_up and adverse_drift_down >= naked_thr`
- Trigger reversal flags when:
  - `adverse_drift_up >= naked_thr and (entry_price_up - mid) < params.exit_reversal`
  - `adverse_drift_down >= naked_thr and (mid - (1.0 - entry_price_down)) < params.exit_reversal`

## 3. Acceptance Criteria

1. A leg filled at `0.45` with `exit_thresh = 0.05` is NOT stopped out at `mid = 0.45`, `mid = 0.43`, or `mid = 0.41`.
2. Stop loss triggers when `mid` reaches `<= 0.40` for UP (drift `0.45 - 0.40 = 0.05`).
3. A DOWN leg filled at `0.45` (implied mid `0.55`) with `exit_thresh = 0.05` triggers stop loss only when `mid >= 0.60` (drift `0.60 - 0.55 = 0.05`).
4. Reversal detection suppresses exit when price moves back to within `exit_reversal` of entry.
5. All targeted unit tests in `test_live_trader.py` and `test_backtest_engine.py` pass cleanly.
