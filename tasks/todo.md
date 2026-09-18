# TODO — Issues #222 + #223: Dead-zone unit & unpaired-leg-at-expiry measurements

- [x] TASK-1 [Research/Core]: Pure measurement logic in `research/sweeps/dead_zone_lab.py` (fill timelines, dead-zone boundaries, buckets, settlement proxy, close-vs-hold arithmetic).
- [x] TASK-2 [QA/Tests]: `tests/test_dead_zone_lab.py` on synthetic windows — written first (TDD).
- [x] TASK-3 [Research/Measurement]: Run the lab → `research/sweeps/dead_zone_222.json` + `research/sweeps/naked_leg_223.json` (≤120s on the built cache).
- [x] TASK-4 [Docs]: `docs/dead-zone-naked-leg-measurements.md` + measured verdicts into `docs/engine-decision-rules.md` §8/§14.
