# Quality Guardrails & Constraints — Issue #394

## Scope & Functional Boundaries
- Restore focus to `btSweepCenter` after progress re-renders if and only if `btSweepCenter` was active before the DOM rebuild.
- If `btSweepCenter` was not focused, do not steal focus or alter activeElement.
- Preserve typed input values and anchor wrapper integrity across progress ticks.
- No modifications to full-card replacement paths or anchor calculation logic.

## Anti-Regression & Verification
- Targeted integration tests must pass: `python -m pytest tests/test_osc_dash_integration.py -q`.
- Must include a regression test in `tests/test_osc_dash_integration.py` running in Node that fails on the unpatched code and passes after the fix.
- No test skipping, suppression, or deletion.
- No new runtime dependencies.
