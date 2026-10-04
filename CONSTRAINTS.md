# CONSTRAINTS — Issue #426

## 1. Zero Regressions
- Targeted test suites covering affected strategy and backtest modules must pass:
  `python -m pytest tests/test_live_trader.py tests/test_param_registry.py tests/test_backtest_engine.py tests/test_docstrings.py tests/test_markets_hosts.py tests/test_legacy_maker_config.py -q`
- No test suite outside modified areas may be broken.

## 2. Integrity & Anti-Cheat
- Strictly forbid skipping, disabling, or deleting existing tests.
- Strictly forbid stripping assertions.
- Do not run the full 932-test suite locally (violates AGENTS.md policy; GitHub Actions CI gates full regressions).

## 3. Scope & Defaults
- No runtime default moves:
  - `LiveTraderEngine.max_pair_cost` remains `0.99`.
  - `BacktestParams` defaults and hash inputs remain unchanged.
- No changes to `strategy/markets.py` exported functions or constants (`GAMMA_HOST`, `CLOB_HOST`, `fetch_live_market`, `select_window`, etc.). Only the dead `__main__` block is touched.
- No new external packages in `requirements.txt`.
