# Todo: Issue #146 — replay cross-check (shadow night vs official backtest)

- [x] Task 0: Branch + baseline (`add/146-replay-cross-check`, engine test baseline)
- [x] Task 1: Tick integrity gate (`verify_tick_data` on ticks_2026-09-12.jsonl)
- [x] Task 2: Exact-config replay driver (engine core, mirror config, scoped range)
- [x] Task 3: Scoped shadow baseline (55 in-coverage events, +$5.265 expected)
- [x] Task 4: Comparison + bias verdict (20% rule: |Δ| > $1.053 → follow-up)
- [x] Task 5: Publish (issue comment + follow-up #160 + engine test gate green)
