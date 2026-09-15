# The `phase*.json` result tables are stale and cannot be regenerated

Every `*.json` in this directory was produced **before** the fixes in issue
#182, by code that is no longer in this directory, against a tick dataset that
no longer exists. Read them as a historical record of how `patient_band_maker`
was arrived at — not as measurements.

## Why they are wrong

Two of the fixed defects change what the published numbers *mean*:

- **`roi_pct_per_window` is 100× too high** in every file. `summarize` divided
  a cents figure by a USD capital base. The corrected `phase6_tapeq_top` ROIs
  are all **negative**, where the committed file reports figures that read as
  acceptable.
- **Every `ci95_*` bound is unreproducible.** The bootstrap was seeded with
  `hash(name)`, which Python salts per process. Those bounds are the stated
  selection criterion — "95% bootstrap CI lower bound above 0" — so the
  criterion cannot be re-checked against these files.

Three more change which configurations were even simulated: the Phase 1
baseline was the engine default rather than the documented `ex_5m=0.08,
rev=0.015`; `fill_model="tapeq"` could not fill at all whenever `queue_gate >
0`; and every print was classified as a sell, so buys that lifted the ask could
fill a resting bid under tapeq.

## One defect that is NOT in these files — and where it does live (issue #191)

`backtest/engine.py` valued a naked leg carried to window close from the held
side's final `best_bid` alone and booked `0.00c` when that bid was absent. A
losing contract loses its bid side before expiry, so the omission only dropped
losses: over the 2026-09-13 → 2026-09-15 capture, 181 naked windows went
unmarked and **all 181 were losers**, a -395.56$ bias at size 5.

It matters where this did and did not apply:

- **The sweep tables here were never affected.** `ev_lab.fast_simulate` and
  `sim2.sim2` already flagged the case (`naked_none`) and recorded the
  redemption value, and `summarize` applies it by default
  (`settle_correct=True`). Every `phase*.json` number is settlement-corrected.
  They are stale for the reasons above, not for this one.
- **Any `scripts/backtest.py` output produced before issue #191 is invalid**
  for a hold-to-settle config (`ex=none`, or any run where a naked leg reaches
  window close). That CLI goes through `backtest/engine.py`, which had no
  correction at all. The symptom to recognise in an old report: a series with
  `0.0%` pair rate *and* `0.0%` exit rate that still shows positive P&L.

`research/sweeps/audit_settlement.py` measures both. Its
`bias (true - engine)` line is the raw historical finding and is kept
unchanged; the `bias (true - fixed)` line runs the same windows through the
engine's full settlement ladder and lands at **-2.88$** against the raw
**-395.56$**. The residual is the gap between a latched mark and true
redemption, plus the handful of windows where the audit's `s_mid` direction and
the resolver's book-mid direction disagree.

The same run reports which stage resolved each window:
`{'direct_bid': 170, 'latched_bid': 179, 'redeemed': 2}`. In this capture the
ladder almost always finds a stale bid to mark against; outright redemption is
the rare fallback, not the common path.

## Why they were not regenerated

Regenerating requires `run/sweeps/window_cache.pkl`, which is built from
`run/ticks/`. Both were deleted on 2026-09-13 during the cleanup that preceded
re-collecting on the hardened WebSocket tape — and the deletion was justified
on its own terms: the REST-era capture yields a 0.0% pair rate under
`fill_model=tape` **and** 0.1% under `book`, so it could not evaluate this
strategy at all.

Correcting the arithmetic in place was considered and rejected. Only
`roi_pct_per_window` is recoverable that way; the CI bounds need the bootstrap
re-run, and three of the defects change which windows filled. A file that was
half-corrected would be harder to reason about than one that is plainly marked
stale.

## What replaces them

The next collection run rebuilds the cache from WebSocket-captured ticks and
re-runs the phases with the fixed code. At that point:

1. Regenerate every `phase*.json` here.
2. Supersede `docs/ev-research-findings-2026-09-11.md` with a findings document
   written against the new dataset, rather than editing numbers in the old one —
   its dataset line (5 collection days, 2,430 windows) describes data that is
   gone.
3. Delete this file.

Until then, the one piece of evidence for the preset that does **not** depend on
this lab is `runs/paper/2026-09-11_22-10_IDT/` — an 11-hour paper run that
tested the configuration out-of-sample on its own capture (+$6.16, 97% win
rate, 53 pairs, 0 stops). One run on three series is a reason to collect more
data, not a proven edge.
