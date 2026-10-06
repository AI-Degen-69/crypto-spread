# CONSTRAINTS.md — Quality Bar & Boundaries for Issue #462

## 1. Zero Regressions
- `python -m pytest tests/test_live_trader.py -q` must pass — the engine state contract and the existing timing instrumentation (#221) share these fields.
- `python -m pytest tests/test_osc_dash_integration.py -q` must pass — the cockpit render path and the SSE framing are asserted there.
- `python -m pytest tests/test_live_trader_streaming.py -q` must pass — the stream payload rides `get_state()`.
- Green at the final verification gate is the PR condition; a red test inside Task 1 is the intended TDD step.

## 2. Performance
- No new I/O, network call, or per-tick allocation in the hot path: the engine change writes two attributes per tick and reads them only inside `get_state()`.
- `get_state()` may not gain a lock acquisition per market; it already copies under `_engine_lock` where needed.
- The page may not gain a poll: the pill rides the existing 5s cockpit refresh and SSE envelopes.

## 3. Anti-Cheat
- No skipping, `xfail`-ing, or weakening assertions. No lint/type suppressions.
- No `except Exception` in the engine for this feature: the recorder takes the exception it is handed; it does not catch. Per-market catching is #461's deliverable and must not be smuggled in here.
- The integration assertions must test rendered behaviour (a pill class/text in the produced HTML/JS contract), not just that a constant exists.

## 4. Boundaries — do not touch
- Trading logic: quoting, entry/exit gates, sizing, stop/bid resolution, and anything consumed by a trade decision.
- `_tick_all_markets`'s exception behaviour: no new **`except`** around the strategy call, so a failing update still aborts the tick exactly as it did before — #461 owns per-market isolation. A `try/finally` that only resets the in-flight attribution mark is allowed: it catches nothing and changes no propagation.
- The stream protocol shape (`DashboardEnvelope`, event names) and `/api/live/state`'s existing fields.
- No new dependencies; the dashboard stays a single self-contained FastAPI module.

## 5. Invariants
- Display-only: no health value may be read by a trading path (SPEC §4).
- A stopped engine never reports staleness.
- Untrusted text (an exception message) is escaped before it reaches HTML or a tooltip.
