# CONSTRAINTS — Issue #148: Unify execution entrypoints

## Scope Lock
1. **Zero Ambiguity in Entrypoints**: Every execution script in the repository (`strategy/live_trader.py`, `scripts/shadow_ev_pilot.py`, `server/osc_dash.py`, `bot/paper_bot.py`, `ten-bankrolls/*`) must have a designated status (Canonical vs Deprecated/Research) and documented role in `AGENTS.md`.
2. **Deprecation Safeguards**: `bot/paper_bot.py` must be explicitly marked deprecated. Its `--live` flag (which contains an unmaintained `pass` stub) must be blocked or disarmed to prevent operator confusion.
3. **Preserve Valid Code & History**: Do not break imports or delete historical research directories unless completely dead; marking deprecated with clean pointers to canonical modules is preferred to maintain reproducibility.
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
