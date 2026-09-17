# CONSTRAINTS — Issue #208: entry-gate defaults measurement (quote_range + dead zone)

## Scope Lock (read first)
1. **Measurement first — no engine changes.** `backtest/engine.py`, `strategy/live_trader.py`,
   and `strategy/book_math.py` must not be modified. The defaults under measurement
   (`quote_range=(0.10, 0.90)`, `dead_zone_val=0.10 pct`) were shipped by #228/#229 and are
   not re-litigated here; this issue measures whether the data supports them.
2. **Only sweep tooling may change.** `scripts/sweep_backtest.py` may gain dead-zone
   sensitivity axes (grid rows + `SENSITIVITY_AXES` entries). No engine decision logic, no
   BacktestParams fields, no default values.
3. **Any default change is out of scope.** If the sweep contradicts a shipped default, the
   verdict is "adjust candidate — operator decision", never a silent patch.
4. **No fabrication of results.** Every published number comes from an actual run against
   `run/ticks/*.jsonl`, labeled with the dataset dates used. No hand-computed placeholders.

## Quality Guardrails
5. **Zero regressions:** targeted suites covering touched files must pass:
   `tests/test_sweep_backtest.py`, `tests/test_backtest_engine.py`, `tests/test_book_math.py`.
   Full-repo sweeps stay with CI on push (AGENTS.md testing policy).
6. **No new external dependencies.** Existing stack only (stdlib, pytest).
7. **No suppression:** no skipping, deleting, or weakening existing tests; no lint suppression.
8. **Structural-limit convention respected** (ADR-0003): dead-zone and quote_range rows are
   generated only under `--include-structural`, same as the existing quote_range axis.

## Performance Thresholds
9. A single sensitivity sweep over one dataset completes in under 10 minutes locally; a
   re-run of one configuration is cheap enough to verify determinism on demand.

## Publication Rules
10. The gh comment on #208 leads with the comparison tables (per axis: baseline vs variants),
    states a one-sentence verdict per knob (quote_range low/high bound; dead zone pct default;
    pct-vs-sec reading), links `docs/engine-decision-rules.md` §6/§8, and labels every number
    with its dataset. The issue is not closed before the comment is posted.
