# Issue Workflow — `crypto-spread`

How the issue-to-PR pipeline works in this repo: intake an idea as a GitHub
issue, pick it, run the 4 stations, ship the PR, and babysit it to merge.

The skills are installed skills living under `~/.agents/skills/` (multiple
authors, including Robert's own `*-issue` family and helper skills with
Addy Osmani / ECC lineage). All 7 family skills are **globally deployed** to
every agent and harness Robert's system holds — Claude Code (`~/.claude/skills/`),
Gemini (`~/.gemini/config/skills/`), and Hermes (`AppData/Local/hermes/skills/`)
— via junctions/symlinks back to the canonical originals under `~/.agents/skills/`,
per #116 (previously #115). `create-issue` additionally ships a file-integrity
sync script: `~/.agents/skills/create-issue/scripts/sync_skill.py`.

Naming follows the `*-issue` convention settled in #116. This doc is
`crypto-spread`-specific: examples name this repo's files and the base branch
is `master`.

---

## 0. The skills in play

Two kinds of skills appear in the pipeline. The **7-skill family** is the
pipeline itself. The **helpers** are not stations — they are skills the
stations delegate to at specific moments.

### The 7-skill family

| Skill | Trigger | One-line purpose | SKILL.md path | Deployed in |
|---|---|---|---|---|
| `create-issue` | "issue create" | Raw idea → researched, published GitHub issue labeled `ready-for-agent`. | `~/.agents/skills/create-issue/SKILL.md` | Claude Code, Gemini, Hermes (symlink) |
| `workflow-issue` | "workflow issue" | Orchestrator: lists open issues, picks one, runs the stations, babysits the PR. | `~/.agents/skills/workflow-issue/SKILL.md` | Claude Code, Gemini, Hermes (symlink) |
| `plan-issue` | "plan" / `/plan-issue <n>` | Station 1: read issue, right-size, detect stack, lock `CONSTRAINTS.md`, write `tasks/plan.md`. | `~/.agents/skills/plan-issue/SKILL.md` | Claude Code, Gemini, Hermes (symlink) |
| `build-plan` | "build auto" / `/build-plan auto` | Station 2: execute `tasks/plan.md` — TDD per task, atomic commits. | `~/.agents/skills/build-plan/SKILL.md` | Claude Code, Gemini, Hermes (symlink) |
| `review-build-and-pr` | "review build" / `/review-build-and-pr` | Station 3: 4-axis review, 100% test gate, push, open PR, hand off to babysitter. | `~/.agents/skills/review-build-and-pr/SKILL.md` | Claude Code, Gemini, Hermes (symlink) |
| `explain-issue` | "explain" / `/explain-issue <n>` | Station 4: HTML ELI5 artifact (Hebrew, RTL) + manual verification guide. | `~/.agents/skills/explain-issue/SKILL.md` | Claude Code, Gemini, Hermes (symlink) |
| `babysit-pr-and-merge` | "PR babysitter" | Sits on the PR through one CodeRabbit review round until mergeable. | `~/.agents/skills/babysit-pr-and-merge/SKILL.md` | Claude Code, Gemini, Hermes (symlink) |

### Helper skills (used by the stations, not stations themselves)

| Helper | Used by | Purpose |
|---|---|---|
| `context-engineering` | `workflow-issue` (Step 1) | Lock session scope and rules before opening files. |
| `interview-me` | `plan-issue`, `workflow-issue` (Step 2) | Extract exact requirements one question at a time when the ask is vague. |
| `spec-driven-development` | `plan-issue`, `workflow-issue` (Step 2) | Build a clean capability map and specification. |
| `constraint-driven-development` | `plan-issue`, `workflow-issue` (Step 2) | Lock non-negotiable test/coverage/lint bars in `CONSTRAINTS.md`. |
| `api-and-interface-design` | `workflow-issue` (Step 2) | Lock type definitions and interface boundaries before logic. |
| `planning-and-task-breakdown` | `workflow-issue` (Step 2) | Generate an ordered bite-sized task plan. |
| `test-driven-development` | `build-plan`, `workflow-issue` (Step 3) | Red → green → refactor per task. |
| `incremental-implementation` | `build-plan`, `workflow-issue` (Step 3) | Small isolated steps with passing tests each step. |
| `source-driven-development` | `build-plan`, `workflow-issue` (Step 3) | Ground library/framework calls in official docs. |
| `debugging-and-error-recovery` | `workflow-issue` (Step 3) | Systematic root-cause investigation when tests fail. |
| `code-simplification` | `build-plan`, `workflow-issue` (Step 3) | Clean up complexity without altering behavior. |
| `code-review-and-quality` | `review-build-and-pr`, `workflow-issue` (Step 4) | Audit the diff across correctness, readability, edge cases, tests. |
| `security-and-hardening` | `review-build-and-pr`, `workflow-issue` (Step 4) | Audit new inputs, secrets, session boundaries, dependencies. |
| `performance-optimization` | `review-build-and-pr`, `workflow-issue` (Step 4) | Audit loops, data access, queries, serialization. |
| `git-workflow-and-versioning` | `review-build-and-pr`, `workflow-issue` (Step 5) | Conventional commits, branch management, changelog. |
| `shipping-and-launch` | `workflow-issue` (Step 5) | Rollout readiness, telemetry, rollback safety. |
| `documentation-and-adrs` | `workflow-issue` (Step 5) | Record architectural decisions / update docs. |

Robert's mental model of the whole loop, in his own phrasing:

```text
issue create → work issue → build auto → ship → PR babysitter
```

Everything below explains what each step actually invokes and what the
stations are under the hood.

---

## 1. Intake — `create-issue`

Raw idea in chat → researched, shaped, published GitHub issue with the
`ready-for-agent` label. Robert's phrasing: **"issue create."**

- One idea = one issue. Split only when the parts have distinct acceptance
  criteria; wire split siblings together with `Part of #<n>` headers and
  cross-reference comments.
- Research happens *before* drafting: real file paths and line numbers, or the
  issue is not `ready-for-agent`.
- Publishes immediately — no approval gate. Adds `needs-triage` only when the
  idea is genuinely unshaped even after research.
- Intake template and conventions: `~/.agents/skills/create-issue/references/issue-tracker.md`.

## 2. Discover & pick — `workflow-issue`

Invoked with **no arguments**, `workflow-issue` is the discovery step, not a silent
pickup: it lists every open issue (`gh issue list --state open --limit 50`),
groups them, recommends an execution order, and calls out the single next
issue — then waits for you to choose. Pass an issue number only after seeing
the inventory. Robert's phrasing: **"work issue."**

As orchestrator it then walks the picked issue through the stations (§5).

## 3. The 4-station pipeline

Each station is its own invocable skill. `workflow-issue` delegates to them; you
can also invoke them directly.

### Station 1 — `plan-issue` (Define & Plan)

Reads the issue (`gh issue view <n> --comments`), claims it
(`gh issue edit <n> --add-assignee @me`), right-sizes the scope
(Trivial / Small / Standard / Large), auto-detects the stack and test
framework, checks whether `interview-me` clarification is actually needed
(no synthetic questions when the issue is clear), runs
`spec-driven-development`, locks `CONSTRAINTS.md` via
`constraint-driven-development`, maps interfaces, and writes
`tasks/plan.md`. Reports every skill it executed, in simple friendly Hebrew.
Robert's phrasing: **"plan"** or `/plan-issue <n>`.

### Station 2 — `build-plan` (Build & Verify)

Consumes `tasks/plan.md` (created by Station 1 — it refuses to run without
one). TDD per task (RED → GREEN → REGRESSION), official-doc grounding via
`source-driven-development`, cleanup via `code-simplification`, atomic git
commits per task, and a language-matched build-error resolver when compile
or test breaks appear. Two modes:

- `/build-plan auto` — run all tasks in sequence; stops only on a stuck test
  or a dangerous, irreversible action. Robert's phrasing: **"build auto."**
- `/build-plan` — execute the single next open task, commit, mark `[x]` in
  `tasks/plan.md`, then stop for inspection.

### Station 3 — `review-build-and-pr` (Review & Ship)

Full test-suite gate first — **100% pass, no merge (or push) on red**. Then a
4-axis parallel review: code quality (`code-review-and-quality`), security
(`security-and-hardening`), test engineering (`test-engineer`), and a
language specialist matched to the project (for this repo: the Python
reviewer — asyncio, typing, PEP 8). Nits are fixed immediately with a fix
commit. Then sync with `master`, push the branch, open the PR via
`gh pr create` with a Conventional-Commits title, `Closes #<n>`, and
`@coderabbitai summary` — and hand off to `babysit-pr-and-merge`. Robert's
phrasing: **"ship."**

### Station 4 — `explain-issue` (Visual Explain & Verify)

Optional closeout after Station 3. Generates an interactive standalone HTML
artifact at `docs/reports/issue_<id>_showcase.html`: dry facts (issue, PR
link, branch/commit), Before vs After architecture, a visual flow diagram,
and a project-tailored manual verification guide — all in Hebrew (ELI5,
RTL), with code/commands in LTR blocks. Opens it live in the browser
automatically. Robert's phrasing: **"explain."**

## 4. Babysitter — `babysit-pr-and-merge`

Sits on the PR through exactly **one** CodeRabbit review round (single-round
cap to eliminate churn and conserve quota):

- **Countdown polling:** 5m → 4m → 3m → 2m → 1m checks.
- **Auto-trigger:** posts `@coderabbitai review` if auto-review is silent or
  skipped; a bounded failure state falls back to the local code reviewer
  rather than blindly merging.
- **Triage:** every comment gets ACCEPT or REJECT with an inline, technically
  argued reply; rejected threads are resolved with rationale.
- **Fix loop:** accepted fixes are applied locally, tests are re-run
  (self-heals by reverting and rejecting its own bad fix if red), batched into
  one commit and pushed.
- **Hard merge gate:** 100% test pass + clean CI/status checks before squash
  merge; escalates to Robert only on blocking issues.

Zero human in the loop otherwise. Invoked with no PR number, it processes the
open-PR queue in priority order (stack dependencies first, then
ready-to-merge, then actionable feedback, pipelining waits). Robert's
phrasing: **"PR babysitter."**

## 5. The orchestrator — `workflow-issue`

`workflow-issue` is the end-to-end assembly of the stations: discovery →
assignment (claim + `context-engineering`) → planning (Station 1) → build
(Station 2) → self-audit (the review helpers) → ship (Station 3) → babysitter.
The station skills are the primitives; `workflow-issue` is the convenience path
when you want one command to walk an issue all the way through.

In practice Robert mixes the two: `work issue` for discovery, then direct
station invocations (`build auto`, `ship`, `PR babysitter`). That is fully
supported — the stations do not require the orchestrator.

---

## Cheatsheet

Robert's phrasing first, canonical trigger second.

| Task | Skill | Robert's phrasing | Canonical trigger |
|---|---|---|---|
| Turn a raw idea into an issue | `create-issue` | "issue create" | `create-issue <idea>` |
| See open issues and pick one | `workflow-issue` | "work issue" | `/workflow-issue` (no args = discovery) |
| Run an issue end-to-end | `workflow-issue` | "work issue 42" | `/workflow-issue 42` |
| Plan the picked issue | `plan-issue` | "plan" | `/plan-issue <n>` |
| Implement the plan, all tasks | `build-plan` | "build auto" | `/build-plan auto` |
| Implement just the next task | `build-plan` | "build" | `/build-plan` |
| Review, push, open PR, babysit | `review-build-and-pr` | "ship" | `/review-build-and-pr` |
| Explain what shipped + how to verify | `explain-issue` | "explain" | `/explain-issue <n>` |
| Track CodeRabbit review and merge | `babysit-pr-and-merge` | "PR babysitter" | `babysit-pr-and-merge` |
| Clarify vague requirements | `interview-me` | "grill me" | `interview-me` |
| Audit the diff pre-commit | `code-review-and-quality` | "use code-review-and-quality" | `code-review-and-quality` |

---

## Appendix: skill inventory (self-verifying)

Confirmed live on this machine (2026-09-11). Junctions verified via directory
listing of each agent's skills root; a future session can re-verify by
checking that each path below resolves.

| Skill | SKILL.md (canonical) | Deployed junctions |
|---|---|---|
| `create-issue` | `~/.agents/skills/create-issue/SKILL.md` | `~/.claude/skills/create-issue`, `~/.gemini/config/skills/create-issue`, `AppData/Local/hermes/skills/create-issue` |
| `plan-issue` | `~/.agents/skills/plan-issue/SKILL.md` | same three roots |
| `build-plan` | `~/.agents/skills/build-plan/SKILL.md` | same three roots |
| `review-build-and-pr` | `~/.agents/skills/review-build-and-pr/SKILL.md` | same three roots |
| `explain-issue` | `~/.agents/skills/explain-issue/SKILL.md` | same three roots |
| `workflow-issue` | `~/.agents/skills/workflow-issue/SKILL.md` | same three roots |
| `babysit-pr-and-merge` | `~/.agents/skills/babysit-pr-and-merge/SKILL.md` | same three roots |

Deployment mechanism: Windows junctions/symlinks back to `~/.agents/skills/`
per #116; `create-issue` file-integrity is additionally ensured by
`~/.agents/skills/create-issue/scripts/sync_skill.py`.

Legacy names still present as junctions in some roots (`issue-create`,
`pr-babysitter`) are deprecated aliases; use the `*-issue` / `babysit-pr-and-merge`
forms above. Codex (`~/.codex/skills/`) does not currently carry the family.

Related: `docs/ecc-flow-guide.md` (the ECC `orch-pipeline` interactive flow,
which hands off to `babysit-pr-and-merge` at its CI Gate).
