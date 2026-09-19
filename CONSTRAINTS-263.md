# CONSTRAINTS — Issue #263: Strategy Geometry Preview

## Scope lock

1. Only the Backtest tab Strategy Geometry Preview may change.
2. No changes to strategy math, backtest engine, parameter values, defaults, APIs, or other charts/tabs.
3. Preserve the existing preview DOM IDs and `updateBacktestParamPreview()` reactive wiring.
4. No new external dependencies.
5. Existing unrelated uncommitted changes, especially Issue #174 work, must remain untouched.

## Quality guardrails

6. `python -m pytest tests/test_osc_dash_integration.py -q` must pass with zero regressions.
7. Existing Node zero-value assertions must remain enabled and pass.
8. New behavior must have focused assertions; no deleted assertions, skipped tests, or lint suppressions.
9. The legend must render at most five grouped entries.
10. Right-side labels must have deterministic non-overlapping placement for default and clustered level values.
11. The chart must remain readable at 1200px and 1920px viewport widths without horizontal overflow or visible SVG distortion.
12. Browser verification is required before Station IV handoff; full-repository pytest remains CI's responsibility.
