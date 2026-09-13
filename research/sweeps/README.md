# EV research sweeps

> **The `phase*.json` tables here are stale — see
> [`RESULTS-ARE-STALE.md`](RESULTS-ARE-STALE.md).** Issue #182 fixed five
> defects that change what those numbers mean, including a `roi_pct_per_window`
> that was 100× too high and bootstrap CI bounds that were never reproducible.
> The drivers below are fixed; the results predate the fixes and cannot be
> regenerated, because the dataset they were computed from has been deleted.

The lab that produced the `patient_band_maker` preset, and the results it
produced. Every number in
[`docs/ev-research-findings-2026-09-11.md`](../../docs/ev-research-findings-2026-09-11.md)
traces to a file here.

These lived in `run/sweeps/` until now. `run/` is gitignored, so a committed
document was citing evidence that any cleanup could delete — which is how
`HANDOFF.md` was lost on 2026-09-13. The drivers are also not historical: the
next collection run replays them against fresh ticks, so they are live tooling,
not an archive.

## Drivers

| file | what it does |
|---|---|
| `ev_lab.py` | window-cache builder, parity-verified fast simulator, bootstrap statistics |
| `sim2.py` | research extensions on top of engine parity: tapeq, leg-chase, entry-delay, entry-band |
| `audit_settlement.py` | the settlement-marking bias audit (see the note below) |
| `phase1_1d.py` … `phase6_tapeq_top.py` | the six sweep phases, in order |
| `validate_top.py` | per-day out-of-sample validation of the top 5 configs |
| `run_exit_rev_110.py` | the exit-reversal 1D sweep behind `docs/backtest-optimization-results.md` |
| `show_base.py` | prints the baseline row |

Each `phase*.py` writes the matching `phase*.json` next to it.

## Two things to know before trusting a number

**Parity.** `ev_lab.py` is a fast re-implementation of
`backtest/engine.py:_simulate_window`, not a wrapper around it. It was checked
bit-for-bit on 6,840 window-checks across 12 configs with 0 mismatches. If the
engine's fill or fee semantics change, that parity claim expires and has to be
re-established before any result here means anything.

**Settlement correction.** The stock engine books 0 PnL when a naked leg has no
final bid (empty book at settlement). `audit_settlement.py` found those 79
windows *all* lost, −39.3¢ true. Uncorrected, every hold-to-settle config looks
roughly 40% better than it is. Results in the findings doc are corrected; a
fresh sweep has to apply the same correction.

## Running a sweep against new ticks

The window cache is derived data and stays out of git; rebuild it first:

```bash
python research/sweeps/ev_lab.py cache --force   # writes run/sweeps/window_cache.pkl
python research/sweeps/phase5_band.py            # grid
python research/sweeps/validate_top.py           # trade-level detail
```

## What this research led to

The sweeps finished 2026-09-11 at 20:55 local. At 22:10 the same evening, an
11-hour paper run tested the configuration they proposed — out-of-sample, on its
own capture rather than on the 5 collection days swept here:

```
preset            patient_band_maker
offset 0.03 · entry_band 0.04 · entry_delay 60s · no stop · hold to settle
total_pnl +6.16 · win_rate 97.0% · 66 trades · 53 pairs merged · 0 stops
```

That record lives at `runs/paper/2026-09-11_22-10_IDT/`, with a `PROVENANCE.md`
pointing back here. `runs/` is gitignored by design (local evidence, not a
committed artifact), so the numbers above are restated here — otherwise the only
account of what this research produced would sit outside version control.

One 11-hour run on three series is a reason to collect more data, not a proven
edge.

## What is not here

`run/sweeps/window_cache.pkl` — a ~336MB compact cache rebuilt from whatever is
in `run/ticks/`. Derived, large, and tied to one dataset, so it stays gitignored
in `run/`.
