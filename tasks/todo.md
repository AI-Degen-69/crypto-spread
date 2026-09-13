# tasks/todo.md — Issue #167

Plan: `tasks/plan.md` · Spec: `SPEC.md` · Gates: `CONSTRAINTS.md`
Issue: https://github.com/AI-Degen-69/crypto-spread/issues/167

- [x] **T1** Cache the gamma market resolution per window — `scripts/collect_ticks.py`
      (saves 10 HTTP calls / ~390 ms per tick)
- [x] **T2** Skip the REST tape while the socket is authoritative — `scripts/collect_ticks.py`
      (saves up to ~970 ms per tick on a healthy socket)
- [ ] **T3** Fan the slate out over a bounded thread pool — `scripts/collect_ticks.py`,
      `strategy/markets.py` (collapses the sequential ~269 ms × 10)
- [ ] **T4** Publish the real cadence, retune `TICK_BUDGET_MS`, fix the docstring —
      `scripts/collect_ticks.py`
- [ ] **T5** Live `--once` run, `verify_tick_data` pass, re-run the scaling benchmark,
      full suite

Deferred by decision (SPEC §7): serving books from the #165 socket instead of REST
`full_book`. Own issue after #167 lands, if adopted.
