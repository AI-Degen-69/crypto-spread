# CONSTRAINTS — Issue #148: Unify execution entrypoints

## Scope Lock
1. **Zero Ambiguity in Entrypoints**: Every execution script in the repository (`strategy/live_trader.py`, `scripts/shadow_ev_pilot.py`, `server/osc_dash.py`, `bot/paper_bot.py`, `ten-bankrolls/*`) must have a designated status (Canonical vs Removed) and documented role in `AGENTS.md`.
2. **Dead Entrypoint Removal**: Legacy `bot/paper_bot.py` and `ten-bankrolls/` are completely removed from the filesystem and git under the completely-dead exception, eliminating dead code and non-functional stubs.
3. **Preserve Valid Code & History**: Canonical execution scripts (`strategy/live_trader.py`, `scripts/shadow_ev_pilot.py`, `server/osc_dash.py`, `scripts/collect_ticks.py`, `scripts/backtest.py`) are strictly preserved and documented.
4. **No Quoting or Risk Logic Changes**: Quoting math, risk parameters, and order routing in `strategy/live_trader.py` remain untouched.
5. **No New Dependencies**: Stdlib only (`warnings`, `sys`, `argparse`).

## Quality Guardrails
6. **Targeted Test Gate**:
   - `python -m pytest tests/test_entrypoints.py -q` must pass (<1s).
   - `python -m pytest tests/test_docstrings.py -q` must pass with 100% coverage across all non-excluded modules.
   - `python -m pytest tests/test_live_trader.py -q` must pass with 0 regressions.
7. **Anti-Cheat**:
   - No disabling, skipping, or weakening tests.
   - Every modified Python file must maintain full PEP 257 docstring compliance.
