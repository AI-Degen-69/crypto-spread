# Plan — Issue #440: correct the book-shadow divergence metric, re-gate the switch

Branch: `i440/featmarket-data-correct-the-book-shadow-divergence` | Issue: #440
Tier: **Large** — cross-cutting (replay + collector + streaming client + docs +
dashboard badge), one pre-registered measurement rule plus a published run.
Task type: **Code + Docs** (metric correction with a pre-registered-rule doc edit).
Stack: Python; test runner `pytest` (targeted files only; full suite = CI).

## Intake notes (Station II §0A)

- CodeRabbit plan comment: none on the issue (3 owner comments only:
  N2/N3 fold-in, re-scope note, undated-fallback scope note). Nothing adopted,
  nothing to verify — all seams verified directly from `master` code instead.
- `needs-answers`: absent. Issue states "Open questions: None"; the gated/blind
  split, age gradient, and bucket defect all come from #438 measurements.
- `code-explorer` persona: skipped and recorded — execution paths traced
  directly (replay `_select_rest_book`/`_handle_ws_event`, collector
  `shadow_compare_book`/call site, streaming dispatch, dashboard badge
  readers). A persona pass would re-derive the same map.
- `type-design-analyzer` persona: run via subagent over contracts C1–C3.
  Verdict adopted: C1 accept; C2 timestamps required keyword-only +
  manifest `divergence_rate` redefined as gated-rate alias during transition;
  C3 provenance-`None` maps to `excluded['no_provenance']`, never blind.

## Interface contracts (frozen before build)

- C1 replay (`scripts/replay_socket_reconciliation.py`): undated fallback
  deleted (§5); `_select_rest_book` returns `None` with an exclusion reason in
  `{no_snapshot, outside_freshness_window, guard_reject,
  no_comparable_quotes}`; every counted comparison tagged `gated`
  (`price_change`/`best_bid_ask` WITH declared quotes) or `blind`; summary
  exposes `gated_rate`+`gated_n`, `blind_rate`+`blind_n`, and the `excluded`
  ledger; no global headline rate in replay output.
- C2 live (`scripts/collect_ticks.py`): `shadow_compare_book(..., *,
  rest_rx, ws_rx)` required keyword-only; `BOOK_SHADOW_FRESHNESS_S = 0.5`;
  manifest block keeps `divergence_rate` (= gated rate, deprecated alias) +
  `tolerance` and adds gated/blind counts + rates, `excluded` ledger,
  `freshness_bound_s`; dashboard badge switches to the gated rate (T3).
- C3 streaming (`strategy/streaming.py`): additive
  `book_snapshot_provenance(token_id)` → `{book, frame_kind|None,
  declared_best_bid|None, declared_best_ask|None}`; `book_snapshot()`
  untouched; dispatch records last frame kind + declared quotes per token
  under the existing lock.

## Improvement proposal (adopted by default — simplification)

Delete the undated-fallback branch (`scripts/replay_socket_reconciliation.py:356-359`)
together with the now-dead `rest_books` map (`:299`, `:334`, `:358`) instead of
building exclusion bookkeeping for it. Evidence: `:334-335` populate both maps
in the same `_handle_rest_snapshot` block (`self.rest_books[tok] = book` /
`self.rest_snapshots[tok].append(...)`), so `rest_books` without `rest_snapshots`
is unrepresentable and the fallback's non-`None` return is unreachable — the
scope note's "unexercised blind spot" dissolves rather than being counted.
Regression test feeds a token with no REST records and asserts exclusion.

## Dependency graph (risk-first order)

T1 (metric definition, riskiest) → T4; T2 (provenance) → T3 → T4; T4 → T5.
T1 and T2 are independent — build T1 first, then T2.

## Tasks

### T1 [Metric/Replay] — M — gated/blind split + exclusion ledger in the replay [x]
Files: `scripts/replay_socket_reconciliation.py`, `tests/test_replay_socket_reconciliation.py`.
Build: checkability tagging per pair; `excluded` reason ledger; fallback + `rest_books`
deletion; summary `gated_rate/blind_rate` + `excluded`; per-type rows kept; one-tick
== agreement pinned by test; middle bucket populable by test.
Helper skills: `test-driven-development`, `incremental-implementation`.
Depends on: none.
Verify: `python -m pytest tests/test_replay_socket_reconciliation.py -q`.

### T2 [Streaming/API] — S — frame-kind + declared-quote provenance [x]
Files: `strategy/streaming.py`, `tests/test_clob_ws_collector.py`.
Build: additive `book_snapshot_provenance`; per-token last-frame record under lock;
`book_snapshot()` byte-for-byte behavior unchanged (existing tests unmodified and green).
Helper skills: `test-driven-development`, `incremental-implementation`.
Depends on: none.
Verify: `python -m pytest tests/test_clob_ws_collector.py -q`.

### T3 [Collector/Live] — M — timestamped live instrument + manifest bound [x]
Files: `scripts/collect_ticks.py`, `server/osc_dash.py` (badge → gated rate),
`tests/test_clob_ws_collector.py`, `tests/test_osc_dash_integration.py`.
Build: required keyword-only timestamps; freshness bound; gated/blind split via T2
provenance (`None` → `excluded['no_provenance']`); manifest keys per C2; badge +
`_shadow_badge_text` on the gated rate; collector tests updated, none deleted.
Helper skills: `test-driven-development`, `incremental-implementation`.
Depends on: T2.
Verify: `python -m pytest tests/test_clob_ws_collector.py tests/test_osc_dash_integration.py -q`.
Checkpoint: both instruments corrected — replay and live classify identically.

### T4 [Docs/Gate] — M — pre-registered rule + cross-check/parity/flag-off tests
Files: `docs/issue-174-socket-book-disagreement.md` (§7 rule section),
`tests/test_replay_socket_reconciliation.py`, `tests/test_clob_ws_collector.py`,
`tests/test_osc_dash_integration.py`.
Build: rule locked in writing BEFORE any measurement (tick multiple, acceptable
rate, min sample size, max gap + reasoning, skew correction); live-vs-replay
cross-check test on one shared capture within stated tolerance; recorded-vs-live
parity through `strategy/book_math`; flag-off REST-unchanged test; venue-publication
freshness with measured latency figure.
Helper skills: `test-driven-development`, `documentation-and-adrs`.
Depends on: T1, T3.
Verify: `python -m pytest tests/test_replay_socket_reconciliation.py
tests/test_clob_ws_collector.py tests/test_osc_dash_integration.py -q`.
Checkpoint: rule locked + all three suites green.

### T5 [Validation/Run] — S — measurement run + verdict either way
Files: docs verdict section (same file as T4), no code.
Build: run corrected instruments against the pre-registered rule on a fresh
capture; publish numbers; record GO/NO-GO verbatim in the doc.
Helper skills: none (run + record).
Depends on: T4.
Verify: numbers present in doc; every rule field has a measured counterpart.

## Sub-issue mapping

Deferred deliberately: single-issue scope with a 5-task graph above; GitHub
sub-issues would fragment review of one instrument change. Revisit only if T1–T3
slip past one session each.

## Rejected / not proposed

- Socket-client hardening of any kind: rejected by the issue (no observed
  failure mode); proposing it would contradict #438 evidence.
- No second proposal: one is the maximum and the fallback deletion is it.
