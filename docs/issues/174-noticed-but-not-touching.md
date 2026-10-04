# Noticed-but-not-touching — Issue #174 (Station VI)

Discovered during the Station VI closeout of PR #435 (docs recording the post-#362
`book_shadow` measurement). Station VI resolves candidates recorded here; other
stations only append `open` rows.

The closeout also closed #174 as measured-NO-GO and re-homed its remaining work on
three narrower issues: #438 (root-cause the residual divergence), #440 (re-gate the
switch on a pre-registered threshold, blocked by #438), #439 (the citation candidate
below).

| ID | Candidate | Discovering station | Evidence | Status | Resolution |
|---|---|---|---|---|---|
| N1 | Three live citations point at per-issue working files that Station VI prunes every closeout, so the rationale each one names is permanently unresolvable | VI | `strategy/book_math.py:158` (`SPEC.md`), `tests/test_run_layout.py:4` (`SPEC.md §6`), `tests/test_orders_trades_table.py:2260` (`CONSTRAINTS.md section 5`) | published | #439 — supersedes this row's same-session `dismissed` verdict (commit `389ddf2`). The dismissal reasoned that name-only pointers are not dependencies and that the pattern had survived ten consecutive prunes; the operator then elected to repair the three sites and gate the recurrence. #439 scopes both, and confines the gate to maintained Python source so that historical Markdown — `docs/issues/298-pristine-manifest-delta-findings.md`, `docs/collector-hosting-runbook.md` — keeps its record of citations that were pruned. |

## Candidates considered and kept without a row

- `docs/issue-174-socket-book-disagreement.md` — PR #435's shipped deliverable and a
  measurement findings record (Section 4 permanent knowledge). Kept.
- `origin/feat/pair-cost-toggle` — remote branch whose PR #40 is MERGED via squash, so its
  tip is not an ancestor of `master`. Violated the Clean Exit Gate's "no merged feature
  branch survives on the server" check; deleted with operator approval
  (`git push origin --delete feat/pair-cost-toggle`).

## Finding worth carrying forward

`scripts/replay_socket_reconciliation.py` replays a raw socket session against two
pillars: in-frame venue quotes and REST ground truth. Run against the only session on
disk it reports 35,333 `ws` records, **0 `rest` records**, and 0 in-frame divergences —
so the REST pillar has never run against real data, and every `full_book` failure in
`scripts/record_raw_socket_session.py` is swallowed as `log.warning`. That gap is the
scope of #438; it is recorded here because it is why the post-#362 residual had no owner.