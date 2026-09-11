# CONSTRAINTS.md — Issue #138: per-fill queue-position telemetry

## Quality Gates & Hard Thresholds

### 1. Test Suite Integrity
- **Pass Rate**: 100% — `python -m pytest -q` fully green; targeted gate `python -m pytest tests/test_live_trader.py -q` green plus new tests.
- **No Test Swallowing**: strictly forbidden to skip, comment out, or mock-pass failing assertions; every fill path needs a telemetry-line test.
- **Anti-Cheat**: no weakening of fill assertions, no stub telemetry (every claimed field must be really computed or explicitly `null`).

### 2. Observation-Only Boundaries
- **Zero behavior change**: quoting, fill, chase, stop, timeout, and rollover logic byte-for-byte identical; telemetry code paths must not gate, delay, or mutate trading state (append-only side effects).
- **Best-effort I/O**: tape fetch and file append wrapped so any exception → `null` fields + warning log; a failed write never raises into the fill path. No new network calls in the per-tick hot path (tape joined on-demand at fill time only; fills are rare, ≤25/market).
- **Degenerate nulls**: empty/missing book side → `queue_ahead_at_rest=null`; missing tape → printed/ratio `null`; ratio denominator guarded (`max(queue,1)`).
- **File location**: sidecar strictly under `run/` (gitignored); analysis script reads — never writes — live state.

### 3. Performance & Dependencies
- **Per-tick cost**: only small dict copies for the book stash + one arithmetic sum per resting leg; no I/O per tick.
- **Dependencies**: no new external libraries without explicit approval (stdlib `json` + existing `requests` session only).
