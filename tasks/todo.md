# Todo — Issue #292

Branch: `i292/golden-dataset-card-tick-files` · Plan: `tasks/plan.md`

- [x] T1 — `GET /api/ticks/golden` endpoint: absent/present/certified states, §1.1+§1.2 checks, policy currency, sidecar-based (no re-stream) [Backend/Logic]
- [x] T2 — Golden card UI at top of Tick Files: state badge, n/N checklist, stale-policy warning, explicit absent state [Frontend]
- [x] T3 — Wire `loadGoldenCard()` into the ticks-tab lifecycle [Backend/Logic]
- [x] T4 — Tests: absent-golden, stale-policy, certified, card HTML invariants [Tests]
- [x] Checkpoint: targeted suite green after T1 and after T3
