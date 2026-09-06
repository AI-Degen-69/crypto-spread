# Tasks: Issue #78 Display live coin actual price vs. RTDS spot price with real-time difference

- [x] Task 1: Concurrent feed tracking & delta calculation in `strategy/streaming.py`
- [x] Task 2: Propagate both feeds & price difference in `strategy/live_trader.py`
- [x] Task 3: Expose feeds in `/api/live/latency` and update Cockpit telemetry card in `server/osc_dash.py`
- [x] Task 4: Enhance `scripts/monitor_stream_latency.py` with actual, RTDS, and diff metrics
- [x] Task 5: Automated unit & integration tests (`tests/test_streaming.py`, `tests/test_osc_dash_integration.py`) and full regression (`python -m pytest -q`)

