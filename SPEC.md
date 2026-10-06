# SPEC.md — Issue #462: Per-market tick health in the live cockpit

## 1. Objective & Scope
Expose, per market, how fresh its strategy updates are and whether one ever failed — and render that in the Cockpit's Market Matrix Grid — so a market left starving by a bad update is visible in real time instead of only in engine logs.

**In scope**
- Engine state: `tick_age_sec`, `tick_stale` per market, plus `tick_error_count`, `last_tick_error`, `last_tick_error_age_sec` when a failure was recorded; the threshold exposed in `params`.
- A health clock of its own, stamped only when a strategy update **completes** — the #221 timing clock is written at the top of the update, so reusing it would report a market as fresh while every one of its updates was failing, and a timing-stats reset would erase the signal.
- Failure attribution at the per-market call site, where the slug is still known, wired into the existing whole-tick catch in `_run_loop` for the log.
- Cockpit: one health pill per market card (quiet / stale / failed) and a stale-markets count in the header.

**Out of scope**
- Any per-market `try/except` around the strategy update — that is #461. This issue records, it does not isolate.
- Any trading behavior: health values must never feed a trading decision.
- New endpoints, new stream event types, or new client polling; alerting/paging; persisting health across restarts.

## 2. Acceptance Criteria
1. `engine.get_state()["markets"][slug]` carries `tick_age_sec`, `tick_stale`, `tick_error_count`, `last_tick_error`, `last_tick_error_age_sec`.
2. `tick_stale` is true only while the engine is running **and** the age of the last *completed* update exceeds `TICK_STALE_THRESHOLD_SEC`; it is false when the engine is stopped.
3. A market that has never completed an update reports `tick_age_sec = None` and `tick_stale = false` (no false alarm at start), and the cockpit shows that market as `failed xN` rather than `waiting` once a failure has been recorded against it.
4. A recorded failure increments `tick_error_count` and stores the message + timestamp; a later successful update does not clear them.
5. The Cockpit renders a health pill per market card: quiet when healthy, visibly amber/red with the age when stale, with the last error (escaped) as its tooltip; the header shows the stale count.
6. Real time: the pill updates without a page reload, riding the existing SSE/refresh path.
7. `python -m pytest tests/test_live_trader.py tests/test_osc_dash_integration.py -q` passes.

## 3. Edge Cases
- Engine stopped → both age and stale are quiet (criterion 2, 3).
- Market present but never updated (fresh start) → `None`/false, quiet pill.
- Failure then recovery → pill returns to quiet, but the error count and last error remain visible.
- Market outside `st.markets` (inactive series) → no pill; the grid is built from the active series only.
- Untrusted text in an error message → escaped before HTML/title insertion.
- Never completed an update while failures pile up → the pill reads `failed xN` and the header counts it as not updating; it must not sit on `waiting` forever.
- A market failing every tick → its age must grow (the attempt clock is not the health clock), so it goes stale instead of reading fresh.

## 4. Key Rules
- Display-only values. A health field must never be read by quoting, exit, or sizing logic.
- Failure attribution happens at the per-market call site, where the loop-local `slug` is still known — the whole-tick catch in `_run_loop` cannot tell which market aborted. It records and re-raises: attribution, never isolation (#461).
- Staleness — not attribution — is the authoritative signal in the cockpit.
- Health measures **completed** updates; any clock stamped before the work began is unusable for it.
- One recorder helper is the single write path for failure history, so #461's guard can call the same helper instead of duplicating it.
