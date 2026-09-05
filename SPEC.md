# Spec: Side-by-Side RTDS vs CLOB Live Stream Monitor & Price Latency Audit (Issue #54)

## 1. Objective

Build a comprehensive real-time streaming comparison engine, standalone CLI monitor, empirical latency audit, and live Cockpit dashboard widget:
1. **Real-Time Synchronized Stream Ingestion**: Concurrently ingest Polymarket RTDS spot ticks (`prices.crypto.binance` / REST fallback) and Polymarket CLOB order book updates (Market WebSocket / REST) aligned to the exact same second.
2. **Standalone CLI Monitor (`scripts/monitor_stream_latency.py`)**:
   - Display a live, formatted console table of spot prices, percentage drift, UP/DOWN best bid/ask/mid, CLOB contract mid, and latency delta ($\Delta t$ ms).
   - Support automated inspection with `--duration <sec>`, `--ticks <n>`, and machine-readable `--json` output.
   - Provide an `--audit` flag to run empirical lead-lag correlation tracking and print summary statistics.
3. **Empirical Latency Audit & Ground Truth Documentation (`docs/rtds-clob-latency-audit.md`)**:
   - Measure empirical lead times: how many milliseconds/seconds elapse between an RTDS spot price shift ($\ge 0.10\%$) and the subsequent CLOB order book response (BBO/mid shift).
   - Formally document the execution ground truth: CLOB is the sole execution venue, RTDS is an informational leading indicator for fast stop-loss execution, and asset units ($65,000 USD vs $0.48 binary contract) are non-fungible.
4. **Dashboard Cockpit Integration (`server/osc_dash.py`)**:
   - Expose side-by-side stream metrics via `/api/live/latency` and real-time SSE broadcasts.
   - Add a compact "Live Stream Telemetry & Latency" telemetry card in Tab 1 (Cockpit) displaying spot price, drift, CLOB mid, feed latency, and leading signal health.

---

## 2. Tech Stack & Dependencies

- **Language & Runtime**: Python 3.10+
- **Web & API Framework**: FastAPI, Starlette, Uvicorn, `sse-starlette`
- **Networking & Ingestion**: `requests`, Python standard library (`asyncio`, `dataclasses`, `collections`, `threading`, `time`, `typing`)
- **Existing Bridge**: [`strategy/streaming.py`](file:///c:/Users/Tiger/Agents/Projects/AI%20Trading/crypto-spread/strategy/streaming.py) (`UnifiedStreamBridge`, `RTDSStreamClient`, `CLOBMarketWSClient`)
- **Testing Framework**: `pytest`, `pytest-asyncio`, FastAPI `TestClient`
- **Zero New Dependencies**: Uses only packages already listed in `requirements.txt`.

---

## 3. Commands

- Run all unit and integration tests:
  ```powershell
  python -m pytest -q
  ```
- Run the stream monitor CLI interactively:
  ```powershell
  python -m scripts.monitor_stream_latency --series btc-up-or-down-5m
  ```
- Run stream monitor with automated duration and JSON output:
  ```powershell
  python -m scripts.monitor_stream_latency --series btc-up-or-down-5m --duration 30 --json
  ```
- Run empirical latency lead-time audit:
  ```powershell
  python -m scripts.monitor_stream_latency --series btc-up-or-down-5m --audit --duration 60
  ```
- Start dashboard server:
  ```powershell
  python -m uvicorn server.osc_dash:app --host 127.0.0.1 --port 8802
  ```

---

## 4. Project Structure & Touchpoints

```
scripts/
  monitor_stream_latency.py        -> [NEW] Standalone CLI monitor and latency auditor
strategy/
  streaming.py                     -> Enhance bridge with latency calculation & synced tick snapshots
server/
  osc_dash.py                      -> Add /api/live/latency endpoint & Cockpit UI telemetry card
docs/
  rtds-clob-latency-audit.md       -> [NEW] Empirical audit report & execution ground truth documentation
tests/
  test_monitor_stream_latency.py   -> [NEW] Unit tests for CLI monitor, calculations & audit tracker
  test_osc_dash_integration.py     -> Add integration tests for /api/live/latency & Cockpit telemetry DOM
SPEC.md                            -> This specification document
```

---

## 5. Code Style & Architecture

### 5.1 Latency Tracker & Stream Synchronizer Architecture
In `scripts/monitor_stream_latency.py`:
```python
from dataclasses import dataclass, field
import time
from typing import Optional, List, Dict, Any

@dataclass
class StreamTickSnapshot:
    timestamp: float
    time_str: str
    symbol: str
    series_slug: str
    spot_price: float
    spot_drift_pct: float
    up_bid: Optional[float]
    up_ask: Optional[float]
    up_mid: Optional[float]
    down_bid: Optional[float]
    down_ask: Optional[float]
    down_mid: Optional[float]
    clob_mid: Optional[float]
    latency_ms: float
    spot_source: str = "RTDS"
    clob_source: str = "WS"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "time_str": self.time_str,
            "symbol": self.symbol,
            "series": self.series_slug,
            "spot_price": round(self.spot_price, 2),
            "spot_drift_pct": round(self.spot_drift_pct, 4),
            "up_bid": self.up_bid,
            "up_ask": self.up_ask,
            "up_mid": self.up_mid,
            "down_bid": self.down_bid,
            "down_ask": self.down_ask,
            "down_mid": self.down_mid,
            "clob_mid": self.clob_mid,
            "latency_ms": round(self.latency_ms, 1),
            "spot_source": self.spot_source,
            "clob_source": self.clob_source,
        }

    def format_row(self) -> str:
        up_str = f"{self.up_bid or 0:.2f}/{self.up_ask or 0:.2f} ({self.up_mid or 0:.3f})"
        dn_str = f"{self.down_bid or 0:.2f}/{self.down_ask or 0:.2f} ({self.down_mid or 0:.3f})"
        sign = "+" if self.spot_drift_pct >= 0 else ""
        return (
            f"[{self.time_str}] | "
            f"Spot: ${self.spot_price:9.2f} ({sign}{self.spot_drift_pct*100:+.2f}%) | "
            f"UP: {up_str:18} | DN: {dn_str:18} | "
            f"CLOB Mid: {self.clob_mid or 0:.3f} | Δt: {self.latency_ms:4.0f}ms"
        )
```

### 5.2 Latency Audit Engine
```python
class LatencyAuditor:
    """Detects spot price impulse moves and measures elapsed time until CLOB book adjustments."""

    def __init__(self, drift_threshold: float = 0.0010, response_window_sec: float = 10.0):
        self.drift_threshold = drift_threshold  # 0.10% spot move
        self.response_window_sec = response_window_sec
        self.events: List[Dict[str, Any]] = []
        self._pending_shock: Optional[Dict[str, Any]] = None

    def record_tick(self, snapshot: StreamTickSnapshot) -> None:
        now = snapshot.timestamp
        # Check if an existing shock event is awaiting CLOB reaction
        if self._pending_shock:
            dt = now - self._pending_shock["shock_ts"]
            if dt <= self.response_window_sec:
                if snapshot.clob_mid is not None and abs(snapshot.clob_mid - self._pending_shock["baseline_mid"]) >= 0.01:
                    self._pending_shock["reaction_time_sec"] = dt
                    self._pending_shock["reaction_time_ms"] = dt * 1000.0
                    self._pending_shock["clob_reacted"] = True
                    self.events.append(self._pending_shock)
                    self._pending_shock = None
            else:
                self._pending_shock["clob_reacted"] = False
                self.events.append(self._pending_shock)
                self._pending_shock = None

        # Detect new spot price shock
        if abs(snapshot.spot_drift_pct) >= self.drift_threshold and not self._pending_shock:
            self._pending_shock = {
                "shock_ts": now,
                "spot_price": snapshot.spot_price,
                "drift_pct": snapshot.spot_drift_pct,
                "baseline_mid": snapshot.clob_mid or 0.50,
                "reaction_time_sec": None,
                "clob_reacted": False,
            }
```

### 5.3 Cockpit UI Widget (`server/osc_dash.py`)
Add a dedicated card in Cockpit Tab 1:
```html
<div class="card" id="card-stream-telemetry">
  <div class="card-title">LIVE STREAM TELEMETRY (RTDS vs CLOB)</div>
  <div class="telemetry-grid">
    <div class="tel-item"><span class="tel-lbl">RTDS SPOT</span><span class="tel-val" id="telSpotPrice">--</span></div>
    <div class="tel-item"><span class="tel-lbl">SPOT DRIFT</span><span class="tel-val" id="telSpotDrift">--</span></div>
    <div class="tel-item"><span class="tel-lbl">CLOB MID</span><span class="tel-val" id="telClobMid">--</span></div>
    <div class="tel-item"><span class="tel-lbl">LEAD LATENCY</span><span class="tel-val" id="telLeadLatency">--</span></div>
    <div class="tel-item"><span class="tel-lbl">FEED HEALTH</span><span class="tel-badge ok" id="telFeedStatus">CONNECTED</span></div>
  </div>
</div>
```

---

## 6. Testing Strategy

### 6.1 Unit Tests (`tests/test_monitor_stream_latency.py`)
1. **Snapshot Formatting & Serialization**: Verify `StreamTickSnapshot` accurately computes drift, mid prices, and generates both formatted table rows and JSON dictionaries.
2. **Latency Auditor State Machine**:
   - Verify shock detection triggers on drift exceeding threshold.
   - Verify reaction detection records accurate $\Delta t$ when CLOB mid updates.
   - Verify timeout handling when CLOB does not react within `response_window_sec`.
   - Verify statistical summaries (mean, median, p95, reaction rate).
3. **CLI Arguments & Execution Modes**:
   - Verify parser options (`--series`, `--duration`, `--ticks`, `--json`, `--audit`, `--threshold`).
   - Mocked loop testing for clean termination on `--ticks` or `--duration`.

### 6.2 Integration Tests (`tests/test_osc_dash_integration.py`)
1. Verify `/api/live/latency` endpoint responds with 200 OK and valid JSON schema containing `spot_price`, `spot_drift`, `clob_mid`, and `latency_ms`.
2. Verify HTML template in `server/osc_dash.py` renders `#card-stream-telemetry` and telemetry display elements (`telSpotPrice`, `telSpotDrift`, `telClobMid`, `telLeadLatency`).

### 6.3 Regression Verification
- Run the full suite with `python -m pytest -q` ensuring all existing 200 tests pass without regression.

---

## 7. Boundaries

- **Always do:**
  - Enforce CLOB as the sole order execution venue.
  - Maintain thread safety when accessing shared state from background streaming workers.
  - Gracefully handle situations where the Polymarket WebSocket SDK is absent or idling by falling back to REST endpoints.
  - Keep test coverage comprehensive and ensure all 200+ tests pass.
- **Ask first:**
  - Altering the core quoting spread calculation or entry criteria in `strategy/live_trader.py`.
  - Adding any new external pip dependency.
- **Never do:**
  - Route trading orders to RTDS or treat RTDS spot prices as binary contract probabilities.
  - Introduce blocking network calls into FastAPI async route handlers.
  - Allow unhandled exceptions in the streaming loop to crash the server or CLI process.

---

## 8. Success Criteria

- [ ] `scripts/monitor_stream_latency.py` exists, is fully documented, and executable with `python -m scripts.monitor_stream_latency --series btc-up-or-down-5m`.
- [ ] Running with `--json` outputs clean, single-line JSON records.
- [ ] Running with `--audit` computes and prints empirical latency lead-time statistics.
- [ ] Documentation report `docs/rtds-clob-latency-audit.md` is published with empirical lead-lag dynamics and ground truth execution rules.
- [ ] FastAPI endpoint `/api/live/latency` is active and tested.
- [ ] Tab 1 (Cockpit) renders the live stream telemetry card and updates via SSE/polling.
- [ ] New unit tests in `tests/test_monitor_stream_latency.py` and integration tests in `tests/test_osc_dash_integration.py` pass.
- [ ] All 200+ existing project tests pass cleanly (`python -m pytest -q`).

---

## 9. Assumptions Surface

1. **RTDS Feed Latency**: RTDS publishes at 1s intervals; WebSocket network transport jitter is typically 50–200ms.
2. **BNB Handling**: As per project rules, BNB lacks Binance/Chainlink RTDS feeds and automatically utilizes the 1s Binance REST fallback.
3. **Execution Ground Truth**: CLOB BBO updates lag underlying spot moves during volatile price action, providing an informational window for fast stop-loss execution ahead of adverse fills.
