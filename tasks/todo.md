# Todo — Issue #90

- [ ] T1: Failing backend test first (`size_matched=5` → `filled == 5.0`, missing → `0.0`, engine → `0.0`) — red confirmed
- [ ] T2: CLOB `size_matched` → `filled` mapping in `get_open_orders_list()` CLOB dict
- [ ] T3: `"filled": 0.0` on 7 engine-tracked dicts + `setdefault` on cancelled passthrough
- [ ] T4: Node render test — Filled cell shows `"5"` for the mocked FILLED order
- [ ] T5: Full `python -m pytest -q` regression + diff self-audit
- [ ] Ship: branch, commit, PR with CodeRabbit standards (unblocks #91 → #97)
