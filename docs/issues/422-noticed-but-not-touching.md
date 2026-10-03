# Noticed-but-not-touching — Issue #422 (Station VI)

Discovered during the Station VI closeout of PR #423. Station VI resolves candidates
recorded here; other stations only append `open` rows.

| ID | Candidate | Discovering station | Evidence | Status | Resolution |
|---|---|---|---|---|---|
| N1 | `backtest/engine.py` entry-delay comment block still says "Resting quotes anchor at the first mid AT/AFTER delay expiry" — under the merged set-and-wait rule the latch is taken at the first in-range mid and the delay never gates it | VI | `backtest/engine.py:1024-1032` | published | #424 |
| N2 | `backtest/engine.py` `orders_live` comment still says "the anchor is repriced every tick, so the price that goes on the book is the mid at placement time" — verbatim the behaviour #422 removed | VI | `backtest/engine.py:1052-1058` | published | #424 |
| N3 | `SPEC-319.md` looks like a finished-issue artifact (issue #319 is CLOSED, single commit, per-issue SPEC in the repo root) but carries 3 live code references that pin a provenance guard | VI | `server/osc_dash.py:1204`, `tests/test_osc_dash_integration.py:6063,6070` | dismissed | Fails the two-gate obsolescence test (zero-inbound-reference gate): actively referenced by shipped code and tests. Kept — see AGENTS.md on `SPEC.md` being a per-issue working file that goes stale on merge, but this one is load-bearing. |

## Candidates considered and kept without a row

- `.claude/plans/dynamic-reentry-pricing.plan.md` + `.claude/prds/dynamic-reentry-pricing.prd.md` — issue #95 is CLOSED and the plan's acceptance boxes are all ticked, but the pair lives in the gitignored harness working dir `.claude/`, not the repo tree. Foreign to this repository's knowledge; left alone.
- `diff.patch` (repo root, untracked, gitignored via `*.patch`) — no issue identity in the content; `git apply --check --reverse` fails, so it is a superseded scratch snapshot from 2026-09-17. Untracked local file, not staged, not committed, left for the operator.
- `wip/dead-sweep-label-line` — unmerged local branch holding a verified dead-code removal (`xTickLabels` map in `server/osc_dash.py:10707` has one definition, one test string-pin, and zero call sites). Owned by open issue #396, which audits exactly this; not touched here.
- Stashes `stash@{0}`..`stash@{8}` — nine pre-existing stashes from closed issues (#376, #353, #302, #233, #155, #185, #140, #96). None created by this issue; owned by #396.