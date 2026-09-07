# CONSTRAINTS.md — Issue #87 Quality & Architectural Constraints

## 1. Testing & Zero Regressions
- All existing tests must remain 100% passing: `python -m pytest -q` (277 passed with this branch).
- New tests (TDD, red→green) must cover:
  - Stop placement on single-leg fill (live with mocked CLOB + paper).
  - Pair completion cancels the resting stop before `PAIR_MERGED` (OCO Case A).
  - Stop fill cancels the opposite entry and records `STOP_EXIT` (OCO Case B).
  - Window rollover cancels the resting stop and resets stop fields (OCO Case C).
  - Resting stop visible in `get_open_orders_list()` / dashboard Orders table.
  - Idempotent placement (no duplicate stop orders).

## 2. Anti-Cheating & Integrity
- Strictly no disabling, skipping, or mocking out real assertions to achieve passing tests.
- Stop-staging failures must never be masked: log at WARNING minimum. The stop is a pre-signed in-memory buffer (STAGED); venue exposure happens only at trigger time via `_execute_stop_exit`. A venue-side stop-cancel failure retains the handle as CANCEL_FAILED and blocks the pair merge until it succeeds.
- No modifications to entry quoting prices (`resting_up`/`resting_down`), default `offset = 0.02`, or `exit_thresh = 0.05`.

## 3. Performance & Integration Guardrails
- No new dependencies in `requirements.txt` (stdlib + existing `py_clob_client` / `py_clob_client_v2` only).
- Hot polling path: at most one extra `get_order(stop_id)` per tick per single-leg market (same pattern as existing entry-order fill checks). Stop *staging* touches the venue zero times (in-memory buffer); only the post-trigger monitored exit may submit one `create_and_post_order`, never per-tick.
- All shared `MarketLiveState` mutations under `self._engine_lock`, log style `[%s] slug` prefix.
