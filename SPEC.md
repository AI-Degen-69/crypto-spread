# SPEC — Issue #273: Tick Dataset Readiness

## Goal
Determine whether each collected tick file is compatible with the canonical replay engine and whether it has enough independent, correctly captured data to support a stated level of backtest claim.

## Required outcomes
1. A schema audit maps every replay-required field to its source and failure behavior.
2. A readiness policy distinguishes exploratory debugging from research-grade parameter comparison.
3. A deterministic readiness report evaluates each file using measured coverage, quality, and independence metrics.
4. The Tick Files page exposes the readiness result and the reasons behind it in plain language.

## Definitions
- **Tick snapshot:** one valid JSONL record written by the collector, representing a timestamped observation of one market window, including books and optional `tape_delta`.
- **Market window:** one unique `(series, cid)` interval. It is not a file and not merely a currency name.
- **Tape entry:** one item inside a snapshot's `tape_delta`; it is not the same as a tick snapshot.
- **Independent observation:** a distinct market window or explicitly separated temporal block; raw rows from the same window must not be counted as independent trades/windows.
- **Research claim:** the declared use of a file, such as mechanics exploration, parameter comparison, or out-of-sample validation. Readiness is claim-relative.

## Compatibility contract
The audit must verify, at minimum:
- `ts`, `start_ts`, `end_ts`, `duration`, `cid`, `series`, and stable window identity;
- both book legs with usable price levels and the recorded one-sided `mid` semantics;
- `tape_delta` shape and counts;
- fields required by fills, stops, dead-zone timing, settlement, and window grouping;
- no unsafe mixing of 5m and 15m durations without duration-aware handling.

Missing, malformed, stale, duplicated, non-monotonic, or estimated data must be visible in the report and must not silently count toward a stronger readiness level.

## Readiness levels
The implementation must define at least:
- **EXPLORATORY:** suitable for reproducing mechanics, debugging, and generating hypotheses; explicitly not sufficient to claim a stable trading edge.
- **RESEARCH_READY:** meets the documented coverage, quality, independence, uncertainty, and trial-count requirements for parameter comparison on the declared sample.

A stronger **OOS_READY** level may be added only if the implementation includes a genuine temporal holdout or walk-forward split.

The exact thresholds must be justified in the plan/report using confidence interval width or statistical power, observed variance/fill rate, number of tested configurations, and coverage across markets, durations, and time blocks. The system must state that no universal count of ticks or trades guarantees a valid edge.

## Acceptance criteria
- The report contains measured values and pass/fail reasons for snapshots, windows, tape entries, all supported markets/durations, time span/regimes, gaps, errors, malformed rows, independence, and OOS separation.
- The September 18 file is classified explicitly with reasons, not just a raw count.
- Existing PASS/WARN/FAIL integrity output remains available and is not overwritten by readiness classification.
- Readiness is deterministic for the same file fingerprint and configuration.
- The API/CLI response is machine-readable and the UI explanation is human-readable.

## Out of scope
Collector protocol changes, replay/fill/exit math changes, acquiring additional historical data, and claims about live profitability.
