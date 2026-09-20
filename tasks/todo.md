# TODO — Issue #266: Sweep Visual sensitivity clarity

- [x] TASK-1 [API/Backend]: Replace `exit_5m` with shared `exit_stop` semantics for 5m and 15m defaults.
- [x] TASK-2 [API/Backend]: Add deterministic best aggregate and best-market metadata.
- [x] TASK-3 [Design/UI]: Humanize the four Sweep Visual axis labels and explain independent replay bars.
- [x] TASK-4 [Design/UI]: Render aggregate and ten market results as signed bar charts with a zero baseline.
- [x] TASK-5 [Design/UI]: Mark the best aggregate bar and best market card/bar, including empty-state behavior.
- [x] TASK-6 [QA/Regression]: Verify the real 2026-09-18 dataset in the browser and run the targeted test gate.

## Acceptance checklist
- [x] Only `queue`, `offset`, `exit_stop`, and `exit_rev` are supported.
- [x] The shared stop sweep changes both 5m and 15m default stop values per run.
- [x] The UI uses humanized parameter descriptions instead of raw machine names.
- [x] Every chart is a discrete bar chart; no line interpolation is used.
- [x] Best overall result and best market are visibly marked in metadata and chart/card styling.
- [x] Empty, sparse, negative, missing-market, and tied-result cases are covered.
- [x] `python -m pytest tests/test_osc_dash_integration.py tests/test_theme_tokens.py -q` passes: 152 tests.
- [x] Browser verification uses `run/ticks/ticks_2026-09-18.jsonl`: 30 windows, aggregate bar chart, ten market cards, best overall `queue=25 (+$0.07)`, best market `05m BTC at queue=10 (+$0.20)`, and no console errors.
