# TODO — Issue #205: Verify fill-rate gap against unified fill rule

- [x] TASK-1 [Research/Logic]: Gates-off replay over `run/ticks/ticks_2026-09-13.jsonl` under
  the unified fill rule; count fills like the #205 table (any-leg / up / down / both / pairs).
  → `research/sweeps/verify_205_fill_rate.py`; all 4 datasets run; deterministic.
- [x] TASK-2 [Research/Logic]: Interpret result against pre-registered expectation band
  (between tape 3.6% and cross 57%, near cross; monotone offset sweep preserved).
  → **Band failed**: unified 99.1%, tape-only 16.7%, ask-only 99.1%. See
  `docs/issue-205-verification-results.md`.
- [ ] TASK-3 [Docs]: Post comparison table + verdict as gh comment on #205, link ADR-0002,
  close the issue. → **Blocked on operator**: result outside expectation band; publishing
  "close as answered" would misrepresent it.
- [x] TASK-4 [QA/Tests]: `python -m pytest tests/test_backtest_engine.py tests/test_book_math.py -q`
  passes; no production code modified (CONSTRAINTS §1). → 171 passed in 0.82s.
