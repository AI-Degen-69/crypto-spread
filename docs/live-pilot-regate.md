# Live micro-pilot re-gate — pre-registered reading rule (issue #465)

Pre-registered **2026-10-06, before the run's artifacts exist**. The rule below was
committed in its own commit (see `git log` — this file's commit predates every file
in the run directory it judges), so no number could influence its wording.

## Background

#143 closed the live micro-pilot gate citing two blockers: the paper 0.50 settle
distortion (fixed by #160) and unviable hold-to-settle without naked-leg protection
(measured by #223: `close`, decisively). #146 then indicted the paper simulator:
100% divergence — a `+$6.74` night reconstructs to roughly `−$19.50` at executable
marks, with no queue-ahead fill model and one pair per window. Both blockers are
resolved; a fresh paper night, replayed through `scripts/replay_shadow_check.py`,
re-opens the gate or keeps it shut.

## Definitions

All replay numbers come from the **verdict leg `gates_pc1.0`** of the run's
`replay_comparison/replay_totals.json`. Paper numbers come from the run's
`manifest.json` (`"final"` block).

- `replay_expectancy` = `legs["gates_pc1.0"].expectancy_usd_per_event`
- `n_events` = `legs["gates_pc1.0"].n_events` (pairs + settles + exits)
- `paper_gross_usd` = `manifest.json "final"."total_pnl"`
- `replay_gross_usd` = `legs["gates_pc1.0"].replay_gross_usd`
- `optimism_pct` = `(paper_gross_usd - replay_gross_usd) / abs(paper_gross_usd) * 100`,
  i.e. how much of the paper night's claimed profit fails to survive the
  executable-marks reconstruction. Defined only when `paper_gross_usd > 0`.

## Decision rule — evaluated strictly in this order

1. **DEFER** if `n_events < 30`. The night is too thin to read; record the run
   length required for a readable night and stop — no gate change.
2. **NO-GO** if `replay_expectancy <= 0`. At executable marks the configuration
   does not pay; the live micro-pilot gate stays shut.
3. **NO-GO** if `paper_gross_usd <= 0`. A non-positive paper night has no claimed
   profit to reconstruct; there is nothing to re-gate on.
4. **GO** if `optimism_pct < 50`. The paper result survives reconstruction with
   less than half of its claimed profit lost to optimism; the live micro-pilot
   gate re-opens.
5. **NO-GO** otherwise (`replay_expectancy > 0` but `optimism_pct >= 50`): the
   paper simulator remains too optimistic to trust for sizing decisions.

## Calibration

#146 measured 100% divergence — paper marks were pure optimism. The 50% threshold
asks a concrete question: did the #160 settle fix plus the measured `close` policy
(#223) close more than half of that gap? The verdict must cite this rule verbatim
and report `optimism_pct` against #146's 100%.
