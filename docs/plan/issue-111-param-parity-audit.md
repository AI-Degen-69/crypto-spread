# Issue #111 — Parameter Parity Audit

Audit of every strategy parameter shared between the backtest engine
(`backtest/engine.py:BacktestParams`) and the live trader
(`strategy/live_trader.py:LiveTraderEngine.__init__`), as of issue #111.

| Parameter | Backtest default | Live default | Status |
|---|---|---|---|
| `offset` | 0.020 | 0.02 | ✅ Match |
| `exit_thresh` | 0.05 (all slugs) | 0.05 | ✅ Match |
| `exit_reversal` | 0.02 | 0.02 (was 0.015) | ✅ **Unified in #111** |
| `quote_shares` / `shares` | 5 | 5 | ✅ Match |
| `reentry_drift_band` | 0.015 | 0.015 | ✅ Match |
| `min_requote_remaining_sec` | 300.0 | 300.0 | ✅ Match |
| `reentry_min_remaining_pct` | 0.30 | 0.30 | ✅ Match |
| `max_reentries_per_window` | 1 | 1 | ✅ Match |
| `max_start_elapsed_pct` | 0.10 | 0.10 | ✅ Match |
| `taker_fee_rate` | 0.07 | 0.0 | ⚠️ Intentional divergence |
| `entry_timeout_pct` | 0.10 | 1.0 | ⚠️ Intentional divergence |

## Deliberate divergences (documented, not unified)

### `taker_fee_rate` — backtest 0.07 vs live 0.0
The backtest uses a 0.07 fee coefficient when computing PnL to model adverse
costs conservatively (replay PnL is a *research estimate*). The live trader
leaves it at 0.0 because on Polymarket's taker-only fee schedule, **makers pay
no fee** — and this strategy rests maker limit orders. Applying a maker fee in
live PnL accounting would misreport actual fills. The two numbers answer
different questions (conservative replay stress vs. accurate live accounting)
and should not converge.

### `entry_timeout_pct` — backtest 0.10 vs live 1.0
The backtest cancels unfilled entry quotes once 10% of the window elapsed so
replays don't count fills that would realistically never happen. Live defaults
to 1.0 (full window) because the cockpit exposes this knob directly and the
operator chooses patience per session; the dashboard posts it explicitly
(cockpit default 100%). The live engine's `max_start_elapsed_pct = 0.10`
late-start guard (issue #96) already provides equivalent protection against
entering deep into a window. Replayed fills under a 10% timeout are strictly
harder to get than live fills under 1.0, so the backtest is the conservative
side; no behavior change needed.

## `exit_reversal` unification (change made in #111)

- Live default changed **0.015 → 0.02** to match `BacktestParams.exit_reversal`.
- Evidence: `docs/backtest-optimization-results.md` §6 (issue #110 isolated
  sweep) — results are bit-identical across 0.010–0.025 on the Sep 8 dataset
  (exits ~4% of windows; the disarm distance is second-order at this baseline).
  No PnL reason to keep the divergence; 0.030 was rejected (+24c single-window
  edge).
- Now dashboard-configurable: `LiveConfigPayload.exit_reversal`
  (cents→decimal normalization, range [0.001, 0.50]) → `update_config`
  (clamped, guarded while running) → cockpit "Exit Reversal Buffer ($)" field.
