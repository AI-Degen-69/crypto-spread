# CONSTRAINTS.md — Issue #90 Quality & Architectural Constraints

## 1. Testing & Zero Regressions
- All existing tests must remain 100% passing: `python -m pytest -q` (293 tests at time of filing).
- New tests (TDD, red→green) must cover:
  - CLOB order with `size_matched=5` → `get_open_orders_list()` returns `filled == 5.0`.
  - CLOB order without `size_matched` / with `None` → `filled == 0.0`.
  - Engine-tracked orders (ENGINE_ACTIVE/STOP/ADVANCE/PAPER) include `filled == 0.0`.
  - Rendered **Filled** cell shows `"5"` (not `"0"`) for the mocked order (Node harness).
- Targeted gate per task: `python -m pytest tests/test_orders_trades_table.py -q`.

## 2. Anti-Cheating & Integrity
- Strictly no disabling, skipping, or weakening existing tests/assertions to get green.
- No masking of conversion failures: unparseable `size_matched` falls back to `0.0`
  explicitly, never silently swallowed elsewhere.
- No changes to order pricing (`price`), sizing (`size`), status mapping, or which
  orders are returned — this issue only ADDS the `filled` key.

## 3. Performance & Integration Guardrails
- No new dependencies in `requirements.txt` (stdlib only; pure dict-key addition).
- No frontend changes in `server/osc_dash.py` — it already reads `o.filled`.
- No backend schema change beyond the additive `filled: float` key; #91 consumes it.
- Hot path (`get_open_orders_list` per poll): O(1) per dict, no extra venue calls.
