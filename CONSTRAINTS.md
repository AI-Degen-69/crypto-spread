# Quality Constraints — Issue #401

## 1. Zero Regressions & Targeted Test Gate
- Targeted tests covering modified modules must pass:
  `python -m pytest tests/test_osc_dash_integration.py -k sweep -q`
- No full test suite execution locally per project AGENTS.md policy (CI is the merge gate).

## 2. Scope Discipline
- Only modify Sweep Visual progress display and timer behavior in `server/osc_dash.py` and its tests in `tests/test_osc_dash_integration.py`.
- Do NOT modify SSE backend payloads (`rows_done`, `rows_total`, `n_windows`, etc.) or backtest engine simulation logic.
- Do NOT alter the final completion stats (Best overall, Best market, Took).

## 3. Anti-Cheat & Quality Bar
- No `@ts-ignore`, linter silencing, skipped or deleted tests.
- All new/modified UI strings and functions must be covered by assertions in `tests/test_osc_dash_integration.py`.
- Preserve clean cleanup of `setInterval` and start timestamps on stop, error, or completion.
