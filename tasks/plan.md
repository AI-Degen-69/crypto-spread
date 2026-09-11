# tasks/plan.md — Issue #116: Standardize naming and deploy 7-skill issue workflow family

Branch: `feat/issue-116-standardize-skill-family` (off `master`)
Constraints: `CONSTRAINTS.md`
Baseline: full suite green on master (414+ tests).

## Concise Spec (spec-driven-development, Standard tier)

**Goal:** Standardize names and directory structures across the 7-skill issue workflow family:
`create-issue`, `plan-issue`, `build-plan`, `review-build-and-pr`, `explain-issue`, `workflow-issue`, `babysit-pr-and-merge`. Deploy all 7 via directory junctions/symlinks to Claude, Gemini, and Hermes. Provide a robust deploy script and generalized `reference.md` in each skill folder.

---

## Tasks

### T1 — Rename directories and update SKILL.md headers
- Rename `C:\Users\Tiger\.agents\skills\issue-create` to `create-issue`.
- Rename `C:\Users\Tiger\.agents\skills\pr-babysitter` to `babysit-pr-and-merge`.
- Update `name:` in `create-issue/SKILL.md` to `create-issue`.
- Update `name:` in `babysit-pr-and-merge/SKILL.md` to `babysit-pr-and-merge`.

### T2 — Update cross-references in SKILL.md files & docs
- Audit and update all references to `issue-create` and `pr-babysitter` across:
  - `create-issue/SKILL.md`
  - `plan-issue/SKILL.md`
  - `build-plan/SKILL.md`
  - `review-build-and-pr/SKILL.md`
  - `explain-issue/SKILL.md`
  - `workflow-issue/SKILL.md`
  - `babysit-pr-and-merge/SKILL.md`
  - `docs/ecc-flow-guide.md:80`

### T3 — Global Deployment Script (`deploy_family_skills.py`)
- Create `scripts/deploy_family_skills.py` (and/or update `create-issue/scripts/sync_skill.py`) that:
  - Defines the 7 canonical skills and their target roots (`~/.claude/skills/`, `~/.gemini/config/skills/`, `AppData/Local/hermes/skills/`).
  - Cleans up legacy junctions/symlinks (`issue-create`, `pr-babysitter`).
  - Creates or verifies Directory Junctions for all 7 skills in all targets.
  - Verifies resolution of each target to live files.

### T4 — Generalized Reference Documentation
- Add a concise, non-repo-specific `reference.md` in each of the 7 skill directories covering:
  - Core intent & one-paragraph summary.
  - When to invoke.
  - Expected inputs and produced outputs.
  - Canonical trigger commands.
  - Scope boundaries.

### T5 — Verification & Gate Check
- Run deploy script and verify all junctions exist and resolve.
- Run `python -m pytest -q` to confirm zero regressions in repository test suite.
- Record deployment verification status in `tasks/plan.md` and walkthrough.
