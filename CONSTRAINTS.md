# CONSTRAINTS.md — Issue #139: cockpit queue panel + PnL histogram

## Quality Gates & Hard Thresholds

### 1. Test Suite Integrity
- **Pass Rate**: 100% — `python -m pytest -q` fully green; targeted gate `python -m pytest tests/test_osc_dash_integration.py -q` green plus new tests.
- **No Test Swallowing**: no skipped assertions; histogram/empty-state behavior covered by HTML-presence + endpoint-math tests (JS rendering itself is visually verified, same as the existing equity chart).
- **Anti-Cheat**: no weakening of existing dashboard assertions to fit new markup.

### 2. UI & API Boundaries
- **No new dependencies**: inline SVG + vanilla JS only (repo ships no chart library).
- **Read-only endpoint**: no trading-state mutation; file-not-found → explicit empty payload, never 500.
- **XSS**: all interpolated trade/market strings through the existing `esc()` helper.
- **Scope honesty**: histogram subtitle states the session window (same trades the table shows); verdict rule deterministic and documented.
- **Perf**: endpoint work is a small-file read + aggregation; bootstrap (2,000 resamples) runs client-side on ≤50 values only.

### 3. Dependencies
- None new (stdlib + existing FastAPI/pydantic only).
