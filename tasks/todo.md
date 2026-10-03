# Todo — Issue #421

- [x] T1 [Design/UI]: hydrate `cockpitPairCost` from `st.params.max_pair_cost` in both `renderCockpitUI()` branches
- [x] T2 [Code/Logic]: branch-sliced source pin — pair cost hydrated from `dollarsToCents`, no literal, identical key set in both branches
- [x] T3 [Code/Logic]: `0.995 ↔ 99.5` helper assertions + stopped-engine endpoint contract + #419 ledger row N1 → resolved; final gate `tests/test_osc_dash_integration.py tests/test_param_registry.py -q` → 368 passed
- [ ] Handoff: run `/iv-review-build-and-pr` on `i421/cockpit-max-pair-cost-hydration`