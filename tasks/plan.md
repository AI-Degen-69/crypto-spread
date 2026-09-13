# Plan: Issue #165 — CLOB WebSocket stream collector for 100% tape trade & book capture

Task Type: Code + Performance / Data Integrity
Size Tier: Standard
Target Files:
- `strategy/streaming.py`
- `scripts/collect_ticks.py`
- `tests/test_clob_ws_collector.py` (new)
- `tests/test_streaming.py`
- `tests/test_collect_ticks_smoke.py`

Decisions locked with user: Requirements fully clear from the issue and `docs/live-dashboard-streaming-spec.md` — `interview-me` was skipped.

---

## 1. Spec Summary (Standard Tier)

### Problem Statement
Currently, `scripts/collect_ticks.py` polls `https://data-api.polymarket.com/trades` once per second via HTTP REST. Intra-second trades are dropped or delayed, resulting in ~98.6% of snapshots having `tape_delta: []`. Backtests using `fill_model="tape"` suffer from severe tape starvation (0 fills across hundreds of windows).

### Core Changes
1. **CLOB Market WebSocket Stream Client (`strategy/streaming.py`)**:
   - Refactor / enhance `CLOBMarketWSClient` to connect directly to `wss://ws-subscriptions-clob.polymarket.com/ws/market` via `websockets`.
   - Parse `last_trade_price` events (extracting asset, price, size, side, timestamp, tx_hash) and buffer them in a thread-safe FIFO trade buffer.
   - Maintain order book state via `book` and `price_change` frames.
   - Transmit `"PING"` text frames every 10s and monitor `"PONG"`.
   - Exponential backoff reconnection loop with jitter.
   - Dynamic token subscription updates when 5m/15m markets rotate.
2. **Background Collector Bridge (`CLOBStreamCollectorBridge`)**:
   - Async background thread runner exposing thread-safe methods: `update_subscribed_tokens()`, `drain_trades()`, `get_book()`, `get_status()`, and `stop()`.
3. **Integration into `scripts/collect_ticks.py`**:
   - Start `CLOBStreamCollectorBridge` on startup (controllable via `--no-ws` CLI flag for diagnostics).
   - Sync active market token IDs to the bridge.
   - In `poll_once`, drain intra-second trades into `tape_delta` (deduplicated against `seen_tape`).
   - Seamlessly fall back to REST `recent_trades` / `full_book` if the WebSocket is disconnected or buffer empty.
   - Record WebSocket streaming health and trade counters in `manifest.json`.
4. **Testing & Verification**:
   - Add unit and integration tests in `tests/test_clob_ws_collector.py`.
   - Verify replay test in `backtest/engine.py` executes realistic tape fills without starvation.
   - Ensure all 552+ tests pass with zero regressions.

---

## 2. Think Outside the Box — Proposed Improvement
**💡 Deduplicated Rolling Tape Buffer (`seen_trades` with Window-TTL) + Dual-Mode Switch (`--no-ws` fallback) + Manifest Telemetry**:
In fast crypto markets, trade prints can arrive in bursts or overlap during token transitions. By keying trade prints with a unique signature `f"{asset}:{price}:{size}:{ts}:{hash}"` in a sliding TTL set matching the window lifecycle, we guarantee **exactly-once tape delivery** into `tape_delta`, preventing duplicate fills in backtesting. Adding `--no-ws` guarantees operators can immediately revert to legacy REST mode for diagnostics if Polymarket's WebSocket infrastructure ever experiences maintenance, and `manifest.json` will expose `ws_connected`, `tape_captured_ws`, and `reconnect_count` for real-time observability.

---

## 3. Tasks Breakdown

### Task 0: Red Test Setup for CLOB WebSocket Trade Stream `[Debug]`
- **Target File**: `tests/test_clob_ws_collector.py`
- **Helper Skill**: `python-testing` / `tdd-workflow`
- **Verification**: `python -m pytest tests/test_clob_ws_collector.py -q`
- Create initial failing tests defining the contract:
  - Mock WebSocket server sending `last_trade_price` events.
  - Assert trade prints are parsed with exact asset, price, size, side, timestamp.
  - Assert trades can be drained from buffer.

### Task 1: Enhance `CLOBMarketWSClient` with `last_trade_price` & Direct WebSockets `[Backend/Logic]`
- **Target File**: `strategy/streaming.py`
- **Helper Skill**: `api-and-interface-design` / `source-driven-development`
- **Verification**: `python -m pytest tests/test_clob_ws_collector.py -k "test_clob_market" -q`
- Implement direct `websockets.connect` mode to `wss://ws-subscriptions-clob.polymarket.com/ws/market`.
- Implement 10s text `"PING"` heartbeat and `"PONG"` tracker.
- Parse `last_trade_price` event into structured trade record:
  `{"asset": asset_id, "price": float(price), "size": float(size), "side": side, "ts": ts, "hash": hash}`.
- Buffer trades into `self._trade_buffer[asset_id]` with thread-safe lock.
- Implement `drain_trades(token_id=None) -> list[dict]`.
- Implement exponential backoff reconnects on connection error.

### Task 2: Implement `CLOBStreamCollectorBridge` Background Runner `[Backend/Logic]`
- **Target File**: `strategy/streaming.py`
- **Helper Skill**: `incremental-implementation`
- **Verification**: `python -m pytest tests/test_clob_ws_collector.py -k "test_bridge" -q`
- Create `CLOBStreamCollectorBridge` running an `asyncio` event loop in a daemon thread.
- Expose thread-safe APIs:
  - `start()`, `stop()`
  - `update_subscribed_tokens(token_ids: list[str])`
  - `drain_trades_for_token(token_id: str) -> list[dict]`
  - `get_book_for_token(token_id: str) -> Optional[dict]`
  - `get_status() -> dict`

### Task 3: Integrate WebSocket Stream into `scripts/collect_ticks.py` `[Backend/Logic]`
- **Target File**: `scripts/collect_ticks.py`
- **Helper Skill**: `incremental-implementation`
- **Verification**: `python -m scripts.collect_ticks --once`
- Add `--no-ws` CLI argument.
- Start `CLOBStreamCollectorBridge` on startup when `--no-ws` is false.
- Pass active tokens across all 10 series to the bridge.
- In `poll_once`, drain real-time trades from WS bridge into `tape_delta`.
- Fall back to REST `recent_trades` if WS buffer is empty or disconnected.
- Update `manifest.json` with `ws_connected`, `tape_captured_ws`, and `ws_reconnects`.
- Clean shutdown on SIGINT / SIGTERM.

### Task 4: Replay & Backtest Verification with Tape Fills `[Test]`
- **Target File**: `tests/test_clob_ws_collector.py`
- **Helper Skill**: `python-testing`
- **Verification**: `python -m pytest tests/test_clob_ws_collector.py -q`
- Test that synthetic ticks populated with WebSocket `tape_delta` trigger proper executions in `backtest.engine.replay` under `fill_model="tape"`.
- Verify zero regressions on `tests/test_collect_ticks_smoke.py` and `tests/test_streaming.py`.

### Task 5: Full Test Suite Verification & Clean Run `[Test]`
- **Target File**: Full repository
- **Helper Skill**: `python-testing`
- **Verification**: `python -m pytest -q`
- Verify all 552+ tests pass with 0 warnings or regressions.
