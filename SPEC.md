# SPEC — Issues #222 + #223: Dead-zone unit & unpaired-leg-at-expiry measurements

## Goal

Two measurement questions, one shared per-window base table, two reports — no engine
changes:

1. **#222 — dead-zone unit.** Should `dead_zone_unit` be `"pct"` (default 10% of window
   remaining) or `"sec"` (absolute seconds)? Answer from data:
   - Distribution of time-to-first-fill and time-to-pair, measured from quote placement,
     split by window length (5m vs 15m).
   - Mid volatility per remaining-time bucket, split by window length.
   - Outcome of windows entered inside each candidate dead zone (paired vs naked leg).
   - Verdict rule: if time-to-pair is roughly constant across window lengths → `sec`
     wins; if the dead tail scales with the window → `pct` stands.

2. **#223 — unpaired leg at expiry.** Should `naked_leg_at_expiry` stay `"close"`
   (default) or become `"hold"`? Answer from data, over legs that reached the dead zone
   unpaired:
   - Bucket by the leg's mid on entering the dead zone (0.05-wide buckets). Realised
     settlement rate per bucket vs the bucket's own price (measures favourite-longshot
     bias directly).
   - Best bid actually available in the dead zone vs the mid (real cost of closing).
   - Realised value of `hold` vs `close` including taker fees, per bucket, with the
     distribution of outcomes (mean, median, std, quartiles, n) — not only the mean.

## Acceptance criteria

1. A single research script (`research/sweeps/dead_zone_lab.py`) builds the per-window
   base table in one pass over the `ev_lab` window cache, reusing:
   - `book_math.resting_bid_filled` — the one fill rule (issue #226, ADR-0002) — never a
     reimplemented variant;
   - the sell-print pre-filter (issue #182) as `sim2` applies it;
   - the sim2 anchoring rule (first valid mid inside `quote_range`, quotes at
     `mid - offset`, `newly_placed` semantics on the placement tick).
2. `research/sweeps/dead_zone_222.json` — fill/pair time distributions by duration,
   volatility profile by remaining time, candidate dead-zone outcome table, and the
   machine-readable verdict with its rationale.
3. `research/sweeps/naked_leg_223.json` — per-bucket settlement rate, bid-vs-mid gap,
   close-vs-hold realised values with fees, and full distribution stats per bucket.
4. `docs/dead-zone-naked-leg-measurements.md` — the report: method, tables, verdicts,
   caveats (dataset span, proxy settlement, sample sizes).
5. `docs/engine-decision-rules.md` §8 and §14 gain the measured verdict in their "open
   question" / "deliberately" passages — cross-referencing the report. **No engine
   default changes unless the data is unambiguous; any default flip is a separate,
   operator-approved change.**
6. A targeted test file (`tests/test_dead_zone_lab.py`) covers the pure logic:
   fill-timeline detection, dead-zone boundary (pct and sec units), bucketing,
   settlement proxy, and close-vs-hold arithmetic — on synthetic windows.
7. Targeted suites stay green: `tests/test_dead_zone_lab.py`,
   `tests/test_ev_sweep_lab.py`, `tests/test_backtest_engine.py` (engine untouched —
   regression guard).

## Edge cases

- `None` mids/books inside a window: skip ticks exactly as `sim2` does; a window whose
  last book is `None` cannot value a leg — record and exclude from #223 with a counter.
- Settlement proxy: captured data ends at window end; the winner is inferred from the
  last two-sided mid (`> 0.5` → up won), the same convention `audit_settlement.py`
  uses. Record the convention in the report; count ambiguous mids (`≈ 0.5`, or missing)
  separately.
- Late-start windows: the dead-zone boundary is measured on remaining time from the
  window's nominal `start_ts`/`duration` (rule 8 semantics), not from first tick.
- Buckets with tiny n: report n per bucket; verdict logic weights only buckets with
  n ≥ 30; smaller buckets are listed but never cited alone.
- Tape-empty days: fills come from the book detector too (ask-through), not only tape;
  windows with no prints at all still contribute via ask-through fills.

## Explicit out-of-scope

- Any change to `backtest/engine.py`, `strategy/live_trader.py`, or engine defaults
  (beyond the §8/§14 doc verdicts).
- Editing `sim2`/`ev_lab` shared code — the lab script consumes them read-only.
- Live-side behaviour, streaming, dashboard changes.
- Re-running stale-sweep numbers (`fill_model`-era reports) — out of scope.
