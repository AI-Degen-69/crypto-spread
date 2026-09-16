# SPEC.md — Issue #204: Backtest pair_cost_gate must test resting pair cost, not touch ask sum

Binding while `fix/backtest-pair-cost-gate-204` is live.

## 1. Goal

The backtest fill detection in `backtest/engine.py` must measure the pair cost that the maker strategy is actually placing (`resting_up + resting_down`), rather than gating every tick on the taker touch sum (`up_ask + dn_ask`).

Under the live default `max_pair_cost = 0.98` (preset `patient_band_maker`), resting quotes must be permitted to fill when `resting_up + resting_down <= pair_cost_gate` (e.g. `0.48 + 0.48 = 0.96 <= 0.98`), rather than being suppressed because `up_ask + dn_ask` is ~1.01–1.02.

Furthermore, the dashboard and backtest reporting must clearly distinguish "0 windows entered / quoted" from zero P&L break-even performance.

## 2. Current Behaviour (The Defect)

`backtest/engine.py:928-938`:
```python
# Touch pair gate (0 or <= 0 disables per Maker strategy)
up_ask = ub.get("best_ask")
dn_ask = db.get("best_ask")
if params.pair_cost_gate <= 0:
    pair_cost_ok = True
else:
    touch = None
    if up_ask is not None and dn_ask is not None:
        touch = up_ask + dn_ask
    pair_cost_ok = (touch is None) or (touch <= params.pair_cost_gate)
```
and `backtest/engine.py:979`:
```python
if not queue_ok or not pair_cost_ok:
    # exit checks ...
    if not filled_up and not filled_down:
        continue # <-- skips FILL DETECTION for this tick
```

Consequences:
1. `touch = up_ask + dn_ask` is the taker cost to lift both asks simultaneously. In a binary market, `up_ask + dn_ask` is typically >= 1.00.
2. At the live default `pair_cost_gate = 0.98`, `pair_cost_ok` is false on every single tick across hundreds of windows.
3. On every tick where neither leg is filled, the engine `continue`s past line 1018 (`# --- FILL DETECTION`), preventing resting orders from ever filling. Across 550 real windows, exactly 0 fills occurred under the live default setting.
4. Divergence from live: In `strategy/live_trader.py`, `max_pair_cost` is used exclusively as a ceiling during leg chase (`entry_price + opposite_chase <= max_pair_cost`). Live has no per-tick touch ask gate.

## 3. Required Behaviour

### 3.1 Maker Resting Pair Cost Gate
A maker strategy's cost for the two-legged resting quote is `resting_up + resting_down`.
With symmetrical offset from mid (`r_mid - offset` and `(1 - r_mid) - offset`), the pair cost is `1.0 - 2 * offset` (or slightly shifted if clamped at boundaries).

In `backtest/engine.py`:
- `params.pair_cost_gate <= 0`: disabled (always passes).
- `params.pair_cost_gate > 0`:
  - If quotes are resting (`resting_up is not None and resting_down is not None`), `resting_cost = resting_up + resting_down`.
  - The entry/quoting cost gate passes if `resting_cost <= params.pair_cost_gate + 1e-6`.
  - Wide market touch asks (`up_ask + dn_ask > pair_cost_gate`) must NOT un-rest or block resting orders from filling.

### 3.2 Leg Chase Cap Preservation
During leg chase (`enable_leg_chase=True` and one leg filled):
`params.pair_cost_gate` continues to cap the chased opposite leg:
`max_opposite_bid = floor((pair_cost_gate - filled_entry_price) * 100) / 100.0`.
This ensures that the total realized cost of the completed pair never exceeds `pair_cost_gate`.

### 3.3 Diagnostic Visibility
1. In `backtest/engine.py` and `server/osc_dash.py`:
   Track and surface `entered_windows`: count of windows where resting quotes were successfully placed (i.e. not skipped by late start, delay, entry band, or cost gate).
2. The UI and metrics must clearly distinguish `entered_windows == 0` from flat break-even trading. If `entered_windows == 0` or 0 fills occurred, surface an informative diagnostic warning.

## 4. Acceptance Criteria

1. With `pair_cost_gate = 0.98` and `offset = 0.02` (`resting_cost = 0.96`), resting quotes are admitted and fill when hit by tape/book trades, even if `up_ask + dn_ask == 1.20`.
2. When `offset = 0.005` (`resting_cost = 0.99`) and `pair_cost_gate = 0.98`, the pair cost gate fails and blocks entry/fills.
3. When `pair_cost_gate = 0.0` or `< 0`, the gate is bypassed regardless of resting cost or touch.
4. Leg chase capping remains strictly enforced: `entry_price + chased_opposite <= pair_cost_gate`.
5. Existing test suite remains green, and new tests prove:
   - Wide touch asks do not prevent resting quotes from filling.
   - Resting pair cost exceeding `pair_cost_gate` correctly prevents fills.
   - Default preset (`pair_cost_gate = 0.98`) produces active trades/fills on real tick data where it previously produced 0.
6. The dashboard API exposes `entered_windows` (or preserves `unfilled_windows` / entered breakdown) and UI distinguishes 0 entries from break-even P&L.

## 5. Out of Scope

- Modifying the fill model mechanics themselves (`tape` vs `cross` calibration, Issue #205).
- Re-anchoring stop loss to fill price (Issue #209).
- Modifying entry band / adverse open thresholds (Issues #208, #213).
- Full live/backtest parity test harness (Issue #214, scheduled after #204).
