# Plan: Issue #151 — Dashboard collector toggle is blind to the external standalone collector

Task Type: Debug + Code
Size Tier: Standard
Target Files: server/osc_dash.py, tests/test_osc_dash_integration.py

Decisions locked with user: freshness threshold 5s on manifest `ts` (per issue) ·
  start refusal = HTTP 409 with explicit reason · stop stays no-op when no child ·
  `collect_ticks` untouched.

## Task Breakdown

### Task 1: Detection helper + status `source` field (TDD)
- **Files**: `server/osc_dash.py`, `tests/test_osc_dash_integration.py`
- **Type**: Debug + Code
- **Description**:
  1. RED: failing test — fresh manifest (`ts` = now) + `_collector_proc = None`
     → `GET /api/collector/status` reports `source == "external"`,
     `external is True`, `running is False`.
  2. GREEN: add `EXTERNAL_COLLECTOR_STALE_SEC = 5.0` +
     `_detect_external_collector()` (never raises; missing/corrupt manifest →
     `live: False`) and wire `source`/`external`/`manifest_age_sec` into
     `api_collector_status` without changing existing keys.
- **Status**: [x]
- **Verification**: `python -m pytest tests/test_osc_dash_integration.py -q -k "external or collector"`

### Task 2: Start guard — 409 while external live (TDD)
- **Files**: `server/osc_dash.py`, `tests/test_osc_dash_integration.py`
- **Type**: Debug + Code
- **Description**:
  1. RED: failing test — fresh manifest + no child →
     `POST /api/collector/start` returns 409 with `ok is False` and a reason
     mentioning the external collector, and no process is spawned (Popen mock
     not called).
  2. GREEN: guard in `api_collector_start`; child-running and none states
     behave exactly as today.
- **Status**: [x]
- **Verification**: `python -m pytest tests/test_osc_dash_integration.py -q -k "collector"`

### Task 3: Status matrix tests (none / external-only / child-only)
- **Files**: `tests/test_osc_dash_integration.py`
- **Type**: Code
- **Description**:
  1. `none`: no manifest (tmp TICKS_DIR), no child → `source == "none"`.
  2. `external-only`: fresh manifest, no child → `source == "external"`.
  3. `child-only`: DummyProc child + stale/missing manifest → `source == "child"`.
  4. Stale manifest (ts older than 5s) + no child → `source == "none"`.
  5. Lifecycle test isolated to tmp TICKS_DIR (real fresh manifest must not
     make it flaky — isolation, not weakening).
- **Status**: [x]
- **Verification**: `python -m pytest tests/test_osc_dash_integration.py -q -k "collector"`

### Task 4: Banner + toggle UI truth (external state + 409 reason)
- **Files**: `server/osc_dash.py` (inline JS: `refreshCollectorStatus`, `toggleCollector`)
- **Type**: Design + Code
- **Description**:
  1. `refreshCollectorStatus()`: `source == "external"` → badge
     `Collector: 🟡 External live · N ticks today`, toggle button reads
     `Start blocked (external live)` and is disabled.
  2. `toggleCollector()`: on 409, render the refusal reason in the badge
     instead of silently re-rendering.
  3. Existing child/none rendering unchanged.
- **Status**: [x]
- **Verification**: `rg -n "External live|start-blocked|source" server/osc_dash.py`; existing test `test_osc_dash*` HTML assertions (`collectorBadge`) still pass

### Task 5 (OPTIONAL — needs operator sign-off): guard `poll-once` too
- **Files**: `server/osc_dash.py`, `tests/test_osc_dash_integration.py`
- **Type**: Code
- **Description**: `POST /api/collector/poll-once` appends to the same daily
  file, so it is the same corruption vector. Refuse with 409 while external
  live. NOT part of the issue scope — implement only if the operator approves
  the improvement below.
- **Status**: [ ] (blocked on approval)
- **Verification**: `python -m pytest tests/test_osc_dash_integration.py -q -k "poll_once"`

### Task 6: Full regression gate
- **Files**: —
- **Type**: Code
- **Description**:
  1. `python -m pytest -q` (0 failures).
  2. `git status --short` shows only `server/osc_dash.py`,
     `tests/test_osc_dash_integration.py`, `SPEC.md`, `CONSTRAINTS.md`,
     `tasks/plan.md`, `tasks/todo.md`.
- **Status**: [x]
- **Verification**: `python -m pytest -q` + `git status --short`
