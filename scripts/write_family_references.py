"""Populate generalized reference.md for all 7 skills in the issue workflow family."""

from pathlib import Path

SKILLS_DIR = Path(r"C:\Users\Tiger\.agents\skills")

REFS = {
    "create-issue": """# create-issue Reference Guide

## Summary
The `create-issue` skill transforms raw, unstructured operator ideas, thoughts, or voice-note fragments into well-structured, production-ready GitHub issues that any agent or engineer can execute independently.

## When to Invoke
- The operator shares a raw thought, feature request, bug report, or architectural enhancement.
- An idea needs scoping, background discovery, and clear acceptance criteria before work begins.
- Complex tasks need to be analyzed to determine if they should be a single atomic issue or broken into linked issues.

## Inputs
- Operator prompt containing an idea, request, feedback, or pain point (in any language).
- Target repository context (inferred via git remotes).

## Outputs
- Researched and structured GitHub issue(s) published via `gh issue create`.
- Standard labels applied (`ready-for-agent`, optional `needs-triage`).
- Defined acceptance criteria, background, scope, and relevant file pointers with line references.

## Canonical Trigger Commands
- `/create-issue` (or `/create-issue "<raw description>"`)

## Scope Boundaries
- **In Scope**: Researching repo files to ground the issue, structuring requirements, splitting compound work items into linked siblings, and publishing to GitHub.
- **Out of Scope**: Planning detailed execution steps or task breakdown (handled by `plan-issue`), code implementation (handled by `build-plan`), or branch creation.
""",

    "plan-issue": """# plan-issue Reference Guide

## Summary
The `plan-issue` skill serves as the Station 1 Planning Orchestrator. It takes a GitHub issue, analyzes project architecture and test frameworks, conducts right-sizing, locks down constraints, and produces a structured `tasks/plan.md` ready for execution.

## When to Invoke
- An issue is ready to be picked up and planned before writing code.
- Requirements, constraints, and test baselines need to be locked down.
- An operator runs `/plan-issue` to review open issues or `/plan-issue <id>` to plan a specific issue.

## Inputs
- GitHub Issue number or open issue backlog.
- Existing codebase structure, test commands, and architectural constraints.

## Outputs
- `CONSTRAINTS.md` updated with strict quality gates and performance baselines.
- `tasks/plan.md` containing an ordered, bite-sized task plan.
- `tasks/todo.md` checklist for tracking task progression.
- Clear recommendation for execution mode (`build-plan auto` vs `build-plan`).

## Canonical Trigger Commands
- `/plan-issue`
- `/plan-issue <issue-number>`

## Scope Boundaries
- **In Scope**: Requirements extraction, technical research, architecture design, constraint locking, and task planning.
- **Out of Scope**: Writing production code or tests (handled by `build-plan`), pushing branches, opening PRs, or merging.
""",

    "build-plan": """# build-plan Reference Guide

## Summary
The `build-plan` skill acts as the Station 2 Execution Orchestrator. It executes the tasks laid out in `tasks/plan.md` through disciplined Test-Driven Development (TDD), source-driven documentation grounding, atomic commits, and automated error resolution.

## When to Invoke
- An approved `tasks/plan.md` exists and is ready for implementation.
- The operator wants either step-by-step verified execution (`/build-plan`) or end-to-end autonomous implementation (`/build-plan auto`).

## Inputs
- `tasks/plan.md` task list and `CONSTRAINTS.md` guardrails.
- Project test suite and build tools.

## Outputs
- Production and test code modifications adhering to TDD.
- Atomic, verified git commits per completed task.
- Updated `tasks/todo.md` tracking completed items.
- Passing test suite with zero regressions.

## Canonical Trigger Commands
- `/build-plan` (single task mode)
- `/build-plan auto` (or `/build-plan all` for autonomous batch mode)

## Scope Boundaries
- **In Scope**: Writing failing tests (RED), implementing minimal green code (GREEN), refactoring, code simplification, fixing build/type errors, and creating local git commits.
- **Out of Scope**: High-level planning (handled by `plan-issue`), pushing to remote, opening pull requests, or code review babysitting (handled by `review-build-and-pr` and `babysit-pr-and-merge`).
""",

    "review-build-and-pr": """# review-build-and-pr Reference Guide

## Summary
The `review-build-and-pr` skill serves as the Station 3 Review & Ship Orchestrator. It subjects implemented code to a 4-axis parallel review, verifies test suite pass rates, pushes the feature branch, opens a GitHub Pull Request, and hands off to review babysitting.

## When to Invoke
- All tasks in `tasks/plan.md` are completed and verified by `build-plan`.
- Code is ready for pre-push review, git sync, PR creation, and automated CI/AI review.

## Inputs
- Feature branch containing verified commits.
- Target branch (`master` / `main`).
- Associated GitHub Issue number.

## Outputs
- 4-axis review results (code quality, security, test engineer, language specialist).
- Remote git branch pushed to origin.
- Published GitHub Pull Request linked to the issue.
- Activation of `babysit-pr-and-merge` for automated review tracking and merging.

## Canonical Trigger Commands
- `/review-build-and-pr`

## Scope Boundaries
- **In Scope**: Pre-push review gates, test verification, pushing branch, opening PRs, and handing off to babysitting.
- **Out of Scope**: Authoring task plans (handled by `plan-issue`), writing initial feature implementation (handled by `build-plan`), or generating end-user visual verification reports (handled by `explain-issue`).
""",

    "explain-issue": """# explain-issue Reference Guide

## Summary
The `explain-issue` skill serves as the Station 4 Visual Showcase & Verification Orchestrator. It generates an interactive, RTL-native HTML report explaining the issue's changes simply (ELI5 style), detailing Before vs After architecture, and providing a hands-on manual verification guide.

## When to Invoke
- Immediately after an issue has been shipped and merged (or during final review).
- When a visual walkthrough or clear manual testing guide is needed for operator validation.

## Inputs
- GitHub Issue number and merged Pull Request metadata.
- Code diff, before/after architectural context, and project run commands.

## Outputs
- Self-contained HTML report (e.g. `docs/reports/issue_<id>_showcase.html`) featuring RTL typography, before/after comparisons, and step-by-step verification commands.
- Live browser preview launch.

## Canonical Trigger Commands
- `/explain-issue` (auto-detects active issue from context)
- `/explain-issue <issue-number>`

## Scope Boundaries
- **In Scope**: Generating visual documentation, ELI5 summaries, visual architecture flowcharts, and manual verification steps.
- **Out of Scope**: Writing code changes, running TDD cycles, or opening pull requests.
""",

    "workflow-issue": """# workflow-issue Reference Guide

## Summary
The `workflow-issue` skill is the master top-level lifecycle orchestrator. It guides an issue through the entire journey from discovery, planning (Station 1), implementation (Station 2), shipping (Station 3), through to visual explanation (Station 4).

## When to Invoke
- When picking up a new task from scratch and desiring end-to-end guidance across all stations.
- When invoked without arguments to list, categorize, and prioritize all open issues in the backlog.

## Inputs
- Open issue backlog or specific issue number.
- Full repository context.

## Outputs
- Prioritized backlog recommendations or complete transition across Stations 1 through 4.

## Canonical Trigger Commands
- `/workflow-issue` (lists open backlog and recommends next issue)
- `/workflow-issue <issue-number>` (orchestrates full issue lifecycle)

## Scope Boundaries
- **In Scope**: Backlog triage, delegating to Station 1 (`plan-issue`), Station 2 (`build-plan`), Station 3 (`review-build-and-pr`), and Station 4 (`explain-issue`).
- **Out of Scope**: Direct implementation of low-level code without delegating to station workflows.
""",

    "babysit-pr-and-merge": """# babysit-pr-and-merge Reference Guide

## Summary
The `babysit-pr-and-merge` skill manages pull requests post-creation through automated review, CI tracking, comment triage, bot discussions, and safe merging with zero required human intervention.

## When to Invoke
- Immediately following PR creation in `/review-build-and-pr` or `/ship`.
- When review comments (e.g. from CodeRabbit) arrive on an open pull request.
- When unhandled review feedback needs resolution before a PR can merge.

## Inputs
- Open GitHub Pull Request number or active feature branch.
- Automated review comments, CI checks, and reviewer feedback.

## Outputs
- Triaged review comments (applied fixes or reasoned dismissals).
- Verified fix commits pushed to the PR branch.
- Automated squash-merge of the PR once all gates and checks pass.

## Canonical Trigger Commands
- `/babysit-pr-and-merge`
- `/babysit-pr-and-merge <pr-number>`

## Scope Boundaries
- **In Scope**: Review comment extraction, triage, applying surgical fixes, pushing updates, and executing PR merges.
- **Out of Scope**: Initial feature planning, authoring full specifications, or original feature code creation.
""",
}


def write_references() -> None:
    """Generate and write generalized reference.md documentation to all 7 canonical skills."""
    for skill, content in REFS.items():
        ref_path = SKILLS_DIR / skill / "reference.md"
        ref_path.write_text(content.strip() + "\n", encoding="utf-8")
        print(f"[OK] Wrote {ref_path}")


if __name__ == "__main__":
    write_references()
