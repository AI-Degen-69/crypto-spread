# SPEC.md — Issue #456: Gate Order Prices Against quote_range

## 1. Objective & Scope
quote_range (default 0.10–0.90) must guard where orders land, not just the mid. A computed leg outside the range rejects the whole pair at latch time — no orders that tick, retry next tick. Applies identically to the live trading engine and the backtest engine (parity).

## 2. Acceptance Criteria
- A mid whose computed UP or DOWN leg falls outside quote_range latches nothing and places no orders that tick (pair rejected as a unit).
- Legs exactly at lo/hi quote normally (inclusive boundary, same as the mid gates).
- In-range mids latch and quote exactly as before (same prices, same timing).
- `python -m pytest tests/test_live_trader.py -q`, `tests/test_quote_range_parity.py -q`, `tests/test_backtest_engine.py -q` green with no regressions.

## 3. Edge Cases
- Clamped legs: the range check runs on the computed (0.01/0.99-clamped) values.
- One leg in, one leg out → whole pair rejected (never a lone leg).
- Dead zone / unpriceable book: existing latch preconditions unchanged, leg check added after them.
- Chase: untouched — with no latch there is nothing to chase; already-resting quotes stand per existing rules.

## 4. Out of Scope
Stop-price flooring, exit thresholds, fill rules, cancellation flow, dashboard rendering, preset/template values, the `_resolve_exit_bid` fallback defect (separate issue).
