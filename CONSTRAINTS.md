# CONSTRAINTS.md — Issue #116 Quality & Execution Constraints

## 1. Zero Broken References & Zero Regressions
- All existing tests stay green: `python -m pytest -q` full suite (409+ tests).
- All skill junctions must resolve to valid paths on disk containing valid `SKILL.md` files.
- Zero dangling references to `issue-create` or `pr-babysitter` across the 7 skills and `docs/ecc-flow-guide.md`.

## 2. Integrity & Non-Destructive Operations
- Master source remains in `~/.agents/skills/`.
- Junctions in `~/.claude/skills/`, `~/.gemini/config/skills/`, and `AppData/Local/hermes/skills/` must be safely managed without deleting canonical source code.
- Idempotent execution for sync/deploy scripts.

## 3. Scope Boundaries
- Scope is strictly bounded to the 7 issue-workflow skills (`create-issue`, `plan-issue`, `build-issue`, `ship-issue`, `explain-issue`, `work-issue`, `review-babysitter`).
- No editing of `docs/issue-workflow.md` (formerly `agent-skills-guide.md`; renamed by Issue #117).
- No modifications to trading strategies or dashboard logic.
