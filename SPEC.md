# SPEC — Issue #213: Quoting around current price across tradeable range & replacing entry_band veto

## Goal
Verify and codify that the trading engine quotes around the current market price (`mid`) across the tradeable range rather than enforcing a hard 0.50-anchored veto, and that out-of-range market states hold placement per-tick without permanently latching the window out.

## Background & Rationale
Historically, `strategy/live_trader.py` evaluated an `entry_band` gate computed as `abs(mid - 0.50) > entry_band`. If triggered, it set `entry_cancelled_timeout = True` and `band_skip = True`, latching the window shut for its entire duration. This forced quoting to exist only near 0.50 (e.g. 0.46–0.54), preventing valid market making in markets trading away from 0.50 (e.g. at 0.70).

Following the pricing fix in Issue #206 (quotes follow current mid rather than hardcoded 0.50), Issue #228 replaced `entry_band` and `adverse_open` with `quote_range = (0.10, 0.90)` (per `docs/engine-decision-rules.md` §6 and ADR-0003).

## Requirements & Acceptance Criteria
1. **Mid-Anchored Quoting:**
   - Both live and backtest engines price resting quotes based on the current two-sided mid (`mstate.mid - offset` and `(1 - mstate.mid) - offset`), not 0.50.
2. **De-latched Range Gate (`quote_range`):**
   - The 0.50-anchored `entry_band` veto is replaced with `quote_range` (default `(0.10, 0.90)`).
   - Evaluated on every tick against the two-sided mid.
   - When the mid is outside `quote_range`, quote placement holds for that tick only; no window-cancelling latch is set.
   - If the mid re-enters `quote_range` with sufficient time remaining, quoting resumes automatically.
3. **Engine Parity:**
   - `backtest/engine.py` and `strategy/live_trader.py` execute identical decision logic regarding `quote_range`.
   - Behavioral parity is validated via `tests/test_quote_range_parity.py` and `tests/test_engine_parity.py`.
4. **UI & API Synchronization:**
   - Backtest and Cockpit tabs in `server/osc_dash.py` expose `quote_lo` and `quote_hi` controls.
   - Legacy `entry_band` field is removed from active configuration payloads.

## Out of Scope
- Re-architecting quote range or changing the default `(0.10, 0.90)` (governed by ADR-0003 and #228).
- Re-entry drift-skip logic (addressed under #212).
- Modifying order execution transport.
