# CONSTRAINTS — Issue #280: Charter the golden tick dataset for backtesting

## Scope guard
- Docs + name-registry change only. **No code behavior changes**: no edits to
  `scripts/collect_ticks.py`, `scripts/verify_tick_data.py`, `scripts/collector_watchdog.py`,
  `backtest/*`, `server/osc_dash.py`, or `strategy/*`.
- No new dependencies. No new Python modules.
- The charter defines targets; it must NOT change `READINESS_POLICIES` thresholds
  (`scripts/verify_tick_data.py:34-59`) — the quality bar is expressed *in terms of* the
  existing metrics and their existing thresholds, with headroom stated beside them.

## Measurable boundaries
- Every numeric target in `docs/golden-tick-dataset.md` must map 1:1 to a named metric that
  `verify_tick_data.py` already emits (`status`, `capture_state().label`, `readiness.level`,
  `sampling_gap_rate`, `late_starts_count`, `early_cutoffs_count`, `windows_count`,
  `market_breakdown`, `time_blocks`). No invented metrics.
- The replay-speed budget must be grounded in measured baselines
  (`docs/measurements/issue-221-gil-contention.json`: 467MB → 34.07s full-scan; `backtest/index.py`
  sidecar: ~50ms per cid jump vs ~1.5s/day full scan) — not in wishful numbers.
- Achievability: coverage/duration targets must be consistent with measured collector throughput
  (full UTC day ≈ 1.5–1.7GB raw, ~160k snaps; `run/ticks/manifest.json` `sampling_interval_s` ≈ 1.4s).

## Anti-cheat
- No skipping/disabling tests, no deleting assertions, no suppressing linters.
- No editing of existing tests to make the doc "pass".

## Zero regressions
- Targeted gate (per issue acceptance criteria):
  `python -m pytest tests/test_verify_tick_data.py tests/test_collect_ticks_smoke.py -q`
- Full-suite runs stay with CI on push (per AGENTS.md testing policy).
