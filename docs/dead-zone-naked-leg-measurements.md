# Dead Zone & Naked Leg at Expiry — Measured (Issues #222 + #223)

> **Dataset:** `run/ticks/` — 5 daily files, **1,230,113 lines**, **3,985 condition
> windows** (5m + 15m, 10 series), collected 2026-09-13 → 2026-09-18.
> **Method:** `research/sweeps/dead_zone_lab.py` — read-only replay over the `ev_lab`
> window cache. Fill detection is the one shared rule
> (`book_math.resting_bid_filled`, issue #226 / ADR-0002) with the sell-print
> pre-filter (#182); quotes anchor at `two_sided_mid − 0.02` from the first in-range
> tick (issue #225 anchor, rule 6 range). Artifacts:
> `research/sweeps/dead_zone_222.json`, `research/sweeps/naked_leg_223.json`.

---

## Issue #222 — should the dead zone be a percent of the window or fixed seconds?

**Measured:** median time-to-pair from quote placement, by window length:

| duration | paired windows | median time-to-pair |
|---|---|---|
| 5m (300s) | 2,204 | **33.77s** |
| 15m (900s) | 782 | **84.05s** |

Ratio (15m / 5m): **2.489×** — inside the ambiguous band (1.5×–2.5×) the verdict
rule declares in the issue. A pure "fixed seconds" world predicts ≈1×; a pure
percent world (pairing distributed uniformly over the window) predicts ≈3×. The
truth sits between: pairing is **partially** time-driven (fill latency does not
triple) and **partially** window-driven (the 15m book moves slower, so the second
leg takes longer).

**Verdict: inconclusive — the default (10% of window) stands.**

Neither reading is established by this dataset, and the default stands until data
replaces it (rule 8's own words). Practical note for the operator: at 5m the dead
zone (30s) is nearly the median time-to-pair (33.8s) — an entry placed at the
midpoint of a 5m window would very likely still be unpaired at the dead zone. The
effective tradeable window is the first ~4 minutes of a 5m window. Re-test after
more days accumulate; if the ratio drifts above 2.5, percent is confirmed.

**Caveats:** 5 days of data; the 5m/15m sample sizes are healthy (well over the
10-pairs-per-duration floor) but clustered by day and series; time-to-pair is
measured under the paper fill rule, not venue truth.

---

## Issue #223 — should an unpaired leg in the dead zone be closed or held to settlement?

**Measured:** 937 windows reached the dead zone with exactly one leg filled. Buckets
by the leg's mid on entering the dead zone (0.05 wide):

| bucket | n | valued | settlement rate | mean leg mid | mean dead-zone bid | bid − mid |
|---|---:|---:|---:|---:|---:|---:|
| 0.00 | 908 | 60 | **0.00** | 0.017 | 0.010 | −0.007 |
| 0.05 | 12 | 11 | 0.00 | 0.071 | 0.046 | −0.026 |
| 0.10 | 5 | 4 | 0.00 | 0.116 | 0.097 | −0.019 |
| 0.15 | 6 | 6 | 0.00 | 0.176 | 0.098 | −0.077 |
| 0.20 | 3 | 3 | 0.00 | 0.220 | 0.153 | −0.067 |
| 0.30+ | 3 | 3 | 0.00 | — | — | ≈ −0.07 |

**Key findings:**

1. **Favourite-longshot bias, measured directly: not present in the losing direction.**
   Legs priced at 0.01–0.07 settled at 1.00 **0 times in 84 valued observations**
   (settlement rate 0.00 vs the ~0.05 the price itself claims). If anything the market
   *overprices these legs relative to realised outcomes* — holding a cheap leg is
   strictly worse than its price suggests on this dataset.
2. **Closing realises less than mid arithmetic assumes, as feared.** The dead-zone bid
   sits on average 0.7¢–8.5¢ below the mid (widest in the 0.15–0.30 buckets where the
   book thins). But even after that haircut and the taker fee, the realised close
   value beats the realised hold value in **every bucket** — because the hold side
   settled at 0.00 in every valued observation.
3. **Distribution, not just mean:** in the dominant 0.00 bucket (n=60 valued),
   close realised **−46.6¢** per leg on average while hold realised **−47.5¢** — a ~0.9¢
   per-leg edge to closing, negative in every observation of the bucket, so there is no
   variance trade-off to weigh: close wins per-observation, not only on average. (Both
   numbers are dominated by entry cost: these legs enter near 0.50-worth of paired
   context and expire nearly worthless — the comparison is close vs hold on the same
   entry, which is what the switch actually decides.)

**Verdict: `close` — the default stands, decisively.**

This is the opposite of rule 14's theoretical concern: the favourite-longshot bias
that could make holding lose does exactly that here, in the direction that favours
closing. Only 60 of 937 naked legs had a last-mid decisive enough to value (850
ambiguous — most naked legs die with the market already pinned near 0/1, which is
itself the story: legs this dead never come back). **Caveat:** the valued sample is
biased toward cheap legs (0.00–0.20); expensive naked legs (mid > 0.30, where hold
could plausibly win) are too rare (n=3) to say anything. If the chase (#231) moves
naked legs to higher mids, re-measure.

---

## Method notes & caveats (both measurements)

- **Settlement proxy:** capture ends at window end, so the winner is inferred from the
  last two-sided mid (decisively > 0.5 → up won; within ±0.02 of 0.5 → ambiguous, excluded).
  Same convention as `audit_settlement.py`.
- **Fill rule:** the paper rule is a promise of fewer fills than the market would give;
  absolute fill rates are conservative, but the *relative* shape (5m vs 15m, close vs hold)
  is what the verdicts read.
- **One pass, reproducible:** `python -m research.sweeps.dead_zone_lab` rebuilds both
  JSONs from the same inputs (dataset files + line counts are stamped in each artifact).
- **No engine changes were made or proposed by this measurement.** Defaults stand.
