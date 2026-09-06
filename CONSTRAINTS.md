# Quality Guardrails & Constraints (`CONSTRAINTS.md`)

## 1. Test & Regression Bar (Non-Negotiable)
- **Zero Regression**: All 242 existing unit and integration tests must remain 100% green at all times (`python -m pytest -q`).
- **Comprehensive Coverage**: New tests must be added in `tests/test_orders_trades_table.py` covering:
  - Live Market Matrix header link rendering with `target="_blank" rel="noopener"`.
  - Open Orders table market link rendering with fallback to series slug.
  - Positions table market link rendering with fallback to series slug.
  - Closed Trades table market link rendering with fallback to series slug.
  - Backwards compatibility when `market_slug` is omitted or empty.

## 2. Performance & Latency Thresholds
- **Zero Polling Overhead**: Data model additions (`market_slug`, `series_slug`) must add zero network calls or latency overhead to the 1s execution loop and SSE streaming.
- **Client Render Performance**: DOM link construction in JavaScript must use direct string concatenation or template literals without introducing external DOM libraries or re-renders.

## 3. Anti-Cheat Discipline
- **No Test Silencing**: No tests may be skipped, commented out, deleted, or weakened to make tests pass.
- **Strict Assertions**: Assertions must verify actual HTML attribute values (`href`, `target="_blank"`, `rel="noopener"`), not just generic substring presence.

## 4. Architectural Boundaries
- **No External Libraries**: No new dependencies in `requirements.txt`.
- **Trading Engine Integrity**: No changes to quoter pricing, order placement, order cancellation, or risk limits. Purely UI data propagation and presentation.
- **Security**: All external anchor tags must include `rel="noopener"` to prevent tab-nabbing vulnerabilities.
