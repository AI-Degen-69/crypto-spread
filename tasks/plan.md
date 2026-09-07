# Plan — Issue #90: Filled column always shows 0

Files: `strategy/live_trader.py` (`get_open_orders_list`, ~:1357-1505),
`tests/test_orders_trades_table.py` (extend). No frontend change
(`server/osc_dash.py:2270,4303` already reads `o.filled`).

Contract (locked before logic): every dict from `get_open_orders_list()` carries
`"filled": float`. CLOB_API → `float(o.get("size_matched", 0.0) or 0.0)`;
all engine-tracked sources → `0.0`; cancelled passthrough → `setdefault("filled", 0.0)`.

## T1 — Failing tests first (TDD red)
- **Files:** `tests/test_orders_trades_table.py`
- **Do:** Extend `test_engine_order_resolution_and_cleanup`: give the mock CLOB order
  `"size_matched": 5` (+ a second CLOB order with no `size_matched` key) and assert
  `clob_order["filled"] == 5.0`, missing-key order `["filled"] == 0.0`, and
  `engine_order["filled"] == 0.0`. Confirm RED (KeyError / assertion failure).
- **Verify:** `python -m pytest tests/test_orders_trades_table.py::test_engine_order_resolution_and_cleanup -q` (must FAIL)

## T2 — CLOB `size_matched` → `filled` mapping
- **Files:** `strategy/live_trader.py` (:1357-1369 dict)
- **Do:** Add `"filled": float(o.get("size_matched", 0.0) or 0.0)` to the CLOB_API
  `orders.append({...})`. Wrap in try-tolerant coercion: unparseable string →
  `0.0`, never raise out of the poll loop.
- **Verify:** `python -m pytest tests/test_orders_trades_table.py::test_engine_order_resolution_and_cleanup -q` (CLOB asserts green)

## T3 — Engine-tracked dicts + cancelled passthrough
- **Files:** `strategy/live_trader.py` (:1378, :1393 ENGINE_ACTIVE; :1409 ENGINE_STOP;
  :1424, :1439 ENGINE_ADVANCE; :1464, :1481 PAPER_SIMULATION; :1496-1505 passthrough)
- **Do:** Add `"filled": 0.0` to each of the 7 engine-tracked dict literals.
  On the cancelled passthrough, apply `.setdefault("filled", 0.0)` to the copied
  dict before append so old retained rows also satisfy the contract.
- **Verify:** `python -m pytest tests/test_orders_trades_table.py -q` (full file green)

## T4 — Node render test: Filled cell shows "5"
- **Files:** `tests/test_orders_trades_table.py` (new Node-harness test beside
  `test_cockpit_dom_rendering_with_state`)
- **Do:** Feed `renderCockpitUI` a mock state whose open_orders include
  `{..., status: 'FILLED', filled: 5, ...}` and one `{..., status: 'OPEN'}` without
  `filled`; assert Orders body HTML contains a Filled cell with `>5<` and no
  regression on the OPEN row (`>0<`).
- **Verify:** `python -m pytest tests/test_orders_trades_table.py -q` (all green; skip if Node absent only with evidence)

## T5 — Full regression + self-audit
- **Files:** —
- **Do:** Run the whole suite; review the diff for contract compliance (no price/size/
  status change, additive key only), edge cases (`None`/missing/garbage
  `size_matched`), and lock discipline around the passthrough block.
- **Verify:** `python -m pytest -q` (all green)

## Ship
- Branch `fix/filled-column-size-matched`, conventional commit
  `fix(live-trader): map CLOB size_matched to filled in open orders`,
  PR titled `@coderabbitai` with `@coderabbitai summary` in the body.
  Unblocks #91 (then #97).
