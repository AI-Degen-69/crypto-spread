# Constraints — Issue #273: Tick Dataset Readiness

## Correctness
- Preserve `backtest.engine.replay()` behavior and all existing tick field semantics.
- Never count raw JSONL row count as a substitute for independent market windows or tape entries.
- A window is identified by `(series, cid)`; repeated snapshots within one window are not independent observations.
- Keep 5m and 15m coverage separate in every readiness calculation.
- Missing, malformed, duplicate, stale, or estimated records must be visible and cannot silently satisfy a stronger readiness level.
- Preserve existing integrity `PASS/WARN/FAIL`; readiness is a separate claim-relative classification.

## Statistical honesty
- Do not present 30, 100, or 200–500 as universal guarantees. The report must label them as project policy thresholds with written rationale and uncertainty limitations.
- Readiness must account for independent windows, tape entries, market/duration coverage, time/regime coverage, observed fill/trade variance, number of parameter trials, and out-of-sample separation.
- A dataset cannot be `RESEARCH_READY` when it has no eligible temporal holdout for a claim that requires out-of-sample validation.
- Any threshold change must update the policy documentation and tests together.

## Performance
- Verification remains streaming and bounded-memory for large JSONL files; do not load a full file into a list.
- A cached report for an unchanged fingerprint must remain fast and deterministic.
- New readiness calculations must not make normal dashboard load rescan every large file synchronously.

## API/UI
- New readiness fields must be machine-readable, versionable, and backwards-compatible with existing manifest/verify fields.
- The UI must show measured values, the selected claim/readiness level, and failed reasons in plain language.
- Do not remove existing integrity details, market breakdown, rescan controls, or explicit Backtest actions.

## Testing and anti-cheat
- Add tests for schema compatibility, mixed durations, sparse/empty tape, duplicate windows, malformed rows, gaps, collector errors, missing markets, temporal coverage, thresholds, and classification.
- Run targeted tests for every modified module; do not skip or weaken existing assertions.
- No new dependency without explicit approval.
- No production/live trading behavior changes.
