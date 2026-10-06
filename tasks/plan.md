Branch: i455/feat-sweep-add-self-improving-iterative-optimizer | Issue: #455

# Implementation Plan — Iterative Sweep Optimizer with Sample Gate

## Size & Stack
- Tier: **Standard** — new search + gate + split + CLI in one script (`scripts/sweep_backtest.py`), tests in `tests/test_sweep_backtest.py`, one glossary line; single architectural decision (coordinate descent + purged holdout, sweep layer only).
- Task type: **Code**. Stack: Python, pytest (`tests/test_sweep_backtest.py`). No UI, no API, no dependency change. No CI gate (workflow deleted per operator order) — targeted local suite only.

## CodeRabbit Intake Note
- Adopted: 3-task skeleton (gate+split / descent+confirm+CLI / tests+glossary) with exact fixtures; seam pointers (SweepRunResult, compute_metrics both constructors, run_sweep:494, sensitivity grid base_params, deduplicate, main presets, iter_ticks/group_by_cid, _make_window_result, _write_dummy_ticks); design choices (filled-window count, purged T-split, grid reuse, best-improvement, exit-0 reporting).
- Rejected: nothing structural — merged phases into 4 atomic tasks per station convention (code split in two, tests+glossary, verification).
- `[UNVERIFIED]` at intake: none left — every cited seam spot-checked (dataclass fields 33-51, both compute_metrics constructors 70-86/129-145, grid signature 148-162 with base_params, run_sweep 494-500, _make_window_result test:24, _write_dummy_ticks test:587, glossary window line 72, ADRs 0001-0003).

## Resolved Open Questions (from code + plan, not asked)
- **Objective form:** net `total_pnl_cents` (already fee-net, size-scaled in `compute_metrics:90-92`) + `>= 30` filled windows + out-of-sample confirmation. Resolved by CodeRabbit design choice 1 + code read; `needs-answers` label removed.
- **Search method:** best-improvement coordinate descent reusing `generate_sensitivity_grid(base_params=incumbent)` + `deduplicate_grid` + `run_sweep` — no new generator, no new deps. Resolved by design choices 3–4.
- **"Trade" definition:** filled window (`filled_up or filled_down`, counted once) — includes settlement-only legs that `pair_captured or exit_taken` misses (CodeRabbit research). Glossary line records it.
- **type-design-analyzer / code-explorer:** skipped — no interface change, sweep layer traced directly.

## Spec
See `SPEC.md` (Standard tier): objective, acceptance, key rules (sample unit, purged split, descent/confirmation rules, exit-0 reporting), out of scope.

## Improvement Proposal
Skipped with reason: the plan is already minimal and complete (sweep-layer only, no new abstractions) — no evidence-backed improvement available without inventing filler.

## Tasks

### [x] Task 1: [Backend/Logic] Sample gate + purged chronological split in `scripts/sweep_backtest.py` (M)
- **Files:** `scripts/sweep_backtest.py` (SweepRunResult, compute_metrics, new helpers)
- **Depends on:** none (primitives everything else builds on — first)
- **Description:** (a) add `filled_windows: int = 0` after non-default dataclass fields, populate in both compute_metrics constructors via `w.filled_up or w.filled_down` counted once (asdict exposes it); (b) add `MIN_FILLED_WINDOWS = 30` + `passes_sample_gate(result, min=MIN)` (no gating of existing presets); (c) add `split_windows_chronologically(grouped, holdout_frac)` reading clocks from first snapshot per CID group post-group_by_cid, excluding non-finite `end_ts > start_ts` as unclocked, T = start_ts at floor(n*(1-frac)), in-sample `end_ts <= T`, holdout `start_ts >= T`, purge rest, chronological order, no mutation. Stdlib only.
- **Skill:** `test-driven-development`
- **Verification:** Task 3 gate/split tests fail before / pass after.

### [x] Task 2: [Backend/Logic] Coordinate descent + holdout confirmation + `iterative` CLI (M)
- **Files:** `scripts/sweep_backtest.py` (new functions + main wiring)
- **Depends on:** Task 1 (gate + split primitives)
- **Description:** (a) `run_coordinate_descent(...)`: baseline `BacktestParams(quote_shares=size)`, per-pass `deduplicate_grid(generate_sensitivity_grid(base_params=incumbent,...))` via `run_sweep`, skip seen (canonical sorted-keys serialization), reject gate failures, best remaining PnL (ties → grid order), strict improvement to accept (incumbent below gate scores -inf), stop at no-improvement or max_passes, per-pass history with `pN:` labels; (b) `confirm_on_holdout(...)`: one run_sweep for baseline+winner, confirmed iff winner holds gate + strict holdout PnL win (baseline retained → not confirmed), short reason strings; (c) CLI: `iterative` preset + `--min-filled-windows` (default 30), `--holdout-frac` (0.3), `--max-passes` (5), ap.error validation (min ≥ 1, 0 < frac < 1, non-empty partitions; keep --only/requires-sensitivity), console tables + JSON keys (split/history/baselines/winner/params/confirmed/reason), exit 0 always. Knobs default, structural only via --include-structural.
- **Skill:** `test-driven-development`
- **Verification:** Task 3 descent/confirm/CLI tests fail before / pass after.

### [x] Task 3: [Backend/Logic] Regression tests + glossary term (M)
- **Files:** `tests/test_sweep_backtest.py`, `docs/glossary.md`
- **Depends on:** Task 2
- **Description:** (a) extend `_make_window_result` with filled_up/down (defaults preserved); metric test (settlement/paired/stopped/no-fill → count 3); RUN_0153-shape gate reject + 30-window accept; (b) split test (15-min crosser purged, no-clock counted, disjoint CIDs, max-in-end ≤ min-hold-start, deterministic); (c) descent test with monkeypatched run_sweep (1-window high-PnL never accepted, queue+offset coordinates preserved, terminates ≤ max_passes) + confirmation accept/reject cases; (d) CLI test (tmp ticks → exit 0, iterative keys, confirmed false on tiny data; SystemExit on empty holdout and frac 0); (e) glossary "filled window" line. Weaken nothing. Record (don't fix) the overnight total_trades undercount as a noticed row if surfaced.
- **Skill:** `test-driven-development`
- **Verification:** new tests fail pre-fix, pass post-fix.

### [x] Task 4: [Backend/Logic] Targeted verification sweep (XS)
- **Files:** none (verification only)
- **Depends on:** Task 3
- **Description:** run `python -m pytest tests/test_sweep_backtest.py -q`. No full-suite local run; no CI gate exists. Confirm glossary terms in comments where touched.
- **Skill:** `incremental-implementation`
- **Verification:** suite green; branch clean except intended files.

## Checkpoints
- After Task 1: gate counts exposure, split purges cleanly.
- After Task 3: n=1 winners provably rejected, descent/confirmed logic locked — ready for `iii-build-plan` handoff review.
