# Plan: Issue #145 — entry-delay + entry-band knobs and winning-config preset

Task Type: Code + Design
Size Tier: Standard
Target Files: `backtest/engine.py` (params + `_simulate_window`),
  `server/osc_dash.py` (API + dashboard HTML/JS), `scripts/backtest.py`
  (CLI flags — Task 6, opt-in), `tests/test_backtest_engine.py`,
  `tests/test_osc_dash_integration.py`

Decisions locked with user: none yet — requirements fully clear from the issue
(`interview-me` skipped). Live semantics ported 1:1 from
`strategy/live_trader.py` delay/band blocks + `sim2.py` anchoring/ex=none.

## Task Breakdown

### Task 1: Params — fields, validation, grouping (`backtest/engine.py:111-187`)
- **Files**: `backtest/engine.py`
- **Type**: Code
- **Description**:
  1. Add `entry_delay_sec: float = 0.0`, `entry_band: float = 0.0` to
     `BacktestParams`; validate delay 0–3600, band 0–0.50 in `__post_init__`
     (raise `ValueError` outside).
  2. Register both under `_PARAM_GROUPS["trading_knobs"]` with label/why.
- **Status**: [x]
- **Verification**: `python -c "from backtest import BacktestParams; BacktestParams(entry_delay_sec=60,entry_band=0.04); BacktestParams(entry_band=9)"` → second raises ValueError

### Task 2: Engine — delay/band in `_simulate_window` (`backtest/engine.py:357+`)
- **Files**: `backtest/engine.py`
- **Type**: Code
- **Description**:
  1. `quotable` gate per snap: `elapsed >= entry_delay_sec` AND
     (`entry_band == 0` OR band evaluated-pass). Pre-quotable snaps still
     record mids/max (full-path classification) but take NO fills/pairs/exits.
  2. Band: once delay expired, first snap with `_two_sided_mid is not None`
     latches `band_gate_evaluated`; `|mid − 0.50| > entry_band` →
     `entry_cancelled = True` (never re-entered; re-entry block only undoes
     `adverse_skipped`). Adverse gate + timeout logic untouched.
  3. Anchor `resting_up/down` at first mid AT/AFTER delay expiry (sim2 parity);
     delay 0 → first valid snapshot = today's behavior exactly.
- **Status**: [x]
- **Verification**: new engine tests (Task 3) green

### Task 3: Engine parity + defaults-unchanged tests
- **Files**: `tests/test_backtest_engine.py`
- **Type**: Code
- **Description**: add tests — delay holds quotes (no fills before 60s on a
  fixture that fills at t=0 without delay); band skips decided window
  (|mid−0.50| > 0.04 at expiry) and admits undecided one; post-delay quote
  anchor differs from t=0 anchor on a drifting fixture; delay/band validation
  rejects out-of-range. Existing tests untouched.
- **Status**: [x]
- **Verification**: `python -m pytest tests/test_backtest_engine.py -q` (0 failures)

### Task 4: API — query params, clamps, echo (`server/osc_dash.py:505-665`)
- **Files**: `server/osc_dash.py`
- **Type**: Code
- **Description**:
  1. Add `entry_delay_sec: float = 0.0`, `entry_band: float = 0.0` params;
     clamp `max(0.0, min(3600.0, ...))` / `max(0.0, min(0.50, ...))`
     (mirror `LiveConfigPayload`); pass into `BacktestParams`.
  2. Echo both in the `params` dict of the empty-window early return AND the
     main path (mirror `max_start_delay` handling).
- **Status**: [x]
- **Verification**: new integration tests (Task 5) green

### Task 5: API passthrough/clamp + UI presence tests
- **Files**: `tests/test_osc_dash_integration.py`
- **Type**: Code
- **Description**: fixture-tick test — `entry_delay_sec=60&entry_band=0.04`
  changes results vs omitted on a delay-sensitive fixture; clamp test
  (`entry_delay_sec=9999` → echoed 3600.0, `entry_band=9` → 0.50); UI presence
  test for `btEntryDelay`, `btEntryBand`, `btnWinningConfig` ids.
- **Status**: [x]
- **Verification**: `python -m pytest tests/test_osc_dash_integration.py -q` (0 failures)

### Task 6 (OPTIONAL — needs operator sign-off): CLI flags
- **Files**: `scripts/backtest.py`
- **Type**: Code
- **Description**: `--entry-delay` (default 0.0) + `--entry-band` (default 0.0),
  wired into `BacktestParams`; printed in the header line. NOT in issue scope
  — included only because #146's replay runs via this CLI.
- **Status**: [x] (opt-in — SKIPPED, no operator approval)
- **Verification**: `python -m scripts.backtest --help` shows both flags

### Task 7: Dashboard inputs + wiring + reset (Design)
- **Files**: `server/osc_dash.py` (HTML ~2216-2272, JS `runBacktest` :3502-3534, `resetBtParams` :3763-3784)
- **Type**: Design
- **Description**:
  1. Operator Controls: `btEntryDelay` (number, min 0, step 1, value 0,
     "Entry Delay (s, 0 = off)") + `btEntryBand` (number, min 0, max 0.5,
     step 0.005, value 0, "Entry Band (0 = off)").
  2. `runBacktest()`: read both via `getVal`, append
     `&entry_delay_sec=&entry_band=` to URL.
  3. `resetBtParams()`: reset both to "0" (no auto-run change).
- **Status**: [x]
- **Verification**: UI presence test (Task 5) + manual `runBacktest` URL check

### Task 8: "Winning config" preset button (Design)
- **Files**: `server/osc_dash.py` (button next to Reset, `applyWinningConfig()`)
- **Type**: Design
- **Description**: `btnWinningConfig` → sets offset 0.03, delay 60, band 0.04,
  fill tape, pairCost 0.98 + toggle ON, size 5, exits 0.49/0.50/0.49/0.49,
  then calls `runBacktest()`. Pair-cost toggle set via existing
  `togglePairCostInput()` path.
- **Status**: [x]
- **Verification**: presence test + click fills all fields (manual or DOM test)

### Task 9: Full regression gate + defaults proof
- **Files**: —
- **Type**: Code
- **Description**:
  1. `python -m pytest tests/test_backtest_engine.py tests/test_osc_dash_integration.py -q`.
  2. Defaults proof: replay a fixture with new params omitted → identical
     `params_hash`/totals as pre-change baseline (existing suite covers;
     call out explicitly in the PR).
- **Status**: [x]
- **Verification**: pytest exit 0 + defaults statement in PR body
