# Issue #205 — Verification Results (Station III build, 2026-09-17)

## What was run

`python -m research.sweeps.verify_205_fill_rate` — measures `run/ticks/ticks_2026-09-13.jsonl`
(550 windows) and the three other available datasets through the sweep lab's own
`ev_lab.build_cache` + `sim2` pipeline (the cached-window research simulator that
implements the #226 unified fill rule), with the two fill detectors separated read-only
by wrapping `book_math.resting_bid_filled` — never editing it, and never touching
engine or lab code. Gates-off maps to: `entry_delay_sec=0` and `quote_range=(0,1)` as
`sim2` call arguments (where the lab's guarded-knob policy expects them),
`queue_gate=0` by default, no dead-zone hold and no chase (the `sim2` behaviour
mirroring the engine defaults). The retired `max_start_elapsed_pct=1.0` from #205's
config is subsumed by the dead-zone default (no hold → enter everything, always).

## Headline numbers (unified rule, offset 0.02)

| dataset | windows | any_leg | any_% | pairs |
|---|---|---|---|---|
| ticks_2026-09-13 | 550 | 545 | 99.1% | 71 |
| ticks_2026-09-14 | 1700 | ~1697 | ~99.8% | — |
| ticks_2026-09-15 | 1695 | ~1695 | ~100% | — |

Deterministic: identical counts on re-run. Offset sweep under `both` (09-13):
`any_leg` pins at 545/550 for offsets 0.01 / 0.02 / 0.05 (pairs respond:
107 / 71 / 50).

## Detector decomposition (same simulator, same data, offset 0.02)

| detector | any_leg | any_% | pairs |
|---|---|---|---|
| tape only | 68/550 | 12.4% | 1 |
| ask-through only | 545/550 | 99.1% | 71 |
| both (unified rule) | 545/550 | 99.1% | 71 |

## Interpretation

1. **The 16x gap does not reproduce like-for-like.** #205's `tape` number (20/550) was taken
   on the pre-#226 engine; the same detector on today's simulator fills 68/550 (12.4%) —
   ~3.4x more. The #226-era changes to entry anchoring, quote range, and pair-cost semantics
   materially changed how often a tape-only rule even gets a quotable rest.
   (Caveat: `sim2` also pre-filters prints to sells — a buys-only tape day would read
   lower here than the engine's unfiltered view. The direction of every conclusion below
   is unaffected: the book-side detector dominates.)
2. **The unified rule is book-side dominated on this dataset.** The ask-through detector
   alone recovers 99.1% — identical to the full rule. On 1-second snapshots with a 1¢-wide
   book (up 0.98/0.99, down 0.01/0.02), the anchor sits 2¢ outside the spread, and any
   fluctuation runs the ask through the resting price in tens of ticks. The #205-era
   `cross` fill rate of 57% never materialized here: `cross` was a *different engine*, not
   just a different detector.
3. **The pre-registered expectation band is failed.** The plan's band was "between 3.6% and
   57%, closer to cross". Measured: 99.1%. The band assumed #205's 57% anchor transferred to
   the current engine — it does not, and the decomposition shows why.
4. **The offset sweep fails its monotonicity check on `any_leg`** (pinned at ceiling for all
   three offsets) but holds it on `pairs` — the same shape #205's table showed for `cross`.

## Conclusion

The gap is closed, but not in the direction the plan expected: the verification shows the
unified rule is *more* optimistic than any of #205's configurations, not conservative. The
ask-through detector at 1-second sampling is effectively "fills whenever the 1¢ book wobbles
2¢", which no real maker queue would honor. That is a known and accepted simplification per
ADR-0002 (the alternative was understating fills), but it means #205's underlying concern —
is the fill model honest? — is **not fully answered by the ADR**: the unified rule trades an
understatement bias for a likely *overstatement* bias on this dataset.
