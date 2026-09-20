# CONSTRAINTS — Issue #266: Sweep Visual sensitivity clarity

## Scope lock
1. Change only the Sweep Visual endpoint, its served UI, focused tests, and issue planning records.
2. Preserve existing Chart.js and theme-token integration; add no dependencies.
3. Keep the feature one-axis only: `queue`, `offset`, `exit_stop`, and `exit_rev`.
4. Treat each tested value as an independent replay; do not add interpolation, trend fitting, or untested values.
5. `exit_stop` must set the 5m and 15m default stop values together in the copied sweep parameters. Do not change production defaults or strategy/backtest math.
6. Preserve existing DOM IDs and canonical ten-market order.
7. Do not include Issue #174 changes, tick data, unrelated dashboard tabs, or presentation artifacts.

## Quality guardrails
8. Targeted gate: `python -m pytest tests/test_osc_dash_integration.py tests/test_theme_tokens.py -q` plus any new focused test module.
9. New behavior requires assertions for bar configuration, humanized axis labels, shared 5m/15m stop semantics, best aggregate and best-market metadata, empty data, zero-fill order, and busy-guard release.
10. Browser verification must use `run/ticks/ticks_2026-09-18.jsonl` when present and record chart count, visible markers, and selected axis text.
11. No skipped/deleted assertions, test weakening, broad snapshot-only coverage, or lint suppression.
12. Do not run the full local test suite; CI remains the merge gate.

## Interface guardrails
- Valid-axis errors must list exactly the supported machine axes.
- Readable labels are additive presentation metadata; machine slugs remain unchanged.
- Best-result metadata is deterministic and safe for empty points.
- Missing markets render zero-valued bars and cannot win through missing-data defaults.
- Negative values render below a visible zero baseline.
