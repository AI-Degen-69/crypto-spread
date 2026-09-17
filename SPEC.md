# SPEC — Issue #208: entry-gate defaults measurement (quote_range + dead zone)

## Context
Issue #208's two code complaints were resolved before this plan opened:
- The `adverse_open` gate (which borrowed `exit_thresh`) and `entry_band` were **deleted** and
  replaced by `quote_range=(0.10, 0.90)` — issue #228, PR #241, `docs/engine-decision-rules.md` §6.
- The end-of-window guard now exists as the **dead zone** (`dead_zone_val=0.10`, unit `pct|sec`),
  "open nothing, close what is open" — issue #229, PR #242, rules §8. `naked_leg_timeout_pct`
  was deleted in its favor, which is the reconciliation the issue demanded.
The blocker it cited (#204: backtest gate rejecting every window) is closed.

What remains is the issue's own last requirement: **"Sweep the proposed thresholds against
real tick data before changing any default."** The new defaults shipped unmeasured, and §6/§8
both explicitly defer to data (§8: "Which unit is actually right is an open question,
deliberately. … the switch exists so it can be measured").

## Goal
Produce measured evidence for or against the shipped defaults, via the existing sweep engine,
and publish a verdict per knob on #208.

## Questions the sweep must answer
1. **quote_range width:** does (0.10, 0.90) beat tighter (0.15-0.85 … 0.30-0.70) and looser
   (0.05-0.95, 0.00-1.00) bounds on net P&L, win rate, and drawdown across the available
   datasets? (Axis already exists in `generate_sensitivity_grid` under `--include-structural`.)
2. **dead zone size (pct):** is 10% the right tail? Sweep 0 / 0.05 / 0.10 / 0.15 / 0.20 /
   0.30 with unit=pct and compare.
3. **dead zone unit:** with the *same absolute seconds* as 10% of a 5m window (30s) and of a
   15m window (90s), does unit=sec beat unit=pct across both window lengths? This is §8's
   open question and needs the new axis (sec unit is currently absent from the sweep grids).

## Acceptance Criteria
1. Sensitivity sweeps run against every file present in `run/ticks/`, results labeled per
   dataset; baseline row included per run.
2. Dead-zone axes (`dead_zone_val` with unit=pct, and unit=sec) exist as 1D sensitivity rows
   gated behind `--include-structural`, plus a `--only dead_zone` filter path, mirroring the
   existing `quote_range` axis; `tests/test_sweep_backtest.py` covers the new rows.
3. A verdict table is produced per knob: for each variant — n_windows, pair_rate, win_rate,
   total/avg P&L, max drawdown, profit factor — computed by `run_sweep`/`compute_metrics`
   (no new metrics code).
4. A gh comment on #208 presents the tables, one-sentence verdicts, and dataset labels, and
   links `docs/engine-decision-rules.md` §6 and §8. Recommendation, not silent change: any
   proposed default adjustment is flagged for operator decision.
5. Zero modifications to `backtest/engine.py`, `strategy/live_trader.py`, `strategy/book_math.py`.
6. Targeted suites `tests/test_sweep_backtest.py`, `tests/test_backtest_engine.py`,
   `tests/test_book_math.py` pass.

## Edge Cases
- Datasets with few windows (collector started late / gaps): state the per-dataset window
  count next to every number; never pool datasets without labeling the pool.
- `dead_zone_val=0.0` disables the guard — a valid sweep point showing the cost of no guard,
  but never a recommended default without evidence.
- Sec-unit rows on mixed 5m/15m datasets must be read per window length, not pooled, or the
  comparison is meaningless — the report must split them.
- If a dataset fails `scripts/verify_tick_data.py` checks, exclude it and say so.

## Out of Scope
- Any change to engine decision logic, BacktestParams fields, or shipped defaults.
- Re-litigating the deletion of `adverse_open`/`entry_band`/`naked_leg_timeout_pct` (#228/#229).
- #210 (leg chase condition), #212 (re-entry paths), #221/#222/#223 (their own measurement
  questions) — referenced only.
- Live (real money) execution changes of any kind.
