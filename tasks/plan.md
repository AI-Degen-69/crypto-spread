# Implementation Plan: Side-by-Side RTDS vs CLOB Live Stream Monitor & Price Latency Audit (Issue #54)

## Overview
Build a real-time stream comparison engine, standalone CLI tool (`scripts/monitor_stream_latency.py`), empirical latency audit with documentation report (`docs/rtds-clob-latency-audit.md`), and Cockpit dashboard integration (`server/osc_dash.py`). The system synchronously captures Polymarket RTDS Binance spot ticks and Polymarket CLOB binary contract order books in the exact same second, measuring price drift, empirical reaction lead times ($\Delta t$), and establishing ground truth execution rules.

## Architecture Decisions
1. **Reuse Existing Streaming Infrastructure**: Leverage `strategy/streaming.py` (`UnifiedStreamBridge`, `RTDSStreamClient`, `CLOBMarketWSClient`) with automatic REST fallbacks for reliable tick capture without adding new third-party dependencies.
2. **Deterministic Time Alignment**: Pair spot ticks and CLOB order book state to the nearest second, calculating both price drift from the baseline open and transit latency ($\Delta t$ ms).
3. **Formal State Machine for Lead-Time Tracking**: Implement `LatencyAuditor` to detect spot price shocks ($\ge 0.10\%$ within $\le 3\text{s}$) and measure the elapsed time until the CLOB book shifts (BBO moves $\ge 1¢$ or mid changes), tracking reaction rates and percentiles (median, p95).
4. **Execution Ground Truth**: Explicitly document and enforce that CLOB is the sole execution venue and ground truth pricing; RTDS is an external reference/leading indicator for stop-loss execution.
5. **Zero-Friction Cockpit Integration**: Expose telemetry via `/api/live/latency` and a dedicated telemetry card in Tab 1 (Cockpit) of `server/osc_dash.py` without disturbing existing bot controls or layout.

## Task List

### Phase 1: Stream Synchronizer & CLI Monitor (`stream-monitor`)
- [x] Task 1: Implement `StreamTickSnapshot` and core synchronizer in `scripts/monitor_stream_latency.py`
  - Acceptance: Ingests RTDS spot ticks and CLOB book for any target series in `strategy/series.py`. Aligns them by timestamp. Computes spot drift % and CLOB mid. Provides string formatting for console table rows and dictionary conversion for JSON.
  - Verify: `python -m pytest tests/test_monitor_stream_latency.py -k test_snapshot -q`
  - Files: `scripts/monitor_stream_latency.py`, `tests/test_monitor_stream_latency.py`

- [x] Task 2: CLI arguments, execution loop, and JSON output mode
  - Acceptance: CLI supports `--series`, `--duration`, `--ticks`, `--threshold`, and `--json`. Prints formatted table or single-line JSON records. Gracefully exits on Ctrl+C or when limits expire.
  - Verify: `python -m scripts.monitor_stream_latency --series btc-up-or-down-5m --ticks 2 --json`
  - Files: `scripts/monitor_stream_latency.py`, `tests/test_monitor_stream_latency.py`

### Checkpoint 1: Core Stream Monitor Operational
- [x] Snapshot formatting, alignment, and CLI unit tests pass.
- [x] CLI runs smoke test with `--ticks 2` and exits cleanly.

### Phase 2: Latency Lead-Time Audit Engine & Documentation (`latency-audit`)
- [x] Task 3: Implement `LatencyAuditor` state machine and `--audit` summary reporting
  - Acceptance: `LatencyAuditor` detects spot shocks, measures elapsed time until CLOB response within a 10s window, and calculates summary metrics (total shocks, reaction count, reaction rate %, median and p95 reaction latency). `--audit` flag prints formatted summary on completion.
  - Verify: `python -m pytest tests/test_monitor_stream_latency.py -k test_latency_auditor -q`
  - Files: `scripts/monitor_stream_latency.py`, `tests/test_monitor_stream_latency.py`

- [ ] Task 4: Publish empirical latency audit report in `docs/rtds-clob-latency-audit.md`
  - Acceptance: Documentation report detailing empirical lead times, lead-lag dynamics, RTDS leading signal vs CLOB execution ground truth, stop-loss trigger mechanics, and unit non-fungibility.
  - Verify: Document exists with verified links and clear quantitative tables.
  - Files: `docs/rtds-clob-latency-audit.md`

### Checkpoint 2: Latency Audit Complete & Documented
- [ ] Auditor unit tests pass.
- [ ] Documentation report is published and fully detailed.

### Phase 3: Dashboard Cockpit Integration (`cockpit-telemetry`)
- [ ] Task 5: Add `/api/live/latency` endpoint in `server/osc_dash.py`
  - Acceptance: GET `/api/live/latency` returns JSON with `spot_price`, `spot_drift`, `clob_mid`, `latency_ms`, and feed health metrics.
  - Verify: `python -m pytest tests/test_osc_dash_integration.py -k test_api_live_latency -q`
  - Files: `server/osc_dash.py`, `tests/test_osc_dash_integration.py`

- [ ] Task 6: Add Live Stream Telemetry card to Cockpit (Tab 1) in `server/osc_dash.py`
  - Acceptance: Renders `#card-stream-telemetry` displaying RTDS Spot, Drift %, CLOB Mid, Lead Latency, and Feed Health. Updates via `pollCockpit()` and SSE.
  - Verify: `python -m pytest tests/test_osc_dash_integration.py -q`
  - Files: `server/osc_dash.py`, `tests/test_osc_dash_integration.py`

### Checkpoint 3: End-to-End Verification
- [ ] All new tests pass.
- [ ] Full regression suite passes cleanly: `python -m pytest -q` (all 200+ tests).

## Risks and Mitigations
| Risk | Impact | Mitigation |
|------|--------|------------|
| Polymarket WebSocket SDK unavailable in environment | Low | `RTDSStreamClient` and `CLOBMarketWSClient` include automatic fallback to REST polling, ensuring uninterrupted operation. |
| Inactive or low-liquidity CLOB books showing stale prices | Medium | Synchronizer tracks timestamp of last book update and marks stale quotes if gap exceeds 10s. |
| High-frequency SSE broadcast saturating client browser | Low | Limit envelope queue size to 100 with safe drop on full, plus 1s tick throttle. |

## Open Questions
- None. Requirements, CLI interface, and API contracts are fully resolved in `SPEC.md`.
