# EV research sweeps

> **The `phase*.json` tables here are a historical record, and the drivers that
> produced them are gone — see [`RESULTS-ARE-STALE.md`](RESULTS-ARE-STALE.md).**
> Issue #182 fixed five defects that change what those numbers mean, including a
> `roi_pct_per_window` that was 100× too high. Issue #226 then replaced the fill
> model they were swept over with a single hard-coded rule and deleted the eight
> phase drivers outright. The tables stay because the findings doc cites them;
> nothing regenerates them.

The lab that produced the `patient_band_maker` preset, and the results it
produced. Every number in
[`docs/ev-research-findings-2026-09-11.md`](../../docs/ev-research-findings-2026-09-11.md)
traces to a file here.

These lived in `run/sweeps/` until now. `run/` is gitignored, so a committed
document was citing evidence that any cleanup could delete — which is how
`HANDOFF.md` was lost on 2026-09-13.

## Drivers

| file | what it does |
|---|---|
| `ev_lab.py` | window-cache builder, parity-verified fast simulator, bootstrap statistics |
| `sim2.py` | research extensions on top of engine parity: leg-chase, entry-delay, entry-band |
| `audit_settlement.py` | the settlement-marking bias audit (see the note below) |
| `selection_bias.py` | the permutation null behind the "is this edge or luck" question |
| `overnight.py` | unattended cache rebuild + selection-bias null, on a timer |
| `show_base.py` | prints the baseline row from a result table |

The eight phase drivers (`phase1_1d.py` … `phase6_tapeq_top.py`,
`validate_top.py`, `run_exit_rev_110.py`) were deleted by issue #226. They swept
a `fill_model` that no longer exists, so they could not run against the current
engine, and `RESULTS-ARE-STALE.md` had already recorded that their output could
not be regenerated. Git history has them.

## Two things to know before trusting a number

**Parity.** `ev_lab.py` is a fast re-implementation of
`backtest/engine.py:_simulate_window`, not a wrapper around it. It was checked
bit-for-bit on 6,840 window-checks across 12 configs with 0 mismatches. If the
engine's fill or fee semantics change, that parity claim expires and has to be
re-established before any result here means anything. **It expired on
2026-09-16**: issue #226 replaced the fill rule in both. `ev_lab` and `sim2` now
call the engine's own `book_math.resting_bid_filled`, so the fill half of parity
is structural, but the bit-for-bit check has not been re-run.

**Settlement correction.** The stock engine books 0 PnL when a naked leg has no
final bid (empty book at settlement). `audit_settlement.py` found those 79
windows *all* lost, −39.3¢ true. Uncorrected, every hold-to-settle config looks
roughly 40% better than it is. Results in the findings doc are corrected; a
fresh sweep has to apply the same correction.

## Running a sweep against new ticks

The window cache is derived data and stays out of git; rebuild it first:

```bash
python research/sweeps/ev_lab.py cache --force   # writes run/sweeps/window_cache.pkl
python research/sweeps/selection_bias.py         # permutation null
```

A grid sweep against the current engine is `python -m scripts.sweep_backtest`,
which replays `backtest/engine.py` directly rather than a parallel
re-implementation of it.

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
