# Plan — Issue #308: backtest market-series and time-frame selection

Branch: `i308/backtest-add-market-series-and-time-frame` | Issue: #308
Size: **Standard** — 4 files + tests, one architectural call (filter-before-group + `per_duration` shape).
Type: **Code [Backend/Logic]** — verification via targeted pytest per task.

## Resolved open questions (from code, not asked)

- **Q1 — breakdown shape:** `per_duration` alongside `per_series` (issue
  default). `WindowResult` already carries `duration` (`backtest/engine.py:468`),
  so keying by `w.duration` mirrors the `w.series` path exactly.
- **Q2 — matching semantics:** case-insensitive substring on the tick's
  `series`/`slug` fields (issue text: "matches series or slug substrings").
  Consistent with `BacktestParams.exit_thresh()` substring style
  (`backtest/engine.py:446-450`). Tokens validated against canonical
  `SERIES` slugs; durations against `supported_durations()`
  (`strategy/series.py:52-59`).

## CodeRabbit plan intake (read once, comment + echo deduplicated)

- **Adopted:** shared selection module in `backtest/`; `per_duration` with the
  `per_series` row shape; `ValueError` on unknown token/unsupported duration
  (CLI error / API 400); coverage outside `replay()`; `params_hash` untouched.
- **Rejected:** 6-task over-split — merged into 4 atomic tasks below; no
  manifest coupling inside `replay()`.
- **[UNVERIFIED] at plan time:** exact worker response fields for
  `per_duration`/coverage passthrough in `_run_backtest_simulation_worker`
  (`server/osc_dash.py:1413+`, own aggregation, does not call `replay()`) —
  verify against live worker code during T4.

## Verified architecture facts (plan grounded on these)

- Tick rows carry `series` (e.g. `btc-up-or-down-5m`), `slug`, `duration`
  (300/900), `cid` — verified on `run/ticks/golden/ticks_2026-09-18.jsonl`.
- `replay()` aggregates `per_series` keyed by `w.series`
  (`backtest/engine.py:1296-1440`); CLI uses `replay()` + `group_by_cid`
  (`scripts/backtest.py:120-149`).
- Dashboard worker (`server/osc_dash.py:1413+`) does NOT call `replay()` — it
  runs `_simulate_window` per group with its own aggregation. CLI/API parity
  therefore means both apply the same shared filter.
- Golden manifest: `days[].market_breakdown[]` with
  `(series, duration, windows)`; totals 4,910 windows over 6 days.

## Interface contracts (locked before build)

- `backtest/selection.py` (new):
  `parse_series_tokens(raw) -> tuple[str, ...]`,
  `parse_durations(raw) -> tuple[int, ...]`,
  `apply_selection(snaps, series_tokens, durations) -> list[dict]` (empty
  selection returns input unchanged),
  `build_coverage(selection, found_pairs, source) -> dict` with keys
  `filtered, selection, pairs_found, pairs_expected, missing_pairs,
  windows_found, windows_expected, expected_source`.
- `replay()` signature unchanged; `aggregate` gains `per_duration`
  (`{"300": <same row as per_series>, "900": ...}`), empty map on empty input.
- CLI: `--series` (repeatable/comma-separated), `--durations` (`300,900`);
  header prints selection + pairs found vs expected; per-duration section.
- API: optional `series: str = ""`, `durations: str = ""` query params, same
  semantics; worker receives them as plain strings (picklable); invalid →
  HTTP 400; response echoes `selection` + `coverage`, keeps existing fields.

## Dependency graph

- T1: none. T2: none. T3: T1, T2. T4: T1, T2.
- Order risk-first: T1 → T2 → checkpoint → T3 → T4 → checkpoint.

## Tasks

### T1 [Backend/Logic] M — shared selection + coverage module
Target: `backtest/selection.py` (new), `tests/test_backtest_selection.py` (new).
Build parsers (comma-split, strip, lowercase, drop empties; empty = all),
validation (`ValueError` naming the bad token/value), raw-tick filter
(token in lowercased tick `series`/`slug` AND duration in set; documents that
one `cid` shares series/duration so whole windows drop), golden-manifest
locator (`<dir>/golden_manifest.json`, `<parent>/golden_manifest.json` for
files; `None` when absent/unreadable), expected-pair extractor (manifest
`market_breakdown` with `windows > 0`, file sources restricted to matching
day entry, fallback to canonical SERIES), coverage builder.
Helper skill: test-driven-development. Depends on: none.
Verify: `python -m pytest tests/test_backtest_selection.py -q` (agreement,
best-price divergence n/a — selection cases: empty=all, btc-only, 300-only,
combined, unknown token raises, bad duration raises, zero-match slice,
manifest + canonical coverage).

### T2 [Backend/Logic] S — `per_duration` in `replay()`
Target: `backtest/engine.py` (`replay()` only), `tests/test_backtest_engine.py`.
Factor per-group row accumulation into one routine keyed by a key function;
build `aggregate.per_duration` keyed by `w.duration` with the `per_series`
row shape; `per_series`/`overall`/simulation byte-for-byte unchanged; empty
input → empty map; docstring notes int keys serialize as `"300"`/`"900"`.
Helper skill: test-driven-development. Depends on: none.
Verify: `python -m pytest tests/test_backtest_engine.py -q`.
CHECKPOINT 1: selection + aggregation unit-proven; demo `per_duration` on a
small slice.

### T3 [Backend/Logic] S — CLI flags + reporting
Target: `scripts/backtest.py`, `tests/test_backtest_cli.py`.
Add `--series` / `--durations`, apply shared filter to `snaps` before
`group_by_cid` (alongside the existing start/end + max-start-delay filters),
print selection + pairs found vs expected in the header, add per-duration
section (same columns as per-series), keep `--out` JSON including new fields.
Helper skill: incremental-implementation. Depends on: T1, T2.
Verify: `python -m pytest tests/test_backtest_cli.py -q` + manual golden
spot-checks (`--series btc`, `--durations 300`, combined).

### T4 [Backend/Logic] M — `/api/backtest` params + worker parity
Target: `server/osc_dash.py` (`api_backtest` + `_run_backtest_simulation_worker`),
`tests/test_osc_dash_integration.py`.
Accept `series`/`durations` query params, validate via shared parsers
(invalid → HTTP 400), pass plain strings into the worker, apply the shared
filter to `snaps` before `group_by_cid`, build `per_duration` with the
worker's existing row conventions, echo `selection` + `coverage` in the
response, keep all existing fields.
Helper skill: incremental-implementation. Depends on: T1, T2.
Verify: `python -m pytest tests/test_osc_dash_integration.py -q` + CLI/API
totals parity on the same golden file.
CHECKPOINT 2: end-to-end — filtered CLI run, API parity, full-run
per-duration sum equals `n_windows`.

## Improvement proposal (adopted by default, edge-case hardening)

Single-file pair completeness stays informational only and is enforced at
directory scope, because midnight windows straddle daily files — evidence,
`backtest/engine.py:93-98`: "Group a chronological snap stream by cid, sorted
by ts within each group. Handles midnight windows that straddle two daily
files (Plan D3): cids appear in whatever file they were sampled, then are
re-sorted by ts." A file slice can therefore legitimately miss a pair.
Drop only on explicit operator rejection.
