# TODO — Issue #208: measure the shipped entry-gate defaults (quote_range + dead zone)

- [x] TASK-1 [Code/Logic]: Dead-zone 1D sensitivity axes (pct + sec) in
  `scripts/sweep_backtest.py` under `--include-structural`, with `dead_zone` filter key;
  unit tests in `tests/test_sweep_backtest.py`.
- [x] TASK-2 [Research/Logic]: Verify datasets (`scripts/verify_tick_data.py`), then run
  `--preset sensitivity --include-structural --only quote_range|dead_zone` on every
  `run/ticks/*.jsonl`; record tables in `docs/issue-208-entry-gates-measurement.md`.
- [x] TASK-3 [Research/Logic]: Apply the pre-registered reading rule; verdict per knob
  (quote_range bounds, dead-zone size, pct vs sec); adjustment candidates flagged for operator.
- [x] TASK-4 [Docs]: Verdict tables + one-sentence verdicts posted as gh comment on #208
  (2026-09-17); issue left open pending the operator's decision on the two adjustment
  candidates (quote_range (0.30,0.70); dead zone 0.30 pct) — closing would misrepresent an
  undecided outcome. pct-vs-sec resolved: keep pct.
- [ ] TASK-5 [QA/Tests]: `python -m pytest tests/test_sweep_backtest.py tests/test_backtest_engine.py tests/test_book_math.py -q`
  passes; CI remains the merge gate.
