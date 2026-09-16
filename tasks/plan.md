# Plan — Issue #204: Backtest pair_cost_gate tests resting pair cost, not touch ask sum

- **Issue:** #204 (`ready-for-agent`, assigned)
- **Branch:** `fix/backtest-pair-cost-gate-204`
- **Size tier:** Standard — 2-4 files (`backtest/engine.py`, `tests/test_backtest_engine.py`, `server/osc_dash.py`), logic correction for backtest gate and diagnostic visibility.
- **Task type:** Debug + Code (eliminating backtest fill suppression and live divergence).
- **Stack:** Python 3.12.10, FastAPI, pytest.
- **Skills routed:** `debugging-and-error-recovery`, `test-driven-development`, `api-and-interface-design`.

## Root Cause (Confirmed by Inspection)

In `backtest/engine.py:928-938`:
```python
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
and consumed at `:979`:
```python
if not queue_ok or not pair_cost_ok:
    ...
    if not filled_up and not filled_down:
        continue # <-- skips fill detection!
```

This gates fill detection on `up_ask + dn_ask` — the taker touch cost. In binary markets, `up_ask + dn_ask` is usually >= 1.00 (~1.01-1.02). With the live default `pair_cost_gate = 0.98`, this fails every tick, skipping fill detection on 100% of windows. The maker strategy rests at `mid - offset` and `(1 - mid) - offset`, so the pair cost is `1.0 - 2*offset` (0.94 at 0.03, 0.96 at 0.02), well under 0.98. Furthermore, live trader uses `max_pair_cost` only as the cap for leg chase, never gating resting quotes on the touch.

## Tasks

### T1 — Red Tests for Resting Pair Cost Gate `[Debug]`
- Add `test_resting_pair_cost_gate_allows_fills_when_touch_is_wide` to `tests/test_backtest_engine.py`:
  - With `up_ask=0.60, down_ask=0.60` (touch=1.20) and `offset=0.02` (`resting_cost=0.96`), when `pair_cost_gate=0.98`, resting quote MUST fill when touched by a trade.
- Add `test_resting_pair_cost_gate_blocks_when_quotes_exceed_cap`:
  - With `offset=0.005` (`resting_cost=0.99`) and `pair_cost_gate=0.98`, entry/fill MUST be blocked because the resting pair exceeds the ceiling.
- **Verification:** `python -m pytest tests/test_backtest_engine.py -q -k resting_pair_cost` must FAIL on master.

### T2 — Fix Gate in `backtest/engine.py` `[Backend/Logic]`
- In `backtest/engine.py:928-938`, replace the touch ask test with resting pair cost:
  ```python
  if params.pair_cost_gate <= 0:
      pair_cost_ok = True
  elif resting_up is None or resting_down is None:
      pair_cost_ok = True
  else:
      resting_pair_cost = resting_up + resting_down
      pair_cost_ok = resting_pair_cost <= (params.pair_cost_gate + 1e-6)
  ```
- Update the legacy test `test_simulate_pair_cost_gate_blocks_wide_touch` in `tests/test_backtest_engine.py` to assert the corrected resting pair cost logic.
- Ensure leg chase cap (`_cap = params.pair_cost_gate` at line 953) continues to function as expected.
- **Verification:** T1 tests pass; `tests/test_backtest_engine.py` passes.

### T3 — Track and Expose `entered_windows` in Backtest Engine & API `[Backend/Logic]`
- In `backtest/engine.py:WindowResult`, add `entered: bool` indicating whether the window was quotable and met entry gates (delay, band, pair cost, queue).
- In `replay()` aggregate metrics, track `entered_windows` and include it in overall summary.
- In `server/osc_dash.py:api_backtest`, expose `entered_windows` in the `overall` payload.
- **Verification:** `python -m pytest tests/test_backtest_engine.py tests/test_osc_dash_integration.py -q`.

### T4 — Dashboard Zero-Entry vs Zero-PnL Diagnostics `[Design/UI]`
- In `server/osc_dash.py`:
  - Update backtest metrics display in JS to show entered windows (`${ov.entered_windows || ov.windows || 0} entered`).
  - Update `btEquityWarning` banner to state clearly when 0 windows were entered due to gates vs 0 fills on entered windows.
  - Enable `btPairCost` input if applicable, ensuring validation matches `BacktestParams.bounds_for("pair_cost_gate")`.
- **Verification:** `python -m pytest tests/test_osc_dash_integration.py -q`.

### T5 — Empirical Verification on Real Ticks `[Research/Debug]`
- Run backtest against `run/ticks/` with preset defaults (`pair_cost_gate=0.98`) to confirm that windows now quote and fill as expected (confirming the zero-fill lock is broken).
- Document results in the PR walkthrough.

### T6 — Full Test Suite & Quality Gate `[Backend/Logic]`
- Run `python -m pytest -q` across the entire repository to ensure zero regressions.
- Verify commit history is clean and atomic per task.

## 💡 Proposed Improvement (Operator Decides — Not Folded In Silently)

Extract `resting_pair_cost(resting_up, resting_down)` into `strategy/book_math.py` with standard rounding and clamp checks, and reuse it across both `live_trader.py` and `backtest/engine.py`. This ensures identical mathematical precision (e.g. 1e-9 epsilon handling) and single-source truth for pair affordability across live and simulation.
