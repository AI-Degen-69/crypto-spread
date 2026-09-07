# Todo — Issue #93

- [ ] T1: Failing test first (entry+advance+stop+exit+cancelled → `reset_pnl()` → orders empty) — red confirmed
- [ ] T2: `reset_pnl` cancel-and-clear core + `_orders_cache_ts=0.0` + FILLED-flag fix
- [ ] T3: Live refuse-while-hot + cancel-when-stopped (mocked CLOB, paper = no CLOB)
- [ ] T4: `POST /api/live/control` refusal → 409 + Stop-first message
- [ ] T5: Existing `reset_pnl` tests updated deliberately + full `python -m pytest -q` green
- [ ] Ship: branch, commit, PR with CodeRabbit standards
