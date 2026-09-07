# Todo — Issue #90

- [x] T1: Failing backend test first (`size_matched=5` → `filled == 5.0`, missing → `0.0`, engine → `0.0`) — red confirmed
- [x] T2: CLOB `size_matched` → `filled` mapping in `get_open_orders_list()` CLOB dict
- [x] T3: `"filled": 0.0` on 7 engine-tracked dicts + `setdefault` on cancelled passthrough
- [x] T4: Node render test — Filled cell shows `"5"` for the mocked FILLED order
- [x] T5: Full `python -m pytest -q` regression + diff self-audit (300 passed)
- [ ] Ship: branch, commit, PR with CodeRabbit standards (unblocks #91 → #97)
