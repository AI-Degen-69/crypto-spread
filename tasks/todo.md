# Todo: Issue #165 — Implement CLOB WebSocket stream collector for 100% tape trade & book capture

- [x] Task 0: Red Test Setup for CLOB WebSocket Trade Stream (`tests/test_clob_ws_collector.py`)
- [x] Task 1: Enhance `CLOBMarketWSClient` with `last_trade_price` & Direct WebSockets (`strategy/streaming.py`)
- [x] Task 2: Implement `CLOBStreamCollectorBridge` Background Runner (`strategy/streaming.py`)
- [x] Task 3: Integrate WebSocket Stream into `scripts/collect_ticks.py` (`scripts/collect_ticks.py`)
- [x] Task 4: Replay & Backtest Verification with Tape Fills (`tests/test_clob_ws_collector.py`)
- [x] Task 5: Full Test Suite Verification & Clean Run (`python -m pytest -q`)
