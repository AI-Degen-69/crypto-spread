# CONSTRAINTS.md — Issue #359 Quality Guardrails

## 1. Zero Regressions
- Modified and new test files (`tests/test_clob_ws_collector.py`) must pass completely.
- Targeted test run command: `python -m pytest tests/test_clob_ws_collector.py -q`.
- Do not run the full test suite locally (local full suite takes ~96s and local execution is strictly prohibited by AGENTS.md; CI gates regressions).

## 2. Production Code Integrity (Non-Invasive Diagnostic Policy)
- Strict non-invasive requirement: Zero behavioral or state-mutating modifications to `strategy/streaming.py` (`apply_price_change`, `run_direct`, `_session`), `strategy/live_trader.py`, or the core tick collector loop in `scripts/collect_ticks.py`.
- This issue is diagnosis only. Any fix to `apply_price_change` or reconnect logic belongs to the follow-up fix issue.

## 3. Performance & Replay Efficiency
- Replay harness (`scripts/replay_socket_reconciliation.py`) must be pure offline (no network calls during replay).
- The harness must process at least 1,000 events/second in-memory to enable fast iterative diagnosis and CI integration.

## 4. Anti-Cheat & Code Cleanliness
- No disabling or skipping existing tests (`@pytest.mark.skip`, `pytest.skip`).
- No deletion of assertions.
- No new external dependencies (pure stdlib + existing project dependencies `requests`, `websockets`, `pytest`).
- All new scripts must adhere to strict type annotations, docstrings, and clean error handling.
