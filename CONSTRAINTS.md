# CONSTRAINTS.md — Issue #151: external collector detection + start guard

## Quality Gates & Hard Thresholds

### 1. Test Suite Integrity
- **Pass Rate**: 100% — `python -m pytest -q` fully green; targeted gate
  `python -m pytest tests/test_osc_dash_integration.py -q` green.
- **No Test Swallowing**: no skipped assertions; the status matrix covers all
  three states (none / external-only / child-only); existing collector tests
  (`test_api_collector_lifecycle_and_status`, tape-metrics test) keep passing
  unmodified in behavior.
- **Anti-Cheat**: no weakening of existing collector assertions to fit the
  change; no deleting tests that reference old response shapes without
  replacing their coverage; no silencing of the 409 path in tests.

### 2. Behavior & Scope Boundaries
- **No writer changes**: `scripts/collect_ticks.py` untouched — no locking,
  rotation, or arg changes (out of scope per issue).
- **Detection is read-only**: `_detect_external_collector` only reads
  `manifest.json`; never writes, kills, or touches any process.
- **Stop semantics unchanged**: `POST /api/collector/stop` with no dashboard
  child stays a no-op success (must NOT attempt to kill the external process).
- **Backward compatible**: `/api/collector/status` keeps all existing keys;
  only additive fields (`source`, `external`, `manifest_age_sec`).

### 3. Perf & Dependencies
- **Perf**: status endpoint does one extra small-file read (`manifest.json`);
  no subprocess spawn, no process enumeration (no `psutil`).
- **Dependencies**: none new (stdlib only).
