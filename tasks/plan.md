# tasks/plan.md — Issue #109: Aggregate counts summary for the Tick Data Files tab

Branch: `feat/issue-109-ticks-aggregate` (off `master`)
Constraints: `CONSTRAINTS.md` (this issue's gates)
Baseline: 394 tests green.

## Concise spec (spec-driven-development, Standard tier — no SPEC.md change)

**Goal.** The 💾 Tick Data Files tab gets an aggregate rollup card above the
per-file table: total files, total bytes, total lines/samples (sum of the
per-file values, respecting the existing estimate heuristic), total distinct
windows (from cheap cached sources only), per-series × duration tick counts
(10 series: BTC/ETH/BNB/SOL/XRP × 5m/15m), and tape entries total.

**Out of scope.** Collector output format; anything under `run/` (gitignored);
Verify modal redesign; backtest/analysis APIs; per-file table changes.

**Acceptance (from the issue).**
- [ ] Aggregate summary above the file table: totals across files (samples/
      lines, windows, per-series×duration counts, tape entries, total size).
- [ ] Aggregated counts agree with the sum of the per-file values shown.
- [ ] Missing/empty `run/ticks/` renders the empty state without errors.
- [ ] `python -m pytest tests/test_osc_dash_integration.py -q` passes with new
      coverage for the aggregate fields/endpoint.

## Interfaces locked before coding (api-and-interface-design)

1. **API — extend `GET /api/ticks/manifest` additively** (no new endpoint; the
   tab already fetches this on load). New `aggregate` key, existing fields
   untouched (addition-over-modification, Hyrum-safe):

   ```json
   {
     "files": [ ...unchanged... ],
     "manifest": { ...unchanged... },
     "aggregate": {
       "total_files": 3,
       "total_bytes": 793678768,
       "total_lines": 805214,
       "total_lines_estimated": true,
       "total_windows": 2820,
       "windows_source": "cache|none",
       "tape_entries_total": 235,
       "series_counts": {"btc-up-or-down-5m": 120411, "...": 0},
       "series_counts_source": "manifest|verify_cache|scan_cache|none"
     }
   }
   ```

   Semantics:
   - `total_files`, `total_bytes`, `total_lines`: exact sums over the same
     `files[]` entries in the same response (per-file `lines` keeps the
     existing `size/950` estimate for files ≥20 MB; `total_lines_estimated`
     is true iff any contributing file was estimated).
   - `tape_entries_total`: from the collector's `manifest.json` if present,
     else `0` (documented as today's-session total, not historical sum —
     historical tape totals would require a scan and are out of scope).
   - `series_counts`: counts keyed by series slug (e.g.
     `btc-up-or-down-5m`). Sources, in priority order, all cheap:
     a. Cached verify report sidecars: `run/ticks/.verify_cache/<file>.json`
        written by `/api/ticks/verify` runs (T2 adds the write-on-verify).
     b. Collector `manifest.json` `series_seen` (names only, no counts) —
        contributes nothing numeric; used only as a hint that counts exist.
     c. Background-style one-time scan with a TTL cache (module-level dict,
        10-minute expiry) for files with no cached report — initiated on
        request but guarded so at most one scan runs; if no cached data and
        scanning is skipped, `series_counts` is `{}` with
        `series_counts_source: "none"`.
   - `total_windows`: sum of `windows_count` from cached verify reports; if
     any contributing file lacks a cached report, `windows_source` is
     `"partial"` and the number is the partial sum (UI labels it "≥").

2. **Module-level cache contract** (`server/osc_dash.py`):
   ```python
   _VERIFY_CACHE_DIR = TICKS_DIR / ".verify_cache"
   _SCAN_CACHE: dict[str, tuple[float, dict]] = {}   # file -> (expiry_ts, counts)
   _SCAN_TTL_SEC = 600.0
   def _read_verify_cache(path: Path) -> dict | None: ...
   def _aggregate_ticks(files: list[Path], manifest: dict | None) -> dict: ...
   ```

3. **Verify writes cache:** `/api/ticks/verify` (single-file path) persists
   `{series_counts, windows_count, valid_ticks, ts}` to
   `.verify_cache/<file>.json` after a successful run. TTL honored on read;
   stale cache = miss (never served as fresh).

4. **UI:** `loadManifest()` renders a totals card above the table (existing
   dark/gold styling): files, size, samples (with `~` if estimated),
   windows (with `≥` if partial), tape entries, and a per-series × duration
   grid (10 cells, BTC/ETH/BNB/SOL/XRP × 5m/15m) when counts are available;
   card hidden when `total_files === 0`. Empty state unchanged.

## Tasks

### T1 — RED: aggregate endpoint tests
Files: `tests/test_osc_dash_integration.py` (append)
Tests: (a) manifest endpoint on a tmp dir with 2 small jsonl files returns
`aggregate` with `total_files==2`, `total_lines` == sum of per-file lines,
`total_bytes` == sum of sizes; (b) missing dir → `aggregate` present, zeroed,
`series_counts_source=="none"`, no error; (c) `manifest.json` in tmp dir with
`tape_entries_total` surfaces it.
Verify: `python -m pytest tests/test_osc_dash_integration.py -q` — new tests
fail, rest green.

### T2 — GREEN: backend aggregation
Files: `server/osc_dash.py`
- Add `_SCAN_CACHE`, `_SCAN_TTL_SEC`, `_read_verify_cache()`,
  `_aggregate_ticks()` per the locked contract.
- Extend `api_ticks_manifest()` to include `aggregate`.
- Extend `api_ticks_verify()` single-file success path to persist the cache
  sidecar (best-effort; failures never break the verify response).
Verify: T1 tests green; full suite green.

### T3 — RED→GREEN: verify-cache write test
Files: `tests/test_osc_dash_integration.py` (append)
Test: call `/api/ticks/verify?file=<f>` on a tmp tick file, then hit
`/api/ticks/manifest` and assert `series_counts` non-empty with
`series_counts_source=="verify_cache"` and `total_windows` agrees with the
verify report.
Verify: targeted gate green.

### T4 — UI: totals card + per-series grid
Files: `server/osc_dash.py` (FULL_APP_HTML: `loadManifest()` + tab markup)
- Render the aggregate card above `manifestTableWrap` (or as its first child),
  existing styling variables; hidden when no files; `~`/`≥` markers per
  source flags; 10-cell series×duration grid when counts exist.
- Keep `btFileSelect` population logic untouched.
Verify: manual — `python -m uvicorn server.osc_dash:app --port 8802`, open 💾
tab with real `run/ticks/` (3 files, ~794 MB) and with an empty temp dir.

### T5 — Full gate + self-audit
- `python -m pytest -q` green (≥397 tests).
- Constraint re-check: no existing assertions touched; no full scans on the
  request path (code-review + security pass over the diff).
- Update CONSTRAINTS.md §5 with the verification numbers.
