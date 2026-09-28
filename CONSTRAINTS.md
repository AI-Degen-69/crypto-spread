# Constraints — Issue #303

## Scope boundaries
- Limit changes to the Tick Files screen in `server/osc_dash.py`, its `/api/ticks/manifest` payload mapping, and `tests/test_osc_dash_integration.py`.
- Do not change the collector, verification gates or logic, sidecar acceptance rules, `run/`, the Golden Dataset card, the backtest dropdown, or the out-of-scope raw/pristine split, PRISTINE badge, and delete protection from #295.
- Preserve existing manifest fields and behavior not explicitly changed by #303.

## Data correctness
- Derive split counts only from accepted, fingerprint-matched verify-sidecar `market_breakdown` data: duration 300 for 5m and 900 for 15m.
- Distinguish unknown from zero: no accepted sidecar means `null`; a verified zero count means `0`. Do not infer exact window counts from snapshots or estimated line counts.
- Deduplicate tier copies by basename in the aggregate, following current aggregate behavior. Preserve `windows_source`: `none` means no files and null split totals; `partial` reports the known subtotal (0 when no files have accepted verification data yet); `cache` reports exact complete totals.
- Do not leak stale-policy readiness into the UI. Per-file count data may still be sourced from accepted matching sidecars as allowed by issue discussion/plan, without changing readiness policy handling.

## UI and contract
- Remove only the Tick Files summary cards for Tick Snapshots and Tape Entries; other diagnostic uses elsewhere remain untouched.
- Exact table order: Last Modified, File Name, 5m / 15m, Integrity, Research Readiness, Actions, Size. Keep seven columns and update all related positional assumptions.
- Keep preferred-file badge and existing actions. Unknown per-file counts display `—` with a "Not verified" tooltip; real zeros display `0`.
- Keep changes out of Golden Dataset content and the backtest dropdown.

## Verification
- Add/update focused integration assertions for exact 5m/15m counts, deduplication, partial and unknown coverage, stale policy behavior, and table/card layout.
- Run only `python -m pytest tests/test_osc_dash_integration.py -q` (or specific tests within it) locally. Never skip, weaken, or delete assertions to make tests pass; no full local suite.
- No new external dependencies. No unrelated file changes.
