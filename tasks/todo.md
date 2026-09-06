# Todo — Issue #87

- [x] T1: State fields + idempotent `place_stop_order` helper (tests first)
- [x] T2: Place stop immediately on single-leg fill (live + paper)
- [x] T3: Pair completion cancels resting stop before merge
- [x] T4: Stop fill → cancel opposite entry + record STOP_EXIT
- [x] T5: Window rollover cancels stop and resets fields
- [x] T6: Stop visible in `get_open_orders_list()` / dashboard Orders table
- [x] T7: Full `python -m pytest -q` regression + diff self-audit (277 passed)
- [ ] Ship: branch, commit, PR with CodeRabbit standards
