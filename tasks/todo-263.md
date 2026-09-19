# TODO — Issue #263: Strategy Geometry Preview

- [x] TASK-1 [Design/UI]: Establish preview layout and visual token contract.
- [x] TASK-2 [Design/UI]: Replace distorted SVG sizing with a readable price-time plot.
- [x] TASK-3 [Design/UI]: Implement collision-free right-side level labels.
- [x] TASK-4 [Design/UI]: Simplify header pills and legend to at most five entries.
- [x] TASK-5 [Code/Regression]: Preserve calculations and reactive behavior with focused tests.
- [x] TASK-6 [QA]: Run issue-specific UI verification and code simplification.

## Acceptance checkpoints

- [ ] Default labels are legible and non-overlapping.
- [ ] 1200–1920px chart layout is proportional and readable.
- [ ] Corridor, delay, dead-zone, quote, and exit visuals remain distinguishable.
- [ ] Legend contains at most five entries.
- [ ] Existing strategy calculations and zero-value behavior are unchanged.
- [ ] `python -m pytest tests/test_osc_dash_integration.py -q` passes.
