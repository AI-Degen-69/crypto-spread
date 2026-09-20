# Issue #280 Planning Checklist

- [x] TASK-1 [Docs] Write `docs/golden-tick-dataset.md` — definition, location (`run/ticks/golden/`), structure (verified day files + `golden_manifest.json`, no physical concat), quality bar on existing verify metrics with headroom
- [x] TASK-2 [Docs] Collection plan: watchdog-guarded 5 full UTC days, measured throughput, day-rotation handling, per-day verify gate, quarantine-with-reason, collector freeze rule
- [x] TASK-3 [Docs] Certification command sequence + re-certification policy (per-day verify → set verify → fresh `.idx` → replay-speed check; keyed to `READINESS_POLICY_VERSION`)
- [x] TASK-4 [Docs] Replay-speed budget grounded in `issue-221-gil-contention.json` (467MB → 34.07s) + `backtest/index.py` sidecar (~50ms)
- [x] TASK-5 [Docs] Register "the golden dataset" in `docs/glossary.md` (Data section) and one line in `AGENTS.md`
- [x] TASK-6 [QA] `python -m pytest tests/test_verify_tick_data.py tests/test_collect_ticks_smoke.py -q` green, zero regressions

## Targeted verification

`python -m pytest tests/test_verify_tick_data.py tests/test_collect_ticks_smoke.py -q`
