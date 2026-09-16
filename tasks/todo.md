# TODO — Issue #206

- [ ] T1 — branch `fix/live-entry-anchor-mid-206`; red test: mid 0.60 must quote 0.57/0.37, capture the 0.47 failure
- [ ] T2 — replace the dead `locals()` fallback with `mstate.mid`; fix the lying comment
- [ ] T3 — pair-sum invariant, clamping, and the mid-0.50 no-change case
- [ ] T4 — `entry_delay_sec=60`: price comes from the placing tick, not the open
- [ ] T5 — comment the deliberate 0.50 in the T+1 pre-quote block at `:4230`
- [ ] T6 — full `python -m pytest -q`, commits, PR with red+green evidence
