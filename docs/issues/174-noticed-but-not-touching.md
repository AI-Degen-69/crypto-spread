# Noticed-but-not-touching — Issue #174 (Station VI)

Discovered during the Station VI closeout of PR #435 (docs recording the post-#362
`book_shadow` measurement). Station VI resolves candidates recorded here; other
stations only append `open` rows.

| ID | Candidate | Discovering station | Evidence | Status | Resolution |
|---|---|---|---|---|---|
| N1 | Three live citations point at per-issue working files that Station VI prunes every closeout, so the rationale each one names is permanently unresolvable | VI | `strategy/book_math.py:158` (`SPEC.md`), `tests/test_run_layout.py:4` (`SPEC.md §6`), `tests/test_orders_trades_table.py:2260` (`CONSTRAINTS.md section 5`) | dismissed | Operator disposition: dismissed. The three hits are name-only rationale pointers, not dependencies — none is loaded, read or asserted at runtime or in the test path. They predate ten consecutive Station VI prunes (`357dffe`, `75e4c67`, `0a4421f`, `df08c9d`, `81f622d`, `a00c3a0`, `c4a6d2a`, `7a40a20`, `976e3da`, `af77420`), so pruning is the established convention and the citations have always dangled across it. Contrast `docs/issues/422-noticed-but-not-touching.md` N3, where a live reference to `SPEC-319.md` was load-bearing and therefore blocked its prune. Rewriting shipped code comments is outside Station VI's single approved code-change exception (dead code). |

## Candidates considered and kept without a row

- `docs/issue-174-socket-book-disagreement.md` — PR #435's shipped deliverable and a
  measurement findings record (Section 4 permanent knowledge). Kept.
- `origin/feat/pair-cost-toggle` — remote branch whose PR #40 is MERGED via squash, so its
  tip is not an ancestor of `master`. Violated the Clean Exit Gate's "no merged feature
  branch survives on the server" check; deleted with operator approval
  (`git push origin --delete feat/pair-cost-toggle`).