# Plan: Issue #132 — Bridge live tick collector to oscillation overview + auto-rebuild

Task Type: Code + Design
Size Tier: Large
Target Files: `strategy/windows.py` (new), `scripts/rebuild_windows.py`,
  `scripts/collect_ticks.py`, `server/osc_dash.py` (API + HTML/JS),
  `tests/test_windows.py` (new), `tests/test_collect_ticks_smoke.py`,
  `tests/test_osc_dash_integration.py`, `tests/test_rebuild_windows.py`

Decisions locked with user: none yet — requirements fully clear from the
issue (detailed 4-phase plan embedded in #132 comments; `interview-me`
skipped). Classification math (base 0.50, threshold 0.02) frozen.

## Task Breakdown

### Task 1: Shared module `strategy/windows.py` (new)
- **Files**: `strategy/windows.py`
- **Type**: Code
- **Description**:
  1. `classify_window(mids) -> str` — moved UNCHANGED from
     `scripts/rebuild_windows.py` (base 0.50, 0.02 threshold).
  2. `finalize_window(mids, touch_pairs, meta) -> dict` — exact rebuild
     schema (series, label, duration, cid, slug, start_ts, end_ts,
     closed_ts, snaps, start_mid, close_mid, max_up, max_down, min_mid,
     max_mid, class, touch_pair_median, url); 4-decimal rounding;
     `https://polymarket.com/market/{slug}` URL.
  3. `compute_summary(windows) -> dict` — `{"ts", "per_series"}` over
     `strategy.series.SERIES` with zero-fill for empty series.
  4. `write_json_atomic(path, data)` — temp file in same dir + `os.replace`
     (mirror `_finalize_upload` in `server/osc_dash.py:1476`).
  5. Docstring on every function (`test_docstrings.py` gate).
- **Status**: [x]
- **Verification**: `python -c "from strategy.windows import classify_window, finalize_window, compute_summary, write_json_atomic; print(classify_window([0.53,0.47]))"` → `oscillating`

### Task 2: Route offline rebuild through shared module
- **Files**: `scripts/rebuild_windows.py`
- **Type**: Code
- **Description**:
  1. Import `classify_window`, `finalize_window`, `compute_summary` from
     `strategy.windows`; replace local copy + inline finalize block in
     `build_windows_from_ticks` with shared calls.
  2. Preserve `build_windows_from_ticks` / `rebuild_windows` signatures
     and `(num_files, num_windows)` return.
  3. Atomic writes for `oscillation_summary.json` (`write_json_atomic`)
     and `oscillation_windows.jsonl`.
- **Status**: [x]
- **Verification**: `python -m pytest tests/test_rebuild_windows.py -q` (0 failures)

### Task 3: Collector accumulates mids/touch_pairs + closes into dataset
- **Files**: `scripts/collect_ticks.py`
- **Type**: Code
- **Description**:
  1. `windows[cid]` init (`poll_once:211-217`) gains `"mids": []`,
     `"touch_pairs": []`; append per-tick `mid` (`:250`) + `touch_pair`
     (`:251-253`) each poll — append-only, snap schema untouched.
  2. Closure block (`:290-293`): before `del windows[cid]`, call
     `finalize_window` with accumulated lists + window metadata.
  3. New `write_window(rec, out_dir)` helper — one JSON line appended to
     `<out_dir>/oscillation_windows.jsonl` (`write_snap` idiom).
  4. All new I/O in `try/except` → `errs`; skip when mids empty
     (`no_data`); summary refresh (read windows file → `compute_summary`
     → `write_json_atomic` with `ts`) ONLY when ≥1 window closed.
- **Status**: [x]
- **Verification**: `python -m scripts.collect_ticks --once` exits 0; new
  smoke tests (Task 6) green

### Task 4: Dashboard `POST /api/rebuild` + provenance fields
- **Files**: `server/osc_dash.py`
- **Type**: Code
- **Description**:
  1. `POST /api/rebuild` — `_verify_safe_origin` guard; runs
     `python -m scripts.rebuild_windows` via `subprocess.run(cwd=ROOT,
     timeout=60, capture_output=True)`; returns `{ok, output[:500]}`
     (mirror `api_collector_poll_once:995-1007`). Docstring required.
  2. `/api/oscillation` gains provenance: source filename
     (`oscillation_windows.jsonl`), file mtime, total closed-window
     count; best-effort guards, explicit `null` when missing; reuse
     `_load_all_windows` mtime/size cache — no background watcher.
- **Status**: [x]
- **Verification**: `python -m pytest tests/test_osc_dash_integration.py -q` (0 failures)

### Task 5: Dashboard HTML/JS — button, badge, tooltip, live goal bar (Design)
- **Files**: `server/osc_dash.py` (`FULL_APP_HTML`, `#app-hdr`, `tick()`)
- **Type**: Design
- **Description**:
  1. "Rebuild Stats" button in `#app-hdr` (`<button class="btn"
     onclick="...">`, `#btnToggleCollector` pattern) + JS fn POSTing
     `/api/rebuild` then calling `tick()`.
  2. Provenance line in Window Capture Targets bar inside `tick()`:
     source file + last-updated age (min) + total closed count
     (`#collectorBadge` style).
  3. Tooltip on "Start Polling" button (1s ticks+tape → `run/ticks/`,
     windows close into dataset).
  4. Goal bar count reads live-refreshed data (newly closed windows
     increment during polling).
- **Status**: [x]
- **Verification**: HTML-string presence tests (Task 6) + manual:
  `python -m uvicorn server.osc_dash:app --port 8802`, check button/badge

### Task 6: Tests — shared module + collector closure + endpoint/provenance
- **Files**: `tests/test_windows.py` (new), `tests/test_collect_ticks_smoke.py`,
  `tests/test_osc_dash_integration.py`
- **Type**: Code
- **Description**:
  1. `test_windows.py`: classify thresholds (`no_data`/`flat`/
     `monotonic`/`oscillating` per `test_rebuild_windows.py` spec);
     `finalize_window` schema + rounding; `compute_summary` per_series
     shape + zero-fill; `write_json_atomic` valid JSON + replace.
  2. `test_collect_ticks_smoke.py`: `write_window` + summary-refresh via
     `tmp_path`, no network/subprocess; assert +1 JSON line per window,
     summary has `ts` + `per_series`.
  3. `test_osc_dash_integration.py`: mocked `subprocess.run` → `{ok,
     output}` shape; cross-origin rejection; HTML contains button + JS
     fn; `/api/oscillation` returns provenance fields.
- **Status**: [x]
- **Verification**: `python -m pytest tests/test_windows.py tests/test_collect_ticks_smoke.py tests/test_osc_dash_integration.py -q` (0 failures)

### Task 7: Rebuild accuracy over existing ticks (gzip + plain)
- **Files**: `tests/test_rebuild_windows.py`
- **Type**: Code
- **Description**: fixture exercising `.jsonl` AND `.jsonl.gz` tick
  sources; assert records match shared `finalize_window` output and
  `rebuild_windows` returns correct `(num_files, num_windows)`;
  all pre-existing assertions stay green.
- **Status**: [x]
- **Verification**: `python -m pytest tests/test_rebuild_windows.py -q` (0 failures)

### Task 8: Full regression gate
- **Files**: —
- **Type**: Code
- **Description**:
  1. `python -m pytest -q` (entire suite, 0 failures).
  2. `python -m scripts.rebuild_windows --quiet` runs clean on real
     `run/ticks/`.
- **Status**: [x]
- **Verification**: pytest exit 0 + rebuild prints file/window counts
