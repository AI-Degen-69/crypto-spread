# Issue #174 Phase 1: Socket Book Disagreement Measurement

## Summary

This document records the empirical measurement results of the `#174 Phase 1` shadow comparison (`book_shadow`) between the maintained WebSocket order book (`CLOBMarketWSClient`) and REST ground truth (`full_book()`), captured via `scripts/collect_ticks.py`.

## Verdict: NO-GO for Phase 2 Switch

The acceptance criterion defined in Issue #174 and Issue #350 for proceeding to Phase 2 (socket-authoritative order books) required disagreement to be **rare and bounded (< ~1%)**.

The observed divergence rate across a full capture session is **29.78% (~30%)** with a maximum gap of **32 cents** on contracts where the spread is 1 cent. Switching quoting or execution to the socket book in this state would risk pricing off stale or corrupted book states.

**Phase 2 is blocked until the root cause is diagnosed and resolved.**

---

## Global Aggregate Statistics

Source: `run/ticks/manifest.json` (`book_shadow` block).

| Metric | Value |
|---|---|
| Total comparisons | 39,414 |
| Divergent comparisons (> 0.001 tolerance) | 11,737 |
| **Divergence rate** | **29.78% (0.2978)** |
| Comparison tolerance | 0.001 ($0.001 / 0.1¢) |
| Mean absolute best bid delta | 0.005889 ($0.0059) |
| Mean absolute best ask delta | 0.005881 ($0.0059) |
| Mean absolute mid delta | 0.005281 ($0.0053) |
| **Max best bid gap (`max_bb`)** | **0.32 ($0.32 / 32¢)** |
| **Max best ask gap (`max_ba`)** | **0.32 ($0.32 / 32¢)** |

---

## Per-Series Breakdown

Full coverage across all 10 series:

| Series | Comparisons | Divergent | Divergence Rate |
|---|---|---|---|
| `sol-up-or-down-5m` | 3,910 | 1,489 | **38.08%** |
| `sol-up-or-down-15m` | 3,971 | 1,508 | **37.98%** |
| `bnb-up-or-down-5m` | 3,890 | 1,319 | **33.91%** |
| `xrp-up-or-down-5m` | 3,901 | 1,285 | **32.94%** |
| `xrp-up-or-down-15m` | 3,979 | 1,202 | **30.21%** |
| `bnb-up-or-down-15m` | 3,983 | 1,191 | **29.90%** |
| `eth-up-or-down-15m` | 3,986 | 1,159 | **29.08%** |
| `eth-up-or-down-5m` | 3,909 | 1,129 | **28.88%** |
| `btc-up-or-down-5m` | 3,904 | 925 | **23.70%** |
| `btc-up-or-down-15m` | 3,981 | 530 | **13.31%** |

---

## Analysis & Diagnostic Findings

1. **Systematic across all series**: Every series shows double-digit divergence rates, ranging from 13.3% on BTC 15m to 38.1% on SOL 5m. Faster-moving 5m windows consistently show higher divergence rates than 15m windows.
2. **Symmetric error distribution**: `mean_abs_bb_delta` (0.005889) and `mean_abs_ba_delta` (0.005881) are virtually identical. The book does not drift unidirectionally; it desynchronizes in time and state.
3. **Severe worst-case gap (32¢)**: A 32-cent gap on a contract priced between 0.01 and 0.99 indicates that the local book can retain stale or misapplied price levels far away from current market truth.
4. **Not caused by reconnect flapping**: The reconnect counter remained static while comparison counts grew by thousands, demonstrating that this is a steady-state defect in incremental delta handling or initial synchronization, not an artifact of connection loss.

---

## Next Steps

As outlined in Issue #350, aggregate statistics cannot isolate whether the defect stems from `price_change` delta application, `tick_size_change`, or post-reconnect resynchronization.

The immediate follow-up is:
- **Issue #359**: `diag(market-data): per-delta socket-book reconciliation replay — name the event type that breaks the #174 book`
- Replay captured tick streams event-by-event against REST snapshots to isolate the exact breaking event sequence.
