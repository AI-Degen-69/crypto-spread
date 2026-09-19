# SPEC — Issue #264: Backtest Sweep Visual

## Goal
Give the operator a visual one-axis sensitivity view in the Backtest tab: one aggregate X-Y chart plus one chart for each of the ten canonical markets, using the existing Chart.js integration and the same replay behavior as the CLI sensitivity sweep.

## Current state
The issue reports that `/api/backtest/sweep` and `btSweepCard` work has started locally. The endpoint currently returns numeric `value` fields, but the UI uses categorical `labels` for Chart.js X axes, the per-market titles use raw slugs, and the focused endpoint/rendering contracts are not yet locked by tests.

## Interface contract
- `GET /api/backtest/sweep` accepts `axis` (`queue`, `offset`, `exit_5m`, `exit_rev`), optional safe basename `file`, and the existing base simulation parameters.
- Valid responses contain `axis`, ordered `points[]`, `series_order`, `n_snaps`, and `n_windows`. Each point contains numeric `value`, readable `label`, `overall`, and `per_series`.
- Invalid axes return HTTP 400 with the valid-axis list. Unsafe paths return HTTP 400. Missing files return HTTP 404. A concurrent run returns HTTP 429, and the guard is released after completion.
- Served HTML retains the existing Sweep Visual IDs and renders one aggregate chart plus ten per-market charts. Chart.js uses a linear X scale with `{x, y}` points sourced from numeric `point.value`; formatted readable labels may be supplied as tick callbacks/tooltips.
- The Sweep Visual display format is fixed-width `05m BTC`, `15m BTC`, `05m ETH`, etc. The numeric `05m` prefix keeps the duration column aligned; machine slugs and existing non-Sweep labels remain unchanged.

## Edge cases
- Empty or sparse replay data still returns a valid response and renders the known series cards without throwing.
- A series absent from a dataset renders zero-valued per-series points while preserving canonical ordering.
- The best-point metadata handles an empty points array without indexing failure.
- Busy state must be cleared on success and worker failure so later sweeps are not permanently rejected.

## Explicit out of scope
Multi-axis/joint-grid/random sweeps, structural-limit sweeps in the UI, per-window scatter plots, strategy calculations, parameter defaults, tick data, other dashboard tabs, new dependencies, and Issue #174 socket-authoritative book changes.

---


## Goal
Make the socket the primary source of order books everywhere, with REST demoted
to periodic reconciliation, so quotes are priced from the freshest book the venue
has published rather than from a snapshot up to a full round old. Measure first,
then switch — no provenance change without published disagreement numbers.

Endpoint of the chain #170 → #171 → #172 → #173. All four are CLOSED/landed,
so this issue is unblocked.

## Current state (what the chain already delivered)
- #170: `strategy/book_math.py` — one shared mid / queue_ahead / two_sided_mid
  used by every consumer; degenerate one-sided books handled identically.
- #171: `strategy/live_trader.py:3860-3871` — live/paper already skips the REST
  book fetch when `is_ws_book_fresh()` holds for both legs (`ws_book_authority`).
- #172: live engine runs the hardened direct WS transport (`run_direct`).
- #173: socket trade prints feed fill detection; collector skips REST tape per
  leg when `ws_leg_authoritative()` holds (`scripts/collect_ticks.py:403-418`).
- `CLOBStreamCollectorBridge.get_book_for_token()` (`strategy/streaming.py:1033`)
  already exposes the socket-maintained book; nothing reads it for quoting yet.

## What is still missing (this issue)
1. **Phase 1 measurement.** No shadow comparison exists anywhere: nobody records
   WS book vs REST book disagreement, so the "rare and bounded" gate for the
   switch cannot be evaluated.
2. **Formal reconciliation.** No periodic REST re-fetch cadence, no
   drift-triggered forced resync, no stated disagreement bound.
3. **Reconnect safety proof.** After a gap the local book is stale until a fresh
   `book` snapshot arrives; no test proves a stale book can never be served.
4. **Collector switch.** `scripts/collect_ticks.py` still fetches both REST books
   every round (~184ms of ~269ms per-series cost). This closes the deferred
   proposal once recorded as collector SPEC §7.

## Plan (from the issue; Phase 2 gated on Phase 1 evidence)
- **Phase 1 — shadow comparison, zero behaviour change.** Collector reads the
  socket book alongside the REST book every round and records disagreement:
  per token `abs(ws_best_bid - rest_best_bid)`, `abs(ws_best_ask - rest_best_ask)`,
  level-set difference count, and a diverge boolean. Aggregates (diverge rate,
  p50/p95/max) go to `manifest.json` socket telemetry; per-tick deltas go into
  the tick JSONL itself so the evidence is replayable (adopted improvement, see
  `tasks/plan.md`). REST remains the recorded/quoted source.
- **Phase 2 — switch, gated on the Phase 1 numbers.** Only if disagreement is
  rare and bounded. WS book becomes the quoting source through the shared
  `book_math` interface; REST stays as a periodic reconciliation fetch (much less
  often than every round) that detects drift and forces a resync.
- **Phase 3 — collector.** Same switch in `scripts/collect_ticks.py`: skip the
  per-round REST book fetch when the socket leg is authoritative, removing the
  largest remaining per-series cost.

## Acceptance criteria
1. Phase 1 produces a real measured disagreement rate, published where an
   operator reads it (dashboard/API + manifest), with per-tick evidence in the
   tick files.
2. No switch without those numbers: Phase 2 tasks are blocked on a recorded
   measurement run, not on an assumption.
3. After the switch a reconnect cannot serve a stale book — resync is proved by
   test (book marked stale on reconnect, served again only after a fresh `book`
   snapshot).
4. Book freshness for quoting is bounded by venue publication, not by the poll loop.
5. Backtest and live consume books through the same interface (#170 `book_math`),
   so a recorded tick and a live tick are read by the same code.
6. Tick JSONL stays backward compatible: disagreement fields are additive and
   optional; old replay code ignores them.

## Edge cases
- Dropped/misapplied `price_change` delta → silent wrong book: caught by the
  reconciliation fetch + drift bound, which forces a resync.
- `tick_size_change` mid-window → price grid changes: snapshot refresh on grid
  change, never incremental patching across it.
- Reconnect gap → stale-until-snapshot window: serve REST (or nothing quotable)
  until resync; never the pre-gap book.
- Crossed/degenerate WS book (`best_bid >= best_ask`, one-sided) → fall back to
  REST for that leg; one-sided books priced only via `book_math` rules.
- Socket disconnected at tick time → Phase 1 records "no WS data" (not zero
  disagreement); Phase 2/3 treat as not-authoritative and use REST.

## Explicit out of scope
- Changing `book_math` pricing rules (#170 owns them).
- Changing tape/fill logic (#173 owns it).
- New venue transports or SDK changes (#172 owns transport).
- Dashboard redesign — one read-only disagreement surface only.
- New external dependencies.
