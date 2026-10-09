# SPEC.md — Issue #440 (per-issue working file, pruned at closeout)

Branch: `i440/featmarket-data-correct-the-book-shadow-divergence` | Issue: #440

## Goal

Make the WS-vs-REST divergence number measure the comparison it actually
performed (gated vs blind populations, magnitude in venue ticks, reference age
first-class, exclusions counted), then re-gate the socket-authoritative switch
on that corrected, pre-registered number. No socket-client hardening: #438
proved the book internally consistent (0 in-frame divergences / 70,869
`price_change` events) and every counted divergence structurally blind.

## Acceptance criteria

- [ ] Every reported rate names gated vs blind and carries its own sample size;
      no single global percentage is emitted as the replay headline (§7.3).
- [ ] Magnitude buckets in venue ticks; middle bucket populable; an exact
      one-tick gap reports as agreement, consistent with the divergence rule
      (PR #469 inconsistency pinned by test).
- [ ] Rate reported by REST reference age; freshness-window exclusions counted
      and reported, never silently dropped.
- [ ] `manifest.json` `book_shadow` block records the freshness bound its rate
      was computed under; carries gated/blind counts + rates and the exclusion
      ledger.
- [ ] Live metric and replay report the same population on one shared capture
      and agree within a stated tolerance — proved by test.
- [ ] `CLOBMarketWSClient` exposes frame kind / declared quotes (additive API;
      `book_snapshot` unchanged) so the live comparison classifies gated/blind.
- [ ] Reading rule pre-registered in `docs/issue-174-socket-book-disagreement.md`
      before the measurement run (tick multiple, rate, sample size, max gap,
      skew correction) — stated on the corrected metric, not a headline rate.
- [ ] Measurement run published against the rule, verdict recorded either way.
- [ ] Flag defaults to off; REST-authoritative path unchanged when off — proved
      by test, not inspection.
- [ ] Quoting freshness bounded by venue publication, not the poll loop —
      demonstrated with a measured latency figure.
- [ ] Parity test green: recorded tick and live tick read through
      `strategy/book_math`.
- [ ] `python -m pytest tests/test_replay_socket_reconciliation.py
      tests/test_clob_ws_collector.py tests/test_osc_dash_integration.py -q`
      passes.
- [ ] Undated-fallback branch deleted (see plan §5): unreachable code provably
      removed, covered by a regression test feeding a token with no REST
      records.

## Edge cases

- Token with zero REST records → excluded `no_snapshot`, never counted.
- REST read older than ±0.5 s → excluded `outside_freshness_window`.
- REST contradicts frame's declared quotes → excluded `guard_reject`.
- `book` / `last_trade_price` frames (no declared quotes) → blind population.
- `price_change` / `best_bid_ask` without declared quotes → blind, not gated.
- Provenance `None` on the live path → excluded `no_provenance`, never blind.
- Exact-threshold float gaps (e.g. `abs(0.55-0.549)` 1 ulp above 0.001) →
      agreement via `DIVERGENCE_EDGE`, bucketed `sub_tick`.
- Dashboard compat: manifest keeps `divergence_rate` redefined as the gated
      rate + `tolerance` until the badge consumes the gated keys; badge shows
      the gated rate by end of issue (no global headline anywhere).
      `comparisons`/`divergent` stay as legacy all-population totals for
      old-manifest readers; the rates are never averaged across populations.

## Out of scope (explicit)

- Socket-client hardening (reconnect invalidation, staleness servability,
  crossed/degenerate detection, `tick_size_change` validation) — no observed
  failure mode (§7.5). Fully-gated divergence gets its own future issue.
- Dashboard `BOOK_SHADOW_TOLERANCE` literal — #470 owns it.
- Tolerance value change outside the pre-registered rule.
- Collector per-series REST cost (Phase 3 of #174).
- Dangling per-issue-file citations — #439 owns them.
