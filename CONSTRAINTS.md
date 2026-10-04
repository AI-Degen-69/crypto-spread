# Quality Constraints — Issue #436

## 1. Zero Regressions & Verification Gates
- Targeted test suites matching touched files must pass cleanly:
  - `python -m pytest tests/test_osc_dash_integration.py -k "golden or backtest or tick_file" -q`
- Targeted test run must finish in < 20 seconds.
- Strictly forbidden: running the full test suite locally (`python -m pytest -q`). Local testing is targeted; CI gates regressions on push.

## 2. Anti-Cheat & Code Quality
- No skipping tests (`@pytest.mark.skip`), commenting out assertions, or silencing lint/type errors.
- No fake/dummy mocks in production endpoints (`server/osc_dash.py`).
- Maintain documentation integrity and preserve existing comments unless specifically superseded.

## 3. Structural & Security Invariants
- **Path Traversal Protection**: Directory resolution in `_resolve_tick_file` must remain strictly locked to allow-listed subdirectories (`_TICKS_SUBDIR_ALLOWLIST`). Arbitrary directory traversal or unlisted subfolders must continue to return `("invalid", None)` or `("not_found", None)`.
- **Backward Compatibility**: Existing single-file resolution (`file="ticks_2026-09-13.jsonl"`, `file="golden/ticks_2026-09-13.jsonl"`, `file="pristine/ticks_..."`) must remain completely unbroken.
- **Aggregation Safety**: `_aggregate_ticks()` must not attempt `stat()` or file-fingerprint reads on directory paths.
- **No New Dependencies**: Zero new external runtime or test dependencies.
