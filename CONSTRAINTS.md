# CONSTRAINTS — Issue #269: Duration-aware backtest timing percentages

## Scope lock
1. Change only Backtest timing controls, Strategy Geometry Preview, duration conversion, and focused tests/docs for this issue.
2. Do not change live cockpit semantics, trading rules, sweep axes, tick data, or unrelated tabs.
3. Preserve timestamp-based simulation and existing internal parity behavior.
4. No new dependencies.

## Quality guardrails
5. Targeted tests must pass: engine timing/parity, dead-zone parity, dashboard integration, and theme/UI contracts.
6. Add assertions for 5m/15m conversion: 10% = 30s/90s, 0%, 100%, mixed-duration windows, invalid values, and partial/invalid clock handling.
7. Browser verification must inspect the Backtest tab at 5m and 15m contexts and confirm a normalized 0–100% axis, correctly positioned delay/dead-zone regions, and no horizontal overflow or label overlap.
8. Do not skip, weaken, delete, or suppress tests. Do not alter expected P&L outcomes except where the old fixed-seconds behavior was demonstrably wrong for 15m.
9. Keep UI interaction responsive: percentage conversion and preview updates must remain client-side and complete without network calls.

## Interface guardrails
- Operator-facing timing inputs are percentages in the inclusive range 0..100.
- Internal conversion uses each window's actual timestamp duration; never infer duration from snapshot count or a 5m default.
- Existing API/file safety and busy-guard behavior remain intact.
- Any compatibility translation from existing second-based fields must be explicit, documented, and tested; no silent ambiguity between seconds and percentages.
