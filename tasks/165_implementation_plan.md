# Implementation Plan - Issue #165: CLOB WebSocket Stream Collector for 100% Tape Trade & Book Capture

Eliminate the 98.6% tape starvation caused by 1-second REST polling on Polymarket's Data API. Replace or augment the tape trade collection in [collect_ticks.py](file:///c:/Users/Tiger/Agents/Projects/AI%20Trading/crypto-spread/scripts/collect_ticks.py) with a real-time, persistent WebSocket connection directly to Polymarket's CLOB WebSocket (`wss://ws-subscriptions-clob.polymarket.com/ws/market`) per [live-dashboard-streaming-spec.md](file:///c:/Users/Tiger/Agents/Projects/AI%20Trading/crypto-spread/docs/live-dashboard-streaming-spec.md).

## User Review Required

> [!IMPORTANT]
> The collector will connect directly to `wss://ws-subscriptions-clob.polymarket.com/ws/market` using the standard `websockets` library (version 15.0.1 already installed). It runs in parallel with active market token rotation, ensuring continuous capture even across 5m/15m window boundaries. A `--no-ws` CLI flag is added to allow immediate fallback to pure REST polling if needed.

## Open Questions

None. The WebSocket protocol and schemas are fully specified in official Polymarket docs and [live-dashboard-streaming-spec.md](file:///c:/Users/Tiger/Agents/Projects/AI%20Trading/crypto-spread/docs/live-dashboard-streaming-spec.md). Requirements were fully clear from the issue — `interview-me` was skipped.

## Proposed Changes

Grouped by component:

---

### Streaming Core (`strategy/streaming.py`)

#### [MODIFY] [streaming.py](file:///c:/Users/Tiger/Agents/Projects/AI%20Trading/crypto-spread/strategy/streaming.py)
- Refactor and extend `CLOBMarketWSClient`:
  - Connect directly to `wss://ws-subscriptions-clob.polymarket.com/ws/market` using `websockets.connect`.
  - Transmit `"PING"` text frames every 10 seconds; process `"PONG"`.
  - Handle messages:
    - `"book"`: apply full book replacement via `apply_book_snapshot`.
    - `"price_change"`: apply incremental level update via `apply_price_change`.
    - `"last_trade_price"`: parse `asset_id`, `price`, `size`, `side`, `timestamp`, `transaction_hash`. Buffer into `_trade_buffer[asset_id]` and trigger optional `on_trade` callback.
    - `"best_bid_ask"`: record top-of-book levels.
  - Implement thread-safe `drain_trades(token_id=None) -> list[dict]`.
  - Implement exponential backoff reconnection (1s -> 2s -> 4s -> max 30s) with jitter.
  - Implement dynamic token subscription updates via `update_tokens(new_tokens)`.
- Implement `CLOBStreamCollectorBridge`:
  - Runs an `asyncio` event loop in a daemon background thread.
  - Thread-safe interfaces: `update_subscribed_tokens()`, `drain_trades_for_token()`, `get_book_for_token()`, `get_status()`, `stop()`.

---

### Collector Engine (`scripts/collect_ticks.py`)

#### [MODIFY] [collect_ticks.py](file:///c:/Users/Tiger/Agents/Projects/AI%20Trading/crypto-spread/scripts/collect_ticks.py)
- CLI args: add `--no-ws` (action="store_true", default False).
- On startup, instantiate and start `CLOBStreamCollectorBridge` unless `--no-ws` is specified.
- Register signal handlers (SIGINT, SIGTERM) to stop the bridge cleanly.
- In `poll_once()`:
  - Aggregate active token IDs across all 10 series and notify the bridge to keep subscriptions in sync.
  - Drain real-time trades from `ws_bridge` into `snap["tape_delta"]`.
  - If WS buffer is empty or disconnected, gracefully fall back to REST `recent_trades()`.
  - Track stats: `ws_connected`, `tape_captured_ws`, `tape_empty_rate`, `reconnects`.
- In `update_manifest()`: include WebSocket telemetry for dashboard and health auditing.

---

### Automated Tests (`tests/`)

#### [NEW] [test_clob_ws_collector.py](file:///c:/Users/Tiger/Agents/Projects/AI%20Trading/crypto-spread/tests/test_clob_ws_collector.py)
- Unit tests for `CLOBMarketWSClient` message parsing (`book`, `price_change`, `last_trade_price`, `best_bid_ask`, `PONG`).
- Unit tests for 10s PING keepalive and exponential backoff reconnection.
- Unit tests for trade buffering and `drain_trades()` thread safety.
- Unit tests for `CLOBStreamCollectorBridge` background thread runner.
- Integration test for `collect_ticks.poll_once` with WebSocket streamed trades.
- Offline backtest replay test demonstrating non-zero tape fills in `backtest.engine.replay` under `fill_model="tape"`.

#### [MODIFY] [test_collect_ticks_smoke.py](file:///c:/Users/Tiger/Agents/Projects/AI%20Trading/crypto-spread/tests/test_collect_ticks_smoke.py)
- Test `--no-ws` CLI parsing.
- Verify manifest serialization includes `ws_connected` and new telemetry fields.

---

## Verification Plan

### Automated Tests
- Run targeted tests:
  ```powershell
  python -m pytest tests/test_clob_ws_collector.py -q
  python -m pytest tests/test_streaming.py -q
  python -m pytest tests/test_collect_ticks_smoke.py -q
  ```
- Run single poll smoke test:
  ```powershell
  python -m scripts.collect_ticks --once
  ```
- Run dataset integrity verification on output:
  ```powershell
  python -m scripts.verify_tick_data run/ticks
  ```
- Run full test suite:
  ```powershell
  python -m pytest -q
  ```

### Manual Verification
- Run collector for 15 seconds to observe real WebSocket connection and live trade/book capture:
  ```powershell
  python -m scripts.collect_ticks --out run/ticks
  ```
- Verify `manifest.json` shows `"ws_connected": true` and non-zero trade capture without cp1252 console encoding errors.
