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

---

## 7. Root-Cause Verdict for the Residual Divergence (Issue #438, 2026-10-07)

Issue #438 asked for the **named cause** of the divergence that survived the #362 ghost-level
pruning, and for a minimal fixture reproducing it. This section is appended; every number above
is pre-existing evidence and none of it was rewritten.

### 7.1 The two rates are different instruments over different populations

This is the first thing the diagnosis establishes, because the residual was previously read as a
single quantity:

| Instrument | Temporal alignment of the compared pair | Population | Rate |
|---|---|---|---|
| Live collector — `shadow_compare_book` (`scripts/collect_ticks.py:556`, called `:948-952`) | **none**: the freshly fetched REST book is compared against the socket's cached book at that instant | 1,940 comparisons (#174 re-measure, 2026-10-04) | **16.80%** (326) |
| Offline replay — `_select_rest_book` (`scripts/replay_socket_reconciliation.py:341`) | nearest REST read within **±0.5 s** plus the in-frame concordance guard (`:373`) | 27,285 REST comparisons, capture `run/diag_ws/raw_session_2026-10-07_00-42-10.jsonl` | **0.17%** (46) |

The replay having its own numbers is not the reason the two differ, and the replay is **not**
expected to reproduce 16.8%: the collector's path has no timestamp comparison at all, so its
denominator includes pairs taken across an unbounded gap. The replay's 0.17% is what remains after
alignment is imposed.

### 7.2 The socket book itself is not corrupt

Across the 2026-10-07 capture (72,370 session lines: 71,690 WebSocket events, 680 REST snapshots,
all 10 series, 0 empty books, 0 fetch errors), the WebSocket client's reconstructed top-of-book
equalled the venue's **own declared quote in the same frame** on **every** one of the 70,869
`price_change` events — `in_frame_divergences == 0`. There is no observation anywhere in this
capture of the maintained book disagreeing with the venue's stated top of book. #362's pruning
holds.

Magnitude over **every** comparable pair (169,023), not only the divergent ones — note that a gap
of exactly one tick is not a divergence, since divergence is strictly greater than
`TOLERANCE = 0.001` (`scripts/replay_socket_reconciliation.py:40`):

| Bucket | Edge | Pairs |
|---|---|---|
| `exact` | gap = 0 | 168,975 |
| `sub_tick` | 0 < gap ≤ 1 tick | 2 |
| `1_3_ticks` | 1 tick < gap ≤ 3 ticks | **0** |
| `>3_ticks` | gap > 3 ticks | 46 |

The split is reported **per series** as well as globally, and it is flat: nine of the ten series are
exact agreement plus their own divergent pairs, and the only two pairs anywhere between exact
agreement and the widest bucket both sit in `bnb-up-or-down-15m`. Both of those are exactly **one
tick** — `abs(0.55 - 0.549)` = `0.0010000000000000009`, the ulp-level overshoot of a one-tick
subtraction — which the divergence rule already treats as agreement. The buckets are measured
against that same boundary: with an edge at plain `TICK`, those two pairs were reported as the
`1_3_ticks` drift population while the rule called them agreement. That inconsistency was caught in
PR #469's review, and the first version of this table was wrong about it.

The third assumption to fall: there is **no sub-tick drift population worth the name**. The issue's
proposed central claim — "a large population of sub-tick drift plus a rare, violent population" —
is falsified by its own instrument. The distribution is instead *exact agreement, or a whole-cent
jump*: 48 pairs out of 169,023 are not exactly equal (0.028%) — 46 divergent in the widest bucket
and 2 sitting exactly on the one-tick edge, with **nothing in between**.

### 7.3 All 46 counted divergences sit where the concordance guard is blind

`_select_rest_book` (`:341`) drops a comparison — returns `None`, counting nothing — when the
candidate REST book disagrees with the **declared quote carried by that same WebSocket frame**
(`:373`). Declared quotes are only extracted for `price_change` and `best_bid_ask` frames
(`:413-414`); for `book` and `last_trade_price` frames the guard has nothing to test and is
structurally a no-op.

Measured on the capture, over pairs that pass the ±0.5 s window:

| Frame type | In-window pairs | Declared quotes present | Guard rejected | Counted | Divergent |
|---|---:|---:|---:|---:|---:|
| `price_change` | 38,005 | **38,005 (100%)** | 10,878 | 27,127 | **0 (0.0%)** |
| `book` | 104 | 0 | 0 | 104 | **31 (29.8%)** |
| `last_trade_price` | 54 | 0 | 0 | 54 | **15 (27.8%)** |

Because `price_change` declares **both** sides on 100% of its in-window pairs, the zero on that row
is not a coincidence but a consequence: the guard passing means the REST read agrees with the
declared quotes, and the declared quotes are exactly what the reconstructed book holds (0 in-frame
divergences, §7.2) — so no pair on a fully declared frame *can* be counted as divergent. The
residual 46 divergences live entirely in the 158 comparisons where the guard is blind: **29.1% of
blind pairs versus 0.0% of gated pairs.**

### 7.4 Hypothesis 4 (benign sampling skew) — RULED IN, with a measured gradient

Every REST read in this capture precedes the WebSocket event it is compared against
(`rest_before_ws` 27,285 / `rest_after_ws` 0), so the compared pair is always two reads of one book
separated in time. Ordering alone cannot decide the hypothesis; the **age gap** can:

| REST reference age | Comparisons | Divergent | Rate |
|---|---:|---:|---:|
| ≤ 50 ms | 3,732 | 0 | 0.0% |
| ≤ 100 ms | 3,013 | 0 | 0.0% |
| ≤ 250 ms | 8,463 | 10 | 0.1% |
| ≤ 500 ms | 12,077 | 36 | 0.3% |

The gradient is present on the gated frame type too, where the disagreement is rejected rather
than counted, and there it is far better sampled — the guard's **rejection rate by reference age**
on `price_change`: 10.4% (≤50 ms) → 21.7% (≤100 ms) → 27.2% (≤250 ms) → 35.0% (≤500 ms). The
counted divergence rate on `book` follows the same monotone shape (0.0% → 0.0% → 20.7% → 41.7%,
n = 104).

The probability that two reads of the same book disagree in the top of book is therefore a
monotone function of the age gap between the reads, and is essentially **independent of which
frame type triggered the read**. Below ~100 ms it is zero in this capture.

### 7.5 Named cause

**Sampling skew is the leading explanation for the residual divergence; this capture does not prove
it is the only one.** Precisely:

1. The maintained WebSocket book agrees with the venue's own declared top of book on every frame
   that carries one (0 in-frame divergences over 70,869 events) — the socket book is internally
   consistent on every frame where it can be checked against the venue, and #362's fix is not
   leaking. That does **not** validate the 158 declaration-free comparisons, which are the only ones
   that can carry a REST divergence at all.
2. What the metric counts as "divergence" is a pair of reads of one book separated in time, and it
   is **only ever counted on frames that carry no declared quote to check the REST read against**
   (`book`: 29.8%, `last_trade_price`: 27.8%). On frames that do carry one, the same disagreement
   is rejected by the guard and the rate is 0.0% — at every reference age, including 11,990 pairs
   at ≤500 ms.
3. Within the blind population the disagreement rate rises with the reference's age (§7.4). That
   gradient is the discriminator: a book that is simply *wrong* on these frames would disagree with
   a fresh REST read too, so it would not be confined to the oldest buckets. It cannot, however,
   exclude a defect that systematically co-occurs with an ageing reference, and the young blind
   sample is small — 29 pairs below 100 ms (§7.7).

**The metric, not the socket, is what needs the change.** The Phase 2 NO-GO above stands on its own
measurements, and REST remaining the primary source of truth is unaffected by this verdict; but
"the socket book is residently wrong ~17% of the time" is not what any of these numbers say. The
hardening issue (#440) should be re-scoped against §7.3 rather than against the headline rate, and
at minimum its metric needs to report the gated and blind populations separately instead of one
global percentage.

### 7.6 Minimal reproducing fixture

The extracted fixture `tests/fixtures/socket_rest_reference_staleness.json` is the first
divergence of the capture (event #670, a `book` frame, max gap $0.01) with the REST read it was
compared against, its receive time, and the 301 ms age between them. It reproduces the mechanism
through `CLOBMarketWSClient` via the existing replay path; the fixture schema gained
`rest_reference` / `ws_rx` / `age_s`, added **only** for REST-sourced divergences so the #359
fixture's shape and reproduction are untouched. One consequence worth recording: before this, an
extracted REST divergence could not reproduce its own failure, because the fixture carried no
reference to compare against.

### 7.7 Limitations

- The collector's live path was **not re-run** (out of scope), so the 16.8% is explained here, not
  re-measured. The instrument argument in §7.1 is a code-level inference supported by the replay's
  measured age distribution.
- One 60 s capture over all 10 series. 46 divergences is a small population, and the per-series
  spread is flat (0.0–0.2%; max gap $0.05): the issue's "5m at 26–29% versus `btc-15m` at 5.2%"
  does **not** reproduce on this sample.
- The ±0.5 s window rejected 103,094 candidate pairs (72% of all candidate comparisons). Nothing
  here claims those would have agreed; they were never evaluated.
- The claim that the gate-rejected pairs and the counted divergences are one population rests on
  matching rates and matching age gradients, not on a per-pair identity.
- The 0-divergence cells below 100 ms **in the blind population** rest on 29 observations (15
  `book` + 14 `last_trade_price`), not on the 6,745 that hold below 100 ms across all REST
  comparisons — the gated majority of those can never be divergent by construction (§7.3).
- Magnitude is reported in units of `TOLERANCE` (0.001), while the venue's top-of-book grid is
  generally 1¢ (ten of those units) — which is why `1_3_ticks` holds 0 of 169,023 comparable pairs
  and cannot separate a moderate population from the violent tail. Recorded as N2 in
  `docs/issues/438-noticed-but-not-touching.md`; not changed here.

## 8. Pre-registered reading rule (Issue #440 — locked before any measurement)

The socket-authoritative switch is re-gated on the CORRECTED metric (§7 re-scoped in #440:
gated/blind populations separate, magnitude in venue ticks, reference age first-class,
exclusions counted). The rule below is written before the measurement run in §9 and may not
be edited after the numbers exist. It is stated on the gated population only — the blind
population can never clear a gate by construction (§7.3).

- **Material divergence:** a best-quote gap strictly greater than one venue tick
  (`> $0.001 + EPSILON`, i.e. buckets `1_3_ticks` and above). Exactly one tick is
  agreement: it cannot move a price we would send beyond grid rounding, and float
  subtraction parks exact-tick gaps on both sides of the boundary.
- **GO threshold:** gated material-divergence rate ≤ 1.0%.
- **Sample size:** `n_gated ≥ 5,000`. At 1% the standard error is ≈0.14%, so a single
  additional divergence moves the rate by 0.02% — the bar is a population statement,
  not one event.
- **Max tolerated single gap:** $0.02. Reasoning: twice the typical 1¢ grid move; anything
  larger on a ~$0.50 leg is book corruption, not skew, and a single such gap vetoes GO
  regardless of the rate.
- **Skew correction:** if the blind population repeats the §7.4 age gradient (young cells
  ≈0%, old cells >0%), blind divergences are read as staleness and stay out of the gate —
  that is the correction, applied by construction since the gate reads gated only.
- **Escape hatch:** any divergence on fully-gated pairs (fresh reference AND declared-quote
  check) is a socket defect, fails the gate, and gets its own issue — it is not averaged away.

Verdict is recorded in §9 either way: GO only if every bullet above holds on the run.

## 9. Measurement run against the §8 rule (Issue #440 — filled by the run, not edited after)

Run: corrected instrument (commit `d08d56b` + T1–T4 code) over
`run/diag_ws/raw_session_2026-10-07_00-42-10.jsonl`
(72,370 lines; 71,690 WS events; 680 REST snapshots; all 10 series).
Rule §8 was locked before this run (same commit); the run below is the first
measurement the corrected instrument ever produced.

- Gated: **0 material divergences / 27,127** → rate **0.0%** (bar ≤ 1.0%) ✓
- Sample: `n_gated = 27,127` (bar ≥ 5,000) ✓
- Max single gated gap: **$0.0000** (veto above $0.02) ✓
- Blind: 46/158 (29.1%) with the §7.4 age gradient intact (0 below 100 ms,
  rising to 0.3% at ≤500 ms) → read as staleness per the skew-correction
  bullet, kept out of the gate ✓
- Escape hatch: zero divergences on fully-gated pairs ✓
- Excluded, now counted instead of silent: 103,094 `outside_freshness_window`,
  10,878 `guard_reject`, 1,302 `no_snapshot`

**Verdict: GO — the gate clears on the corrected metric.**

Caveat, recorded so the verdict cannot be over-read: the §8 thresholds were set
with knowledge of §7's old-instrument measurements on this same capture (the
issue body quotes them), so this is a confirmation run on known data, not an
out-of-sample trial. Recommended before acting on it: one fresh-capture
confirmation run against the unchanged §8 rule. Flipping the Phase 2 switch
itself is out of scope here; the standing NO-GO is untouched by this verdict.

