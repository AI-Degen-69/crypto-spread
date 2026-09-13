# SPEC.md — Issue #160: Fix paper sim settles mark naked legs to 0.50 (bids never maintained)

## 1. Goal
Eliminate the silent `0.50` settlement fallback in `strategy/live_trader.py` for naked legs at window expiry. In paper mode and live rollover, positions must mark to true executable book bids, binary complement quotes (`1.0 - opposite_ask`), or latched valid bids. Any failure to determine an executable mark must fail loud, ensuring P&L honesty and unblocking the live pilot path (#144, #143).

## 2. Background & Evidence
- Cross-check analysis in #146 (`runs/paper/2026-09-11_22-10_IDT/replay_comparison/comparison.md`) proved that all 10 in-scope shadow settles booked `(0.50 - fill) * 5` exactly.
- Replay-grade ticks showed real executable bids existed at expiry (0.99 / 0.01 / 0.001). Reconstructed at true marks, the 10 settles produced -$24.49 instead of the booked +$0.24, turning a claimed +$6.74 shadow night into an actual ~$19.5 loss.
- At rollover (`strategy/live_trader.py:4935-4939`), `mstate.up_bid or 0.50` and `mstate.down_bid or 0.50` silently fell back to 0.50 whenever `up_bid` / `down_bid` were None (which happens when books clear at the boundary or when boundary book polls overwrite state).
- `scripts/shadow_ev_pilot.py` omitted `up_bid` and `down_bid` from its snapshot exporter.

## 3. In Scope
1. **Bid Maintenance & Persistence in `strategy/live_trader.py`**:
   - Add `last_valid_up_bid` and `last_valid_down_bid` to `MarketLiveState`.
   - In `_update_market_strategy` and `on_book_update`: whenever a non-null bid is received, update `up_bid` / `down_bid` AND latch `last_valid_up_bid` / `last_valid_down_bid`.
   - Preserve latched bids across boundary ticks; never wipe them when an expiring window's book becomes temporarily empty.
2. **True Mark-to-Book Settlement Logic**:
   - In `_handle_window_rollover`:
     - For UP leg: mark bid resolution order:
       1. `mstate.up_bid` (if valid float > 0)
       2. Binary complement: `round(1.0 - mstate.down_ask, 4)` (if `mstate.down_ask` is valid float < 1.0)
       3. Latched bid: `mstate.last_valid_up_bid`
       4. Latched complement: `round(1.0 - mstate.last_valid_down_ask, 4)`
     - For DOWN leg: mark bid resolution order:
       1. `mstate.down_bid`
       2. Binary complement: `round(1.0 - mstate.up_ask, 4)`
       3. Latched bid: `mstate.last_valid_down_bid`
       4. Latched complement: `round(1.0 - mstate.last_valid_up_ask, 4)`
     - **Fail-Loud Guard**: If no market bid or complement can be resolved, raise `RuntimeError` or log a CRITICAL alert and fail the settle explicitly with a designated `MARK_UNAVAILABLE` error rather than silently defaulting to 0.50.
   - Record `settle_source` and `mark_bid` in `TradeEvent.notes` for complete auditability.
3. **Snapshot Telemetry in `scripts/shadow_ev_pilot.py`**:
   - Include `up_bid`, `down_bid`, `up_ask`, `down_ask` in the per-minute market snapshot dict.
4. **Entry-Fill Validation & Engine Structural Deltas**:
   - Record formal entry-fill documentation: the 45 pairs filled on touch/mid in paper mode; real queue toxicity measurement is deferred to #143 queue telemetry.
   - Document the structural delta between engine 1-pair/window cap vs paper re-quoting.
5. **Comprehensive Automated Tests**:
   - Add tests in `tests/test_live_trader.py`:
     - Direct book settle: assert settle uses actual `up_bid` / `down_bid`.
     - Complement settle: assert missing `up_bid` resolves via `1.0 - down_ask`.
     - Latched bid settle: assert boundary empty book uses latched bid.
     - Fail-loud assertion: assert bid-less fixture cannot silently produce 0.50.

## 4. Out of Scope
- Modifying backtest engine fill algorithms or re-entry logic.
- Live real-money trading or deploying order placement changes to mainnet CLOB.
- Sizing increases or changing the #143 pilot parameters.

## 5. Acceptance Criteria
- [ ] No paper settle can silently mark to 0.50 when books exist (test with bid-less fixture fails loud or uses last book).
- [ ] Re-running the scoped comparison shows settle distortion < 20% of scoped paper P&L.
- [ ] Entry-fill validation recorded and deferred to #143 with clear rationale.
- [ ] `python -m pytest tests/test_live_trader.py -q` green (123+ tests passing).
- [ ] Entire test suite `python -m pytest -q` passes without regressions.
