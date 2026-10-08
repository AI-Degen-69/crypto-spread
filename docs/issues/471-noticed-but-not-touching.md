# Noticed-but-not-touching — Issue #471 (Station III/IV)

Candidates spotted while executing `tasks/plan.md` for #471. Only Station VI
(`vi-close-pipeline`) resolves rows here; other stations append `open` rows.

| ID | Candidate | Discovering station | Evidence | Status | Resolution |
|---|---|---|---|---|---|
| N1 | `AGENTS.md` states "GitHub Actions CI is the sole merge gate" and that pushing a branch triggers full-suite CI, but the repository has **no test-running workflow** — so the documented merge gate does not exist, and the same section's "932 tests across 18 files" is stale (actual: 1630 across 59). Out of #471's scope: that issue fixed the socket-drift guard, not the delivery pipeline. | IV | `AGENTS.md:38` vs `.github/workflows/summary.yml` (only workflow; trigger is `issues: [opened]`, runs no tests); `gh pr checks 473` reported only the CodeRabbit and GitGuardian apps | published | #474 — re-verified on merged master before publishing; the workflow set is still just `summary.yml`. |