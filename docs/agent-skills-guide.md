# Agent Skills Guide — `crypto-spread`

Practical guide for using the 25 production-grade agent skills (`addyosmani/agent-skills`) installed globally across Antigravity, Claude Code, Hermes Agent, and Codex.

---

## 1. Global Issue Workflow Integration (`work-issue`)

When you invoke `/work-issue`, the agent runs a 6-step lifecycle that automatically invokes the right skills at each stage:

```text
DISCOVER       DEFINE & PLAN             BUILD & VERIFY              REVIEW          SHIP & MERGE
┌────────┐    ┌────────────────────┐    ┌──────────────────────┐    ┌──────────┐    ┌────────────┐
│ Step 1 │───▶│ Step 2             │───▶│ Step 3               │───▶│ Step 4   │───▶│ Step 5 & 6 │
│ Claim  │    │ Spec & Constraints │    │ TDD & Incremental    │    │ 5-Axis   │    │ Ship & PR  │
└────────┘    └────────────────────┘    └──────────────────────┘    └──────────┘    └────────────┘
```

| Step | Lifecycle Stage | Active Skills Invoked | Purpose |
|---|---|---|---|
| **Step 1** | **Discovery** | `context-engineering` | Locks session boundaries and sets rules before touching code. |
| **Step 2** | **Planning** | `interview-me`, `spec-driven-development`, `constraint-driven-development`, `api-and-interface-design`, `planning-and-task-breakdown` | Dissects requirements, locks quality contracts in `CONSTRAINTS.md`, designs APIs, and breaks work into bite-sized tasks. |
| **Step 3** | **Orchestration** | `test-driven-development`, `incremental-implementation`, `source-driven-development`, `debugging-and-error-recovery`, `code-simplification` | Writes failing tests first, implements code incrementally against official docs, diagnoses test failures, and simplifies complexity. |
| **Step 4** | **Self-Audit** | `code-review-and-quality`, `security-and-hardening`, `performance-optimization` | Audits the diff across correctness, secrets/inputs, and computational bottlenecks before committing. |
| **Step 5** | **Shipping** | `ship`, `git-workflow-and-versioning`, `shipping-and-launch`, `documentation-and-adrs` | Merges base (`master`), runs full tests & coverage audit, updates version/changelog, commits, pushes, and creates PR. |
| **Step 6** | **Babysitting** | `pr-babysitter` | Autonomous single-round CodeRabbit review babysitter (5-4-3-2-1m polling countdown, auto-trigger `@coderabbitai review`, auto-triage, inline API reply/resolve, test verify, and squash merge). |

---

## 2. When to Invoke Each Skill in `crypto-spread`

### Phase 1: Define & Specify (Before Any Code)

* **`interview-me`**: Use when a strategy rule or window requirement is vague.
  * *Example prompt:* `"Use interview-me to clarify the take-profit and stop-loss rules for the new 15m XRP spread strategy."`
* **`idea-refine`**: Use when brainstorming quant models or market filters.
  * *Example prompt:* `"Use idea-refine to stress-test our queue fill estimation model against high-volatility tick bursts."`
* **`spec-driven-development`**: Use to draft a clean PRD or spec before starting.
  * *Example prompt:* `"Use spec-driven-development to write a specification for the WebSocket live streaming engine."`

### Phase 2: Plan & Guardrails (Design Contracts First)

* **`constraint-driven-development`**: Use to lock quality bars into `CONSTRAINTS.md`.
  * *Example prompt:* `"Use constraint-driven-development to ensure backtest run times stay under 2 seconds and test coverage remains ≥95%."`
* **`api-and-interface-design`**: Use when adding new dashboard endpoints or strategy classes.
  * *Example prompt:* `"Use api-and-interface-design to define request/response schemas for /api/live/orders in server/osc_dash.py."`
* **`planning-and-task-breakdown`**: Use to order implementation into 5-minute tasks.
  * *Example prompt:* `"Use planning-and-task-breakdown to plan the refactor of strategy/live_trader.py."`

### Phase 3: Build & Implement (TDD & Safe Increments)

* **`test-driven-development`**: Enforces red `→` green `→` refactor cycles.
  * *Example prompt:* `"Use test-driven-development to write tests in tests/test_backtest_engine.py for the new pair cost calculation."`
* **`incremental-implementation`**: Keeps edits isolated and safe.
  * *Example prompt:* `"Use incremental-implementation to add the new exit threshold in scripts/measure_5m_oscillation.py step by step."`
* **`source-driven-development`**: Uses official library and API documentation.
  * *Example prompt:* `"Use source-driven-development to write the Polymarket CLOB book parsing logic using official docs."`

### Phase 4: Verify & Debug (Root Cause First)

* **`debugging-and-error-recovery`**: Systematic debugging (investigate `→` hypothesize `→` fix).
  * *Example prompt:* `"Use debugging-and-error-recovery to find why tick timestamps drift in scripts/collect_ticks.py."`
* **`performance-optimization`**: Profiles hot loops and disk I/O.
  * *Example prompt:* `"Use performance-optimization to speed up JSONL line parsing in scripts/sweep_backtest.py."`
* **`code-simplification`**: Eliminates unnecessary code while preserving behavior.
  * *Example prompt:* `"Use code-simplification on strategy/config.py to clean up legacy parameters."`

### Phase 5: Review & Ship (Quality Gates & PR Creation)

* **`code-review-and-quality`**: 5-axis review before commit (correctness, safety, types, readability, tests).
  * *Example prompt:* `"Use code-review-and-quality to audit git diff before committing live trader fixes."`
* **`ship`**: Automated ship workflow that merges base (`master`), executes tests, runs coverage audits, bumps version/changelog, commits, pushes, and opens the PR.
  * *Example prompt:* `"Run ship"` or `"/ship"`
* **`security-and-hardening`**: Verifies input sanitization and secret handling.
  * *Example prompt:* `"Use security-and-hardening to inspect how API keys and slug regexes are handled in strategy/markets.py."`
* **`git-workflow-and-versioning`**: Enforces conventional commits and release tags.
  * *Example prompt:* `"Use git-workflow-and-versioning to format our commit for the backtest engine fix."`

### Phase 6: Babysitting & Autonomous Merge

* **`pr-babysitter`**: Sits on the newly opened PR through a single focused CodeRabbit review round until merged.
  * **Ship Handshake**: Invoked directly after `/ship` on the opened PR.
  * **Auto-Trigger**: If CodeRabbit auto-review is silent or disabled, posts `@coderabbitai review` comment.
  * **Countdown Polling**: Polls review status at 5m → 4m → 3m → 2m → 1m intervals using non-blocking timers.
  * **Single Round Cap**: Runs exactly 1 review round to eliminate review churn and conserve CodeRabbit quota.
  * **Quota Limit Fallback**: If CodeRabbit hits limit, immediately invokes the local `code-reviewer` subagent.
  * **Zero Human in the Loop**: Triages comments, replies with reasons via GitHub API, resolves threads via GraphQL, applies code fixes, tests with `pytest`, self-heals, and performs squash merge.
  * *Example prompt:* `"Run pr-babysitter"` or `"/pr-babysitter"`

---

## 3. Fast Cheatsheet

| Task Scenario | Primary Skill | Trigger Command |
|---|---|---|
| Need to clarify unclear requirements | `interview-me` | `"grill me"` or `"use interview-me"` |
| Starting a new feature from an issue | `work-issue` | `"/work-issue <issue-number>"` |
| Writing quant logic or math formulas | `test-driven-development` | `"use test-driven-development"` |
| Diagnosing a bug or broken test | `debugging-and-error-recovery` | `"use debugging-and-error-recovery"` |
| Code review before PR | `code-review-and-quality` | `"use code-review-and-quality"` |
| Ship branch and open PR | `ship` | `"ship"` or `"/ship"` |
| Babysit CodeRabbit and merge PR | `pr-babysitter` | `"pr-babysitter"` or `"/pr-babysitter"` |
