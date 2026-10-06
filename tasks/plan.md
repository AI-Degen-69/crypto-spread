Branch: i462/feat-dash-tick-health-pill | Issue: #462

# Implementation Plan — Per-Market Tick Health in the Live Cockpit

## Size & Stack
- **Tier: Standard** — four files across two layers (engine state + dashboard render), one architectural decision (health is display-only state derived from an existing timestamp, recorded through a single helper).
- **Task type: Code + Design/UI**. Stack: Python 3.12, FastAPI + inline JS/CSS in `server/osc_dash.py`, pytest. Verification: unit tests for the engine half, the dashboard's existing integration tests for the render half, and Station IV's browser gate for the visual result (this station runs no browser).

## CodeRabbit Intake Note
- **Adopted:** the whole skeleton — display-only `TICK_STALE_THRESHOLD_SEC` module constant; hidden runtime attrs added in `MarketLiveState.__post_init__` (the #221 pattern already there); `_record_tick_error` + `_attribute_tick_failure` helpers; the in-flight-slug attribution inside `_tick_all_markets`; health fields added inside the existing `win_duration_sec` enrichment loop in `get_state()`; `.pill-stale` CSS, a header stale badge, and an escaped tooltip; and its three rules (no health in trading decisions, no per-market try/except because that is #461, always `esc()` untrusted text).
- **Rejected / superseded:** the issue body's own suggestion to attribute by "newest `_last_strategy_tick_perf`" — CodeRabbit's in-flight-slug variant (its Design Choice 2) is exactly as cheap and does not blame the last market when the error comes from a later step, so the plan takes that instead and keeps the heuristic label.
- **`[UNVERIFIED]` left:** none. Spot-checked against live code: `MarketLiveState.__post_init__` at `strategy/live_trader.py:831-834`, module constants at `:97-120`, `get_state()`'s enrichment loop at `:2578-2588`, `_tick_all_markets` dispatch at `:3872-3886`, `_run_loop` catch at `:3867-3869`, CSS pills at `server/osc_dash.py:5696-5698`, header badge at `:6695`, `esc` at `:6793`, grid render at `:13341-13362`, cockpit poll `setInterval(pollCockpit, 5000)` at `:14419`.

## Resolved Open Questions (from code, not asked)
- **Threshold:** `TICK_STALE_THRESHOLD_SEC = 3.0` — three missed ticks at the engine's 1s cadence (`_run_loop` sleeps 1.0s). Module constant, display-only, matching `FILL_RATIO_FLAG_THRESHOLD` and friends.
- **Attribution source:** the in-flight slug set around each `_update_market_strategy` call, read by `_run_loop`'s existing `except`. Exact for errors raised inside a market's own update; a heuristic for errors raised by tick bookkeeping, which a comment states. #461's guard will call the same recorder for exact attribution.
- **No-update-yet market:** `tick_age_sec = None`, `tick_stale = false` — a fresh start must not paint five markets red.
- **Inactive series:** no pill; the grid iterates the active series only.

## Spec
See `SPEC.md` (Standard tier): objective, 7 acceptance criteria, edge cases, and the display-only rule.

## Improvement Proposal
**Adopted (simplification, evidence-backed).** Evidence, verbatim from the issue body: *"the staleness signal does not depend on it"* and from `strategy/live_trader.py:3991-3994`, where `mstate._last_strategy_tick_perf` is already maintained per market next to the #221 interval instrumentation. Proposal: build the whole health contract on that existing timestamp — no new clock, no new per-tick timestamp field, no new endpoint — and keep the failure history as plain runtime attributes so #461 can feed it without touching this surface. Folded into the tasks below.

## Tasks

### [x] Task 1: [Backend/Logic] Engine tick-health state and its tests (M)
- **Files:** `strategy/live_trader.py`, `tests/test_live_trader.py`
- **Depends on:** none (riskiest: it defines the state contract everything else renders)
- **Description:** (a) module constant `TICK_STALE_THRESHOLD_SEC = 3.0`; (b) in `MarketLiveState.__post_init__`, add `_last_tick_error: Optional[str] = None`, `_last_tick_error_perf: Optional[float] = None`, `_tick_error_count: int = 0` (runtime-only, matching #221's non-serialized telemetry); (c) `LiveTraderEngine.__init__`: `_inflight_strategy_slug = None`; (d) `_record_tick_error(slug, exc)` — one write path: stores `str(exc)`, the perf timestamp, and increments the count; (e) `_attribute_tick_failure(exc)` — reads `_inflight_strategy_slug`, records against that market when set, otherwise logs without attribution; (f) in `_tick_all_markets`, set `_inflight_strategy_slug = slug` before `self._update_market_strategy(slug, res, now)` and clear it in a `finally`; (g) `_run_loop`'s `except Exception` calls `_attribute_tick_failure(e)` with a comment naming the heuristic; (h) `get_state()`'s enrichment loop adds `tick_age_sec` (perf-clock age, `None` when never ticked), `tick_stale` (running and age > threshold, false when stopped), `tick_error_count`, `last_tick_error`, `last_tick_error_age_sec`, and `params["tick_stale_threshold_sec"]`.
- **Skill:** `test-driven-development`
- **Verification:** new tests in `tests/test_live_trader.py` — never-ticked market (`None`/false), running + aged beyond threshold (stale), stopped engine (not stale), error history survives a later successful update, and `_attribute_tick_failure` attributes to the market whose update was in flight. `python -m pytest tests/test_live_trader.py -q` green.

### [x] Task 2: [Design/UI] Cockpit health pill and stale badge (M)
- **Files:** `server/osc_dash.py`, `tests/test_osc_dash_integration.py`
- **Depends on:** Task 1 (reads the new fields)
- **Description:** (a) `.pill-stale` CSS beside the existing pill classes; (b) `cockpitStaleMarketsBadge` next to `cockpitActiveMarketsBadge` in the cockpit header; (c) in `renderCockpitUI`'s Market Matrix Grid loop, build a health pill per card — quiet (`pill-flat`) when healthy, `pill-stale` with the age in seconds when stale, and a `pill-mono` "failed" state when `tick_error_count > 0`, with `esc(last_tick_error)` as the tooltip; (d) update the header stale count; (e) confirm the 5s cockpit poll reaches `renderCockpitUI` and add the call only if a refresh path is missing it.
- **Skill:** `frontend-ui-engineering`
- **Verification:** `python -m pytest tests/test_osc_dash_integration.py -q` green, including new assertions that the cockpit HTML/JS contract exposes the stale badge element, the pill class, and the escaped tooltip path. The visual result is verified by Station IV's browser gate, not here.

### [x] Task 3: [Debug] Regression and docs sweep (S)
- **Files:** `docs/live-dashboard-streaming-spec.md` (only if it enumerates the per-market payload)
- **Depends on:** Task 2
- **Description:** run the touched suites together (`test_live_trader.py`, `test_osc_dash_integration.py`, `test_live_trader_streaming.py`); check whether the streaming blueprint enumerates market fields and, if it does, add the health fields so the doc does not drift; confirm nothing in the trading path reads the new keys.
- **Skill:** `debugging-and-error-recovery`
- **Verification:** the three suites green in one run; a repo-wide grep shows the health keys consumed only by `get_state()`/the dashboard/their tests.

## Dependency Graph
`Task 1 → Task 2 → Task 3` (state contract gates the render; the render gates the sweep).

## Checkpoints
- **After Task 1:** engine state green — 152 passed across `test_live_trader.py`, `test_live_trader_streaming.py`, `test_gil_contention_instrumentation.py`; the five new tests were red first (`KeyError: 'tick_age_sec'`, missing `_attribute_tick_failure` / `_inflight_strategy_slug` / `_record_tick_error`).
- **After Task 3:** branch ready for Station IV (which owns the browser gate). Sweep green: `test_live_trader.py` + `test_live_trader_streaming.py` 148 passed, `test_osc_dash_integration.py` 353 passed, `test_theme_tokens.py` 14 passed.

## Build Record
- Commits: `e2a6621` (engine state + its five tests), then the cockpit surface commit.
- Docs: **no change needed** — `docs/live-dashboard-streaming-spec.md` §2 documents the RTDS/CLOB/user-order stream topics, not the cockpit's `markets[]` payload, and no doc enumerates those market fields (`grep` for `win_duration_sec`/`time_remaining_sec` under `docs/` is empty).
- Proven display-only: a repo-wide grep for `tick_stale` / `last_tick_error` / `tick_age_sec` returns only `strategy/live_trader.py`, `server/osc_dash.py` and tests — no trading path reads them.
- No `docs/issues/462-noticed-but-not-touching.md`: nothing out of scope was noticed during this build that is not already tracked as #461.
