# SPEC.md — Issue #81: Floating Side Toast Notifications

## Overview
Add a floating side toast notification stack (#toastContainer) to the Cockpit dashboard to provide real-time, non-intrusive feedback when critical trading milestones occur:
- **Order Filled**: Neutral styling (.toast-filled).
- **Position Merged**: Green styling (.toast-merged).
- **Stop-Loss Exit**: Red styling (.toast-stoploss).

## Objectives & Requirements
1. **DOM Container**: #toastContainer fixed at top-right (z-index: 9999) inside FULL_APP_HTML.
2. **Styling & Animations**:
   - Smooth slide-in and fade-out animations matching the dark cyber-trading theme (IBM Plex Mono, Space Grotesk).
   - Distinct color themes: Green (--up / #33c9b5) for merges, Red (--down / #f0684d) for stop-loss exits, Neutral (--tx / --panel2 / --line-hi) for order fills.
3. **Lifecycle & Dismissal**:
   - Auto-dismiss after 5 seconds (durationMs = 5000).
   - Manual dismiss button (×) for instant dismissal.
4. **State Reconciliation & Diffing**:
   - Compares consecutive state snapshots from both SSE (/api/live/stream) and REST polling (/api/live/state).
   - Initial boot/load seeds the cache (_seenTradeIds, _prevMarketFills) without triggering historical toasts.
   - Detects new PAIR_MERGE trades, STOP_EXIT_* trades, and market leg fill transitions (illed_up, illed_down).
5. **Testing**:
   - Verify HTML container and CSS classes via FastAPI TestClient.
   - Verify toast creation, variant styling, auto-dismiss, and initial boot suppression via Node.js DOM test harness.

## Out of Scope
- No changes to underlying trading execution in strategy/live_trader.py.
- No browser OS desktop push notifications (Notification.requestPermission()).
- No cross-reload persistent toast storage beyond existing trades/timeline.
