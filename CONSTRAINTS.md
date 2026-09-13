# CONSTRAINTS.md — Issue #164: one parameter contract for Backtest and Cockpit

Binding while `feat/unify-params-164` is live.

## 1. Test suite integrity

- **Zero regressions.** `python -m pytest -q` must be green before and after
  every task. Current baseline: **713 passed**.
- Every new knob ships with tests for its *off* value proving prior behaviour
  is byte-identical, and for its *on* value proving the new behaviour.
- **Anti-cheat.** No `skip`, `xfail`, deleted assertion, loosened tolerance, or
  silenced linter to make a task pass. If a test blocks the change, the change
  is wrong until argued otherwise in the PR.

## 2. Simulation compatibility (the expensive one)

Adding engine knobs changes simulated P&L. That is accepted for this issue, but
bounded:

- **Defaults must reproduce today's results exactly.** Every new
  `BacktestParams` field defaults to the value that preserves current
  behaviour (`naked_leg_timeout_pct=0.0`, `stop_loss_enabled=True`,
  `enable_leg_chase=False`, `exit_thresh_naked=None` → falls back to
  `exit_thresh`). A replay with default params must produce an identical
  `params_hash`-keyed result to master for the same input.
- A test must pin that: same ticks, default params, identical per-window P&L
  against a recorded fixture.

## 3. Live engine is read-only here

- `strategy/live_trader.py` execution logic is **not** modified. Live is the
  reference implementation; the backtest moves toward it, never the reverse.
- Cockpit UI may gain inputs for knobs `update_config()` already accepts. It
  may not gain a knob the live engine does not implement.

## 4. The registry is the only source of truth

- After this issue, a shared parameter's label, unit, default, and bounds exist
  in exactly one place. A second hard-coded copy of any of those in
  `server/osc_dash.py` is a defect.
- A test must fail if a surface renders a shared knob whose label does not come
  from the registry.

## 5. Scope fences

- No new external dependencies.
- No change to venue logic, settlement, or order routing.
- `scripts/collect_ticks.py` is **untouched** — a 24h capture is running and a
  restart loses the continuous run.
- Research artifacts under `research/sweeps/` stay stale-marked; this issue does
  not regenerate them (see `research/sweeps/RESULTS-ARE-STALE.md`).

## 6. Verification mode per task type

- `[Backend/Logic]` → `python -m pytest -q tests/test_backtest_engine.py tests/test_live_trader.py`
- `[Design/UI]` → served-HTML assertions in `tests/test_osc_dash_integration.py`
  plus a live DOM read against `http://127.0.0.1:8802` before the task is called done.
- Full suite before every commit.
