# Plan: Issue #139 — cockpit queue-telemetry panel + PnL histogram

Task Type: Design + Code
Size Tier: Standard
Target Files: server/osc_dash.py, tests/test_osc_dash_integration.py

## Task Breakdown

### Task 1: Queue-telemetry endpoint (aggregation, verdict, empty state)
- **Files**: `server/osc_dash.py` (new `GET /api/live/queue_telemetry`)
- **Type**: Code
- **Description**:
  1. Read `run/live_fill_telemetry.jsonl` + settlement join by importing `bucketize`/readers from `scripts/bucket_fills` (no duplicated bucket logic).
  2. Return buckets [{count, mean}], chased {count, mean} separately, total fills, deterministic verdict (`awaiting fills`/`tape-like`/`queue-toxic`/`mixed/unclear`).
  3. Missing/empty/malformed file → explicit empty payload, HTTP 200.
  4. Adopted improvement: 5s server-side TTL cache on the aggregated payload (mirrors `_orders_cache`), so concurrent viewers × 5s polls don't re-read/re-bucket per tick.
- **Status**: [x]
- **Verification**: `python -m pytest tests/test_osc_dash_integration.py -q -k queue_telemetry`

### Task 2: Queue panel UI (bars, verdict, empty state, fallback)
- **Files**: `server/osc_dash.py` (cockpit HTML + JS render fn)
- **Type**: Design + Code
- **Description**:
  1. New card/panel near the trades area: 4 SVG bucket bars + mean annotations + chased row + verdict line, dark theme, `<title>` tooltips.
  2. `<details>` table fallback with identical numbers; explicit empty message when endpoint reports empty.
  3. Fetch in `fetchCockpitState` (same 5s cadence); all strings via `esc()`.
- **Status**: [x]
- **Verification**: integration asserts panel IDs + fallback markup in cockpit HTML

### Task 3: PnL histogram UI (binning, zero bin, mean + CI-lo, fallback)
- **Files**: `server/osc_dash.py` (cockpit HTML + JS render fn)
- **Type**: Design + Code
- **Description**:
  1. Client-side render from `st.trades`: Freedman–Diaconis bins capped 12–20, exact-zero own bin, profit/loss colors, mean + 2,000-resample bootstrap CI-lo annotation, subtitle stating the session window.
  2. `<details>` table fallback; count equals the trades-table row count.
- **Status**: [ ]
- **Verification**: integration asserts histogram IDs + fallback markup; manual screenshot via dashboard

### Task 4: Tests + regression gate
- **Files**: `tests/test_osc_dash_integration.py`
- **Type**: Code
- **Description**:
  1. Endpoint math on synthetic telemetry (buckets, chased separation, verdict transitions, empty-file state).
  2. Cockpit HTML assertions (panel/histogram IDs, fallback elements, empty-state strings).
  3. Targeted gate then full suite.
- **Status**: [ ]
- **Verification**: `python -m pytest tests/test_osc_dash_integration.py -q` then `python -m pytest -q`
