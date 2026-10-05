Branch: i442/run-overnight-param-opt-golden-dataset | Issue: #442

# Implementation Plan — Overnight Parameter Optimization Sweep on Golden Dataset

## CodeRabbit Intake Note
- CodeRabbit plan was requested in Issue #442 comment.
- Plan implemented directly against repository seams (`scripts/sweep_backtest.py`, `backtest/engine.py`, `run/ticks/golden/`).

## Resolved Open Questions
- Sampling Budget: Full Cartesian space is billions of points. Runner supports `--iterations N` (default 50-1000) using stratified random sampling across all 8 requested parameter axes, preceded by the Baseline benchmark run.
- Memory: Uses structured window loading with CID-grouping across daily tick files (`run/ticks/golden/ticks_*.jsonl`).

## Tasks

### [x] Task 1: [Backend/Logic] Multi-Dimensional Parameter Sampler & Overnight Sweep Engine
- **Files:** `scripts/run_overnight_sweep.py`
- **Depends on:** none
- **Size:** M
- **Description:** Implemented `scripts/run_overnight_sweep.py` with:
  1. Parameter search space matching Issue #442 (offset, queue, share size, entry delay, late entry, leg chase, reversal buffer, exit stop loss per slug/default).
  2. `generate_candidates()` with deterministic seeding and baseline insertion.
  3. Window loader reading `run/ticks/golden/` windows preserving midnight windows across days.
  4. Simulation loop evaluating each parameter configuration across 10 series, catching and logging exceptions.
  5. Incremental CSV writer logging each run with all required dimensions and risk metrics.
- **Verification:** Run `python -m scripts.run_overnight_sweep run/ticks/golden --iterations 3 --max-windows 50` passes cleanly.

### [x] Task 2: [Backend/Logic] Summary Report & Metrics Aggregator
- **Files:** `scripts/run_overnight_sweep.py`
- **Depends on:** Task 1
- **Size:** S
- **Description:** Implemented `generate_summary_report()` outputting `overnight_summary.md`:
  1. Benchmark comparison against Baseline across all 10 series.
  2. Baseline breakdown across BTC, ETH, BNB, XRP, and SOL.
  3. Top 3 configurations overall ranked by Risk-Adjusted Return.
  4. Top 3 configurations per asset.
  5. Quantitative market insights and observations.
- **Verification:** Generated `overnight_summary.md` and verified table structure and rankings.

### [x] Task 3: [Verification/QA] Test Suite & Golden Dataset Smoke Test
- **Files:** `tests/test_run_overnight_sweep.py`
- **Depends on:** Task 1, Task 2
- **Size:** S
- **Description:** Wrote comprehensive unit tests covering:
  1. Parameter space bounds and types.
  2. Candidate generation determinism.
  3. Incremental CSV logging and header format.
  4. Evaluation logic and per-asset slicing.
  5. Summary report generation and ranking.
- **Verification:** `python -m pytest tests/test_run_overnight_sweep.py tests/test_backtest_engine.py tests/test_sweep_backtest.py -q` passes 211 tests.
