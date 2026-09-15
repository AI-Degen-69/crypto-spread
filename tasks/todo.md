# TODO — issue #193: Stats Summary hero cards reflect live oscillation data

Branch: `fix/summary-hero-live-data-193`. Full detail in `tasks/plan.md`.

- [x] **T0** Branch off `master`: `git checkout -b fix/summary-hero-live-data-193`
- [x] **T1** `[Debug/Logic]` Node-harness test for `computeOscillationHeadline`
      (populated / empty / zero-denominator / missing-field), then implement it
      pure in `server/osc_dash.py`
- [x] **T2** `[Debug/Logic]` Tests + implementation for `formatOscPct` and
      `formatOscAsOf` (em-dash on null/NaN/Infinity/ts=0)
- [x] **T3** `[Design/UI]` Replace the Research Conclusion card literals with the
      five em-dash placeholder spans; tests assert literals gone + ids present
- [x] **T4** `[Design/UI]` `renderOscillationHero(summary)` + call it from
      `renderSummaryCharts()` reusing the existing fetch; DOM test for populated
      and empty payloads
- [x] **T5** `[Design/UI]` Stop-loss card provenance line — **decided: option A**
      (`SPEC.md` section 6)
- [x] **T6** `[Gate]` `python -m pytest -q tests/test_osc_dash_integration.py tests/test_orders_trades_table.py`,
      then `python -m pytest -q` (898 passed, 82s), then verify the committed tree
      matches what was tested

## Decisions

- **Stop-loss card provenance — A (locked 2026-09-15).** No document in the repo
  produces those thresholds, and `docs/ev-research-findings-2026-09-11.md:36`
  argues the opposite. The card declares itself an unsourced static heuristic and
  points at that newer research. Numbers untouched.

## Build result (Station III, 2026-09-15)

- Branch `fix/summary-hero-live-data-193`, 3 commits: `ad63ec1` (pure helpers),
  `9266394` (hero markup + wiring), `fef1c39` (stop-loss provenance).
- Targeted gate: 150 passed. Full suite: **898 passed**, 82s, 0 failures.
- Note: the suite is 898 tests, not the ~390 recorded in the planning instinct.
