# TODO — Issue #226

- [x] T0 — correct the rule text: no taker fill on an entry (rules doc §3, ADR-0002, issue body)
- [ ] T1 — `resting_bid_filled` helper; delete `fill_model`; price stays ours, no entry fee
- [ ] T2 — live paper sim fills fully-through, still at our price
- [ ] T3 — dashboard + `/api/backtest`: the knob is gone
- [ ] T4 — CLI scripts; shadow-check legs collapse 8 → 4
- [ ] T5 — `ev_lab.py` / `sim2.py` / `selection_bias.py`: one path, `tapeq` deleted
- [ ] T6 — delete the 8 frozen sweep drivers; keep the .json tables; fix the docs that cite them
- [ ] T7 — `tests/test_fill_rule_parity.py`: same fills, same prices, both engines
- [ ] T8 — docs: AGENTS.md, operations.md, backtest-optimization-results.md
