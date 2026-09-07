# SPEC — Issue #90: Filled column always shows 0

## Goal
Orders with status `FILLED` show `0` in the dashboard **Filled** column because
`get_open_orders_list()` never supplies the `filled` key the frontend reads
(`server/osc_dash.py:2270`). Map the CLOB field `size_matched` through so the
column renders the real fill quantity.

## Background (current behavior)
- Frontend: `filledNum: o.filled != null ? Number(o.filled) : 0` (`osc_dash.py:2270`),
  rendered as `filledStr` (`osc_dash.py:4303`). Correct once the backend supplies the key.
- Backend `strategy/live_trader.py:get_open_orders_list()` builds order dicts with
  no `filled` field anywhere: CLOB_API dict (`:1357-1369`), ENGINE_ACTIVE up/down
  (`:1378`, `:1393`), ENGINE_STOP (`:1409`), ENGINE_ADVANCE up/down (`:1424`, `:1439`),
  PAPER_SIMULATION up/down (`:1464`, `:1481`), cancelled passthrough (`:1503` copy).

## New behavior
1. Every dict returned by `get_open_orders_list()` includes `"filled": float`.
2. CLOB_API orders: `"filled": float(o.get("size_matched", 0.0) or 0.0)` —
   covers missing key, `None`, and string values via `float(...)`.
3. Engine-tracked orders (ENGINE_ACTIVE / ENGINE_STOP / ENGINE_ADVANCE /
   PAPER_SIMULATION): `"filled": 0.0` — fill quantity unknown without a CLOB
   round-trip; key present so no silent missing-key path.
4. Retained cancelled passthrough (`c_ord.copy()`): `setdefault("filled", 0.0)` so
   the "every dict" contract holds for old retained rows too.
5. No frontend change — `osc_dash.py` already handles the key.

## Acceptance criteria (mirrors issue #90)
- [ ] `get_open_orders_list()` returns `"filled": float(o.get("size_matched", 0.0) or 0.0)`
      for every CLOB API order.
- [ ] Engine-tracked orders include `"filled": 0.0` (no missing key).
- [ ] Unit test: mock CLOB order with `size_matched=5` → rendered **Filled** cell shows `"5"`.
- [ ] `python -m pytest tests/test_orders_trades_table.py -q` green, no regressions.
- [ ] `python -m pytest -q` fully green.

## Out of scope
- UI layout changes to the orders table.
- Moving FILLED orders to the Positions tab (#91 — lands after this).
- Sorting Open Orders (#97 — lands after #90+#91).
- Historical trade replay or backtest changes.

## Edge cases
- `size_matched` missing / `None` / `""` → `0.0` (the `or 0.0` guard).
- `size_matched` as string (`"5"`) → `float()` coerces; garbage string → fall back
  to `0.0` rather than raising (mirror the defensive style of the price/size casts).
- Status `FILLED` but CLOB reports `size_matched=0` → renders `0`; backend reports
  what the venue says, no synthesis.
- Old retained cancelled rows without the key → `setdefault` covers them.
