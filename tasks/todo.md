# Todo — Issue #383

- [x] T1 — RED backend tests: active-run record, confirmed kill/escalation, cancel endpoint
- [x] T1 — GREEN: `_ActiveBacktestRun` + `_publish_active_backtest_run` + `_terminate_backtest_pool_confirmed` + `POST /api/backtest/cancel` + all four handlers bound to `record.pool`
- [x] T2 — RED frontend tests: Stop button, `updateBtStopVisibility`, `markBacktestStopped`, `stopBacktestRun`, `_btStopRequested` busy-wait exit
- [x] T2 — GREEN: Stop control + idle reset + stopped notice wired into both run paths
- [x] T3 — Contract regression: `tests/test_osc_dash_integration.py` + `tests/test_theme_tokens.py` + `tests/test_backtest_engine.py` green
- [ ] T4 — Live proof in the browser (long sweep → Stop → idle → second Run, no 429)
- [ ] Station IV gate: reviewers + Spec axis → push branch + open PR
- [ ] Station V: CodeRabbit resolved, CI green, squash merge, fast-forward `master`
- [ ] Station VI: close #383, sweep stale #378 per-issue artefacts, clean-exit gate
