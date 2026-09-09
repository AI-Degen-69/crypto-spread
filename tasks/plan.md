# tasks/plan.md — Issue #110: Sweep exit_reversal 0.010–0.030 (mercy distance)

Branch: `sweep/issue-110-exit-reversal` (off `master`)
Spec: concise spec below (Small tier — no SPEC.md change; #111 consumes the results)
Constraints: `CONSTRAINTS.md §6`
Baseline: 367 tests green · data `run/ticks/ticks_2026-09-08.jsonl` (177 MB, present).

## Concise spec (spec-driven-development, Small tier)

**Goal.** Isolated 1D sweep of `exit_reversal` at
`[0.010, 0.015, 0.020, 0.025, 0.030]`; all other params pinned to baseline:
`offset=0.02, queue_gate=0, exit_5m=0.08, fill_model=tape`, size 5.
Baseline exit dict follows the repo's own convention (sensitivity loop at
e=0.08 for the 5m keys; joint-grid convention `default_15m = e5 + 0.01`):
`{default_5m: 0.08, btc-5m: 0.05, sol-5m: 0.07, default_15m: 0.09,
btc-15m: 0.09, sol-15m: 0.09}`. The exact dict must be printed in the report.

**Out of scope.** Mercy-rule logic changes; joint/random grid value sets;
per-series or per-duration breakdowns; any change to engine/live/dashboard
defaults (that's #111).

**Acceptance (from the issue).**
- [ ] Sweep runs on the latest tick file at the five values, rest at baseline.
- [ ] Output table per value: exit_rate, pair_rate, total_pnl, avg_pnl,
      win_rate, max_dd, profit_factor, sharpe (all already on `SweepResult`,
      `scripts/sweep_backtest.py:30-50` — `sharpe_proxy` is the sharpe column).
- [ ] Written recommendation: optimal mercy distance + whether to unify
      live (0.015) / backtest (0.02) defaults → feeds #111.
- [ ] `python -m pytest tests/test_sweep_backtest.py -q` passes.

## Interfaces locked before coding (api-and-interface-design)

1. **Grid value** (`scripts/sweep_backtest.py:218`):
   ```python
   reversals = [0.010, 0.015, 0.020, 0.025, 0.030]
   ```
   Label format unchanged: `exit_rev=0.025`.
2. **New CLI flag** (sensitivity preset only):
   ```
   --only {exit_rev | offset | queue | exit_5m | pair_cost | reentry_band | requote_min}
   ```
   Filters `generate_sensitivity_grid()` entries by label prefix so the issue's
   *isolated* run executes exactly the 5 `exit_rev=` configs (baseline config
   itself is excluded from the 1D grid by construction — the loop skips
   `r == base.exit_reversal` — so the sweep must explicitly include the 0.020
   baseline point; see T2).
3. **Report artifact:** `--out run/sweeps/exit_reversal_110.json` (gitignored).
   Table + recommendation go to the issue as a comment and as a dated section
   appended to `docs/backtest-optimization-results.md`.

---

## Tasks

### T1 — RED: isolated-axis tests
Files: `tests/test_sweep_backtest.py` (append)
Tests: (a) `generate_sensitivity_grid()` contains an `exit_rev=0.025` entry with
`exit_reversal == 0.025` and all other fields equal to base; (b) the `--only`
filter helper returns exactly the 5 `exit_rev=` entries (incl. the 0.020
baseline point) and drops every other axis; (c) unknown `--only` value →
nonzero exit / clear error.
Accept: new tests FAIL for the right reason (`0.025` missing, no `--only` flag).
Verify: `python -m pytest tests/test_sweep_backtest.py -q` shows the new failures only.

### T2 — GREEN: 0.025 + --only filter
Files: `scripts/sweep_backtest.py`
Add `0.025` to `reversals` (`:218`); add `--only` argparse flag and apply the
prefix filter to the sensitivity grid in `main()`; ensure the 0.020 baseline
point is present in an `--only exit_rev` run (add it explicitly if the
`r != base.exit_reversal` skip would drop it). Joint/random grids untouched.
Accept: `python -m pytest tests/test_sweep_backtest.py -q` fully green (10 old + new).
Also smoke: `python -m scripts.sweep_backtest --help` shows `--only`.

### T3 — Run the isolated sweep on real data
Files: none (run only)
Command (baseline per issue; exit dict as pinned above):
```
python -m scripts.sweep_backtest run/ticks/ticks_2026-09-08.jsonl --preset sensitivity --only exit_rev --fill-model tape --size 5 --out run/sweeps/exit_reversal_110.json
```
Accept: exit 0; JSON artifact exists with 5 runs; console table shows all five
`exit_rev=` rows. If the 177 MB file makes the run slow, that is fine — it runs
once, unattended.

### T4 — Table + recommendation (the actual deliverable)
Files: `docs/backtest-optimization-results.md` (append dated § for #110)
Build the 5-row table (exit_rate, pair_rate, total_pnl, avg_pnl, win_rate,
max_dd, profit_factor, sharpe_proxy per value), note flat vs peaked response,
and write the verdict: keep 0.02, converge live to it, move both to another
value, or "indifferent — keep divergence documented". Post the same as an
issue comment via `gh issue comment 110 --body-file`.
Accept: acceptance checkboxes 1–3 of the issue are checkable from the comment.

### T5 — Full suite + gates
Accept: `python -m pytest -q` green (367 + T1 new tests), zero edits to existing
assertions; `CONSTRAINTS.md §6` verification line updated with the run date.
