# Plan — Issue #174, Phase 1 (shadow comparison, no behaviour change)

## Scope for this pipeline run
Phase 1 ONLY: measure WS-vs-REST book disagreement in the collector and publish it.
No provenance switch, no REST removal. Phases 2/3 stay gated on these numbers.

## Design (per the issue's own plan + CodeRabbit coding plan on #174)
1. **Shadow site**: `scripts/collect_ticks.py` — it already fetches both books
   every round (`full_book` REST) and already runs `start_ws_bridge()` with
   `get_book_for_token()` available.
2. **Shadow compare per tick, per token (up/down of every series)**:
   - Pull WS book snapshot via `ws_bridge.get_book_for_token(token)`.
   - Compare against the REST book just fetched: best_bid delta, best_ask delta,
     mid delta, and whether the book diverges at all (delta > tolerance).
   - Accumulate rolling counters in `stats["book_shadow"]`:
     `{comparisons, divergent, abs_bb_sum, abs_ba_sum, abs_mid_sum, max_bb, max_ba, per_token:{...}}`.
   - Tolerance: 0.001 (0.1¢) — half a tick-grid step on most markets; below it
     the two books are the same for pricing purposes.
3. **Publishing**:
   - `manifest.json` already mirrors public `stats` keys → `book_shadow` lands there automatically.
   - Add derived rates (`divergence_rate`, `mean_abs_bb_delta`) rounded to 4 decimals.
4. **Behaviour neutrality**: the comparison is read-only; the REST book stays the
   recorded book. WS failures never touch the run (guarded by try/except, counter only).

## Tests (targeted: tests/test_clob_ws_collector.py + new shadow tests)
- Shadow counters accumulate from two synthetic rounds (equal books → 0 divergence;
  perturbed book → divergent with correct deltas).
- WS snapshot None (not connected) → comparisons not counted, no crash.
- Manifest public keys include `book_shadow` derived rates.
- Neutrality: recorded snap bytes unchanged when shadow enabled vs disabled.

## Acceptance mapping (#174 Phase 1)
- Real measured disagreement rate → `manifest.json` + `stats["book_shadow"]`.
- No behaviour change → recorded book provenance untouched.
