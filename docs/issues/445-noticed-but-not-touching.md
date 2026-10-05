# #445 — Noticed but not touching

| ID | Candidate | Discovering station | Evidence | Status | Disposition |
|----|-----------|--------------------|----------|--------|-------------|
| N1 | `@app.on_event("startup"/"shutdown")` deprecated in installed FastAPI (DeprecationWarning, use lifespan handlers) | iii-build-plan | `server/osc_dash.py:5379,5391` + pytest warnings | open | — |
| N2 | Backtest tab has no seconds control: a template carrying `entry_delay_sec` (e.g. overnight-majors preset, 60s) shows the notice and silently drops the delay on Load/Run (pre-existing #413 design, pinned by tests) | iiib-iterate-after-build | `server/osc_dash.py:9492-9493`, `tests/test_osc_dash_integration.py:10204-10212` | resolved (partial) | Loader now converts seconds to the closest percent of the shortest scoped timeframe (60s → 20% for the preset) and the notice states the per-timeframe approximation; exact seconds control remains a future feature |
