# Issue Workflow — `crypto-spread`

How the issue-to-PR pipeline works in this repo: intake an idea as a GitHub
issue, pick it, run the stations, ship the PR, and babysit it to merge.

The skills are the global pipeline skills living under `~/.agents/skills/`,
deployed to every harness via junctions/symlinks (Hermes, Gemini/Antigravity;
Claude Code was retired 2026-09-22). Naming follows the Station Roman-numeral
convention. This doc is `crypto-spread`-specific: examples name this repo's
files and the base branch is `master`.

---

## 0. The pipeline at a glance

| Station | Skill | Trigger | One-line purpose |
|---|---|---|---|
| Entry | `pipeline-triage` | dirty repo / unclear next step | Read-only triage of git state, routes onward |
| X | `x-workflow-issue` | "work issue" / `/x-workflow-issue <n>` | Orchestrator: discovery (no args) or drives II–VII |
| I | `i-create-issue` | "issue create" | Raw idea → researched GitHub issue (`ready-for-agent`) |
| II | `ii-plan-issue` | "plan" / `/ii-plan-issue <n>` | Read issue, right-size, lock `CONSTRAINTS.md`, write `tasks/plan.md` |
| III | `iii-build-plan` | "build auto" / `/iii-build-plan auto` | Execute `tasks/plan.md` — TDD per task, atomic commits |
| IIIB | `iiib-iterate-after-build` | corrections after build | Human-feedback fix loop (no push) |
| IV | `iv-review-build-and-pr` | "ship" | Review, verification gate, push, open PR |
| V | `v-babysit-pr-and-merge` | "PR babysitter" | One CodeRabbit round, triage, squash merge |
| VI | `vi-prune-artifacts` | "prune" | Sweep closed-issue artifacts, preserve knowledge |
| VII | `vii-present-pr` | "explain" / `/vii-present-pr <n>` | Standalone ELI5 HTML showcase + verification guide |

### Helper skills (used by the stations, not stations themselves)

| Helper | Used by | Purpose |
|---|---|---|
| `context-engineering` | Station X (Step 1) | Lock session scope and rules before opening files. |
| `interview-me` | Station II, X (Step 2) | Extract exact requirements one question at a time when the ask is vague. |
| `spec-driven-development` | Station II | Build a clean capability map and specification. |
| `constraint-driven-development` | Station II | Lock non-negotiable test/coverage/lint bars in `CONSTRAINTS.md`. |
| `api-and-interface-design` | Station II | Lock type definitions and interface boundaries before logic. |
| `planning-and-task-breakdown` | Station II | Generate an ordered bite-sized task plan. |
| `test-driven-development` | Station III | Red → green → refactor per task. |
| `incremental-implementation` | Station III | Small isolated steps with passing tests each step. |
| `source-driven-development` | Station III | Ground library/framework calls in official docs. |
| `debugging-and-error-recovery` | Station III | Systematic root-cause investigation when tests fail. |
| `code-simplification` | Station III | Clean up complexity without altering behavior. |
| `code-review-and-quality` | Station IV | Audit the diff across correctness, readability, edge cases, tests. |
| `security-and-hardening` | Station IV | Audit new inputs, secrets, session boundaries, dependencies. |
| `performance-optimization` | Station IV | Audit loops, data access, queries, serialization. |
| `git-workflow-and-versioning` | Stations IV–V | Conventional commits, branch management, changelog. The rules it applies are the ones in `docs/git-workflow.md`; where the helper disagrees with that doc, **the doc wins** for this repo. |
| `shipping-and-launch` | Station X (Step 5) | Rollout readiness, telemetry, rollback safety. |
| `documentation-and-adrs` | Station X (Step 5) | Record architectural decisions / update docs. |

Robert's mental model of the whole loop, in his own phrasing:

```text
issue create → work issue → plan → build auto → ship → PR babysitter → prune
```

---

## 1. Intake — `i-create-issue` (Station I)

Raw idea in chat → researched, shaped, published GitHub issue with the
`ready-for-agent` label. Robert's phrasing: **"issue create."**

- One idea = one issue. Split only when the parts have distinct acceptance
  criteria; wire split siblings together with `Part of #<n>` headers and
  cross-reference comments.
- Research happens *before* drafting: real file paths and line numbers, or the
  issue is not `ready-for-agent`.
- Publishes immediately — no approval gate. Ambiguous intent becomes an
  **Open questions** section plus the `needs-answers` label (resolved from
  code at planning time), never a blocking interrogation.
- Intake template and conventions: `~/.agents/skills/i-create-issue/references/issue-tracker.md`.

## 2. Discover & pick — `x-workflow-issue` (Station X)

Invoked with **no arguments**, `x-workflow-issue` is the discovery step, not a
silent pickup: it lists every open issue (`gh issue list --state open --limit
50`), groups them, recommends an execution order, and calls out the single
next issue — then waits for you to choose. Pass an issue number only after
seeing the inventory. Robert's phrasing: **"work issue."**

As orchestrator it then walks the picked issue through the stations.

## 3. Stations II–III — plan and build

### Station II — `ii-plan-issue` (Define & Plan)

Reads the issue (`gh issue view <n> --comments`), claims it
(`gh issue edit <n> --add-assignee @me`), right-sizes the scope
(Trivial / Small / Standard / Large / X-Large), auto-detects the stack and
test framework, resolves `needs-answers` open questions from code, checks
whether `interview-me` clarification is actually needed (no synthetic
questions when the issue is clear), locks `CONSTRAINTS.md` via
`constraint-driven-development`, maps tasks to sub-issues with `blocked-by`
edges for Standard/Large work, and writes `tasks/plan.md`. Robert's
phrasing: **"plan"** or `/ii-plan-issue <n>`.

### Station III — `iii-build-plan` (Build & Verify)

Consumes `tasks/plan.md` (created by Station II — it refuses to run without
one). TDD per task (RED → GREEN → REGRESSION), official-doc grounding via
`source-driven-development`, cleanup via `code-simplification`, atomic git
commits per task, and a language-matched build-error resolver when compile
or test breaks appear. Two modes:

- `/iii-build-plan auto` — run all tasks in sequence; stops only on a stuck
  test or a dangerous, irreversible action. Robert's phrasing: **"build auto."**
- `/iii-build-plan` — execute the single next open task, commit, mark `[x]`
  in `tasks/plan.md`, then stop for inspection.

Corrections from the operator after a fresh build go to Station IIIB
(`iiib-iterate-after-build`), not back to III.

## 4. Station IV — `iv-review-build-and-pr` (Review & Ship)

**Proof-before-review gate first**: targeted tests for touched modules (this
repo's fast-iteration policy — never the full suite locally; CI runs it) or
the live browser check for UI changes. **No review runs on unproven code.**

Then the review axes: OCR delegation preview (deterministic file scope +
rules), dynamic reviewers (`code-review-and-quality`, `security-and-hardening`,
Python reviewer for this repo — asyncio, typing, PEP 8), the silent-failure
hunt, and the Spec axis (missing / added-not-asked / implemented-wrong vs the
issue and plan). Findings are merged into one deduplicated list, fixed with a
`fix(review):` commit, and the full post-review verification gate runs before
anything is pushed.

Then sync with `master`, push the branch, open the PR via `gh pr create` with
a Conventional-Commits title, `Closes #<n>`, and `@coderabbitai summary` —
and hand off to Station V. Robert's phrasing: **"ship."**

## 5. Station V — `v-babysit-pr-and-merge` (Babysitter)

Sits on the PR through exactly **one** CodeRabbit review round (single-round
cap to eliminate churn and conserve quota):

- **Countdown polling:** 5m → 4m → 3m → 2m → 1m checks.
- **Auto-trigger:** posts `@coderabbitai review` if auto-review is silent or
  skipped; a bounded failure state falls back to the local code reviewer
  rather than blindly merging.
- **Triage:** every comment gets ACCEPT or REJECT with an inline, technically
  argued reply; rejected threads are resolved with rationale.
- **Fix loop:** accepted fixes are applied locally, targeted tests re-run,
  batched into one commit and pushed once. No secondary review round.
- **Hard merge gate:** CI green (the full suite in GitHub Actions is the sole
  merge gate for this repo) + blockers resolved before squash merge; the
  squash title keeps Conventional Commits format but references the **PR**
  number. Escalates to Robert only on blocking issues.

Zero human in the loop otherwise. Robert's phrasing: **"PR babysitter."**

## 6. Station VI — `vi-prune-artifacts` (Post-merge sweep)

After Station V reports MERGED, run the `vi-prune-artifacts` skill: it
deletes per-issue artifacts whose work is CLOSED and unreferenced —
`docs/issues/<id>-*-*.html` for closed issues, orphaned scratch drafts —
while always keeping dated finding reports, knowledge docs, and anything
referenced from surviving documentation. Robert's phrasing: **"prune."**

## 7. Station VII — `vii-present-pr` (Visual Explain & Verify)

Optional closeout after the merge. Generates an interactive standalone HTML
artifact at `docs/issues/<id>-presentation-*.html`: dry facts (issue, PR
link, branch/commit), Before vs After architecture, a visual flow diagram,
and a project-tailored manual verification guide — all in Hebrew (ELI5, RTL),
with code/commands in LTR blocks. Robert's phrasing: **"explain."**

### Artifact homes — global rule

Three homes, three purposes — never mixed:

1. `runs/.../research-papers/` — **per-run** papers (one run's methodology, results, conclusions).
2. `docs/issues/<id>-*.html` — **per-issue** HTML artifacts from this workflow.
3. `.freebuff/`, `%TEMP%` — **scratch / transient preview only**. Never the canonical home of anything.

---

## Cheatsheet

Robert's phrasing first, canonical trigger second.

| Task | Skill | Robert's phrasing | Canonical trigger |
|---|---|---|---|
| Turn a raw idea into an issue | `i-create-issue` | "issue create" | `i-create-issue <idea>` |
| See open issues and pick one | `x-workflow-issue` | "work issue" | `/x-workflow-issue` (no args = discovery) |
| Run an issue end-to-end | `x-workflow-issue` | "work issue 42" | `/x-workflow-issue 42` |
| Plan the picked issue | `ii-plan-issue` | "plan" | `/ii-plan-issue <n>` |
| Implement the plan, all tasks | `iii-build-plan` | "build auto" | `/iii-build-plan auto` |
| Implement just the next task | `iii-build-plan` | "build" | `/iii-build-plan` |
| Review, push, open PR | `iv-review-build-and-pr` | "ship" | `/iv-review-build-and-pr` |
| Track CodeRabbit review and merge | `v-babysit-pr-and-merge` | "PR babysitter" | `v-babysit-pr-and-merge` |
| Sweep stale artifacts post-merge | `vi-prune-artifacts` | "prune" | `vi-prune-artifacts` |
| Explain what shipped + how to verify | `vii-present-pr` | "explain" | `/vii-present-pr <n>` |
| Clarify vague requirements | `interview-me` | "grill me" | `interview-me` |
| Audit the diff pre-commit | `code-review-and-quality` | "use code-review-and-quality" | `code-review-and-quality` |

---

## Related

- Git conventions (branching, commits, PRs, the CI merge gate) live in
  [`docs/git-workflow.md`](git-workflow.md) — the two docs are complementary:
  this one is the pipeline, that one is the repo's git law. Where the
  `git-workflow-and-versioning` helper disagrees with `git-workflow.md`,
  the doc wins.
- The old ECC `orch-pipeline` guide (`docs/ecc-flow-guide.md`) and the former
  7-skill `*-issue` family (`create-issue`, `plan-issue`, `build-plan`,
  `review-build-and-pr`, `explain-issue`, `workflow-issue`) were superseded by
  the global Station pipeline (2026-09-22) and removed.
