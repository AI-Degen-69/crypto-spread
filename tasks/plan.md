# Task Plan — Issue #208: measure the shipped entry-gate defaults (quote_range + dead zone)

**Size tier:** Standard — 2–3 files (sweep script axes, docs/tasks, gh comment), one sweep
run per dataset, no engine changes. The two code complaints in #208 were already resolved by
#228/#229 (PRs #241/#242); what remains is the issue's explicit final requirement:
*"Sweep the proposed thresholds against real tick data before changing any default."*
**Task type:** Research + Code (sweep tooling axes) — verification run, verdict published.

## Context
- `quote_range=(0.10, 0.90)` (structural limit, rules §6) and `dead_zone_val=0.10 pct`
  (structural limit, rules §8) shipped unmeasured; §8 leaves pct-vs-sec open on purpose.
- The sweep engine (`scripts/sweep_backtest.py`) already has a `quote_range` 1D axis under
  `--include-structural`, but **no dead-zone axis at all** — the pct/sec unit question cannot
  be answered today. `BacktestParams.dead_zone_val/dead_zone_unit` and
  `book_math.is_in_dead_zone` already exist, so the axis is grid-generation work only.
- Datasets: `run/ticks/ticks_2026-09-13..16.jsonl`. **Integrity check (TASK-2):** 09-13 (550
  windows), 09-14 (1700), 09-15 (1695) — sound (0 crossed books, 0 corrupt); 09-16 has only
  10 ticks (collector started late that day) — **excluded**, per SPEC edge-case rule.

## Tasks

- [x] **TASK-1 [Code/Logic]**: Add dead-zone sensitivity axes to the sweep engine
  - Target files: `scripts/sweep_backtest.py`.
  - Build: in `generate_sensitivity_grid`, under `include_structural`, add a
    `dead_zone_pct` axis (`0.0, 0.05, 0.10, 0.15, 0.20, 0.30`, unit=pct) and a
    `dead_zone_sec` axis (`15, 30, 60, 90, 120` sec) — labels `dead_zone_pct=…` /
    `dead_zone_sec=…` — plus a combined `dead_zone` filter key in `SENSITIVITY_AXES`
    (matching both prefixes). Follow the existing quote_range axis pattern (skip values equal
    to baseline, `replace(base, …)`).
  - Helper skill: `incremental-implementation`.
  - Verify: `python -m pytest tests/test_sweep_backtest.py -q` green; new unit test asserting
    both axes generate the expected rows and `filter_sensitivity_grid(grid, "dead_zone")`
    keeps baseline + both.
- [x] **TASK-2 [Research/Logic]**: Run the sweeps on real tick data
  - Target files: none (runs) → results in `docs/issue-208-entry-gates-measurement.md`.
  - Build: per dataset — `--preset sensitivity --include-structural` for the quote_range axis
    (`--only quote_range`), then `--only dead_zone`. Record full metric tables per variant.
    Sanity-check datasets first with `scripts/verify_tick_data.py`.
  - Helper skill: `idea-refine` (spike → numbers).
  - Verify: every published number traces to an actual run output; per-dataset window counts
    stated; sec-unit rows split by window length (5m vs 15m).
- [x] **TASK-3 [Research/Logic]**: Interpret against the shipped defaults
  - Build: per knob, a one-sentence verdict over the tables: does `quote_range=(0.10,0.90)`
    hold; is 10% the right dead-zone tail; pct or sec? Pre-register the reading rule first:
    a default holds unless a variant beats it on avg P&L **and** drawdown consistently across
    all datasets; a mixed result is "no change, evidence inconclusive". Flag any adjustment
    candidate for the operator — no silent default change (CONSTRAINTS §3).
  - Helper skill: `doubt-driven-development`.
  - Verify: conclusions cite only measured numbers; inconclusive stated as inconclusive.
- [x] **TASK-4 [Docs]**: Publish evidence on #208
  - Target files: gh comment on issue #208; findings folded into
    `docs/issue-208-entry-gates-measurement.md`.
  - Build: comment leads with the verdict table per knob, links
    `docs/engine-decision-rules.md` §6/§8, labels datasets; close the issue after posting.
  - Helper skill: `documentation-and-adrs`.
  - **Done:** comment posted 2026-09-17; verdicts: quote_range (0.10,0.90) does not hold
    (candidate (0.30,0.70), operator decision), dead zone 10% too small (candidate 0.30 pct),
    unit=pct confirmed. Issue left open for the operator's decision on the candidates.
- [x] **TASK-5 [QA/Tests]**: Regression gate
  - Build: no engine changes (CONSTRAINTS §1), so the gate is the touched-file suites:
    `python -m pytest tests/test_sweep_backtest.py tests/test_backtest_engine.py tests/test_book_math.py -q`.
  - Helper skill: `test-driven-development` (as gate only).
  - **Done:** 197 passed in 0.86s (sweep + engine + book_math). Committed on master
    (3bd47e9) — per docs/git-workflow.md §1 this issue is a measurement/tooling change made
    directly on the base branch; CI on push remains the merge gate.

## Verification Matrix
| Task | Method |
|---|---|
| TASK-1 | `tests/test_sweep_backtest.py` incl. new axis tests |
| TASK-2 | Measured runs, per-dataset labels, dataset integrity pre-check |
| TASK-3 | Pre-registered reading rule applied to tables |
| TASK-4 | `gh issue view 208 --comments` shows the tables |
| TASK-5 | Targeted pytest suites pass |
