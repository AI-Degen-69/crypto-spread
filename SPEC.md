# SPEC.md — Issue #445: Overnight BTC/ETH Winners as Loadable Backtest Preset

## 1. Objective & Scope
Ship one named backtest template (`overnight-majors-btc-eth`) baking in the PR #444 overnight-sweep BTC/ETH winning settings, loadable in one click on the Backtest tab via the existing Issue #413 template system. The operator stops retyping seven knobs.

Winning set (measured RUN_0153 from `run/backtest_results_overnight.csv`, chosen
in Station III over the unmeasured findings-report compromise of 0.035/25/200:
no sweep run ever used those values, so no honest summary exists for them):
- `offset` 0.10, `queue_gate` 5, `quote_shares` 75, `entry_delay_sec` 60,
  `enable_leg_chase` false, `exit_reversal` 0.02, exits 0.30/0.045
  (`exit_default_5m/15m`, `exit_btc_5m` 0.30), `max_pair_cost` 0.99,
  scoped to `series` BTC,ETH.
- Measured scope outcome: +$15.00 over 2011 windows, 1 pair, zero drawdown
  (BTC +$15.00/1 trade; ETH 0 trades). Small size (75) avoids the
  size-scaling distortion of the +$950/+$700 runs.

## 2. Interface Contract (locked)
- Seed file: `backtest/seed_templates/overnight-majors-btc-eth.json` — a full template
  record per `backtest/templates.py:22-33` (`REQUIRED_RECORD_KEYS`) with `request_args`
  shaped like `tests/test_backtest_templates.py:28-51` (`offset`, `queue`, `pair_cost`,
  `exit_*`, `exit_reversal`, `size`, `quote_lo/hi`, `entry_delay_*`, `dead_zone_*`,
  `naked_leg_at_expiry`, `enable_leg_chase`, `series: "BTC,ETH"`, `durations`, `file`).
- Seeding function (in `server/osc_dash.py` near `BACKTEST_TEMPLATES_DIR`): copy seed
  into `run/backtest_templates/` **only when the name is missing**; recompute
  `params_hash` at copy time via `_prepare_backtest_request` so registry drift can
  never ship a 409-stale preset. Never overwrite an existing template file.
- Load path unchanged: `GET /api/backtest/templates/{name}` → 200, validated
  against the current registry (`server/osc_dash.py:4291-4330`).

## 3. Acceptance Criteria
1. `GET /api/backtest/templates/overnight-majors-btc-eth` returns 200 and the record
   applies on the Backtest tab restoring all seven winning knobs scoped to BTC,ETH.
2. The preset exists on a fresh checkout with no manual API calls (startup seeding).
3. Seeding never overwrites an operator-saved template of the same name.
4. `python -m pytest tests/test_backtest_templates.py -q` passes with no regressions.

## 4. Edge Cases
- Operator deleted the seed on purpose → startup copy resurrects it (copy-if-missing
  semantics; documented here, not fixed — deletion is not a supported workflow).
- Registry drift (new required arg) → hash recomputed at copy time; static seed file
  updated only if the rebuild rejects its args (test guards this).
- Name validation: `normalize_name` rules apply (`backtest/templates.py:43-59`).

## 5. Explicitly Out of Scope
- SOL/XRP preset (sibling #446, reuses this seeding path), `BacktestParams`
  default changes, fill-rule changes, live-trader config, BNB handling.
