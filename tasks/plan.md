# Task Plan — Issue #264: Backtest Sweep Visual

**Size tier:** Standard — the issue spans the dashboard endpoint, one served HTML/JavaScript component, and focused integration/rendering tests, but keeps the existing replay engine and Chart.js dependency.
**Task type:** Code + Design/UI + API/Backend + QA/Regression.
**Stack:** Python 3.12, FastAPI, pytest, inline served HTML/JavaScript, existing Chart.js integration. Specialized guidance verified: `api-and-interface-design`, `frontend-ui-engineering`, `test-driven-development`, `incremental-implementation`, `spec-driven-development`, `constraint-driven-development`, and `planning-and-task-breakdown`.

## Issue-driven approach
The endpoint will be contract-tested first, then the UI will consume numeric `{x, y}` points on a linear X scale while retaining readable labels. Sweep Visual titles will use the aligned fixed-width format `05m BTC` / `15m BTC`, while machine slugs and unrelated dashboard labels remain unchanged. The real 2026-09-18 tick file will be used for browser verification if present. Existing unrelated working-tree changes, especially Issue #174 and presentation artifacts, remain out of scope.

## Evidence-based improvement (adopted by default)
Return an additive `series_labels` mapping alongside `series_order`, sourced from `strategy/series.py:SERIES`, so the UI can apply the aligned `05m BTC` / `15m BTC` display format without duplicating market-universe knowledge in JavaScript.

## Tasks

- [x] **TASK-1 [Backend/Logic]**: Lock the sweep endpoint contract with focused tests.
  - Target: `tests/test_osc_dash_integration.py`, `server/osc_dash.py` only as needed.
  - Build: deterministic fixture coverage for valid shape, numeric values, ordered points, ten canonical series, unknown-axis 400, unsafe/missing file responses, and 429 busy behavior including guard release after completion/failure.
  - Helper: `api-and-interface-design` + `test-driven-development`.
  - Verify: `python -m pytest tests/test_osc_dash_integration.py -q`.

- [x] **TASK-2 [Backend/Logic]**: Make the response carry canonical series label metadata without changing replay math.
  - Target: `server/osc_dash.py`, `strategy/series.py` only if an existing helper is insufficient.
  - Build: preserve the locked response fields and add the minimal additive mapping needed by the UI; keep absent-series points/order deterministic and format display labels as zero-padded duration first (`05m BTC`, `15m BTC`).
  - Helper: `api-and-interface-design` + `incremental-implementation`.
  - Verify: endpoint contract tests and response-shape assertions in `tests/test_osc_dash_integration.py`.

- [x] **TASK-3 [Design/UI]**: Render the aggregate and ten market charts with a genuinely numeric X axis.
  - Target: `server/osc_dash.py` inline Sweep Visual HTML/JavaScript near `btSweepCard` and `renderSweepVisual()`.
  - Build: retain existing DOM IDs and theme tokens; use Chart.js linear scales and numeric point values, with formatted tick/tooltips for readability; show friendly titles and best-point metadata; keep busy/error UI behavior.
  - Helper: `frontend-ui-engineering` + `test-driven-development`.
  - Verify: served-HTML rendering contract tests in `tests/test_theme_tokens.py` and integration assertions for one aggregate plus ten per-market canvases/configuration.

- [x] **TASK-4 [QA/Regression]**: Verify the delivered view against the real tick dataset and document observable caveats.
  - Target: test assertions and handoff evidence only; do not commit generated `run/` data.
  - Build: run the dashboard/browser against `run/ticks/ticks_2026-09-18.jsonl` when available and record chart count, friendly labels, numeric axis behavior, and any dataset-specific zero-fill caveat.
  - Helper: `frontend-ui-engineering` + `incremental-implementation`.
  - Verify: browser preview/manual UI check plus targeted pytest gate. Completed against `ticks_2026-09-18.jsonl`: 30 windows, one aggregate chart, ten market charts, and aligned labels rendered; absent markets remain zero-filled.

## Verification matrix
| Task | Verification |
|---|---|
| TASK-1 | `python -m pytest tests/test_osc_dash_integration.py -q` |
| TASK-2 | Endpoint response-shape and validation tests |
| TASK-3 | `python -m pytest tests/test_theme_tokens.py tests/test_osc_dash_integration.py -q` + browser rendering |
| TASK-4 | Real-dataset browser run, then same targeted gate |

## Risks and mitigations
| Risk | Mitigation |
|---|---|
| Existing UI uses category labels | Test for `type: 'linear'` and numeric `{x, y}` data, not only text presence. |
| Dataset lacks some markets | Preserve canonical order and render zero-filled points/cards. |
| Sweep remains busy after worker failure | Assert release in failure-path/concurrency tests and keep cleanup in `finally`. |
| Shared checkout contains unrelated edits | Do not stage or rewrite unrelated files; implementation scope stays within endpoint/UI/tests. |

## Post-build gate
Run only the targeted suites required by the issue: `python -m pytest tests/test_osc_dash_integration.py tests/test_theme_tokens.py -q`. Full-suite execution remains CI's responsibility.

---


**Size tier:** Large — cross-cutting change over live engine + collector +
streaming bridge + manifest + dashboard surface, with a phased rollout gated on
measured evidence and a provenance change for real-money quoting books.
**Task type:** Code + Performance (Phase 1 is measurement research feeding a
perf switch: ~184ms REST book cost per series per round).

## Context & Problem
Both consumers still treat REST books as the source of truth every round while
the socket publishes every venue update in between: the collector fetches both
books over REST each poll (`scripts/collect_ticks.py:456-464`, `_book_or_err` →
`full_book`), and live/paper re-fetches REST books whenever `is_ws_book_fresh()`
is false (`strategy/live_trader.py:3860-3874`). The chain #170–#173 landed the
prerequisites (shared `book_math`, live REST-skip fast-path, hardened direct
transport, socket tape authority), and `get_book_for_token()`
(`strategy/streaming.py:1033`) already exposes the maintained book — but no
shadow comparison exists, so the "rare and bounded disagreement" gate for the
switch cannot be evaluated. Switching provenance without that evidence risks
quoting (and replaying) from a silently wrong book: dropped `price_change`
deltas, `tick_size_change` grid shifts, post-reconnect staleness, and crossed
books all look healthy with no error anywhere.

## Interface contracts (locked before logic)
- `CLOBStreamCollectorBridge.get_book_for_token(token_id) -> Optional[dict]`
  (`strategy/streaming.py:1033`) and `CLOBMarketWSClient.book_snapshot(token_id)`
  (`streaming.py:590`): the only read path for socket books. Returns an isolated
  snapshot or `None` (no data / not subscribed). No new accessors.
- `strategy/book_math.py` (`mid`, `queue_ahead`, `two_sided_mid`): the only
  pricing arithmetic for WS and REST books alike (#170 parity — a recorded tick
  and a live tick are read by the same code). Unchanged by this issue.
- Freshness predicate, one definition shared by both consumers: collector's
  `ws_leg_authoritative()` (`collect_ticks.py:403`) and engine's
  `is_ws_book_fresh()` (`live_trader.py:1881`) keep their existing shapes; the
  book-authority check reuses the same connected + subscribed + recent-update
  conditions, never a new fourth notion of "fresh".
- Tick JSONL: new optional per-leg fields only
  (`ws_best_bid`, `ws_best_ask`, `ws_rest_bid_diff`, `ws_rest_ask_diff`,
  `ws_level_diff`, `ws_diverged`, `ws_available`). Old readers ignore them.
- `manifest.json` socket telemetry: new aggregate keys only
  (`ws_book_diverge_rate`, `ws_book_bid_diff_p50/p95/max`,
  `ws_book_ask_diff_p50/p95/max`, `ws_book_compare_n`, `ws_book_unavailable_n`).
- Reconciliation: `WS_BOOK_RECONCILE_SEC` (periodic REST re-fetch, much larger
  than the 1s round) + `WS_BOOK_DRIFT_BOUND` (max tolerated top-of-book
  disagreement before forced resync). Values set in Phase 2 from Phase 1 data.

## Proposed improvement (adopted by default)
Per-tick disagreement goes into the tick JSONL itself, not only as aggregates
in `manifest.json` — the issue calls the tick files "the replay dataset", so
the evidence must travel with the data to stay replayable and auditable by the
backtest; the manifest keeps only the distribution summary.

## Tasks

### Phase 1 — shadow comparison, zero behaviour change

- [ ] **TASK-1 [Backend/Logic]**: Shadow WS book capture in the collector
  - Target: `scripts/collect_ticks.py` (poll path near `fetch_slate_books` /
    `SeriesFetch`), `strategy/streaming.py` (read-only use of
    `get_book_for_token`)
  - What is built: each round reads the socket book for both legs via the
    bridge alongside the unchanged REST fetch; REST remains the recorded/quoted
    source; `ws_available=False` recorded when the socket has no book.
  - Helper skill: `test-driven-development`
  - Verify: `python -m pytest tests/test_ws_book_shadow.py -q` (new) +
    `python -m scripts.collect_ticks --once` smoke.

- [ ] **TASK-2 [Backend/Logic]**: Disagreement metrics + tick-file fields
  - Target: `scripts/collect_ticks.py`, tick JSONL writer, `manifest.json`
    telemetry section
  - What is built: per token `abs()` top-of-book bid/ask diffs, level-set diff
    count, diverge boolean (any diff > 1 tick); per-tick optional fields (see
    contracts) + manifest distribution aggregates (diverge rate, p50/p95/max,
    compare_n, unavailable_n).
  - Helper skill: `test-driven-development`
  - Verify: `python -m pytest tests/test_ws_book_shadow.py
    tests/test_collect_ticks_smoke.py -q`.

- [ ] **TASK-3 [Backend/Logic]**: Operator-visible disagreement surface
  - Target: `server/osc_dash.py` (read-only endpoint or ticks/manifest section)
  - What is built: the diverge rate + distribution published where an operator
    reads it (dashboard surface backed by `manifest.json`, no new storage).
  - Helper skill: `incremental-implementation`
  - Verify: `python -m pytest tests/test_osc_dash_integration.py -q -k ticks`
    (or manifest) + manual dashboard read.

### Checkpoint: measurement live
- [ ] Phase 1 runs with zero behaviour change: `slow_tick` rate unchanged,
  REST books still recorded, shadow fields present, operator can read the rate.

### Phase 2 — switch, gated on Phase 1 evidence

- [ ] **TASK-4 [Backend/Logic]**: REST reconciliation + WS quoting source
  - Target: `strategy/live_trader.py` (`_poll_single_market`, quoting path),
    `strategy/book_math.py` (use only, no rule changes)
  - What is built: WS book becomes the quoting source through `book_math`;
    REST demoted to periodic reconciliation (`WS_BOOK_RECONCILE_SEC`) plus
    drift-triggered forced resync when disagreement exceeds
    `WS_BOOK_DRIFT_BOUND` (bound chosen from Phase 1 distribution).
  - Depends on: recorded Phase 1 measurement run (gate, not a code dep).
  - Helper skill: `api-and-interface-design`
  - Verify: `python -m pytest tests/test_live_trader.py -q` + new resync tests.

- [ ] **TASK-5 [Debug/Resilience]**: Reconnect-stale + crossed-book safety
  - Target: `strategy/streaming.py` (stale marking on reconnect), consumers'
    fallback paths
  - What is built: post-reconnect book marked stale and never served until a
    fresh `book` snapshot arrives (REST or unquotable until then);
    `tick_size_change` forces snapshot refresh; crossed/degenerate WS book
    (`best_bid >= best_ask`, unpriceable per `book_math`) falls back to REST
    for that leg.
  - Helper skill: `debugging-and-error-recovery`
  - Verify: `python -m pytest tests/test_clob_ws_collector.py
    tests/test_streaming.py -q` incl. new reconnect-resync + crossed-book tests.

- [ ] **TASK-6 [Backend/Logic]**: Live/paper quoting through the shared interface
  - Target: `strategy/live_trader.py` quoting path, parity with backtest
  - What is built: live and paper legs priced from WS books exclusively via
    `book_math` (same code the backtest replays ticks with); `is_ws_book_fresh`
    remains the single freshness predicate.
  - Helper skill: `test-driven-development`
  - Verify: `python -m pytest tests/test_live_trader.py tests/test_book_math.py
    tests/test_backtest_engine.py -q`.

### Checkpoint: switch safe
- [ ] Resync-after-reconnect proved by test, crossed-book fallback proved by
  test, quoting freshness bounded by venue publication.

### Phase 3 — collector switch (perf)

- [ ] **TASK-7 [Performance]**: Collector skips REST books when socket is authoritative
  - Target: `scripts/collect_ticks.py` (`fetch_slate_books` / `_book_or_err`
    call sites)
  - What is built: per-round REST book fetch skipped per leg when the socket
    leg is authoritative; reconciliation fetch retained at
    `WS_BOOK_RECONCILE_SEC` cadence; round-time logging before/after against
    the ~184ms/series REST baseline.
  - Helper skill: `performance-optimization`
  - Verify: `python -m pytest tests/test_collect_ticks_smoke.py -q` + measured
    round-time drop; neutral timing is a revert.

- [ ] **TASK-8 [QA/Regression]**: Targeted regression gate
  - Target: tests covering all modified files; tick-file backward compat
  - What is built: new tick files (with shadow fields) replay in backtest and
    verify tooling untouched; all targeted suites green.
  - Helper skill: `python-testing`
  - Verify: `python -m pytest tests/test_ws_book_shadow.py
    tests/test_collect_ticks_smoke.py tests/test_clob_ws_collector.py
    tests/test_live_trader.py tests/test_book_math.py -q`.

## Verification matrix
| Task | Method |
|---|---|
| TASK-1 | new shadow tests + `collect_ticks --once` |
| TASK-2 | shadow + collector smoke suites |
| TASK-3 | dashboard integration suite + operator read |
| TASK-4 | live-trader suite + resync tests |
| TASK-5 | ws collector + streaming suites (resync/crossed proof) |
| TASK-6 | live-trader + book_math + backtest engine suites |
| TASK-7 | collector smoke suite + round-time measurement |
| TASK-8 | combined targeted run, zero regressions |

## Risks and mitigations
| Risk | Impact | Mitigation |
|---|---|---|
| Shadow read slows the 1s round | Med | Local snapshot only; `slow_tick` before/after check in checkpoint 1 |
| Phase 1 shows large disagreement | Med | That is the design working — Phase 2 stays blocked, issue reports why |
| Silent delta-drop book looks healthy | High | Drift bound + periodic reconciliation + resync tests (TASK-4/5) |
| Tick schema change breaks replay | High | Append-only optional fields; TASK-8 replays new files with old code paths |

## Post-build gates (Station IV)
- Targeted suites in CONSTRAINTS §7 green, <15s each file.
- Full suite stays with CI on push — never run locally.
