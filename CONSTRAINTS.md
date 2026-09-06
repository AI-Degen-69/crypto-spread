# Quality Guardrails & Constraints (`CONSTRAINTS.md`) — Issue #78

## 1. Test & Regression Bar (Non-Negotiable)
- **Zero Regression**: All 256 existing unit and integration tests must remain 100% green (`python -m pytest -q`).
- **Comprehensive Coverage**: New tests must be added covering:
  - `UnifiedStreamBridge`:
    - Concurrent tracking of both `binance_spot_prices` and `rtds_spot_prices`.
    - Calculation of `price_diff` and `price_diff_pct`.
    - Forwarding RTDS ticks to SSE broadcasts without dropping ticks when Binance is connected.
    - Preserving fallback leading tick behavior when Binance is disconnected.
    - Health and telemetry metrics in `get_status()`.
  - `MarketLiveState` & `LiveTraderEngine`:
    - Retaining `actual_price`, `rtds_price`, `price_diff`, and `price_diff_pct`.
    - Exposure of delta metrics in `get_state()`.
  - API & Dashboard Integration:
    - `/api/live/latency` returns `actual_price`, `rtds_price`, `price_diff`, and `price_diff_pct`.
    - SSE stream dispatches spot events with actual, RTDS, and diff values.
    - Live Stream Telemetry card renders both prices and signed, color-coded price difference.
  - Latency Monitor:
    - `StreamTickSnapshot` to_dict and format_row include actual and RTDS prices and diffs.

## 2. Performance & Latency Thresholds
- **Zero Ingestion Lag**: Math operations (`price_diff = binance - rtds`) must be O(1) in-memory float operations (< 1µs per tick).
- **Non-Blocking SSE**: Broadcasting to SSE queues must never block the WebSocket receiving loops.
- **Client DOM Efficiency**: UI updates in `renderStreamTelemetry()` must use direct element ID references and Avoid unnecessary DOM reflows during sub-second ticks.

## 3. Anti-Cheat Discipline
- **No Test Silencing**: No tests may be skipped, commented out, deleted, or assertions weakened to pass.
- **Strict Calculations**: Mathematical tests must verify exact float delta calculations (`diff == round(binance - rtds, 4)`).

## 4. Architectural Boundaries
- **No External Libraries**: No new dependencies in `requirements.txt`. Only Python stdlib, `fastapi`, `uvicorn`, `requests`, and existing WS clients.
- **Feed Independence**: Binance WebSocket and Polymarket RTDS clients must run independently without tight failure coupling.
- **Safety First**: Order execution and stop-loss logic must continue relying on verified leading spot signals without race conditions.
