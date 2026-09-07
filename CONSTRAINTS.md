# CONSTRAINTS.md — Issue #93 Quality & Architectural Constraints

## 1. Testing & Zero Regressions
- All existing tests must remain 100% passing: `python -m pytest -q` (293 tests at
  time of filing, plus the new one).
- New tests (TDD, red→green) must cover:
  - Populated market (entry + advance + stop + exit + `cancelled_orders`) →
    `reset_pnl()` → `get_open_orders_list() == []` (stopped/paper path).
  - Every listed handle cleared + `_orders_cache_ts == 0.0` + no
    `filled_*=False`/`order_status_*=FILLED` contradiction.
  - Live + running + outstanding → refusal dict, no state cleared, endpoint
    carries Stop-first message.
  - Live + stopped → mocked CLOB cancel asserted before handles cleared.
  - Paper → no CLOB interaction (`get_clob_client` never called / returns None).
- Targeted gates per task: `python -m pytest tests/test_live_trader.py -q`,
  `python -m pytest tests/test_osc_dash_integration.py -q`.
- Existing coverage to extend, not replace: `test_live_trader_reset_pnl`
  (`test_live_trader.py:120-130`), `test_reset_pnl_clears_open_positions`
  (`:1017-1028`), `reset_pnl` endpoint tests (`test_osc_dash_integration.py:650-675`).

## 2. Anti-Cheating & Integrity
- Strictly no disabling, skipping, or weakening existing tests/assertions.
- No silent venue-side cancel in live-running state — refusal path only.
- No dropping order ids without a cancel attempt on the live-stopped path; cancel
  errors must surface, never be swallowed into a fake success.
- No changes to PnL math, fill detection, rollover, or `stop()`/`cancel_all_orders()`
  semantics; reset reuses them, does not rewrite them.

## 3. Performance & Integration Guardrails
- No new dependencies in `requirements.txt` (stdlib + existing CLOB client only).
- `reset_pnl()` stays synchronous and fast: O(markets), one venue cancel burst max,
  no per-tick work; 5s orders cache path (`get_state():1647-1650`) untouched except
  invalidation.
- Contract change is additive/narrow: `reset_pnl()` return dict + refusal HTTP
  status on one branch; no other endpoint shapes change.
- Threading: all local clears under `self._engine_lock` (match `stop()`/`cancel_all_orders()`).
