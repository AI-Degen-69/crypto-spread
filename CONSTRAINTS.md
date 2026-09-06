# Quality Guardrails & Constraints (`CONSTRAINTS.md`)

## 1. Test & Regression Bar (Non-Negotiable)
- **Zero Regression**: All 248 existing unit and integration tests must remain 100% green (`python -m pytest -q`).
- **Comprehensive Coverage**: New tests must be added covering:
  - Live Market Matrix card rendering:
    - Displays `FLAT (STOPPED OUT)` or `FLAT` when `status === 'STOP_EXIT'` or `exit_taken === true`.
    - Shows inactive/cancelled bids indicator instead of active resting bid prices when market is stopped out, drift-skipped, or not running.
  - Live Trader order lifecycle:
    - Retaining cancelled orders per window in paper and live modes.
    - Opposite leg cancellation during stop-loss exit.
    - Entry timeout / adverse open order cancellation and retention.
    - Window rollover clearing retained cancelled orders.
  - Orders Table:
    - Rendering status `CANCELED` for cancelled orders.
    - Disabling / omitting the `✖ Cancel` action button for cancelled or filled orders.
    - Excluding cancelled legs from false `Paired` grouping status.

## 2. Performance & Latency Thresholds
- **Zero Polling Overhead**: In-memory retention of window cancelled orders must have O(1) appending and minimal memory footprint (< 100 orders per cycle).
- **Client Render Performance**: DOM rendering must remain fast with template literals and pure vanilla JS, without UI lag during 1s SSE or polling refreshes.

## 3. Anti-Cheat Discipline
- **No Test Silencing**: No tests may be skipped, commented out, deleted, or assertions weakened to pass.
- **Strict Assertions**: Assertions must verify exact state transitions, dictionary fields, and rendered HTML/DOM structures.

## 4. Architectural Boundaries
- **No External Libraries**: No new dependencies in `requirements.txt`.
- **In-Memory Retention**: Cancelled orders are retained in memory per window lifecycle; no database or disk persistence requirement for cancelled order logs beyond existing trade logs.
- **Safety First**: Orders marked cancelled must never be re-quoted or submitted to CLOB.
