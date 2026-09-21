# Plan — Issue #287: pipeline leftovers hygiene rule

Stack: docs-only · Size: **Small** (one rule, one file) · Type: Docs

Spec (embedded, Small): add the leftover rule to `docs/git-workflow.md` —
showcase pages (`docs/issues/<id>-presentation-*.html`) are committed by the
station that creates them as part of that issue's PR; per-issue scratch is
removed by the post-merge sweep; `git status --porcelain` must be empty
apart from the issue's own work before push. Master is already clean
(chore `adc806a`), so no cleanup commit is part of this plan.

| ID | Tag | Target files | What is built | Helper skill | Verification |
|---|---|---|---|---|---|
| TASK-1 | [Docs] | `docs/git-workflow.md` | Leftover rule (≤10 lines, tone-matched) | documentation-and-adrs | `git status --porcelain` clean; rule renders under §5/§7 |
| TASK-2 | [Docs] | — | Closeout: confirm clean tree on branch | — | Porcelain empty; push via normal Station IV/V flow |
