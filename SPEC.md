# SPEC.md — Issue #151: Dashboard collector toggle is blind to the external standalone collector

## 1. Goal
The dashboard collector badge and Start/Stop toggle must tell the truth when a
standalone `scripts.collect_ticks` process (started outside the dashboard) is
writing `run/ticks/`, and must refuse to spawn a second writer on the same
daily file.

## 2. Background (observed 2026-09-12)
- `api_collector_status/start/stop` track only the dashboard child
  `_collector_proc` (`server/osc_dash.py:130,854-922`); they know nothing about
  an external process.
- `api_collector_start` runs `[sys.executable, -m, scripts.collect_ticks]`
  with default args → same `run/ticks/ticks_YYYY-MM-DD.jsonl` → interleaved
  JSONL plus manifest races.
- Observed: banner read `Paused / 208,610 ticks` while the file grew every second.

## 3. In Scope
1. External-writer detection: if `run/ticks/manifest.json` `ts` is fresh
   (age ≤ 5s) but no dashboard child exists → status reports `External · live`
   instead of `Paused`.
2. Guard: `POST /api/collector/start` refuses (explicit reason) while an
   external writer is detected; banner documents why.
3. Banner + toggle UI reflects the three states (none / external-only /
   child) and surfaces the refusal reason.
4. Tests: status matrix (no writer / external only / child only) in
   `tests/test_osc_dash_integration.py`.

## 4. Out of Scope
- Changing `collect_ticks` locking/rotation. Merging the two collector modes
  (#148 territory).
- Guarding `/api/collector/poll-once` (proposed as optional improvement —
  needs operator sign-off, see tasks/plan.md Task 5).

## 5. Interfaces (locked before logic)
- `server/osc_dash.py`:
  - `EXTERNAL_COLLECTOR_STALE_SEC = 5.0` — manifest `ts` freshness threshold.
  - `_detect_external_collector(now: float | None = None) -> dict` — reads
    `TICKS_DIR / "manifest.json"`, returns
    `{"live": bool, "manifest_age_sec": float | None}`; `live` is True iff
    `ts` exists and `now - ts <= EXTERNAL_COLLECTOR_STALE_SEC`. Never raises
    (missing/corrupt manifest → `live: False`).
  - `GET /api/collector/status` response gains:
    - `source: "child" | "external" | "none"` — who (if anyone) is writing.
    - `external: bool` — external writer live (for banner logic).
    - `manifest_age_sec: float | None`.
    - Existing keys (`running`, `pid`, `total_ticks_collected`, tape metrics)
    unchanged.
  - `POST /api/collector/start` — while external live and no dashboard child:
    HTTP 409 + `{"ok": False, "error": "external collector live …", "source": "external"}`
    and does NOT spawn a process. Child-running and none states behave as today.
- Frontend (inline JS in `osc_dash.py`):
  - `refreshCollectorStatus()` — `External 🟡 live` badge + `Start blocked (external live)`
    toggle label/disabled state when `source == "external"`.
  - `toggleCollector()` — surfaces the 409 reason in the badge instead of
    silently re-rendering.

## 6. Acceptance Criteria
- [ ] Banner states truth with external collector running.
- [ ] Start is refused with a reason while external writer is live.
- [ ] `python -m pytest tests/test_osc_dash_integration.py -q` green.
- [ ] `python -m pytest -q` green (no regressions).
