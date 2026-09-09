# CONSTRAINTS.md — Issue #95 Quality & Architectural Constraints

## 1. Testing & Zero Regressions
- All existing tests stay green: `python -m pytest -q` (**367 passed**, latest
  verification run on this branch — see §5). Zero modifications to existing assertions.
- The #92 gate tests (`tests/test_live_trader.py:1653-1733`) and the #92/#96 parity
  tests (`tests/test_entry_timeout.py:419-455` and below) must pass unchanged —
  the gate itself is not being weakened.
- New tests (red -> green), one per behavior — all nine have landed and are
  green (§5):
  1. drift-skipped window whose mid reverts inside the band re-enters and rests orders
  2. drift-skipped window whose mid stays outside the band does not re-enter (BTC 15m case)
  3. entry-timeout-cancelled window (`is_late_start`) never re-enters, even at mid 0.50
  4. #96 late-start-skipped window never re-enters
  5. re-entry blocked when remaining window time < `min_requote_remaining_sec`
  6. per-window re-entry cap enforced (second revert does not re-enter)
  7. `open_mid` / `open_drift` still readable after re-entry; `last_action` names the drift
  8. `reentry_count` cleared by `_handle_window_rollover()` and by `reset_pnl()`
  9. backtest: adverse-skipped window re-enters; timeout-cancelled window does not
- Targeted gates: `python -m pytest tests/test_live_trader.py -q`,
  `python -m pytest tests/test_entry_timeout.py -q`,
  `python -m pytest tests/test_backtest_engine.py -q`,
  `python -m pytest tests/test_osc_dash_integration.py -q`.

## 2. Anti-Cheating & Integrity
- No disabling, skipping, weakening or deleting existing tests or assertions.
- Re-entry is conditioned on `mstate.adverse_open`, never on
  `entry_cancelled_timeout` alone. `is_late_start` and `late_start_skip` are never
  cleared, bypassed or removed from `can_place_entry`.
- The adverse-open gate threshold (`exit_thresh`) is not lowered, and
  `open_gate_evaluated` is not used to re-run the gate against a friendlier mid.
- No widening of the band to make a test pass; tests adapt to the documented
  default, not the other way round.

## 3. Parameters & Parity

**Backtest mirror landed.** `BacktestParams` (`backtest/engine.py:141-143`)
carries the three re-entry fields with `__post_init__` validation (`:155-168`),
byte-identical to `LiveTraderEngine` (`strategy/live_trader.py:569-571`); the
bullets below hold. See §5, item 9.

- `reentry_drift_band`, `min_requote_remaining_sec`, `reentry_min_remaining_pct`
  and `max_reentries_per_window` carry byte-identical defaults in
  `LiveTraderEngine.__init__` and `BacktestParams` (`0.015`, `300.0`, `0.30`, `1`).
  A change to one without the other is a defect.
- `exit_reversal`, `exit_thresh`, `entry_timeout_pct`, `max_start_elapsed_pct` and
  the `0.50 - offset` resting anchor are untouched by this issue.
- `BacktestParams.__post_init__` validates the new fields in the existing style
  (finite, `0.0 <= reentry_drift_band <= 0.5`, `min_requote_remaining_sec >= 0`, shared with issue #89,
  `max_reentries_per_window >= 0`).
- `update_config()` treats `reentry_drift_band` like the other scalar knobs: it
  participates in the "cannot change parameters while running" guard.

## 4. Performance & Integration Guardrails
- No new dependencies in `requirements.txt`.
- The re-entry check is O(1) per market per tick, no network calls, no extra CLOB
  round-trips beyond the order placement that already follows `can_place_entry`.
- All `mstate` mutations on the re-entry path happen under `self._engine_lock`,
  matching the surrounding cancellation and rollover blocks.
- Contract change is additive only: three new params-payload/config fields and new
  `MarketLiveState` fields with defaults. No existing API shape changes.
- `reentry_count` / `reentry_mid` / `reentry_drift` reset on window rollover and on
  `reset_pnl()`, alongside the other per-window state.

## 5. Verification status (checked Sep 7, 2026 · feat/issue-95-drift-reentry)

Gates: `python -m pytest tests/test_live_trader.py tests/test_entry_timeout.py
tests/test_backtest_engine.py tests/test_osc_dash_integration.py -q` (re-run after
the round-1 review fixes) — full collection: **367 tests / 18 files** via
`python -m pytest -q`.

| # | Behavior | Implementation | Test (green) |
|---|---|---|---|
| 1 | mid reverts inside band → re-enters + rests orders | `_maybe_reenter_drift_skipped` (`strategy/live_trader.py:2886`), wired before `can_place_entry` (`:3278`, `:3283`) | `tests/test_live_trader.py:1760` |
| 2 | mid stays outside band → no re-entry | band check `live_trader.py:2926` | `tests/test_live_trader.py:1798` |
| 3 | entry-timeout cancel never re-enters | gated on `adverse_open` AND `entry_cancelled_timeout` (`:2914`) | `tests/test_live_trader.py:1814` |
| 4 | #96 late-start skip never re-enters | `late_start_skip` / `is_late_start` guard (`:2916`) | `tests/test_live_trader.py:1840` |
| 5 | remaining < `min_requote_remaining_sec` blocked | `:2924` | `tests/test_live_trader.py:1859` |
| 6 | per-window cap enforced | `:2922` | `tests/test_live_trader.py:1875` |
| 7 | `open_mid` / `open_drift` preserved; `last_action` names drift | opening snapshot untouched; snapshot kept for telemetry | `tests/test_live_trader.py:1779` |
| 8 | `reentry_count` cleared by rollover and `reset_pnl()` | `:3691-3693` (rollover) and `:2569-2571` (`reset_pnl`) | `tests/test_live_trader.py:1903` |
| 9 | backtest: adverse-skipped window re-enters; timeout-cancelled does not | ✅ `adverse_skipped` split + per-tick re-entry rule (`backtest/engine.py:381-401`; gate sets the flag at `:378`); fields `BacktestParams:141-143`, validation `:155-168` | ✅ `tests/test_entry_timeout.py:663-734` — 6 tests: revert-inside-band fills, outside-band no, timeout-cancelled never, <`min_requote_remaining_sec` no, defaults parity with live, out-of-range rejection |

Additional checks: `reentry_drift_band` config API (`ConfigPayload`
`server/osc_dash.py:881`, `update_config` running-guard `live_trader.py:1840`,
clamp `:1942-1945`) is covered by `tests/test_osc_dash_integration.py:1204`
(POST 0.9 → 422 at `:1223`). Anti-cheating §2 holds in code. Backtest parity
(T5/T6, item 9) landed in the working tree. The three #95 commits and this
change touched no dependency files.

## 6. Issue #110 — exit_reversal isolated sweep (tooling only, no strategy change)

- **Zero production behavior change.** `backtest/engine.py`, `strategy/live_trader.py`
  and `server/osc_dash.py` are untouched; `BacktestParams` defaults
  (`exit_reversal = 0.02`) stay as-is until #111 decides otherwise on the basis
  of this sweep's results.
- **Scope lock:** only the 1D sensitivity list gains `0.025`
  (`scripts/sweep_backtest.py:218`) plus an `--only` axis filter for the
  `sensitivity` preset. Joint (`generate_joint_grid`) and random
  (`generate_random_grid`) value sets are deliberately unchanged (out of scope
  per the issue).
- **Determinism:** the sweep replays a fixed tick file
  (`run/ticks/ticks_2026-09-08.jsonl`) with all non-target params pinned to the
  documented baseline (`offset=0.02, queue_gate=0, exit_5m=0.08,
  fill_model=tape`); results are saved with `--out` JSON so the table is
  reproducible from the artifact.
- **No new dependencies.** No changes under `run/` format; sweep output JSON
  lives under gitignored `run/sweeps/`.
- **Tests:** `python -m pytest tests/test_sweep_backtest.py -q` green (extended
  with `--only` filter + `0.025` grid tests); full suite `python -m pytest -q`
  stays at **367 passed**, zero modifications to existing assertions.

## 6. Verification status (checked Sep 9, 2026 · sweep/issue-110-exit-reversal)

- `python -m pytest tests/test_sweep_backtest.py -q` → **14 passed** (10 existing
  + 4 new: 0.025 grid entry, `--only` filter, bad-axis rejection, e2e).
- `python -m pytest -q` → **386 passed**, zero failures, zero edits to existing
  assertions.
- Sweep artifact `run/sweeps/exit_reversal_110.json` (gitignored): 5 runs,
  distinct `exit_reversal` params confirmed; verdict + table in
  `docs/backtest-optimization-results.md §6` and as issue #110 comment.
