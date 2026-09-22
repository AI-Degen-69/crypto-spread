# Todo — Issue #295

Branch: `i295/list-pristine-tick-files-in-the-dashboard` · Plan: `tasks/plan.md`

- [x] T1 — Shared allow-listed tick-file resolver in `server/osc_dash.py` [Backend/Logic]
- [x] T2 — Route `api_backtest` / `api_backtest_sweep` / `api_ticks_verify` through the resolver [Backend/Logic]
- [x] T3 — Subpath-aware verify-cache sidecars (`_verify_sidecar_path` / `_write_verify_sidecar` + callers) [Backend/Logic]
- [x] T4 — Manifest lists pristine files with `pristine/<name>` + `is_pristine`; frontend ids sanitized, delete omitted [Backend/Frontend]
- [x] T5 — Tests: sidecar helper for subpaths, manifest disambiguation, pristine ranking, backtest/sweep resolution + rejection [Tests]
- [x] Checkpoint: targeted suite green after T2 and after T4
