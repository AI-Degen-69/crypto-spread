# SPEC — Issue #205: Verify the fill-rate gap against the unified fill rule

## Goal
Re-run the exact measurement issue #205 reported (gates off, 550 windows of
`run/ticks/ticks_2026-09-13.jsonl`) under the current engine — which since issue #226 /
ADR-0002 has one fill rule (`book_math.resting_bid_filled`: print at our price OR best ask
fully through) and no `fill_model` knob — and publish whether the 16x tape-vs-cross gap is
closed.

## Acceptance Criteria
1. A deterministic, repeatable replay produces fill counts comparable to the issue's table
   (any-leg / up / down / both / pairs) for the unified rule on the same dataset.
2. The run's gate mapping is documented (dead-zone entry rule standing in for the retired
   `max_start_elapsed_pct`; entry delay, band, pair-cost gate, queue gate all off).
3. A gh comment on #205 shows: comparison table (old tape 20/550, old cross 314/550, new
   unified number), one-sentence verdict, links to ADR-0002 and `docs/engine-decision-rules.md` §3.
4. The verdict uses a pre-registered expectation band (see tasks/plan.md TASK-2); a result
   outside the band is reported as "needs re-plan", never spun as a pass.
5. Zero modifications to `backtest/engine.py`, `strategy/book_math.py`, `strategy/live_trader.py`.
6. Targeted suites `tests/test_backtest_engine.py` + `tests/test_book_math.py` pass.

## Edge Cases
- The 2026-09-13 file may have thin tape coverage that day — cross-check `run/ticks/manifest.json`
  tape stats before interpreting; if tape is near-empty the unified rule degenerates toward
  the book detector and that must be stated in the comment.
- Windows missing from the file vs the issue's 550 count — state the actual denominator used.
- Dataset drift: `run/` is gitignored and regenerable; if the file no longer exists, fall back
  to the newest available date and label every number with its dataset.

## Out of Scope
- Any change to fill behavior, entry gates, or the collector.
- Reopening the `fill_model` knob (ADR-0002 is accepted; not relitigated here).
- Fixing #204 (entry-gate blocking) — separate issue, referenced only.
