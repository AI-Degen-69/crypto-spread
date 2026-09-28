# SPEC.md — Issue #308: backtest market-series and time-frame selection

Scope: this file is a per-issue working file for #308 only. It goes stale on merge.

## Goals

1. The backtest CLI (`scripts/backtest.py`) accepts `--series` and `--durations`
   filters so the operator can replay e.g. BTC-only or 5-minute-only slices.
2. The dashboard endpoint `/api/backtest` accepts the same `series` /
   `durations` params with identical semantics (CLI/API parity).
3. A full-dataset run exposes a per-duration breakdown (same row shape as
   `per_series`) so time-frame splits are readable without re-running.
4. A filtered run reports pairs-found vs pairs-expected (completeness), derived
   from the golden manifest when adjacent, else the canonical SERIES universe —
   so silently-dropped markets are visible.

## Acceptance criteria (from the issue)

- `python -m scripts.backtest run/ticks/golden --series btc` runs only BTC
  windows; the header reports (series, duration) pairs found vs expected.
- `python -m scripts.backtest run/ticks/golden --durations 300` runs only
  5-minute windows; output includes a per-duration breakdown with `300` totals.
- `--series eth --durations 900` selects exactly the ETH 15-minute windows.
- `/api/backtest?series=btc&durations=300` returns the same filtered totals as
  the CLI on the same file.
- Unfiltered full-dataset run: per-duration windows sum equals overall
  `n_windows` (4,910 on the golden set).
- `python -m pytest tests/test_backtest_engine.py tests/test_backtest_cli.py
  tests/test_osc_dash_integration.py -q` passes with new tests for selection,
  per-duration aggregation, and the completeness count.

## Edge cases

- Empty selection (`--series` omitted / empty string) = all series; same for
  durations. Unfiltered behavior byte-for-byte identical.
- Unknown series token (matches no canonical SERIES slug) → `ValueError` in
  the shared parser → CLI error / API HTTP 400. Never a silent zero-window run.
- Unsupported duration (not in `supported_durations()`) → same error path.
- Valid selection matching zero ticks in the source → zero windows, not an
  error; coverage reports the missing pairs.
- Single-file sources: pair completeness is informational only — midnight
  windows straddle daily files, so a file slice may legitimately miss a pair.
  Pair-count completeness is enforced at directory scope.
- `duration` keys serialize to JSON strings (`"300"`, `"900"`).

## Out of scope

- Engine simulation logic (`_simulate_window`, fill model, fees).
- `BacktestParams` schema and `params_hash` — selection is a dataset filter
  (like `--max-start-delay`), echoed separately, never hashed.
- New chart UI; sweep presets (#307); changes to the golden dataset itself.
- `/api/backtest/sweep` axis behavior.
