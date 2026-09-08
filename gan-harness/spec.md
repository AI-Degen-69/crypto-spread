# ISSUE 96 Guard Demo Spec 🛡️
## Goals 🎯
1. Visualize the live late-start guard so a restart mid-window is legible in <3s: grey `LATE START SKIPPED` badge, elapsed-vs-cutoff readout, and `last_action` reason string.
2. Prove backtest/live parity is explicit: backtest uses `max_start_delay_sec` (seconds) while live uses `max_start_elapsed_pct=0.10` (fraction); demo documents the conversion `cutoff_sec = pct * win_duration`.
3. Keep the demo replayable without venue calls: seeded engine state + static dashboard section, verifiable by Playwright screenshot and `pytest -q`.
## Scope 🔍
1. Live guard visualization: surface existing `strategy/live_trader.py:2771-2816` latch fields (`first_tick_elapsed_sec`, `late_start_cutoff_sec`, `status=LATE_START_SKIPPED`, `last_action`) in the `/api/oscillation` payload and render them in a Guard Demo strip.
2. Dashboard badge: add `LATE START SKIPPED` pill style (grey, alongside existing `.tel-badge` / `.pill-osc` / `.pill-flat` in `server/osc_dash.py:1410-1414,3977-3981`) with tooltip showing `Engine started {elapsed}s into window (>= {cutoff}s) — waiting for next window`.
3. Backtest parity note: small caption + code comment mapping `max_start_elapsed_pct=0.10` to `backtest/engine.py:128,507-511` `max_start_delay_sec` (e.g. 30s on a 300s window), no backtest logic change required.
## Out of Scope 🚫
- No changes to guard threshold, latch logic, order placement, cancellation, or fee math.
- No new venue calls, no CLOB polling changes, no collector changes.
- No backtest engine behavior change; parity is documentation only.
- No auth, persistence, or multi-window history beyond one demo window.
## File Touch List 📁
1. `server/osc_dash.py` — add Guard Demo section + `LATE START SKIPPED` badge CSS + include guard fields in `/api/oscillation` response.
2. `strategy/live_trader.py` — expose-only: ensure `first_tick_elapsed_sec`, `late_start_skip`, `last_action`, `max_start_elapsed_pct` are serialized for the dashboard (no logic change).
3. `backtest/engine.py` — comment-only parity note mapping `max_start_delay_sec` to `max_start_elapsed_pct * win_duration`.
4. `tests/test_issue96_guard_demo.py` — new: latch unit test + dashboard payload assertion + badge-string assertion.
## Acceptance Criteria ✅
1. Engine started 45s into a 300s window with `max_start_elapsed_pct=0.10` (cutoff 30s) sets `status == "LATE_START_SKIPPED"`, places zero orders, and sets `last_action` containing `waiting for next window`.
2. Engine started 5s into the same window does not skip and proceeds to normal entry gating.
3. `GET /api/oscillation` includes `first_tick_elapsed_sec`, `late_start_cutoff_sec`, and `status` for the demo window when guard fires.
4. Dashboard renders a grey `LATE START SKIPPED` badge with elapsed-vs-cutoff text (e.g. `45s >= 30s`) visible in a Playwright screenshot at 1280x720 without horizontal scroll.
5. Demo caption states the parity formula `cutoff_sec = 0.10 * win_duration` and names `max_start_delay_sec` as the backtest equivalent.
6. `python -m pytest tests/test_issue96_guard_demo.py -q` passes and full suite stays green (`python -m pytest -q`, 186+ tests).
## Demo Script ▶️
1. Seed one 5m window, inject first tick at `elapsed=45s`, assert skip + badge payload.
2. Reload dashboard `:8802`, screenshot Guard Demo strip showing badge + reason.
3. Toggle to `elapsed=5s` seed, confirm badge absent and quoting resumes.
