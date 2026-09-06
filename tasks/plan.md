# Task Plan: Issue #78 — Display Live Coin Actual Exchange Price vs. RTDS Spot Price with Real-Time Difference

## Overview
Enable concurrent ingestion of Binance Direct WebSocket spot prices and Polymarket RTDS prices in `UnifiedStreamBridge` without discarding RTDS ticks. Compute instantaneous price differences ($ and %), propagate them through `MarketLiveState` and `LiveTraderEngine`, expose them in `/api/live/latency` and `/api/live/state`, and render both feeds side-by-side with signed, color-coded price differences in the Cockpit telemetry card and monitor scripts.

---

### Task 1: Concurrent Feed Tracking & Delta Calculation in `strategy/streaming.py`
- **Target File**: `strategy/streaming.py`
- **Details**:
  - In `UnifiedStreamBridge.__init__`:
    - Add optional callback `on_rtds_tick: Optional[Callable[[str, int, float], None]] = None`.
    - Expose `binance_spot_prices` and `rtds_spot_prices` property or dict references.
  - In `_handle_binance_spot_tick`:
    - Look up `rtds_price = self.rtds.spot_prices.get(symbol.lower())`.
    - Calculate `price_diff = round(price - rtds_price, 4)` and `price_diff_pct = round(((price - rtds_price) / rtds_price) * 100.0, 4)` if `rtds_price` is available.
    - Include `actual_price`, `rtds_price`, `price_diff`, and `price_diff_pct` in the `"spot"` broadcast envelope.
  - In `_handle_rtds_spot_tick`:
    - Do NOT return early when Binance is connected.
    - If `on_rtds_tick` is provided, call `on_rtds_tick(symbol, ts, price)`.
    - If `not self.binance.is_connected` and `self.on_spot_tick_ext`: invoke `self.on_spot_tick_ext(symbol, ts, price)` (preserving fallback behavior).
    - Look up `binance_price = self.binance.spot_prices.get(symbol.lower())`.
    - Calculate `price_diff` and `price_diff_pct` when `binance_price` is available.
    - Broadcast `"spot"` envelope with `price=binance_price or price`, `actual_price=binance_price`, `rtds_price=price`, `price_diff`, `price_diff_pct`, `source="RTDS"`.
  - In `get_status`:
    - Include `binance_prices`, `rtds_prices`, `price_diffs`, and `price_diff_pcts`.
- **Verification**: `python -m pytest tests/test_streaming.py`

---

### Task 2: Propagate Both Feeds & Divergence in `strategy/live_trader.py`
- **Target File**: `strategy/live_trader.py`
- **Details**:
  - In `MarketLiveState`:
    - Add `actual_price: Optional[float] = None`, `rtds_price: Optional[float] = None`, `price_diff: Optional[float] = None`, `price_diff_pct: Optional[float] = None`.
  - In `LiveTraderEngine.__init__`:
    - Wire `on_rtds_tick=self.on_rtds_tick` to `UnifiedStreamBridge`.
  - Add `on_rtds_tick(self, symbol: str, ts_ms: int, price: float) -> None`:
    - Updates `m.rtds_price = price`.
    - Recalculates `m.price_diff` and `m.price_diff_pct` if `m.actual_price` or `m.spot_price` exists.
  - In `on_spot_tick`:
    - Set `m.actual_price = price` (when source is Binance or primary).
    - If `m.rtds_price` is present, recalculate `m.price_diff` and `m.price_diff_pct`.
  - In `get_state()`:
    - Include `actual_price`, `rtds_price`, `price_diff`, `price_diff_pct` in each market state dictionary.
- **Verification**: `python -m pytest tests/test_live_trader.py`

---

### Task 3: Expose Feeds in `/api/live/latency` and Cockpit Card in `server/osc_dash.py`
- **Target File**: `server/osc_dash.py`
- **Details**:
  - In `/api/live/latency`:
    - Extract `actual_price`, `rtds_price`, `price_diff`, `price_diff_pct` from market state or bridge fallback.
    - Return them in the JSON response.
  - In `#card-stream-telemetry` HTML markup:
    - Display `ACTUAL SPOT (BINANCE)` with id `telActualPrice`.
    - Display `RTDS SPOT` with id `telSpotPrice`.
    - Display `PRICE SPREAD / DIFF` with id `telPriceDiff`.
    - Maintain `CLOB MID`, `LEAD LATENCY`, and `FEED HEALTH`.
  - In `renderStreamTelemetry(data)`:
    - Populate `telActualPrice` formatted as `$XX,XXX.XX`.
    - Populate `telSpotPrice` formatted as `$XX,XXX.XX`.
    - Populate `telPriceDiff` formatted with signed dollar and percentage: e.g. `+$11.52 (+0.014%)`, with green for positive, red for negative, dim for zero.
  - In `renderCockpitUI` and SSE message handler (`liveEventSource.onmessage`):
    - Feed incoming spot price deltas (`actual_price`, `rtds_price`, `price_diff`, `price_diff_pct`) into telemetry rendering.
- **Verification**: `python -m pytest tests/test_osc_dash_integration.py`

---

### Task 4: Enhance `scripts/monitor_stream_latency.py`
- **Target File**: `scripts/monitor_stream_latency.py`
- **Details**:
  - In `StreamTickSnapshot`:
    - Add `actual_price: Optional[float] = None`, `rtds_price: Optional[float] = None`, `price_diff: Optional[float] = None`, `price_diff_pct: Optional[float] = None`.
  - In `to_dict()`:
    - Include new fields.
  - In `format_row()`:
    - Include actual vs RTDS price display and basis spread.
- **Verification**: `python -m pytest tests/`

---

### Task 5: Automated Unit & Integration Tests and Full Regression
- **Target Files**: `tests/test_streaming.py`, `tests/test_osc_dash_integration.py`
- **Details**:
  - Test concurrent Binance and RTDS tick tracking in `UnifiedStreamBridge`.
  - Test `price_diff` and `price_diff_pct` calculation logic and rounding.
  - Test `/api/live/latency` response structure includes new fields.
  - Test telemetry card markup contains new element IDs and labels.
  - Run full test suite: `python -m pytest -q` ensuring all 256+ tests pass.
- **Verification**: `python -m pytest -q`

