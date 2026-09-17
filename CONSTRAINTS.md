# CONSTRAINTS — Issue #205: Verify the fill-rate gap against the unified fill rule

## Scope Lock (read first)
1. **Verification only — no engine changes.** `backtest/engine.py`, `strategy/book_math.py`,
   and `strategy/live_trader.py` must not be modified. ADR-0002 (issue #226) already resolved
   the structural question; this issue measures whether the numbers agree. If the measurement
   exposes a suspected code bug, **stop and re-plan** — do not fix silently inside a
   verification issue.
2. **No fabrication of results.** Every number published on the issue comes from an actual run
   against `run/ticks/ticks_2026-09-13.jsonl` (or its stated replacement). No hand-computed
   placeholders, no copying numbers from the issue body or ADR into the results table.

## Quality Guardrails
3. **Zero regressions:** targeted suites covering anything touched must pass:
   `tests/test_backtest_engine.py`, `tests/test_book_math.py`, plus any test file for a new
   helper script. Full-repo sweeps stay with CI on push.
4. **No new external dependencies.** Use the existing stack (`requests`, `pytest`, stdlib).
5. **No suppression:** no skipping, deleting, or weakening existing tests; no lint suppression.
6. **Gates-off equivalence is documented:** #229 replaced `max_start_elapsed_pct` with the
   dead-zone entry rule, so the issue's `max_start_elapsed_pct=1.0` has no direct field. The
   run must state exactly which `BacktestParams` values reproduce "all gates off" (entry delay,
   band, pair-cost gate, queue gate, dead zone) and why each maps.

## Performance Thresholds
7. The 550-window measurement run completes in under 2 minutes on the local machine; a full
   re-run must be cheap enough to repeat when challenged.

## Publication Rules
8. The gh comment on #205 leads with the comparison table (old tape / old cross / new unified
   rule), states the verdict in one sentence, and links ADR-0002 + `docs/engine-decision-rules.md`
   §3. The issue is closed only after the comment is posted.
