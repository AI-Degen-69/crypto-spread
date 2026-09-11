# SPEC.md — Issue #116: Standardize Issue Workflow Family & Global Symlink Deployment

## 1. Goal
Standardize naming, directories, and trigger commands across Robert's 7-skill issue workflow family:
1. `create-issue` (renamed from `issue-create`)
2. `plan-issue`
3. `build-issue`
4. `ship-issue`
5. `explain-issue`
6. `work-issue`
7. `review-babysitter` (renamed from `pr-babysitter`)

Ensure canonical directory sources in `~/.agents/skills/` are deployed via Directory Junctions / Symlinks across all agent harnesses (`~/.claude/skills/`, `~/.gemini/config/skills/`, `AppData/Local/hermes/skills/`), update all cross-references, create a family-level deploy script (`deploy_skills.py`), and add generalized reference documentation to each skill folder.

---

## 2. In Scope
1. **Renames under `C:\Users\Tiger\.agents\skills\`**:
   - `issue-create/` -> `create-issue/`
   - `pr-babysitter/` -> `review-babysitter/`
2. **SKILL.md `name:` field updates**:
   - `create-issue/SKILL.md` -> `name: create-issue`
   - `review-babysitter/SKILL.md` -> `name: review-babysitter`
3. **Cross-reference updates**:
   - Inside all 7 family `SKILL.md` files: replace mentions of `issue-create` with `create-issue`, and `pr-babysitter` with `review-babysitter`.
   - In `docs/ecc-flow-guide.md:80`: update `pr-babysitter / CI Gate` to `review-babysitter / CI Gate`.
4. **Global Deployment (Junctions / Symlinks)**:
   - For all 7 skills, ensure target folders in:
     - `~/.claude/skills/<skill-name>`
     - `~/.gemini/config/skills/<skill-name>`
     - `AppData/Local/hermes/skills/<skill-name>`
     point directly to `~/.agents/skills/<skill-name>`.
   - Remove stale links/folders (`issue-create`, `pr-babysitter`) in target roots.
5. **Deployment Script**:
   - Update / create `deploy_skills.py` (under `~/.agents/skills/create-issue/scripts/` or `scripts/deploy_family_skills.py`) to automate junction/symlink creation for all 7 skills across targets with idempotent execution.
6. **Generalized Reference Documentation**:
   - Add a concise `reference.md` to each of the 7 skill directories in generalized, harness-agnostic form (intent, when to invoke, inputs, outputs, triggers, boundaries).

---

## 3. Out of Scope
- Rewriting `docs/agent-skills-guide.md` (Issue #117).
- Modifying skill runtime logic or adding new skills beyond the 7.
- Modifying other unrelated skills in `~/.agents/skills/`.

---

## 4. Acceptance Criteria
- [ ] `create-issue` and `review-babysitter` directories exist in `~/.agents/skills/`; old names removed.
- [ ] `SKILL.md` `name:` headers match directory names.
- [ ] No dangling references to `issue-create` or `pr-babysitter` in `~/.agents/skills/` family skills or `docs/ecc-flow-guide.md`.
- [ ] All 7 skills resolve via directory junction/symlink in Claude, Gemini, and Hermes skill roots.
- [ ] Family deploy script successfully verifies and provisions all 7 junctions idempotently.
- [ ] Each of the 7 skills contains a `reference.md` in generalized format.
