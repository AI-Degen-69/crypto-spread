# CONSTRAINTS.md — Issue #165: CLOB WebSocket stream collector for 100% tape trade & book capture

## Quality Gates & Hard Thresholds

### 1. Test Suite Integrity
- **Zero Regressions**: 100% pass rate on the full test suite (`python -m pytest -q`, currently 552 tests passing).
- **New Behavior Tests**: Explicit unit tests for:
  - WebSocket protocol parsing (`book`, `price_change`, `last_trade_price`, `best_bid_ask`, `PONG`).
  - 10s PING keepalive transmission and heartbeat timeout detection.
  - Exponential backoff reconnection loop.
  - Thread-safe trade queue buffering and draining (`drain_trades()`).
  - Dynamic token update and resubscription upon market rollover.
  - Integration with `collect_ticks.poll_once` writing into `tape_delta`.
  - Offline backtest replay verification demonstrating fills on streamed tape prints.
- **Anti-Cheat**: Strictly forbid disabling tests, deleting assertions, or suppressing linter checks.

### 2. Protocol & Networking Invariants
- **Endpoint**: Must connect to `wss://ws-subscriptions-clob.polymarket.com/ws/market`.
- **10s Heartbeat**: Must transmit raw text frame `"PING"` every 10 seconds; receive `"PONG"`.
- **Reconnection**: Must implement exponential backoff (base 1.0s, factor 2.0x, max 30.0s) with jitter.
- **Graceful Fallback**: If WebSocket disconnects or drops, collector must seamlessly fall back to REST `recent_trades` / `full_book` without crashing or dropping ticks.
- **Thread Safety**: Any background asyncio runner or buffer accessed across threads must use synchronization primitives (`threading.Lock`, thread-safe queues, or loop-safe call_soon_threadsafe).

### 3. Data Schema & Platform Compatibility
- **Tick Schema Invariant**: Output records appended to `run/ticks/ticks_YYYY-MM-DD.jsonl` must strictly pass all checks in `scripts/verify_tick_data.py`.
- **Tape Delta Schema**: Each trade in `tape_delta` must have `asset` (str), `price` (float), `size` (float), matching `backtest/engine.py` consumption.
- **Windows UTF-8 Console Safety**: Console output must maintain `sys.stdout.reconfigure(encoding="utf-8")` safety without crashing on emoji or Unicode characters under `cp1252`.
- **No Extra Dependencies**: Rely only on standard library, `websockets`, and existing packages in `requirements.txt`.
