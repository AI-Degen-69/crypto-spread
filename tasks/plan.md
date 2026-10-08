# Plan — Issue #474: CI workflow for the test suite + AGENTS.md merge-gate correction

`Branch: i474/configure-add-a-ci-workflow-that-runs-the-test-sui | Issue: #474`

## Stack (auto-detected)
- Python 3.12.10 (`python --version`), deps `requirements.txt` (fastapi, uvicorn, requests, sse-starlette, anyio); `pytest` is a dev dependency, not listed.
- Existing CI: exactly one workflow `.github/workflows/summary.yml` (`on: issues: [opened]`, `ubuntu-latest`, `actions/checkout@v4` + `actions/ai-inference@v1`) — runs no tests.
- Suite size verified 2026-10-08: `python -m pytest --collect-only -q` → **1630 tests**, `ls tests/test_*.py | wc -l` → **59 files**.

## Size tier + rationale
- **Small** — one new file (workflow YAML) + one docs edit (`AGENTS.md:35-38`); no behavior change, no interface change, straightforward once read.
- **Task type:** Code (CI config) + Docs (AGENTS.md correction).

## Step 0C — quick-fix divert
- Labels re-read live: `enhancement`, `ready-for-agent` — no `quick-fix` label → continue to Step 0A, no gate text, no lane offered.

## Step 0A — CodeRabbit plan + open questions
- CodeRabbit plan: **none** — `gh issue view 474 --json comments` → 0 comments. Nothing adopted, nothing rejected, nothing `[UNVERIFIED]` from CodeRabbit.
- `needs-answers` label: absent. No "Open questions" section in the issue. All seams verified from code (file paths + counts above); no operator questions asked.
- `code-explorer` persona: skipped (Small, familiar surface — one workflow file + one docs section).

## Step 1 — domain routing (skills verified present in the account-skills library)
- `ci-cd-and-automation` → T1 (workflow authoring).
- `documentation-and-adrs` → T2 (AGENTS.md correction).
- `incremental-implementation` → overall (small vertical slices).

## Step 2 — spec (embedded; Small → no SPEC.md ceremony)
- Goal: pushing a branch or opening a PR executes the full pytest suite on a clean Linux runner; `AGENTS.md:35-38` describes the gate that actually exists with correct counts.
- Acceptance (from the issue, unchanged):
  1. `.github/workflows/tests.yml` exists, triggers on `push` to base branch + `pull_request`.
  2. Installs `requirements.txt` + `pytest`, runs full suite on pinned Python 3.12.
  3. Suite green on a clean runner (proven on a test PR before merge).
  4. `AGENTS.md:36-38` states the real gate + correct counts (1630 / 59).
  5. Verified by full `pytest -q` + one deliberately-failing test observed red on a throwaway PR run.
- Out of scope: lint gate, pre-commit hooks, branch protection, coverage, app/test code changes.
- Edge: timing-sensitive tests (e.g. drift-guard reads of a real clock under `WS_BOOK_COMPARABLE_AGE_SEC` 0.1s) may behave differently on a loaded Linux runner — accepted and observed via the throwaway-PR proof, not solved here.

## Step 4 — interface contracts
- Skipped: no public API, schema, or function-signature change. Contract surface = workflow `on:` triggers + `AGENTS.md` prose (locked in CONSTRAINTS.md).

## Step 5 — one improvement proposal (adopted by default: simplification)
- Evidence (verbatim, `AGENTS.md:35-38`): `"NEVER run the full test suite locally... takes ~96 seconds"`.
- Proposal: when reconciling `AGENTS.md`, carry forward **counts only (1630 tests / 59 files)** and drop the `~96 seconds` duration claim — durations depend on the runner and re-stale the same way the counts just did. Simplification, no scope expansion; folded into T2. (No scope-expansion proposal; none asked.)

## Step 6 — dependency graph + tasks
- Graph: T1 ⟷ T2 independent (parallelizable); T3 depends on T1 + T2.
- Checkpoint after T1+T2 (workflow file exists + docs corrected), then T3 verifies.

### T1 [CI/Config] (S) — add `.github/workflows/tests.yml` [x]
- Files: `.github/workflows/tests.yml` (new). Do NOT touch `summary.yml`.
- Build: `on: push (branches: [master]) + pull_request`; `runs-on: ubuntu-latest`; `actions/checkout@v4`; `actions/setup-python@v5` with `python-version: "3.12"`; pip cache; `pip install -r requirements.txt + pytest`; run `python -m pytest -q`.
- Skill: `ci-cd-and-automation`. Depends on: —.
- Verify: YAML parses; `git status` shows only the new file; trigger lines eyeball-reviewed against `summary.yml` structure.

### T2 [Docs] (XS) — reconcile `AGENTS.md:35-38` [x]
- Files: `AGENTS.md` (Testing & Fast Iteration Policy block only).
- Build: keep the sole-merge-gate claim (gate now exists); correct counts to 1630 tests / 59 files; drop the stale `~96 seconds` duration per Step 5; keep the targeted-tests-only local policy untouched.
- Skill: `documentation-and-adrs`. Depends on: —.
- Verify: `grep -n "1630\|59 files\|sole merge gate" AGENTS.md`; `git diff --stat` shows only `AGENTS.md` + workflow file.

### T3 [CI/Config] (XS) — local verification gate (no full-suite local run) [x]
- Files: none (verification only).
- Build: `python -m pytest --collect-only -q | tail` (expect 1630), YAML parse check, `git status --porcelain` shows only issue work. Record: full `pytest -q` + deliberately-failing-test-red proof happen on the throwaway PR at Station IV/V (requires push), per CONSTRAINTS.md.
- Skill: `incremental-implementation`. Depends on: T1, T2.
- Verify: collect count = 1630; YAML parses; status clean except issue files.
