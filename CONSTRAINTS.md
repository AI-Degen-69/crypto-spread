# CONSTRAINTS — Issues #222 + #223: Dead-zone unit & unpaired-leg-at-expiry measurements

## Scope Lock
1. **Measurement only.** No changes to `backtest/engine.py`, `strategy/live_trader.py`,
   `research/sweeps/sim2.py`, or `research/sweeps/ev_lab.py`. New code lives in
   `research/sweeps/dead_zone_lab.py` + `tests/test_dead_zone_lab.py` + docs.
2. **One fill rule.** Every fill decision goes through
   `strategy.book_math.resting_bid_filled` (issue #226 / ADR-0002) with the sell-print
   pre-filter (issue #182). Reimplementing the fill rule in the lab is forbidden.
3. **No engine-default flip inside this task.** The reports may recommend; the
   `dead_zone_unit` / `naked_leg_at_expiry` defaults change only in a follow-up approved
   by the operator.
4. **Doc verdicts only where the issues live:** §8 (`dead_zone`) and §14
   (`naked_leg_at_expiry`) of `docs/engine-decision-rules.md` gain the measured verdict
   and a link to the report. No other sections touched.
5. **Naming per `docs/glossary.md`:** "dead zone" (not entry timeout), "unpaired leg"
   (not naked position), "tradeable range" wording for quote_range.

## Quality Guardrails
6. **Zero regressions on targeted suites:**
   - `python -m pytest tests/test_dead_zone_lab.py -q` (new, all green)
   - `python -m pytest tests/test_ev_sweep_lab.py -q` (lab untouched — parity guard)
   - `python -m pytest tests/test_backtest_engine.py -q` (engine untouched — parity guard)
7. **Anti-cheat:** no skipping/weakening tests, no suppressed warnings in the lab script,
   no fabricated numbers — every reported figure must be reproducible by re-running
   `python -m research.sweeps.dead_zone_lab`.
8. **Reproducibility:** the lab script takes optional dataset paths, defaults to every
   `run/ticks/ticks_*.jsonl`, and stamps the dataset files + row counts into both JSON
   artifacts. Same inputs → same outputs (no sampling randomness).
9. **Dependencies:** no new external dependencies; stdlib + existing project imports only.

## Performance
10. **Runtime ceiling:** full-dataset lab run completes in under 120s on the built
    `ev_lab` window cache (single pass, no per-config re-parse). Cache build (~2–3 min
    one-off) is outside the ceiling and reported separately.
