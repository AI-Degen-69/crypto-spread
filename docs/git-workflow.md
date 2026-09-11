# Git Workflow — `crypto-spread`

The conventions actually practiced in this repo. One canonical process doc for
how code moves from a working tree to `master`. (The agent-station pipeline
that *produces* the work is documented in `docs/issue-workflow.md`; the older
`ecc-flow-guide.md` pipeline was superseded by it and has been removed.)

---

## 1. Branching

- **Base branch: `master`.** All PRs target `master`.
- **Branch naming** — `<type>/<short-kebab-desc>` with an optional issue number:
  - `feat/<desc>` — new functionality (`feat/filled-to-positions`)
  - `docs/<desc>` — docs-only work (`docs/issue-workflow-handbook-117`)
  - `fix/<desc>` — bug fixes
  - Suffix with the issue number when the work tracks one
    (`feat/skill-family-renames-and-ci` for #129).
- **One concern per branch/PR.** Never mix unrelated work into a PR — if the
  tree has dirty files from another task, leave them unstaged and out of the
  commits (this recurs constantly; see §6).
- Branch off current `master`; rebase onto `master` if it moved while you
  worked.

## 2. Commits

- **Conventional Commits** format: `<type>(<scope>): <imperative summary>`
  - Types seen in history: `feat`, `fix`, `docs`, `test`, `chore`.
  - Scopes match the touched area: `strategy`, `dash`, `backtest`,
    `trading`, `skills`, `docs`.
  - Examples from history:
    - `feat(trading): dynamic symmetric quoting across both pages and backtest engine (#130)`
    - `fix(skills): deploy family skills as directory symlinks instead of junctions`
    - `docs(skills): rename workflow family skills to descriptive names`
- **Atomic commits** — each commit is one coherent change and leaves the
  *targeted* tests for touched code green (per `build-plan`'s slimmed
  regression step; the full suite is CI's job — see §4).
- Body optional; use it when the "why" isn't obvious from the summary.
- Never commit anything but your own work: stage files explicitly
  (`git add <paths>`), never `git add -A` on a shared tree.

## 3. Pull Requests

- Open the PR from the feature branch to `master` as soon as the build is
  reviewed (Station 3 / `review-build-and-pr` does this automatically).
- **PR body:** summary of the change, which issue it closes
  (`Closes #N`), and `@coderabbitai summary` to trigger the review
  immediately on open.
- **Review:** CodeRabbit reviews every PR. The PR author's agent triages
  comments (accept-and-fix, or reject with a reasoned reply) — this is the
  `babysit-pr-and-merge` skill's job, exactly one focused review round.
- **PR title:** Conventional Commits format referencing the *issue*,
  `feat(scope): summary (#N)` — set by `review-build-and-pr`.
- **Merge:** squash-merge; the squash commit title keeps the conventional
  format but references the *PR* number, `feat(scope): summary (#PR)` —
  matching `git log` history. Don't "fix" either number into the other.
- **Helper skill:** `workflow-issue` delegates commit/branch discipline to the
  `git-workflow-and-versioning` helper; the rules it applies are the ones in
  this doc. Where the generic helper disagrees with this doc, **this doc wins**
  for this repo:
  - Helper's "run full tests before every commit" → here: **targeted tests
    for touched code only**; the full suite is CI's job (§4).
  - Helper's `feature/<desc>` naming and `main` base → here: `feat/<desc>`
    (§1) and base branch **`master`**.
  - Helper's anti-squash stance → applies to *commit* discipline only; PRs
    here are still squash-merged (§3).

## 4. CI merge gate

- `.github/workflows/tests.yml` runs the full suite
  (`python -m pytest -q`) on every PR and on pushes to `master`/`main`.
  Python 3.12, deps from `requirements.txt` + `pytest` + `httpx2`.
- **CI is the full-regression gate.** Local runs during the station flow are
  targeted only (the test files for touched code); nobody runs the full suite
  locally — that's CI's job.
- **A PR does not merge until `gh pr checks` is green.** If CI fails on push,
  fix immediately and push again — do not leave a red PR.

## 5. What never gets committed

- `run/` — all captured/replay data (gitignored; regenerated).
- `CONSTRAINTS.md`, `SPEC.md`, `tasks/plan.md`, `tasks/todo.md` are
  per-issue working files: they may be committed *as part of that issue's
  work*, but stale content from a previous task must never ride along in an
  unrelated PR.
- Skill folders live outside the repo (`~/.agents/skills/`) — repo-side
  changes to them are limited to `scripts/deploy_family_skills.py`,
  `scripts/write_family_references.py`, and `docs/issue-workflow.md`.

## 6. Shared-tree discipline

Multiple agents and the user work in the same checkout. Therefore:

- Before any consequential git operation: `git branch --show-current` and
  `git status --short` — the branch may have been switched under you.
- Track exactly which hunks/files are yours; stage only those.
- If ownership of a dirty file is ambiguous, leave it uncommitted and say so.
