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

---

# Results & verdict (appended 2026-10-07 — the section above is unchanged)

## The run this rule judges

| | |
|---|---|
| Run | `runs/paper/2026-10-06_17-48_UTC+03-00/` (`--hours 2`, `mode=paper`) |
| Recorded configuration | `naked_leg_at_expiry="close"`, `offset 0.03`, `entry_delay_sec 60`, `max_pair_cost 0.98`, leg chase on, 5 shares |
| Window replayed (T0→T1) | 2026-10-06T14:48:37Z → 16:48:43Z, universe `xrp-up-or-down-15m`, `bnb-up-or-down-15m`, `eth-up-or-down-5m` |
| Tick coverage | `run/ticks/ticks_2026-10-06.jsonl` — verifier status **WARN** / `PARTIAL CAPTURE` / readiness `EXPLORATORY`, 0 corrupt lines, 58,258 valid ticks (`replay_comparison/verify_ticks.json`) |
| Replay artifact | `replay_comparison/replay_totals.json`, verdict leg `gates_pc1.0`, params hash `d62541ef190a` |
| Rule committed | `8376e59` @ 2026-10-06T17:39:00+03:00 — this commit predates the run's first artifact (`data/meta.json` @ 17:48:37+03:00) and every file it judges |

Windows: 36 included, 0 no-event, 3 excluded pre-coverage, 3 strict-late, 1 touch-insane.
Scoped snaps: 12,274. Legs (gates × pair cap) agreed exactly on the verdict leg:
`gates_pc0.98` and `gates_pc1.0` both −$35.92.

## Inputs the rule names

- `replay_expectancy` = `legs["gates_pc1.0"].expectancy_usd_per_event` = **−0.9978**
- `n_events` = **36** (27 pairs + 8 settles + 1 exit)
- `paper_gross_usd` = `manifest.json "final"."total_pnl"` = **−95.69**
- `replay_gross_usd` = **−35.92**
- `optimism_pct` — **undefined**: the rule defines it only when `paper_gross_usd > 0`, and this night's paper result is a loss.

## The rule, applied strictly in order

> 1. **DEFER** if `n_events < 30`. The night is too thin to read; record the run
>    length required for a readable night and stop — no gate change.
> 2. **NO-GO** if `replay_expectancy <= 0`. At executable marks the configuration
>    does not pay; the live micro-pilot gate stays shut.
> 3. **NO-GO** if `paper_gross_usd <= 0`. A non-positive paper night has no claimed
>    profit to reconstruct; there is nothing to re-gate on.
> 4. **GO** if `optimism_pct < 50`. The paper result survives reconstruction with
>    less than half of its claimed profit lost to optimism; the live micro-pilot
>    gate re-opens.
> 5. **NO-GO** otherwise (`replay_expectancy > 0` but `optimism_pct >= 50`): the
>    paper simulator remains too optimistic to trust for sizing decisions.

1. `n_events` = 36 ≥ 30 → not a defer. The night is readable.
2. `replay_expectancy` = −0.9978 ≤ 0 → **rule 2 fires.**
3. (Not reached.) `paper_gross_usd` = −95.69 ≤ 0 would have returned the same verdict.
4–5. (Not reached.) Optimism was never on the critical path.

## Verdict: **NO-GO**

**The live micro-pilot gate stays shut.** At executable marks the measured
configuration does not pay: −$0.9978 per event across 36 events, −$35.92 over the
night. That loss is concentrated in `eth-up-or-down-5m` (23 windows → −701.1
cents/share ≈ −$35.06); `xrp-up-or-down-15m` added −49.6 cents/share ≈ −$2.48 over
7 windows, and `bnb-up-or-down-15m` was the only series in the black (+32.3
cents/share ≈ +$1.62 over 6 windows). The paper night itself also lost money (−$95.69,
realized −$92.34, 258 trades, 31.4% win rate, 177 stops), so there is no claimed
profit for this rule's optimism test to reconstruct.

## Divergence against #146's 100%

`optimism_pct` cannot be computed here, and that is a finding rather than a gap:
the rule scopes optimism to a *profitable* paper night ("how much of the paper
night's claimed profit fails to survive the executable-marks reconstruction"), and
this night claimed none. What can be reported against #146 is the direction of the
residual: #146's paper book was **100% optimistic** — a claimed +$6.74 night became
−$19.50 at executable marks. This night's paper book was **pessimistic**: the paper
simulator marked itself down −$95.69 where the strict replay, on the same windows
and the same recorded configuration, reconstructs −$35.92. The paper side claimed
2.66× the loss the replay can produce (|−95.69| / |−35.92|), so the simulator still
misses by a wide margin, now on the wrong side of the sign. Whatever the #160
settle fix plus the measured `close` policy did, it did not move the paper
simulator toward agreement — it moved it to the far side of the replay.

## Scope of this verdict

This is a verdict on the configuration actually run (`close` at expiry, delay 60,
offset 0.03, cap 0.98) over 2 hours of one evening, at executable marks, on a
`WARN`/`EXPLORATORY` tick capture. Per this issue's own key rule, any parameter
change the numbers suggest is a new issue — nothing in the record above was
allowed to become one. The verdict is not a defer: the night is readable, so no
additional run length would move a rule-2 NO-GO.

## Method notes (what it took to produce the numbers)

- The driver could not scope a real run as shipped in T2: `derive_scope` looked for
  `started_utc` in `data/final.json`, but no writer has ever put it there (the pilot
  stamps it in `data/meta.json`; `final.json` carries only `stopped_utc`) — T2's tests
  passed on a fixture that invented the key. Fixed at T4 by reading the start stamp
  from `meta.json` with a fallback to `final.json`, plus a regression test built on
  the recorded layout. No number above existed before that fix.
- The replay only covers 36 of the night's windows: 3 open before tick coverage, 3
  have a first snap later than the 2s strict-start bound, 1 breaches the 0.50–1.50
  touch sanity bounds. 11 of the paper night's events fall before T0 and are outside
  the comparison — the same scope rule #146 used.
- Engine taker fees (45.31 cents) are tracked separately and excluded from the
  comparison: paper books pairs and settles gross.
