# tasks/todo.md — Issue #214 (parity harness only)

- [ ] T1 `[Test/Harness]` `snaps_to_polls` — backtest snaps to live `poll_data`
- [ ] T2 `[Test/Harness]` `live_outcome` — drive the live path headlessly in paper mode
- [ ] T3 `[Test/Harness]` `backtest_outcome` + `assert_parity` with a readable diff
- [ ] T4 `[Test/Scenarios]` seed scenarios for #204, #206, #207, #209
- [ ] T5 `[Docs]` `AGENTS.md` names the rules document and the parity gate

Gate before every commit (CONSTRAINTS.md §1):

```
python -m pytest tests/test_engine_parity.py tests/test_backtest_engine.py tests/test_live_trader.py -q
```

The rule changes are **not** in this issue — they are #224 through #233, defined in
`docs/engine-decision-rules.md`. The task list that used to live here was split into those
issues on 2026-09-16.
