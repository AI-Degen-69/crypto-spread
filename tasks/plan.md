# Plan: Issue #403 — Investigate and fix empty sweeper results under queue_gate

Branch: `i403/empty-results-sweeper-queue-gate` | Issue: `#403`

## Summary & Findings
- **Investigation / Root Cause**: Real order-book queue depths for BTC/ETH on Polymarket range from 180 to 1600+ shares (BTC 5m average ~847 shares, max ~1563). The parameter sweeper and dashboard sweep visual only swept queue gate values up to 200 shares (`[0, 10, 25, 50, 100, 200]`). Because `queue_gate` strictly requires resting depth on both legs `q <= queue_gate`, 100% of BTC/ETH ticks were filtered out at every non-zero gate point, yielding empty tables ($0 PnL, 0 pairs captured).
- **Solution**: Widen the queue sensitivity sweep axis to `[0.0, 10.0, 25.0, 50.0, 100.0, 200.0, 500.0, 1000.0, 2000.0]` across `scripts/sweep_backtest.py` and `server/osc_dash.py:SWEEP_AXES`, and include corresponding high-depth sampling in random/joint grids. Add regression tests verifying deep-book windows produce active trades at high queue gate thresholds.

## Tasks

- [x] **Task 1 (S)**: `[Quant/Sweeper]` Widen queue gate axis in `scripts/sweep_backtest.py`
  - Target files: `scripts/sweep_backtest.py`
  - Details:
    - Update `generate_sensitivity_grid()`: set `queues = [0.0, 10.0, 25.0, 50.0, 100.0, 200.0, 500.0, 1000.0, 2000.0]`.
    - Update `generate_random_grid()`: preserve legacy stream draws while supporting high queue depth variations.
    - Update `generate_joint_grid()` default queues to include representative high queue depth (`(0.0, 25.0, 50.0, 100.0, 200.0, 500.0, 1000.0)`).
  - Depends on: None
  - Verification: `python -m pytest tests/test_sweep_backtest.py -q`

- [x] **Task 2 (S)**: `[Dashboard/Backend]` Widen queue axis in dashboard sweep visual
  - Target files: `server/osc_dash.py`
  - Details:
    - Update `SWEEP_AXES["queue"] = [0.0, 10.0, 25.0, 50.0, 100.0, 200.0, 500.0, 1000.0, 2000.0]`.
  - Depends on: Task 1
  - Verification: `python -m pytest tests/test_osc_dash_integration.py -k "sweep" -q`

- [x] **Task 3 (S)**: `[Testing]` Add regression unit tests for widened queue sensitivity & high-depth window simulation
  - Target files: `tests/test_sweep_backtest.py`, `tests/test_osc_dash_integration.py`
  - Details:
    - Add test verifying `generate_sensitivity_grid` outputs queue labels up to `queue=2000`.
    - Add test simulating a high-depth window (e.g. depth=600) showing it is blocked at `queue_gate=200` but trades successfully and captures pairs at `queue_gate=1000`.
    - Verify dashboard `/api/analysis` sweep endpoint returns valid curve data for all widened queue axis points.
  - Depends on: Task 1, Task 2
  - Verification: `python -m pytest tests/test_sweep_backtest.py -q` && `python -m pytest tests/test_osc_dash_integration.py -k "sweep" -q`
