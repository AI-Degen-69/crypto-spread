# TODO — Issue #319 (branch i319/dashboard-jungle-king-tab-present-the-ofat)

Plan: tasks/plan.md · SPEC: SPEC-319.md · Guardrails: CONSTRAINTS-319.md
Sub-issues: T1=#320, T2=#321, T3=#322

- [x] TASK-1 (#320) — `GET /api/jungle-king`: write failing tests → implement endpoint (manifest groups, registry join, baseline_in_values, graceful error) → green ✔
- [ ] TASK-2 (#321) — tab skeleton: sidebar button + `tab-jungleking` container + `switchTab()` hook + `loadJungleKing()` stub fetch → HTML assertions green
- [ ] TASK-3 (#322) — presentation: per-param cards, prominent baseline + highlighted chip, value chips in manifest order, class badges, inline error/empty notice → render-level tests green + browser check on :5515
- [ ] Regression gate: `python -m pytest tests/test_osc_dash_integration.py tests/test_param_registry.py -q`
