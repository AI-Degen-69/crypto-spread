# Task Plan — Issue #205: Verify the fill-rate gap (tape 3.6% vs cross 57%) against the unified fill rule

**Size tier:** Standard — 2–3 files (one new research script, docs, tasks files), one decision
already locked by ADR-0002, no engine changes.
**Task type:** Research + Docs (verification spike; no production code paths touched).

## Context
Issue #205 asked *why* `fill_model="tape"` filled 3.6% of 550 windows while `fill_model="cross"`
filled 57%. Answer, landed by issue #226 / ADR-0002 on 2026-09-16: the `fill_model` knob was
removed; both engines now share the single fill rule `strategy/book_math.resting_bid_filled`
(print at our price **or** best ask fully through), which is the `both` model the live engine
always ran. The ADR explains the 16x gap but never re-ran the #205 measurement. This plan runs
that measurement and publishes the evidence.

## Tasks

- [x] **TASK-1 [Research/Logic]**: Reproduce the #205 baseline under the current engine
  - Target files: new `research/sweeps/verify_205_fill_rate.py` (or extend
    `scripts/replay_shadow_check.py` if it already fits — check before writing).
  - Build: gates-off `BacktestParams` per CONSTRAINTS §6 (document the dead-zone mapping for
    the retired `max_start_elapsed_pct`); run `backtest.engine.replay()` over
    `run/ticks/ticks_2026-09-13.jsonl` (550 windows) with the unified rule; count
    `filled_up`/`filled_down`/any-leg/pairs exactly as the issue's table does.
  - Helper skill: `idea-refine` (spike → numbers).
  - Verify: run completes <2 min; counts are deterministic across two consecutive runs.
  - **Done:** `research/sweeps/verify_205_fill_rate.py`; 550/1700/1695/10 windows across
    4 datasets; deterministic. **Accepted improvement folded in:** date-parameterized, all
    datasets measured (not only 09-13).
- [x] **TASK-2 [Research/Logic]**: Interpret the numbers against the expectation band
  - Build: compare against the issue's tape (20/550) and cross (314/550) anchors. Expected:
    unified-rule fill rate sits strictly between them, closer to cross (the ask-through
    detector alone recovers most of the book model's fills). Fill-rate drop vs cross is
    *consistent* if unified ≥ ~35% of windows and the offset sweep keeps its monotone shape
    (tighter offset → more fills). A result outside that band means either the tape capture
    is thin (check `manifest.json` tape stats for the date) or a matcher bug — report as
    "needs re-plan", not as a pass.
  - Helper skill: `doubt-driven-development`.
  - Verify: interpretation written before looking at code again; conclusions cite only
    measured numbers.
- [ ] **TASK-3 [Docs]**: Publish evidence on #205
  - Target files: gh comment on issue #205; `docs/backtest-optimization-results.md` header
    note (its numbers predate #226) pointing at the new result.
  - Build: comment leads with the comparison table (tape / cross / unified), one-sentence
    verdict, links ADR-0002 + `docs/engine-decision-rules.md` §3; close the issue after
    posting.
  - Helper skill: `documentation-and-adrs`.
  - Verify: comment visible via `gh issue view 205 --comments`; table numbers match TASK-1
    output exactly.
  - **Status: blocked on operator.** The measured result (99.1% unified, tape-only 16.7%,
    ask-only 99.1%) **falls outside the pre-registered expectation band** (between 3.6% and
    57%). Per SPEC acceptance criterion 4, the verdict written is "needs re-plan", not a
    pass — see `docs/issue-205-verification-results.md`. Publishing "all clear, closing" as
    a comment would misrepresent the result; publishing "needs re-plan" contradicts the
    plan's framing (ADR-0002 already landed). Decision deferred to the operator.
- [x] **TASK-4 [QA/Tests]**: Regression gate
  - Build: no production code changes, so the gate is confirmation, not new tests — run
    `python -m pytest tests/test_backtest_engine.py tests/test_book_math.py -q` and record
    results.
  - Helper skill: `test-driven-development` (as gate only).
  - Verify: both suites pass; CI (push) remains the merge gate per AGENTS.md.
  - **Done:** 171 passed in 0.82s.

## Verification Matrix
| Task | Method |
|---|---|
| TASK-1 | Measured run, deterministic re-run check |
| TASK-2 | Pre-registered expectation band vs measured numbers |
| TASK-3 | `gh issue view 205 --comments` shows the table |
| TASK-4 | Targeted pytest suites pass |
