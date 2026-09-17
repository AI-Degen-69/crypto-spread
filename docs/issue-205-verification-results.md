# Issue #205 — Verification Results (Station III build, 2026-09-17)

## What was run

`python -m research.sweeps.verify_205_fill_rate` — replays `run/ticks/ticks_2026-09-13.jsonl`
(550 windows) and the three other available datasets under gates-off `BacktestParams`
(`entry_delay_sec=0`, `quote_range=(0.0,1.0)`, `queue_gate=0`, `dead_zone_val=0`,
chase off). The retired `max_start_elapsed_pct=1.0` from #205's config maps to
`dead_zone_val=0.0` (no dead zone → enter everything, always).

## Headline numbers (unified rule, offset 0.02)

| dataset | windows | any_leg | any_% | pairs |
|---|---|---|---|---|
| ticks_2026-09-13 | 550 | 545 | 99.1% | 1925 |
| ticks_2026-09-14 | 1700 | 1697 | 99.8% | 7596 |
| ticks_2026-09-15 | 1695 | 1695 | 100.0% | 7697 |
| ticks_2026-09-16 | 10 | 0 | 0.0% | 0 |

Deterministic: identical counts on re-run. Offset sweep under `both`: `any_leg` pins at
545/550 for offsets 0.01 / 0.02 / 0.05 (only `pairs` responds: 2807 / 1925 / 867).

## Detector decomposition (same engine, same data, offset 0.02)

| detector | any_leg | any_% | pairs |
|---|---|---|---|
| tape only | 92/550 | 16.7% | 12 |
| ask-through only | 545/550 | 99.1% | 1804 |
| both (unified rule) | 545/550 | 99.1% | 1925 |

## Interpretation

1. **The 16x gap does not reproduce like-for-like.** #205's `tape` number (20/550) was taken
   on the pre-#226 engine; the same detector on today's engine fills 92/550 (16.7%) —
   ~4.6x more. The #226-era changes to entry anchoring, quote range, and pair-cost semantics
   materially changed how often a tape-only rule even gets a quotable rest.
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
