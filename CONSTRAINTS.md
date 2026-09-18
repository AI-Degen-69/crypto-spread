# CONSTRAINTS — Issue #213: Quoting around current price across tradeable range & replacing entry_band veto

## Scope Lock
1. **Decision Rules Compliance:** Strictly conform to `docs/engine-decision-rules.md` §6 and ADR-0003: `quote_range` is a structural limit, not a 0.50-anchored tuning knob.
2. **No 0.50 Anchors:** Never re-introduce hardcoded 0.50 distance checks (`abs(mid - 0.50)`) into quoting gates or order pricing.
3. **No Latched Window Vetoes:** Out-of-range market conditions must hold quoting per-tick only; do not set permanent window-cancelling flags (`entry_cancelled_timeout`, `band_skip`) on mid range checks.
4. **Shared Behavioral Parity:** Both engines (`strategy/live_trader.py` and `backtest/engine.py`) must evaluate `quote_range` identically on the two-sided mid.

## Quality Guardrails
5. **Zero Regressions on Targeted Suites:**
   - `python -m pytest tests/test_quote_range_parity.py -q` (all 7 scenarios green).
   - `python -m pytest tests/test_engine_parity.py -q` (all 18 scenarios green).
   - `python -m pytest tests/test_live_trader.py -q` (all 110 tests green).
   - `python -m pytest tests/test_backtest_engine.py -q` (all 136 tests green).
   - `python -m pytest tests/test_osc_dash_integration.py -q` (all integration tests green).
6. **Anti-Cheat:**
   - Strictly forbid disabling, skipping, or weakening tests.
   - Do not suppress linters or error logs.
   - No new external dependencies.

