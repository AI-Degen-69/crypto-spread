# CONSTRAINTS.md — Quality Bar & Boundaries for Issue #442

## 1. Zero Regressions
- Modified files and integration paths must pass targeted testing:
  ```powershell
  python -m pytest tests/test_backtest_engine.py tests/test_sweep_backtest.py -q
  ```
- Full test suite is strictly reserved for GitHub Actions CI (never run `python -m pytest` locally).

## 2. Memory & Performance Guardrails
- **No Monolithic In-Memory Dumps:** The Golden Dataset contains ~4.5 GB of raw tick lines. The simulation must not call `list(iter_ticks(golden_dir))` at once. Use streaming or chunked window iteration.
- **Incremental Persistence:** Must flush or append each completed run to disk immediately. If a run crashes, previous results must remain intact on disk.

## 3. Resilience & Error Handling
- Wrap individual parameter iteration loops with try/except blocks to guard against division-by-zero, negative square roots, or degenerate book scenarios.
- Log error details with parameter values and proceed to next run.

## 4. Dependencies & Anti-Cheat
- Standard library (`random`, `itertools`, `csv`, `json`, `math`, `argparse`, `dataclasses`, `pathlib`) and existing project dependencies only. No new third-party libraries.
- No disabling or deleting existing tests.
- Pure backtest engine (`backtest/engine.py:replay`) remains deterministic and reproducible.
