# SPEC: Display Live Coin Actual Exchange Price vs. RTDS Spot Price with Real-Time Price Difference (Issue #78)

## 1. Objective
Enhance the Live Stream Telemetry system and Live Trading Cockpit dashboard to concurrently ingest and display both the direct exchange spot price (Binance Direct WebSocket) and Polymarket's RTDS feed price (`prices.crypto.binance`), calculating and displaying the real-time price difference (in $ and %) to monitor feed basis, drift, and latency.

## 2. Background & Problem Statement
Polymarket binary crypto markets settle against external oracle feeds broadcast over RTDS (`prices.crypto.binance`). In `strategy/streaming.py`, `UnifiedStreamBridge` currently maintains both a Binance WebSocket client and an RTDS WebSocket client, but drops incoming RTDS ticks whenever Binance is connected (`if self.binance.is_connected: return`).

On the Cockpit dashboard (`server/osc_dash.py`), the Live Stream Telemetry card only displays a single spot value under "RTDS SPOT". Operators cannot observe whether Polymarket's RTDS feed lags behind Binance or drifts away from it, nor can they quantify the basis/spread between the two feeds. Ingesting and displaying both prices simultaneously alongside their instantaneous difference (`Actual - RTDS = Δ$ / Δ%`) provides crucial real-time visibility into feed lag, basis divergence, and potential latency arbitrage.

## 3. Scope

### In Scope
1. **`strategy/streaming.py`**:
   - Concurrently track both feed prices in `UnifiedStreamBridge`: direct exchange spot prices (`binance_spot_prices`) and Polymarket RTDS prices (`rtds_spot_prices`).
   - Do NOT drop RTDS ticks when Binance WS is active; update `self.rtds.spot_prices` and broadcast RTDS telemetry.
   - Calculate instantaneous price divergence:
     - `price_diff = binance_price - rtds_price`
     - `price_diff_pct = ((binance_price - rtds_price) / rtds_price) * 100.0`
   - Include both prices and divergence metrics in `UnifiedStreamBridge.get_status()` and `DashboardEnvelope(stream_id="spot")` broadcasts.
   - Forward leading ticks to `on_spot_tick_ext` (Binance when connected, fallback to RTDS when disconnected) without breaking existing tests or triggering duplicate stop-losses.
   - Add an optional `on_rtds_tick` callback to `UnifiedStreamBridge` so engine state updates RTDS basis immediately.

2. **`strategy/live_trader.py`**:
   - Add fields to `MarketLiveState`:
     - `actual_price: Optional[float] = None`
     - `rtds_price: Optional[float] = None`
     - `price_diff: Optional[float] = None`
     - `price_diff_pct: Optional[float] = None`
   - Update `on_spot_tick` and `on_rtds_tick` to record both prices and recalculate `price_diff` and `price_diff_pct`.
   - Expose these fields in `engine.get_state()` for each market.

3. **`server/osc_dash.py`**:
   - Update `/api/live/latency` to return `actual_price`, `rtds_price`, `price_diff`, and `price_diff_pct`.
   - Update `#card-stream-telemetry` HTML layout:
     - `ACTUAL SPOT (BINANCE)`: e.g. `$79,875.10`
     - `RTDS SPOT`: e.g. `$79,863.58`
     - `SPREAD / DIFF`: e.g. `+$11.52 (+0.014%)` with color coding (green positive, red negative, dim zero).
     - Keep `CLOB MID`, `LEAD LATENCY`, and `FEED HEALTH`.
   - Update `renderStreamTelemetry()` and SSE stream handler (`liveEventSource.onmessage`) to update all elements in real time.
   - Update `renderCockpitUI` when state snapshots arrive.

4. **`scripts/monitor_stream_latency.py`**:
   - Add `actual_price: Optional[float] = None`, `rtds_price: Optional[float] = None`, and `price_diff: Optional[float] = None` to `StreamTickSnapshot`.
   - Include these metrics in `to_dict()` and `format_row()`.

5. **Automated Tests**:
   - Unit tests in `tests/test_streaming.py` verifying concurrent tracking, delta math, `get_status()`, and broadcast envelopes.
   - Integration tests in `tests/test_osc_dash_integration.py` verifying `/api/live/latency` and `/api/live/state` payload fields.

### Out of Scope
- Changing market settlement or resolution contracts on Polymarket.
- Connecting to non-Binance external exchanges (e.g. Coinbase, Kraken).
- Altering core maker order sizing, spread offset, or stop loss thresholds.

---

## 4. Interfaces & Data Contracts

### 1. `UnifiedStreamBridge.get_status()`
```python
{
    "is_running": bool,
    "binance_ws_connected": bool,
    "rtds_connected": bool,
    "clob_ws_connected": bool,
    "user_ws_connected": bool,
    "active_spot_source": str,
    "symbols": Dict[str, float],         # primary active prices
    "binance_prices": Dict[str, float],  # direct exchange prices
    "rtds_prices": Dict[str, float],     # polymarket rtds prices
    "price_diffs": Dict[str, float],     # binance - rtds ($)
    "price_diff_pcts": Dict[str, float], # binance - rtds (%)
    "token_count": int,
    "open_orders_count": int,
    "seq": int,
}
```

### 2. Spot Broadcast Envelope (`stream_id="spot"`)
```python
{
    "type": "delta",
    "stream_id": "spot",
    "seq": int,
    "server_time": int,
    "data": {
        "symbol": str,
        "timestamp": int,
        "price": float,
        "actual_price": Optional[float],
        "rtds_price": Optional[float],
        "price_diff": Optional[float],
        "price_diff_pct": Optional[float],
        "slug": Optional[str],
        "slugs": List[str],
        "source": str,  # "BINANCE_WS" | "RTDS"
    }
}
```

### 3. `/api/live/latency` Response Schema
```python
{
    "ok": True,
    "series": str,
    "symbol": str,
    "spot_price": Optional[float],
    "actual_price": Optional[float],
    "rtds_price": Optional[float],
    "price_diff": Optional[float],
    "price_diff_pct": Optional[float],
    "spot_drift": float,
    "clob_mid": Optional[float],
    "latency_ms": Optional[float],
    "streaming_active": bool,
    "is_running": bool,
    "binance_ws_connected": bool,
    "rtds_connected": bool,
    "active_spot_source": str,
    "clob_ws_connected": bool,
    "updated_ts": Optional[float],
}
```
