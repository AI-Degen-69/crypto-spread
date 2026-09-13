# SPEC.md — Issue #165: Implement CLOB WebSocket stream collector for 100% tape trade & book capture

## 1. Goal
Eliminate the 98.6% tape starvation caused by 1-second REST polling on Polymarket's Data API. Replace or augment the tape trade collection in `scripts/collect_ticks.py` with a real-time, persistent WebSocket connection directly to Polymarket's CLOB WebSocket (`wss://ws-subscriptions-clob.polymarket.com/ws/market`) per `docs/live-dashboard-streaming-spec.md`. Capture 100% of executed trades (`last_trade_price`) and book updates into `run/ticks/ticks_YYYY-MM-DD.jsonl` with zero lost prints, enabling accurate `fill_model="tape"` backtesting and replay research.

## 2. Background & Evidence
- Currently, `scripts/collect_ticks.py` polls `https://data-api.polymarket.com/trades` once per second over HTTP REST.
- As demonstrated in Issue #146 cross-checks and manifest audits, ~98.6% of snapshots have `tape_delta: []` because REST polling is too slow and high-latency to catch intra-second trade prints on Polymarket.
- This creates an unworkable dichotomy in research:
  1. `fill_model = "tape"` suffers from tape starvation and records 0 fills across hundreds of windows.
  2. `fill_model = "book"` is overly optimistic and assumes every vanished level was a fill.
- The blueprint already exists in `docs/live-dashboard-streaming-spec.md:46-56`. Moving trade collection from REST polling to a streaming WebSocket connection solves the tape starvation problem at the root.

## 3. In Scope
1. **CLOB Market WebSocket Client Enhancement (`strategy/streaming.py`)**:
   - Direct connection to `wss://ws-subscriptions-clob.polymarket.com/ws/market` using standard `websockets` library.
   - Subscription frame:
     ```json
     {
       "assets_ids": ["<token_id>", ...],
       "type": "market",
       "custom_feature_enabled": true
     }
     ```
   - Ingestion of `last_trade_price` events to record every trade with exact price, size, side, timestamp, and transaction hash.
   - Maintenance of order book state via `book` snapshots and `price_change` level mutations.
   - Application-level keepalive: 10s PING text frame sending and PONG response tracking.
   - Automatic exponential backoff reconnection with jitter upon socket disconnects.
   - Dynamic multi-token subscription updates when 5m/15m markets rotate.
2. **Thread-Safe Streaming Bridge (`CLOBStreamCollectorBridge`)**:
   - Background asyncio loop managed in a daemon thread.
   - Thread-safe trade queue buffering with `drain_trades()` per token or across all tokens.
   - Thread-safe order book cache queries (`get_book()`).
   - Connection status and health metrics (`get_status()`).
3. **Collector Integration (`scripts/collect_ticks.py`)**:
   - Integrate `CLOBStreamCollectorBridge` into the 1-second tick loop.
   - Dynamically subscribe to the 20 active UP/DOWN tokens across the 10 series in `strategy/series.py:SERIES`.
   - In `poll_once`, drain intra-second trades from the WebSocket buffer into `snap["tape_delta"]`.
   - Maintain dedup logic via `seen_tape` to prevent duplicates.
   - Graceful fallback: If WebSocket is reconnecting, gracefully fall back to REST `recent_trades` / `full_book`.
   - CLI flag `--no-ws` to allow forcing pure REST mode for diagnostics.
   - Update `manifest.json` with WebSocket connection status, trades captured via WS, and stream metrics.
   - Windows-safe UTF-8 console output and clean signal handling (SIGINT/SIGTERM).
4. **Comprehensive Automated Tests**:
   - Unit tests in `tests/test_clob_ws_collector.py`:
     - Test WebSocket message parser for `book`, `price_change`, `last_trade_price`, `best_bid_ask`, and `PONG`.
     - Test trade buffering and draining semantics.
     - Test 10s PING heartbeat and reconnection backoff.
     - Test dynamic token updates and resubscription.
     - Test `poll_once` with WebSocket bridge feeding tape trades.
     - Test backtest replay fill execution with streamed tape prints.
   - Smoke tests in `tests/test_collect_ticks_smoke.py`:
     - Verify `--no-ws` CLI parsing.
     - Verify manifest schema includes `ws_connected` and stream counters.

## 4. Out of Scope
- Order placement or execution signing (lives in `strategy/live_trader.py`).
- Frontend dashboard UI changes (separate issue).
- Direct Binance spot feed changes (already handled in `BinanceDirectWSClient`).

## 5. Acceptance Criteria
- [ ] WebSocket client connects to `wss://ws-subscriptions-clob.polymarket.com/ws/market` and subscribes to tokens for all 10 series in `strategy/series.py`.
- [ ] 100% of executed trades (`last_trade_price`) are captured in real-time and written to the tick stream (`tape_delta`).
- [ ] Heartbeat ping sent every 10s; automatic reconnection on socket closure or network disconnect.
- [ ] Replay test demonstrates non-zero, realistic tape trade fills in backtest engine without starvation.
- [ ] `python -m pytest -q` passes with 0 regressions.
