# Task Plan — Issue #255: Document sample discrepancies in tick data and how the backtester treats them

**Size tier:** Tiny / Docs — documentation addition in `docs/operations.md` + terminology alignment in CLI `--verbose` / `--help` of `scripts/verify_tick_data.py`. No behavior changes in verifier, engine, or dashboard.
**Task type:** Docs (verification by targeted tests `tests/test_verify_tick_data.py` and CLI check).

## Context & Background

- The dashboard's Tick Files tab displays a **Sample Discrepancies** panel listing up to 10 issues from `sample_issues` (e.g. `Row 1234: mid price 1.15 out of bounds [-0.01, 1.01]`, `Row 567: json_decode_error ...`).
- In `scripts/verify_tick_data.py`, `sample_issues` caps at 20 (`max_sample_issues`) per file (`scripts/verify_tick_data.py:284`), capturing:
  1. `json_decode_error` (line 344)
  2. `schema_or_book_issue` (line 361) from `verify_tick_record` (line 131)
  (Note: `collector_errors` is tracked as a separate counter on `err` presence and is not appended to `sample_issues`).
- The dashboard slices the first 10 items (`server/osc_dash.py:4994`).
- In `backtest/engine.py:74`, `_json_or_skip` returns `None` for empty/malformed/non-dict lines, and `iter_ticks` silently drops `None` items. Replay proceeds without crashing, and without logging skipped rows.
- Replay results can therefore run over "dirty" data without warning unless the operator verifies the dataset or inspects the Tick Files tab.
- Distinct hazard: `docs/glossary.md` defines `mid` (two-sided mid) vs `the recorded mid` (`"mid"` field in tick file, up-leg alone). This distinction must be referenced and not conflated with sample discrepancies.

## Tasks

- [x] **TASK-1 [Docs]**: Add `## Sample Discrepancies & Replay Integrity` section to `docs/operations.md`
  - Target file: `docs/operations.md`
  - What is built:
    - Definition of "Sample Discrepancy" and the distinction between sampled issues (`json_decode_error`, `schema_or_book_issue`) and separate counters like `collector_errors`.
    - Explanation of the 20-sample cap in `sample_issues` and 10-item display on the dashboard vs the full file-level counters (`corrupt_lines`, `schema_errors`, `crossed_books`, `book_anomalies`, `collector_errors`).
    - The backtester's silent skip contract: `_json_or_skip` and `iter_ticks` drop invalid rows silently, making pre-replay verification a required data-quality habit.
    - Explicit "When to Care" guidelines: distinguishing acceptable noise (isolated late starts/minor gaps) from replay-invalidating defects (crossed books, high corrupt line ratios, missing legs).
    - Cross-reference `docs/glossary.md` regarding `mid` vs `the recorded mid`.
  - Helper skill: `documentation-and-adrs`
  - Verify: Markdown format check and verification against codebase facts.

- [x] **TASK-2 [Docs/CLI]**: Align CLI terminology and `--help` in `scripts/verify_tick_data.py`
  - Target file: `scripts/verify_tick_data.py`
  - What is built:
    - Update `--verbose` help description to mention "sample discrepancies" (`--verbose`: "Show sample discrepancies / issues").
    - In `format_verification_report`, update the verbose header from `Sample Issues:` to `Sample Discrepancies (sample_issues):` and add a one-line reference pointer to `docs/operations.md`.
    - Retain exact dict key `sample_issues` to ensure zero API or schema breaking changes.
  - Helper skill: `documentation-and-adrs`
  - Verify: `python -m scripts.verify_tick_data --help` and `python -m pytest tests/test_verify_tick_data.py -q`.

- [x] **TASK-3 [QA/Verification]**: Run test suite gate
  - Target: `tests/test_verify_tick_data.py`
  - What is run: Run pytest to ensure all 17 tests pass with zero regressions.
  - Helper skill: `test-driven-development`
  - Verify: `python -m pytest tests/test_verify_tick_data.py -q`.

## Verification Matrix
| Task | Method |
|---|---|
| TASK-1 | Review content against `docs/glossary.md`, `scripts/verify_tick_data.py`, `backtest/engine.py` |
| TASK-2 | `python -m scripts.verify_tick_data --help` |
| TASK-3 | `python -m pytest tests/test_verify_tick_data.py -q` |

## Post-build gates (Station IV)
- `python -m pytest tests/test_verify_tick_data.py -q` passes with 0 failures.
- Clean `git diff` with no unintended modifications to engine logic or dashboard rendering.
