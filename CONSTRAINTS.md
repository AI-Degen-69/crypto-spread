# Quality Guardrails & Constraints (`CONSTRAINTS.md`) — Issue #83

## 1. Test & Regression Bar (Non-Negotiable)
- **Zero Regression**: All existing unit and DOM tests must remain 100% green (`python -m pytest -q`).
- **Comprehensive Coverage**: New assertions in `tests/test_orders_trades_table.py` covering:
  - Merged `Time` cell with `rowspan` attribute matching `grp.rowspan` for paired orders.
  - Absence of redundant `Time` cell on follow-up legs (`idx > 0`).
  - Leading status accent border (`border-left: 2px solid <color>`) on the `Time` cell reflecting pair status (`Paired` = green, `Partial` = gold, `Cancelled` = dim).
  - Removal of `border-left: 2px solid` from the `Market` cell (`mktCell`).
  - Merged `Time` cell and proper row rendering for paired positions in `#cockpitPositionsBody`.

## 2. Performance & DOM Efficiency
- **Zero Runtime Overhead**: Grouping and string template interpolation occurs in O(N) where N is the number of active open orders/positions (typically < 30 rows).
- **Clean Semantic Markup**: Table layout strictly preserves valid HTML table structure with matching row/column spans across header and body.

## 3. Anti-Cheat Discipline
- **No Test Silencing**: No tests may be skipped, commented out, deleted, or assertions weakened to pass.
- **Strict Verification**: DOM assertions must check actual rendered HTML attributes (`rowspan`, `style*="border-left:2px solid"`) using Node.js execution harness.

## 4. Architectural Boundaries
- **Scope Isolation**: Strictly confined to dashboard rendering in `server/osc_dash.py` and test verification in `tests/test_orders_trades_table.py`.
- **Zero Backend Changes**: No modifications to `strategy/live_trader.py`, `strategy/streaming.py`, or data schemas.
- **No External Libraries**: Zero changes to `requirements.txt`.
