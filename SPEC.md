# SPEC.md — Issue #132: Bridge live tick collector to oscillation overview + auto-rebuild

## 1. Goal
Close the pipeline disconnect: `scripts/collect_ticks` window closures must
produce `run/oscillation_windows.jsonl` records and refresh
`run/oscillation_summary.json`, and the dashboard must expose dataset
provenance plus a one-click rebuild — so the Oscillation Overview stops
showing a static 2,940-window dataset while `run/ticks/` grows.

## 2. Background (verified 2026-09-13 in code)
- Collector (`scripts/collect_ticks.py:poll_once:194-294`): polls 10 SERIES,
  writes full-depth snaps via `write_snap`, tracks `windows[cid]` in-memory;
  closure block (`:290-293`) deletes state WITHOUT computing metrics.
  Per-tick `mid` (`:250`) and `touch_pair` (`:251-253`) already computed.
- Offline rebuild (`scripts/rebuild_windows.py`): `classify_window` (base
  0.50, threshold 0.02), `build_windows_from_ticks` (window-record schema),
  `compute_summary` (per_series over `strategy.series.SERIES`, zero-fill),
  `rebuild_windows -> (num_files, num_windows)`. Legacy
  `scripts/measure_5m_oscillation.py` must NOT be modified.
- Dashboard (`server/osc_dash.py`): `/api/oscillation` (`:294-309`) returns
  `{now, summary, windows, live, goals, default_goals}`; `_load_all_windows`
  (`:195-215`) has mtime/size cache; `load_summary` expects
  `{"ts": <number>, "per_series": {...}}`; `poll-once` pattern
  (`:995-1007`, subprocess + `_verify_safe_origin`, `{ok, output[:500]}`);
  header controls `#app-hdr` with `#btnToggleCollector` / `#collectorBadge` /
  `#tapeBadge` (`FULL_APP_HTML`, `:2203-2205`).
- Tests: `tests/test_docstrings.py` requires a docstring on every new
  function/class in non-test modules. Patterns: `tmp_path` direct calls,
  subprocess mocking, HTML-string assertions.

## 3. In Scope
1. `strategy/windows.py` (new): `classify_window` (math UNCHANGED),
   `finalize_window(mids, touch_pairs, meta) -> dict` (exact rebuild schema,
   4-decimal rounding, `https://polymarket.com/market/{slug}` URL),
   `compute_summary(windows) -> dict` (SERIES-iterated, zero-fill),
   `write_json_atomic(path, data)` (temp-file + `os.replace`).
2. `scripts/rebuild_windows.py` imports the shared trio; signatures and
   return values preserved; atomic writes for both outputs.
3. `scripts/collect_ticks.py`: `windows[cid]` gains `"mids": []` +
   `"touch_pairs": []` (append-only); closure calls `finalize_window` +
   new `write_window(rec, out_dir)` helper; summary refresh on closure only;
   ALL new I/O best-effort (`try/except` → `errs`, never aborts poll);
   `no_data` (empty mids) skipped.
4. `server/osc_dash.py`: `POST /api/rebuild` (origin-guarded, rebuild
   subprocess, `{ok, output}` shape); `/api/oscillation` gains provenance
   fields (source filename, mtime, total closed windows; explicit `null`
   when missing); no background watcher.
5. Dashboard HTML/JS: "Rebuild Stats" button (`#app-hdr`, existing btn
   pattern) → POST + `tick()`; provenance line in Window Capture Targets
   bar (badge style); "Start Polling" tooltip; goal bar updates from live data.
6. Tests for all of the above + rebuild accuracy over `.jsonl` and
   `.jsonl.gz` fixtures.

## 4. Out of Scope
- Classification math changes; `measure_5m_oscillation.py` edits; new
  dependencies; background watchers; quoting/risk logic; #146 replay itself.

## 5. Interfaces (locked before logic)
- `strategy.windows.classify_window(mids: list[float]) -> str`
- `strategy.windows.finalize_window(mids, touch_pairs, meta: dict) -> dict`
  (keys: series, label, duration, cid, slug, start_ts, end_ts, closed_ts,
  snaps, start_mid, close_mid, max_up, max_down, min_mid, max_mid, class,
  touch_pair_median, url)
- `strategy.windows.compute_summary(windows: list[dict]) -> dict`
  (`{"ts": float, "per_series": {...}}`)
- `strategy.windows.write_json_atomic(path, data) -> None`
- `scripts.collect_ticks.write_window(rec: dict, out_dir: Path) -> None`
  (appends one JSON line to `<out_dir>/oscillation_windows.jsonl`)
- `POST /api/rebuild` → `{"ok": bool, "output": str[:500]}`
- `/api/oscillation` += `{source: str, source_mtime: float|null,
  total_windows: int}` (field names locked at implementation; tests assert).

## 6. Acceptance Criteria
- [ ] Live `poll_once` closure appends exactly one JSON line per closed
      window and refreshes the summary; poll loop never crashes on I/O error.
- [ ] `python -m scripts.rebuild_windows` output byte-consistent with
      pre-refactor schema (existing `test_rebuild_windows.py` green).
- [ ] Dashboard shows Rebuild button + provenance freshness; new windows
      appear without manual rebuild.
- [ ] `python -m pytest -q` fully green (367+ new tests).
