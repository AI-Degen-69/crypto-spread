# CONSTRAINTS.md — Issue #146: replay cross-check (shadow night vs official backtest)

## Quality Gates & Hard Thresholds

### 1. Test Suite Integrity
- **Pass Rate**: 100% for the touched gate —
  `python -m pytest tests/test_backtest_engine.py -q` fully green before and after.
- **New behavior needs tests**: the replay driver asserts its own config
  mirror (params hash) and scope filter (universe + time range) before totals
  are accepted; no silent empty-replay (n_events > 0 required).
- **Anti-Cheat**: no touching existing fixtures/expectations; no skipped
  tests, no weakened assertions, no linter suppressions. Engine math and
  classification constants byte-identical — this issue changes NO production code.

### 2. Behavior & Scope Boundaries
- **Read-only production code**: `backtest/`, `server/`, `scripts/backtest.py`,
  `scripts/sweep_backtest.py`, `strategy/` NOT modified. Known warts
  (`scripts/backtest.py --help` crash, missing --entry-delay/--entry-band CLI
  flags) are documented, NOT fixed here.
- **Exact-config mirror**: every `BacktestParams` field set from the shadow
  `final.json` params (§1 mapping in `tasks/plan.md`); any field that cannot
  be mirrored is disclosed in the verdict, never silently defaulted.
- **Scoped honesty**: only the 57 in-coverage events are compared; the 9
  pre-coverage events are reported separately, never folded into replay totals.
- **Units**: cents↔USD normalization explicit in-artifact before any verdict.
- **No new dependencies** (stdlib + existing stack only). New files limited to
  `scripts/replay_shadow_check.py` + `replay_comparison/` artifacts.
- **No live money, no re-running the shadow, no order-flow changes.**

### 3. Verdict Discipline
- **20% rule**: |replay − shadow| > $1.053 on scoped realized P&L ($5.265)
  mandates a follow-up finding issue — never silently accepted.
- Every comparison cell traceable to `trades.jsonl` or replay JSON.
- Verdict states magnitude + direction (validated / optimistic by X /
  pessimistic by X) plus known model deltas (tape vs touch, chase, re-entry).

### 4. Perf & Dependencies
- **Perf**: single-file replay over `ticks_2026-09-12.jsonl` (700MB) — stream
  via `iter_ticks`, never load whole file into memory; reuse existing engine
  batching. Expected runtime: minutes, not hours.
- **Dependencies**: none new.
