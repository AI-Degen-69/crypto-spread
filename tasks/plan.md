Branch: i350/read-174-phase-1-book-shadow-numbers | Issue: #350

# Implementation Plan — Issue #350: Read Phase 1 book_shadow numbers & record verdict

## 1. Context & Scope
- **Issue**: #350 `ops: read the #174 Phase 1 book_shadow numbers and record the go/no-go verdict`
- **Size Tier**: Tiny (Docs / Ops)
- **Goal**: Read final `book_shadow` metrics from `run/ticks/manifest.json`, record them in `docs/issue-174-socket-book-disagreement.md`, post verdict to #174, and prepare for PR merge and closeout.

## 2. Evidence from Ground Truth (`run/ticks/manifest.json`)
- Comparisons: 39,414 across all 10 series
- Divergent: 11,737
- Divergence Rate: 29.78% (0.2978) vs tolerance 0.001
- Max best bid delta: 0.32 (32 cents)
- Max best ask delta: 0.32 (32 cents)
- Mean abs bb delta: 0.005889, Mean abs ba delta: 0.005881, Mean abs mid delta: 0.005281
- Verdict: **NO-GO** for Phase 2 socket switch (far exceeds <1% threshold).

## 3. Improvement Proposal (Evidence-based, Adopted by default)
- Explicitly link from `docs/issue-174-socket-book-disagreement.md` to Issue #359 (`diag(market-data): per-delta socket-book reconciliation replay`), so developers investigating the divergence immediately find the diagnostic replay roadmap without search friction.

## 4. Tasks

| Task ID | Size | Domain | Description | Target Files | Depends on | Verification Method | Status |
|---|---|---|---|---|---|---|---|
| TASK-1 | XS | [Docs] | Create `docs/issue-174-socket-book-disagreement.md` with complete breakdown, stats table, NO-GO verdict, and link to #359 | `docs/issue-174-socket-book-disagreement.md` | - | Automated file existence & markdown check | [x] Completed (02c65c2) |
| TASK-2 | XS | [Ops] | Post final summary comment on Issue #174 linking the document and recording the NO-GO gate result | Remote GitHub Issue #174 | TASK-1 | `gh issue view 174 --comments` check | [x] Completed |
| TASK-3 | XS | [Review] | Verify repository status, ensure no unintended changes, and prepare for review | Repo root | TASK-2 | `git status` & targeted test sanity check | [x] Completed |

## 5. Checkpoints
- Checkpoint 1: Verified document format and stats match manifest exactly.
- Checkpoint 2: Verified comment posted to #174.
