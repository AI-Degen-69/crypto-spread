# Todo — Issue #93

- [x] T1: Failing test first (entry+advance+stop+exit+cancelled → `reset_pnl()` → orders empty) — red confirmed
- [x] T2: `reset_pnl` cancel-and-clear core + `_orders_cache_ts=0.0` + FILLED-flag fix
- [x] T3: Live refuse-while-hot + cancel-when-stopped (mocked CLOB, paper = no CLOB)
- [x] T4: `POST /api/live/control` refusal → 409 + Stop-first message
- [x] T5: Existing `reset_pnl` tests untouched + full `python -m pytest -q` green (301 passed)
- [ ] Ship: branch, commit, PR with CodeRabbit standards
