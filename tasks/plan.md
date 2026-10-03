# Plan — Issue #392: Align the PR-title rule between docs/git-workflow.md and .coderabbit.yaml

Branch: `i392/align-pr-title-rule` | Issue: #392

## Overview
The repository documents Conventional Commits PR titles (`feat(scope): summary (#N)`) in `docs/git-workflow.md`, but `.coderabbit.yaml` currently enforces an incompatible `[TAG]` vocabulary at `mode: error`, leading to merge-blocking false positives.
We will align all repository artifacts to use Conventional Commits referencing the issue number as the single canonical PR-title rule.

## CodeRabbit Intake Summary
- **Adopted:** Conventional Commits `<type>(<scope>): <imperative summary> (#<issue>)` format, updating `.coderabbit.yaml` (`auto_title_instructions` and `pre_merge_checks.title.requirements`), updating `docs/git-workflow.md` §3 with worked example and `.coderabbit.yaml` enforcement note, aligning `docs/issue-workflow.md`, and verifying YAML validity.
- **Rejected:** None.
- **Status:** Verified and ready.

## Tasks

- [x] **Task 1: Update `.coderabbit.yaml` to Conventional Commits PR-title format**
  - **Size:** XS
  - **Domain:** `[Config/Workflow]`
  - **Files:** `.coderabbit.yaml`
  - **Depends on:** None
  - **Details:** Rewrite `reviews.auto_title_instructions` and `reviews.pre_merge_checks.title.requirements` to enforce `<type>(<scope>): <imperative summary> (#<issue>)` where type is in `feat`, `fix`, `docs`, `test`, `chore`, `refactor`, `perf`, `ci`, `style`, `revert`. Instruct CodeRabbit not to fail valid technical identifiers, scopes, or standard abbreviations. Preserve `custom_checks` (`No Hardcoded Secrets`) in the same `pre_merge_checks` mapping. Update header comments.
  - **Verification:** `python -c "import yaml; data=yaml.safe_load(open('.coderabbit.yaml')); assert 'title' in data['reviews']['pre_merge_checks']; assert 'custom_checks' in data['reviews']['pre_merge_checks']"`

- [x] **Task 2: Align `docs/git-workflow.md` and `docs/issue-workflow.md`**
  - **Size:** XS
  - **Domain:** `[Docs/Workflow]`
  - **Files:** `docs/git-workflow.md`, `docs/issue-workflow.md`
  - **Depends on:** Task 1
  - **Details:** In `docs/git-workflow.md` §3, update the `PR title:` bullet to clearly describe the Conventional Commits format referencing the issue, note that `.coderabbit.yaml` enforces it at `mode: error`, and add a real worked example (`feat(backtest): a Stop control for backtest and sweep runs (#383)`). In `docs/issue-workflow.md`, ensure all PR title mentions refer to `docs/git-workflow.md` §3.
  - **Verification:** `grep -n "PR title" docs/git-workflow.md docs/issue-workflow.md`

- [x] **Task 3: Verification gate and check for obsolete `[TAG]` strings**
  - **Size:** XS
  - **Domain:** `[Verification]`
  - **Files:** `.coderabbit.yaml`, `docs/git-workflow.md`, `docs/issue-workflow.md`
  - **Depends on:** Task 2
  - **Details:** Run YAML validation, check that no stray `[TAG]` or `[ADD]` requirements remain in `.coderabbit.yaml`, and confirm all git status changes are clean and expected.
  - **Verification:** YAML load check and grep inspection.
