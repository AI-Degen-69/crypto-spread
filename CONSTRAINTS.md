# CONSTRAINTS — Issue #264: Backtest Sweep Visual

## Scope lock
1. Change only the Backtest Sweep Visual endpoint, served UI, and focused tests; do not alter strategy calculations, tick data, other dashboard tabs, or Issue #174 socket-authoritative work.
2. Preserve the existing Chart.js integration and theme-token system; add no external dependencies.
3. Keep the endpoint one-axis only: `queue`, `offset`, `exit_5m`, and `exit_rev`; no joint-grid, random, structural-limit, or scatter features.
4. Preserve the documented response contract: `axis`, ordered `points[]` with numeric `value`, `label`, `overall`, and `per_series`, plus `series_order`, `n_snaps`, and `n_windows`; any added label metadata must remain additive.
5. Preserve the existing DOM contract: `btSweepCard`, `btSweepAxis`, `btnRunSweepVisual`, `btSweepMeta`, `chartSweepAgg`, and `btSweepGrid`.
6. Sweep Visual market titles must use the aligned display format `05m BTC` / `15m BTC` (and the equivalent ETH/BNB/SOL/XRP labels); do not rename machine slugs or unrelated dashboard labels.

## Quality guardrails
7. Targeted gate: `python -m pytest tests/test_osc_dash_integration.py tests/test_theme_tokens.py -q`.
8. New endpoint and rendering behavior requires focused assertions for valid output, validation errors, file safety, busy-guard release, numeric X-axis configuration, one aggregate chart, ten per-market charts, friendly labels, and best-point metadata.
9. Verify the real dataset `run/ticks/ticks_2026-09-18.jsonl` in a browser/UI run if present; record missing-data or zero-fill caveats rather than weakening tests.
10. No skipped/deleted assertions, suppressed failures, broad snapshot-only tests, or unrelated file changes.
11. No new dependencies and no strategy/backtest math changes.

---


## Scope lock
1. **Phase 1 changes nothing behavioural.** REST stays the recorded and quoted
   book source until Phase 2; shadow code only observes and records.
2. **No switch without published numbers.** Phase 2/3 code lands only against a
   recorded disagreement run (diverge rate + distribution); an assumption is not
   a gate.
3. **A reconnect never serves a stale book.** Post-gap book is stale until a
   fresh `book` snapshot; until then the leg reconciles via REST or is unquotable.
4. **Crossed/degenerate WS books fall back.** `best_bid >= best_ask` or a book
   `book_math` cannot price never reaches quoting; that leg uses REST.
5. **Tick schema is append-only.** Disagreement fields are optional additions;
   existing replay, backtest and verify code must read new files untouched.
6. **No new external dependencies** without explicit approval.

## Quality guardrails
7. **Targeted test gate (only suites covering modified files):**
   - `python -m pytest tests/test_ws_book_shadow.py -q` (new: shadow + metrics)
   - `python -m pytest tests/test_clob_ws_collector.py tests/test_streaming.py -q`
   - `python -m pytest tests/test_collect_ticks_smoke.py -q`
   - `python -m pytest tests/test_live_trader.py -q`
   - `python -m pytest tests/test_book_math.py tests/test_backtest_engine.py -q`
   All must pass with zero regressions. Full-suite runs stay with CI on push.
8. **New behaviour requires tests.** Every contract below (metrics, resync,
   fallback, reconciliation skip) ships with a failing-first test; resync-after-
   reconnect and crossed-book fallback are proved by test per the issue.
9. **Anti-cheat:** no skipping/disabling tests, no deleting assertions, no
   linter suppressions to get green.
10. **Performance ceilings:** Phase 1 must not slow the collector round
    (shadow read is a local snapshot; measure `slow_tick` rate before/after);
    Phase 3 must reduce mean per-series book cost vs the ~184ms REST baseline —
    a neutral timing result is a revert, not a keep.
