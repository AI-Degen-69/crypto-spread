# CONSTRAINTS — Issue #255: Document sample discrepancies in tick data and how the backtester treats them

## Scope Lock
1. **Target Documentation**: Add a dedicated section `## Sample Discrepancies & Replay Integrity` in `docs/operations.md`.
2. **Glossary Integrity**: Must reference `docs/glossary.md` regarding the distinction between `mid` (two-sided mid across both legs) and `the recorded mid` (`"mid"` field in tick files, up leg alone), without conflating them with sample discrepancies.
3. **Zero Behavior Changes**: Strictly no changes to the backtest engine skip contract (`backtest/engine.py:_json_or_skip`), verification thresholds or logic (`scripts/verify_tick_data.py`), or dashboard rendering (`server/osc_dash.py`).
4. **Data Contract Preservation**: All JSON report structures and dictionary keys (`sample_issues`, `corrupt_lines`, etc.) must remain 100% intact and backward-compatible. Only human-facing CLI output labels / `--help` text in `scripts/verify_tick_data.py` may be aligned with documentation terminology.
5. **No New Dependencies**: Stdlib and existing dependencies only.

## Quality Guardrails
6. **Targeted Test Gate**:
   - `python -m pytest tests/test_verify_tick_data.py -q` must pass with 0 failures.
7. **Anti-Cheat**:
   - No disabling, skipping, or weakening tests.
   - No suppressing warnings or linters.
8. **CLI Verification**:
   - `python -m scripts.verify_tick_data --help` must run cleanly and display updated descriptions without syntax errors.
