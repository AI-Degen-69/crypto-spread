Branch: i449/fixpaper-clear-unfilled-entry-orders-at-window-rol | Issue: #449

# Implementation Plan — Clear Unfilled Paper Entry Orders at Window Rollover

## Size & Stack
- Tier: **Small** — one source module (`strategy/live_trader.py`, rollover + synthesis cluster) plus regression tests; single decision (mirror live cleanup locally, no venue calls).
- Task type: **Code + Debug**. Stack: Python, pytest (`tests/test_live_trader.py`, `tests/test_stop_orders.py`). No UI, no API, no dependency change.

## CodeRabbit Intake Note
- Adopted: 4-task skeleton merged into 3 atomic tasks; seam pointers (rollover 5107+, synthesis 2366-2408, reset 5242+, cache 3607-pattern); test cases near `test_window_rollover_clears_cancelled_orders`.
- Rejected: over-split Phase/Task ceremony and invented abstractions — merged into one source task; no new files, no helper extraction unless direct reuse of the pre-quote computation proves impossible.
- `[UNVERIFIED]` at intake: none left — every cited seam spot-checked against live code (5122-5127 live gate, 5214-5240 promotion, 5237-5240 else-clear, 5242+ reset without resting_up/down reset, 2366-2408 synthesis without handle/anchor checks, 2414-2428 retained guard, 2592-2594 cache, 3607 invalidation pattern, 1118-1151 stop-clear, 1402+ `_clear_order_handles` with paper provenance).

## Resolved Open Questions (from code, not asked)
- **Cancel-sim vs handle-clear:** resolved from code. `_clear_order_handles` (`live_trader.py:1402`) already records a `CANCELLED` row with `PAPER_SIMULATION` provenance and clears the handle; the rollover reset (`5244`) clears `cancelled_orders` later in the same function. Decision: call `_clear_order_handles` under `_engine_lock` for each unfilled paper entry leg — visible transition, then reset removes it. Matches the existing rollover-clears-history contract (`test_window_rollover_clears_cancelled_orders`).
- **Retained `end_ts` guard coverage:** resolved from code. Guard at 2416-2418 skips settled windows; new paper rows are recorded then removed by the same reset, so they never reach the list. No guard change needed.
- **Stop handle:** resolved from code. `_cancel_stop_order` (1118) clears paper STAGED stops locally already; only RESTING live stops need venue cancel. No stop change needed — tests preserve the behavior.
- **type-design-analyzer / code-explorer:** skipped — no interface change, execution path traced directly in `live_trader.py`.

## Spec (embedded — Small tier)
- Goal: after paper-mode window rollover, OPEN ORDERS shows no leg priced from the dead window's anchor.
- Acceptance: (1) unfilled paper entry legs cleared, no stale rows/IDs/prices; (2) live venue cancels fire exactly as before; (3) new regression test for resting paper legs; (4) targeted suites green.
- Out of scope: live cancel flow, CLOB client, backtest engine, dashboard rendering.

## Improvement Proposal (adopted — edge-case hardening)
- Evidence (code): "`get_state` caches the order list for 5 seconds. Only PnL reset and demo seeding invalidate the cache." + `_orders_cache_ts = 0.0` pattern at 3607.
- Proposal: reset `_orders_cache_ts` at the end of completed rollover (both modes, local timestamp only) so `get_state` cannot serve pre-rollover rows for 5s. Adopted into Task 1 (not on the failed-stop early-return path, which changes no order state).

## Tasks

### [x] Task 1: [Debug] Paper rollover cleanup in `strategy/live_trader.py` (M)
- **Files:** `strategy/live_trader.py` (`_handle_window_rollover`, `get_open_orders_list` synthesis block)
- **Depends on:** none (riskiest: synthesis-guard interaction with existing QUOTING tests — first)
- **Description:** (a) paper branch beside live entry-cancel (5122-5127): for each unfilled leg with a current entry handle, call `_clear_order_handles` under `_engine_lock`, no venue calls; (b) paper-only quote reset: when no advance handle is promoted, set `resting_up/down` to `None`; synthesis skips legs with `None` resting price; (c) promotion pricing: promoted paper legs get the advance quote price (reuse pre-quote computation, no formula copy); synthesis skips the synthetic row when a tracked current entry handle exists (one row per leg); (d) reset `_orders_cache_ts` at end of completed rollover, both modes, not on failed-stop early return. Verify entry gates refuse `None` resting price (add minimal guard only if a gap exists).
- **Skill:** `debugging-and-error-recovery`
- **Verification:** new regression tests (Task 2) fail-before/pass-after for the stale-row case.

### [x] Task 2: [Debug] Rollover regression tests in `tests/test_live_trader.py` (S)
- **Files:** `tests/test_live_trader.py` (near `test_window_rollover_clears_cancelled_orders`)
- **Depends on:** Task 1
- **Description:** (a) resting-paper-entry test: QUOTING market, both tokens, two RESTING handles, asymmetric prices (0.52/0.01), `get_state()` to fill cache, rollover → handles cleared, nothing RESTING, no old IDs/prices in list or state, no `cancel*` client calls, paper stop handle cleared; (b) promotion test: seeded advance handles → promoted current, exactly one row per leg at advance price; (c) STOP_EXIT_PENDING test: one filled leg → exit recorded, no handles remain, status QUOTING; (d) live test: patched `cancel_live_order` returns True → exactly one call per unfilled leg ID.
- **Skill:** `test-driven-development`
- **Verification:** `python -m pytest tests/test_live_trader.py -q` green.

### [x] Task 3: [Backend/Logic] Targeted verification sweep (XS)
- **Files:** none (verification only)
- **Depends on:** Task 2
- **Description:** Run `python -m pytest tests/test_live_trader.py -q` and `python -m pytest tests/test_stop_orders.py -q`. Confirm no full-suite local run. Confirm glossary terms in comments ("entry leg", "advance handle", "stop handle").
- **Skill:** `incremental-implementation`
- **Verification:** both suites green; branch clean except intended files.

## Checkpoints
- After Task 1: paper rollover leaves no handle/price/cache from the dead window (shown by Task 2 tests).
- After Task 3: plan acceptance criteria provable, ready for `iii-build-plan` handoff review.
