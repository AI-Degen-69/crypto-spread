# CONSTRAINTS.md — Issue #474 (per-issue working file, pruned at closeout)

## Scope fence
- IN: `.github/workflows/tests.yml` (new) + `AGENTS.md` Testing policy lines 35–38 (counts + gate claim). Nothing else.
- OUT (must not ride along): lint enforcement (`ruff` has 318 pre-existing findings), pre-commit hooks, branch-protection config, coverage thresholds, any change to app or test code.

## Zero regressions
- No `tests/` file is modified by this issue; targeted suites covering modified files = N/A (workflow YAML + docs only).
- Workflow YAML must parse (`python -c "import yaml"` or equivalent) and its `on:` triggers must cover `push` to base branch + `pull_request`.
- `AGENTS.md` must state the gate that actually exists after this change + correct counts: **1630 tests / 59 files** (verified 2026-10-08 via `python -m pytest --collect-only -q`).

## Performance
- CI job must finish in <15 min on `ubuntu-latest` (pip cache enabled).
- Local verification uses `--collect-only` + YAML parse + targeted fast checks; the full `pytest -q` proof runs on the CI runner (throwaway PR), not as a local gate — `AGENTS.md` forbids local full-suite runs.

## Anti-cheat
- No skipped/disabled tests, no deleted assertions, no new lint/type suppressions to get green.
- No new external dependencies without explicit operator approval (workflow installs only `requirements.txt` + `pytest`).
- If any check must be changed to match the requested behavior, explain why in `tasks/plan.md` and verify the behavior it guards.

## Dependencies
- Pinned runtime: Python 3.12 (matches local 3.12.10). Actions: `actions/checkout@v4`, `actions/setup-python@v5` (same floating-tag style as existing `summary.yml`).
