# Issue #174 Phase 1: Socket Book Disagreement Measurement

## Summary

This document records the empirical measurement results of the `#174 Phase 1` shadow comparison (`book_shadow`) between the maintained WebSocket order book (`CLOBMarketWSClient`) and REST ground truth (`full_book()`), captured via `scripts/collect_ticks.py`.

## Verdict: NO-GO for Phase 2 Switch

Issue #174 requires measured evidence before proceeding to Phase 2. Issue #350 treats common or bursty disagreement as a **NO-GO** condition and defers the numeric threshold to Phase 2 planning.

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
| `btc-up-or-down-5m` | 3,904 | 925 | **23.69%** |
| `btc-up-or-down-15m` | 3,981 | 530 | **13.31%** |

---

## Analysis & Diagnostic Findings

1. **Systematic across all series**: Every series shows double-digit divergence rates, ranging from 13.3% on BTC 15m to 38.1% on SOL 5m. Faster-moving 5m windows generally show higher divergence rates than 15m windows, with ETH as an exception.
2. **Similar mean absolute deltas**: `mean_abs_bb_delta` (0.005889) and `mean_abs_ba_delta` (0.005881) are virtually identical, indicating that the magnitude of bid and ask discrepancies is comparable across sides.
3. **Severe worst-case gap (32¢)**: A 32-cent gap on a contract priced between 0.01 and 0.99 indicates that the local book can retain stale or misapplied price levels far away from current market truth.
4. **No counted unclean reconnects observed**: The reconnect counter remained static while comparison counts grew by thousands. This excludes unclean reconnects recorded by `CLOBMarketWSClient`, but it does not rule out clean token-rotation reconnects or post-reconnect resynchronization as contributors.

---

## Next Steps

As outlined in Issue #350, aggregate statistics cannot isolate whether the defect stems from `price_change` delta application, `tick_size_change`, or post-reconnect resynchronization.

The immediate follow-up was:
- **Issue #359**: `diag(market-data): per-delta socket-book reconciliation replay — name the event type that breaks the #174 book`
- **Issue #362** (Commit `a704e4d`): Pruned ghost levels outside declared `best_bid` and `best_ask` in `apply_price_change`.

---

## 6. Post-#362 Empirical Live Re-measurement (2026-10-04)

Following the Issue #362 ghost-level pruning fix, a fresh live capture session was conducted across all 10 series with `scripts/collect_ticks.py` to evaluate whether divergence was driven down to the < ~1% GO-threshold.

### Measurement Summary

- **Session Timestamp**: 2026-10-04 22:08 UTC+3
- **Git Commit Baseline**: `a704e4d` (PR #363 / Issue #362)
- **Tooling**: `python -m scripts.collect_ticks --no-align`
- **Total Comparisons**: 1,940 (all 10 series)
- **Divergent Comparisons (> 0.001)**: 326
- **Live Divergence Rate**: **16.80% (0.1680)** (vs 29.78% pre-fix baseline)
- **Max Best Bid Gap (`max_bb`)**: **0.080 (8.0¢)** (vs 32.0¢ pre-fix)
- **Max Best Ask Gap (`max_ba`)**: **0.090 (9.0¢)** (vs 32.0¢ pre-fix)
- **Mean Absolute Deltas**:
  - `mean_abs_bb_delta`: 0.002004 (0.20¢ vs 0.59¢ pre-fix)
  - `mean_abs_ba_delta`: 0.002025 (0.20¢ vs 0.59¢ pre-fix)
  - `mean_abs_mid_delta`: 0.002000 (0.20¢ vs 0.53¢ pre-fix)

### Per-Series Breakdown (Post-#362)

| Series | Comparisons | Divergent | Divergence Rate |
|---|---|---|---|
| `btc-up-or-down-15m` | 194 | 10 | **5.15%** |
| `eth-up-or-down-5m` | 194 | 20 | **10.31%** |
| `sol-up-or-down-15m` | 194 | 22 | **11.34%** |
| `eth-up-or-down-15m` | 194 | 23 | **11.86%** |
| `bnb-up-or-down-15m` | 194 | 29 | **14.95%** |
| `btc-up-or-down-5m` | 194 | 30 | **15.46%** |
| `xrp-up-or-down-15m` | 194 | 33 | **17.01%** |
| `sol-up-or-down-5m` | 194 | 51 | **26.29%** |
| `bnb-up-or-down-5m` | 194 | 52 | **26.80%** |
| `xrp-up-or-down-5m` | 194 | 56 | **28.87%** |

### Post-#362 Verdict: NO-GO for Phase 2 Switch

While the fix in Issue #362 successfully pruned ghost levels—cutting the divergence rate from ~30% to **16.8%**, compressing mean delta by 66%, and reducing the worst-case gap from **32¢ to 9¢**—the measured divergence rate remains **~17x higher than the < ~1% threshold required to switch**.

A 9-cent maximum gap on contracts priced with 1-cent spreads confirms that socket-authoritative order books cannot safely replace REST as the primary pricing authority in live execution.

**Conclusion**: The Phase 2 switch to socket-authoritative order books remains **BLOCKED (NO-GO)**. REST must remain the primary source of truth, with WebSocket order books utilized only for top-of-book leading indicators and tape deltas.

