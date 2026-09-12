# SPEC.md — Issue #145: entry-delay + entry-band knobs and winning-config preset

## 1. Goal
Close the gap between the EV-winning config and the official replay path:
`BacktestParams` + `/api/backtest` + dashboard gain `entry_delay_sec` /
`entry_band` with live-identical semantics, plus a one-click "Winning config"
preset — so #146 can replay the shadow night through the official engine.

## 2. Background (verified 2026-09-13 in code)
- Live semantics (`strategy/live_trader.py`): delay holds ALL quotes until
  `elapsed >= entry_delay_sec` (transient, nothing latched; fills bypass);
  post-delay, the first two-sided tick latches the band gate once —
  `|mid − 0.50| > entry_band` → window skipped (`BAND_SKIPPED`); adverse-owned
  and re-entered windows bypass the band; while armed-but-unevaluated,
  placement is held (`band_hold`). Clamps: delay 0–3600, band 0–0.50
  (`server/osc_dash.py:1224-1225`).
- Research parity (`run/sweeps/sim2.py:36-66`): observe-only until delay;
  band vs two-sided mid at first quoted tick; resting quotes anchored at first
  mid AT/AFTER delay expiry; `ex=none` = `exit_thresh` 0.49/0.50
  (`phase6_tapeq_top.py:33-39`) = effectively never exits (hold-to-settle).
- Official engine today (`backtest/engine.py:357+`): no delay/band; quotes
  anchored at first valid snapshot; fills from loop start. `_two_sided_mid`
  helper already exists (`engine.py:315`) for the band check.
- Dashboard: Operator Controls inputs `btOffset/btQueue/...` + `runBacktest()`
  URL builder (`osc_dash.py:3502-3534`) + `resetBtParams()` (`:3763-3784`).

## 3. In Scope
1. `BacktestParams.entry_delay_sec = 0.0` (0 = off) + `entry_band = 0.0`
   (0 = off), validated 0–3600 / 0–0.50 in `__post_init__`, grouped under
   `trading_knobs` (operator-controlled, live-replicable).
2. `_simulate_window` honors both with live-identical semantics:
   delay → observe-only (mids/classification still use the FULL path);
   band evaluated once at delay expiry on first two-sided mid, latched per
   window; no fills until delay expired AND (band off OR band passed);
   resting quotes anchored at first mid at/after delay expiry.
3. `/api/backtest` gains `entry_delay_sec` / `entry_band` query params with
   live-identical clamps, passed into `BacktestParams`, echoed in BOTH
   `params` dicts (empty-window early return AND main path).
4. Dashboard: `btEntryDelay` + `btEntryBand` inputs in Operator Controls,
   wired into `runBacktest()` URL + `resetBtParams()` defaults (0 / 0);
   `applyWinningConfig()` preset button → offset 0.03, delay 60, band 0.04,
   fill tape, pair_cost 0.98 (toggle ON), size 5, exits 0.49 / 0.50 /
   0.49 / 0.49 (ex=none mirror), then auto-runs.
5. Engine parity tests + API passthrough/clamp tests + UI presence test.

## 4. Out of Scope
- Changing ANY backtest default (delay/band default 0 = byte-identical
  behavior; existing fixture hashes untouched); live-trader changes; sweep
  scripts; the #146 replay comparison itself.

## 5. Interfaces (locked before logic)
- `BacktestParams(offset=..., entry_delay_sec=60.0, entry_band=0.04, ...)` —
  frozen dataclass, `params_hash()` covers new fields automatically.
- `GET /api/backtest?...&entry_delay_sec=60&entry_band=0.04&fill_model=tape`
  — clamps: `delay = max(0.0, min(3600.0, v))`, `band = max(0.0, min(0.50, v))`.
- Dashboard ids (locked): `btEntryDelay` (number, min 0, step 1, value 0),
  `btEntryBand` (number, min 0, max 0.5, step 0.005, value 0),
  preset button `btnWinningConfig` → `applyWinningConfig()`.
- Winning preset values (locked): `{offset:0.03, delay:60, band:0.04,
  fill:tape, pairCost:0.98(enabled), size:5, exit5m:0.49, exit15m:0.50,
  exitBtc:0.49, exitSol:0.49}`.

## 6. Acceptance Criteria
- [ ] `/api/backtest?...&entry_delay_sec=60&entry_band=0.04&fill_model=tape`
      shows delay+band behavior identical to live semantics on a fixture file.
- [ ] Dashboard shows both inputs + working preset button filling all fields.
- [ ] Defaults unchanged: omitted params replay exactly as before (fixture
      hashes untouched).
- [ ] `python -m pytest tests/test_backtest_engine.py
      tests/test_osc_dash_integration.py -q` green.
