# Quality Guardrails & Constraints — Issue #403

## Scope & Functional Boundaries
- Investigate and resolve empty simulation results ($0 PnL, zero pairs captured) for high-volume markets (BTC, ETH) in parameter sweeps when `queue_gate > 0`.
- Root cause: Real-market order book queue depths for BTC/ETH range from 180 to 1600+ shares, while existing sweep axes only tested `[0, 10, 25, 50, 100, 200]`, blocking 100% of BTC/ETH ticks at all non-zero sweep points.
- Code changes:
  - `server/osc_dash.py`: Update `SWEEP_AXES["queue"]` to `[0.0, 10.0, 25.0, 50.0, 100.0, 200.0, 500.0, 1000.0, 2000.0]`.
  - `scripts/sweep_backtest.py`: Update `generate_sensitivity_grid()`, `generate_random_grid()`, and `generate_joint_grid()` queue axis values to include high-depth values (`500.0`, `1000.0`, `2000.0`).
  - Add regression tests in `tests/test_sweep_backtest.py` and `tests/test_osc_dash_integration.py` verifying that sweeps include the widened queue points and produce non-zero results on high-depth windows.
- Invariant & Non-Goals:
  - Do NOT weaken or bypass the binary queue gate logic in `backtest/engine.py:_simulate_window` (queue gate semantics remain strict: `q_up <= gate and q_dn <= gate`).
  - No changes to `BacktestParams` schema or bounds (`(0.0, 100000.0)` already accommodates up to 100k shares).
  - No new dependencies.

## Anti-Regression & Verification
- Targeted unit tests in `tests/test_sweep_backtest.py` and `tests/test_osc_dash_integration.py` pass.
- Verification command: `python -m pytest tests/test_sweep_backtest.py -q`.
- Verification command: `python -m pytest tests/test_osc_dash_integration.py -k "sweep" -q`.
