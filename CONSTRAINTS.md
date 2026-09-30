# CONSTRAINTS.md — Issue #364 Guardrails

## Quality & Non-Regression Rules
1. **Zero Strategy Drift:** No changes to quoting logic, entry bands, leg chasing, stop-loss triggers, or `max_pair_cost` caps.
2. **Backwards Parity:** `pair_rate` in `overall`, `per_series`, and `per_duration` MUST retain its existing formula (`pairs / total_windows`). CLI output in `scripts/backtest.py` must not change format or values.
3. **Dual Aggregation Synchronization:** Aggregate keys and semantics in `backtest/engine.py` and `server/osc_dash.py` MUST be identical.
4. **Targeted Test Gate:** Fast targeted tests (`python -m pytest tests/test_backtest_engine.py -q`) MUST pass in under 2 seconds.
5. **No Test Suppression:** No test skipping, assertion removal, or linters suppression permitted.
