# CONSTRAINTS.md — Issue #216: Dashboard resting-price fallback reads up_mid/down_mid

Binding while working on Issue #216. These are gates, not suggestions: a violation blocks the PR.

## 1. Zero regressions

- Targeted test suite must pass:
  - `python -m pytest tests/test_osc_dash_integration.py -q`
- NEVER run the full test suite locally (`python -m pytest -q` is strictly forbidden per repo AGENTS.md; CI gates full suite).
- All existing dashboard and cockpit integration tests must remain green.

## 2. Anti-cheat

- No `@pytest.mark.skip`, `xfail`, deleted assertions, or loosened tolerances.
- No `# noqa` / `# type: ignore` added to mask bugs.
- Do not keep dead code or dead ternary branches disguised as fallbacks.

## 3. Scope discipline

- Files touched:
  - `server/osc_dash.py` (client-side template script: shared resting/leg price helper and 6 call sites)
  - `tests/test_osc_dash_integration.py` (integration tests asserting no phantom keys and correct offset-aware fallback)
  - Station II planning files: `CONSTRAINTS.md`, `SPEC.md`, `tasks/plan.md`, `tasks/todo.md`
- Out of scope:
  - Modifying live execution logic or backend order placement (`strategy/live_trader.py`)
  - Premature changes to other open issues (#207, #208, #209, #210, #211, #212, #213, #214)
  - No new external dependencies

## 4. UI & Mathematical Correctness

- Dead `m.up_mid` and `m.down_mid` branches must be deleted across all 6 sites.
- Single shared helper must compute fallback quote prices from `m.mid` (or 0.50 anchor) and live `offset` (from `st.params.offset` if present, else 0.02).
- Price calculations must clamp to `>= 0.01` and round to 2 decimal places (`.toFixed(2)`).
- A market with null resting prices and non-default offset (e.g. 0.03) must never display `0.48`.
