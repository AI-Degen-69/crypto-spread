# Todo: Issue #146 — replay cross-check (shadow night vs official backtest)

- [ ] Task 0: Branch + baseline (`add/146-replay-cross-check`, engine test baseline)
- [ ] Task 1: Tick integrity gate (`verify_tick_data` on ticks_2026-09-12.jsonl)
- [ ] Task 2: Exact-config replay driver (direct `replay()`, mirror config, scoped range)
- [ ] Task 3: Scoped shadow baseline (57 in-coverage events, +$5.665 expected)
- [ ] Task 4: Comparison + bias verdict (20% rule: |Δ| > $1.133 → follow-up)
- [ ] Task 5: Publish (issue comment + follow-up if needed + engine test gate)
