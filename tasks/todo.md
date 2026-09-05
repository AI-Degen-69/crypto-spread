# Tasks: Issue #54 Side-by-Side RTDS vs CLOB Live Stream Monitor & Price Latency Audit

- [x] Task 1: Implement `StreamTickSnapshot` and core synchronizer in `scripts/monitor_stream_latency.py`
  - Acceptance: Ingests RTDS spot ticks and CLOB book for any target series in `strategy/series.py`. Aligns them by timestamp. Computes spot drift % and CLOB mid. Provides string formatting for console table rows and dictionary conversion for JSON.
  - Verify: `python -m pytest tests/test_monitor_stream_latency.py -k test_snapshot -q`
  - Files: `scripts/monitor_stream_latency.py`, `tests/test_monitor_stream_latency.py`

- [x] Task 2: CLI arguments, execution loop, and JSON output mode
  - Acceptance: CLI supports `--series`, `--duration`, `--ticks`, `--threshold`, and `--json`. Prints formatted table or single-line JSON records. Gracefully exits on Ctrl+C or when limits expire.
  - Verify: `python -m scripts.monitor_stream_latency --series btc-up-or-down-5m --ticks 2 --json`
  - Files: `scripts/monitor_stream_latency.py`, `tests/test_monitor_stream_latency.py`

## Checkpoint 1: Core Stream Monitor Operational
- [x] Snapshot formatting, alignment, and CLI unit tests pass.
- [x] CLI runs smoke test with `--ticks 2` and exits cleanly.

- [ ] Task 3: Implement `LatencyAuditor` state machine and `--audit` summary reporting
  - Acceptance: `LatencyAuditor` detects spot shocks, measures elapsed time until CLOB response within a 10s window, and calculates summary metrics (total shocks, reaction count, reaction rate %, median and p95 reaction latency). `--audit` flag prints formatted summary on completion.
  - Verify: `python -m pytest tests/test_monitor_stream_latency.py -k test_latency_auditor -q`
  - Files: `scripts/monitor_stream_latency.py`, `tests/test_monitor_stream_latency.py`

- [ ] Task 4: Publish empirical latency audit report in `docs/rtds-clob-latency-audit.md`
  - Acceptance: Documentation report detailing empirical lead times, lead-lag dynamics, RTDS leading signal vs CLOB execution ground truth, stop-loss trigger mechanics, and unit non-fungibility.
  - Verify: Document exists with verified links and clear quantitative tables.
  - Files: `docs/rtds-clob-latency-audit.md`

## Checkpoint 2: Latency Audit Complete & Documented
- [ ] Auditor unit tests pass.
- [ ] Documentation report is published and fully detailed.

- [ ] Task 5: Add `/api/live/latency` endpoint in `server/osc_dash.py`
  - Acceptance: GET `/api/live/latency` returns JSON with `spot_price`, `spot_drift`, `clob_mid`, `latency_ms`, and feed health metrics.
  - Verify: `python -m pytest tests/test_osc_dash_integration.py -k test_api_live_latency -q`
  - Files: `server/osc_dash.py`, `tests/test_osc_dash_integration.py`

- [ ] Task 6: Add Live Stream Telemetry card to Cockpit (Tab 1) in `server/osc_dash.py`
  - Acceptance: Renders `#card-stream-telemetry` displaying RTDS Spot, Drift %, CLOB Mid, Lead Latency, and Feed Health. Updates via `pollCockpit()` and SSE.
  - Verify: `python -m pytest tests/test_osc_dash_integration.py -q`
  - Files: `server/osc_dash.py`, `tests/test_osc_dash_integration.py`

## Checkpoint 3: End-to-End Verification
- [ ] All new tests pass.
- [ ] Full regression suite passes cleanly: `python -m pytest -q` (all 200+ tests).
