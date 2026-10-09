# CONSTRAINTS.md — Issue #440 (per-issue working file, pruned at closeout)

Branch: `i440/featmarket-data-correct-the-book-shadow-divergence` | Issue: #440

## Test gates (targeted only — AGENTS.md forbids local full-suite runs)

- After replay changes: `python -m pytest tests/test_replay_socket_reconciliation.py -q`
- After streaming changes: `python -m pytest tests/test_clob_ws_collector.py -q`
- After collector/dashboard changes: add `tests/test_osc_dash_integration.py -q`
- Final gate (acceptance): all three files together. Full suite (1630 tests)
  stays with GitHub Actions CI on push — never run locally.

## Anti-cheat

- No skipping, xfail-ing, or deleting tests or assertions to reach green.
- No suppression of linters/type checks; no new `@ts-ignore`-style escapes
  (Python equivalent: no `noqa`, no silenced warnings).
- Every new behavior ships with its test written first (RED before GREEN).
- `rest_books` map removal must be covered by a regression test proving the
  fallback was unreachable (token with no REST records → excluded, uncounted).

## Compatibility

- `book_snapshot()` signature and return shape unchanged (streaming change is
  purely additive).
- Manifest `book_shadow` keeps `divergence_rate` (redefined = gated rate) and
  `tolerance` keys until the badge consumes the gated keys; existing dashboard
  tests updated, none deleted.
- `shadow_compare_book` timestamps are required keyword-only at the single
  call site (`scripts/collect_ticks.py:951`); existing unit call sites in
  tests are updated, not deleted.

## Performance

- No new I/O, network, or blocking calls in the collector poll loop; the
  shadow comparison stays pure arithmetic on already-fetched books.
- Replay stays offline-only; no venue calls added.

## Scope discipline

- Socket-client hardening is forbidden by this issue (see SPEC out-of-scope).
- No new external dependencies without explicit operator approval.
- #470 (dashboard tolerance literal) and #439 (dangling citations) are not
  touched.
