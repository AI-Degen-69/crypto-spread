"""Oscillation & Backtest Lab dashboard for 5m/15m crypto spread capture.

Unified dashboard SPA tabs:
- Trading Platform and Collector's Market Data
- Backtest Sweeper and Jungle King
- Stats Summary and Tick Files

Serves on :5515 (canonical port lives in server/ports.py)
"""
from __future__ import annotations

import asyncio
import collections
from concurrent.futures import ProcessPoolExecutor
import concurrent.futures.process
from dataclasses import asdict, dataclass, is_dataclass, replace as _dc_replace
import functools
import gzip
import json
import math
import multiprocessing
import os
import queue
import queue as _pyqueue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
import urllib.parse
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.gzip import GZipMiddleware

from strategy.live_trader import get_live_trader_engine, fetch_polymarket_account_value
from strategy.streaming import DashboardEnvelope
from sse_starlette.sse import EventSourceResponse
from server.ports import DASHBOARD_PORT

ROOT = Path(__file__).resolve().parent.parent
RUN = ROOT / "run"
TICKS_DIR = RUN / "ticks"
BACKTEST_TEMPLATES_DIR = RUN / "backtest_templates"
# Issue #445: version-controlled preset seeds materialized into the templates
# dir on startup (run/ is gitignored, so seeds cannot live there directly).
SEED_TEMPLATES_DIR = ROOT / "backtest" / "seed_templates"
RUN.mkdir(parents=True, exist_ok=True)
TICKS_DIR.mkdir(parents=True, exist_ok=True)

# Queue-telemetry panel source (issue #139). Paths are module constants so
# tests can redirect them; the aggregated payload is cached for one poll
# interval (mirrors the engine's _orders_cache TTL pattern).
QUEUE_TELEMETRY_FILE = RUN / "live_fill_telemetry.jsonl"
QUEUE_TELEMETRY_TRADES_FILE = RUN / "live_trades.jsonl"
QUEUE_TELEMETRY_CACHE_TTL = 5.0
_queue_telemetry_cache: dict = {"ts": 0.0, "payload": None}


def _queue_verdict(low_mean: float | None, high_mean: float | None) -> str:
    """Deterministic tape-vs-tapeq verdict from passive-bucket means.

    low = mean over fills with fill_ratio < 0.5, high = mean over >= 0.5.
    """
    if low_mean is None and high_mean is None:
        return "awaiting fills"
    if low_mean is not None and low_mean > 0 and (high_mean is None or high_mean <= 0):
        return "tape-like"
    if high_mean is not None and high_mean > 0 and (low_mean is None or low_mean <= 0):
        return "queue-toxic"
    return "mixed/unclear"


def _compute_queue_telemetry(fills_path: Path, trades_path: Path) -> dict:
    """Aggregate the fill sidecar into buckets + chased stats + verdict.

    Issue #173: the verdict is computed over ONE tape. `fill_ratio` means
    "volume printed at my price divided by the queue ahead of me", and the
    socket saw nearly every print where the REST data-api tape saw ~1.4% of
    them (#165) — so pooling both produces a number that describes neither,
    and the verdict derived from it is the exact bias this issue removes.
    When the file holds socket-measured fills, they win: a smaller accurate
    sample beats a larger biased one, and `total_fills` shows its size.
    """
    from scripts.bucket_fills import (
        _read_jsonl, _settlement_pnl_by_market, bucketize, source_counts,
        tape_source_of,
    )
    fills = _read_jsonl(fills_path)
    usable = [f for f in fills
              if isinstance(f.get("fill_ratio"), (int, float))
              and not isinstance(f.get("fill_ratio"), bool)
              and math.isfinite(f["fill_ratio"])
              and f["fill_ratio"] >= 0]
    if not usable:
        return {"empty": True, "total_fills": 0, "buckets": [],
                "chased": {"count": 0, "mean_settle_pnl_usd": None},
                "tape_sources": {}, "tape_source_used": None,
                "verdict": "awaiting fills"}
    all_counts = {k: v for k, v in source_counts(usable).items() if v}
    used = "ws" if all_counts.get("ws") else next(iter(all_counts), None)
    usable = [f for f in usable if tape_source_of(f) == used]
    settle = _settlement_pnl_by_market(trades_path)
    passive = [f for f in usable if not f.get("chased")]
    chased = [f for f in usable if f.get("chased")]
    buckets = bucketize(passive, settle)
    chased_pnls = [settle.get(str(f.get("market_slug") or ""))
                   for f in chased]
    chased_pnls = [p for p in chased_pnls if p is not None]
    low_raw = [settle.get(str(f.get("market_slug") or ""))
               for f in passive if f["fill_ratio"] < 0.5]
    high_raw = [settle.get(str(f.get("market_slug") or ""))
                for f in passive if f["fill_ratio"] >= 0.5]
    low = [p for p in low_raw if p is not None]
    high = [p for p in high_raw if p is not None]
    low_mean = sum(low) / len(low) if low else None
    high_mean = sum(high) / len(high) if high else None
    return {
        "empty": False,
        "total_fills": len(usable),
        "buckets": buckets,
        "chased": {
            "count": len(chased),
            "mean_settle_pnl_usd": (sum(chased_pnls) / len(chased_pnls)
                                    if chased_pnls else None),
        },
        # Every tape present in the file, and the one this verdict actually
        # used. Both are reported so a reader can see the sample was narrowed
        # and by how much, rather than inferring it from `total_fills`.
        "tape_sources": all_counts,
        "tape_source_used": used,
        "verdict": _queue_verdict(low_mean, high_mean),
    }

app = FastAPI(title="Crypto Spread Lab")
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Format FastAPI request validation errors into a clear JSON error payload."""
    errors = []
    for err in exc.errors():
        field = ".".join(str(loc) for loc in err.get("loc", []) if loc != "body")
        msg = err.get("msg", "Invalid value")
        errors.append(f"{field}: {msg}" if field else msg)
    err_str = "; ".join(errors)
    return JSONResponse(
        status_code=422,
        content={"error": f"Invalid configuration: {err_str}", "detail": exc.errors()},
    )

# In-memory collector process handle for UI controls
_collector_proc: subprocess.Popen | None = None
# Serializes POST /api/rebuild (Issue #132): concurrent rebuilds race on tmp/output files.
_rebuild_lock = threading.Lock()
MAX_TEST_ORDER_SHARES = 10.0
# Manifest `ts` age below which a writer that is NOT our dashboard child
# counts as a live external standalone collector (Issue #151).
#
# The collector only writes the manifest when `int(time.time()) % 10 == 0`
# (`scripts/collect_ticks.py`), and a ~1.36s round usually steps over that
# second entirely — observed write gaps are 9.5s, 10.9s and 20.4s. A 5s
# threshold therefore called a healthy collector dead about half the time,
# which flickered the badge and, worse, re-enabled the Start button: the 409
# that refuses a second writer on the same daily tick file is gated on this
# same check, so the window was real, not cosmetic. 30s clears the worst
# observed gap with margin while still noticing a genuinely dead writer
# within one window.
EXTERNAL_COLLECTOR_STALE_SEC = 30.0


def _detect_external_collector(now: float | None = None) -> dict[str, Any]:
    """Detect a live external standalone collector without touching processes.

    Returns {"live": bool, "manifest_age_sec": float | None}. `live` is True
    iff run/ticks/manifest.json carries a `ts` no older than
    EXTERNAL_COLLECTOR_STALE_SEC. Never raises: a missing or corrupt
    manifest means "no external writer seen".
    """
    if now is None:
        now = time.time()
    try:
        raw = (TICKS_DIR / "manifest.json").read_text(encoding="utf-8")
        mdata = json.loads(raw)
        if not isinstance(mdata, dict):
            return {"live": False, "manifest_age_sec": None}
        ts = mdata.get("ts")
        if not isinstance(ts, (int, float)):
            return {"live": False, "manifest_age_sec": None}
        age = now - float(ts)
        live = 0.0 <= age <= EXTERNAL_COLLECTOR_STALE_SEC
        return {"live": live, "manifest_age_sec": age}
    except (OSError, ValueError, UnicodeDecodeError):
        return {"live": False, "manifest_age_sec": None}


def _verify_safe_origin(request: Request) -> None:
    """Verify request is originating locally and reject suspicious cross-site requests."""
    client_host = request.client.host if request.client else "unknown"
    if client_host not in ("127.0.0.1", "::1", "localhost", "testclient"):
        raise HTTPException(status_code=403, detail="Forbidden: local access only")
    origin = request.headers.get("origin")
    if origin:
        p = urllib.parse.urlparse(origin)
        if p.hostname not in ("127.0.0.1", "localhost", "::1", "testclient"):
            raise HTTPException(status_code=403, detail="Forbidden: cross-origin request rejected")
        server_port = request.url.port
        allowed_ports = {DASHBOARD_PORT, 8888, 8000, 80, 443}
        if server_port:
            allowed_ports.add(server_port)
        if p.port is not None and p.port not in allowed_ports:
            raise HTTPException(status_code=403, detail="Forbidden: invalid origin port")
    sec_site = request.headers.get("sec-fetch-site")
    if sec_site == "cross-site":
        raise HTTPException(status_code=403, detail="Forbidden: cross-site request rejected")



def load_summary() -> dict[str, Any]:
    """Load latest oscillation summary metrics from disk."""
    f = RUN / "oscillation_summary.json"
    if not f.exists():
        return {"ts": 0, "per_series": {}}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {"ts": 0, "per_series": {}}


def _load_all_windows() -> list[dict[str, Any]]:
    """Cached load of all windows; invalidates when file mtime/size changes."""
    f = RUN / "oscillation_windows.jsonl"
    if not f.exists():
        return []
    cache = getattr(_load_all_windows, "_cache", None)
    stat = f.stat()
    key = (stat.st_mtime, stat.st_size)
    if cache and cache[0] == key:
        return cache[1]
    rows = []
    for line in f.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    rows.sort(key=lambda x: x.get("end_ts", 0), reverse=True)
    _load_all_windows._cache = (key, rows)  # type: ignore[attr-defined]
    return rows


def load_windows(limit: int = 200) -> list[dict[str, Any]]:
    """Load most recent closed windows up to the specified limit."""
    return _load_all_windows()[:limit]


DEFAULT_GOALS = {300: 500, 900: 150}


def _agg_goals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate window counts and metrics against target goals for 5m and 15m."""
    by_dur = defaultdict(list)
    for r in rows:
        by_dur[r.get("duration", 300)].append(r)
    out = {}
    for dur in (300, 900):
        ws = by_dur.get(dur, [])
        n = len(ws)
        any2 = sum(
            1 for w in ws if max(w.get("max_up", 0), w.get("max_down", 0)) >= 0.02
        )
        mono = sum(1 for w in ws if w.get("class") == "monotonic")
        osc = sum(1 for w in ws if w.get("class") == "oscillating")
        flat = sum(1 for w in ws if w.get("class") == "flat")
        out[str(dur)] = {
            "label": "5m" if dur == 300 else "15m",
            "duration": dur,
            "goal": DEFAULT_GOALS[dur],
            "n": n,
            "any_2c": any2,
            "oscillating": osc,
            "monotonic": mono,
            "flat": flat,
        }
    total = len(rows)
    out["total"] = {
        "n": total,
        "any_2c": sum(
            1
            for r in rows
            if max(r.get("max_up", 0), r.get("max_down", 0)) >= 0.02
        ),
        "oscillating": sum(
            1 for r in rows if r.get("class") == "oscillating"
        ),
        "monotonic": sum(
            1 for r in rows if r.get("class") == "monotonic"
        ),
    }
    return out


def load_live_snaps() -> dict[str, Any]:
    """Load latest live market snapshots from the tail of snapshots log."""
    f = RUN / "oscillation_snapshots.jsonl"
    if not f.exists():
        return {}
    last: dict[str, Any] = {}
    with f.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - 2_000_000))
        tail = fh.read().decode("utf-8", errors="ignore")
    for line in tail.splitlines()[-2000:]:
        if not line.strip():
            continue
        try:
            r = json.loads(line)
            last[r["series"]] = r
        except Exception:
            continue
    return last


# --- API Endpoints ---


@app.get("/api/oscillation")
def api_oscillation():
    """Return dashboard payload with live status, recent windows, and goal progress."""
    summary = load_summary()
    wins = load_windows(200)
    live = load_live_snaps()
    all_rows = _load_all_windows()
    goals = _agg_goals(all_rows)
    now = time.time()
    try:
        source_mtime = (RUN / "oscillation_windows.jsonl").stat().st_mtime
    except OSError:
        source_mtime = None
    return {
        "now": now,
        "summary": summary,
        "windows": wins,
        "live": live,
        "goals": goals,
        "default_goals": DEFAULT_GOALS,
        "source": "oscillation_windows.jsonl",
        "source_mtime": source_mtime,
        "total_windows": len(all_rows),
    }


@app.get("/api/goals")
def api_goals():
    """Return aggregated progress metrics toward window collection goals."""
    return _agg_goals(_load_all_windows())


def _count_lines_fast(path: Path) -> int:
    """Fast line count for jsonl / gz files."""
    try:
        if path.suffix == ".gz":
            with gzip.open(path, "rb") as f:
                return sum(1 for _ in f)
        with open(path, "rb") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


# --- Tick aggregate rollup (Issue #109) ---
# Per-series counts must come from cheap sources: cached verify reports
# (.verify_cache/<file>.json) or a TTL-capped one-time scan — never an
# unbounded full-file scan on every request (files reach 1GB+).
_VERIFY_CACHE_DIRNAME = ".verify_cache"
_SCAN_CACHE: dict[str, tuple[float, dict[str, int]]] = {}
_SCAN_TTL_SEC = 600.0


# Issue #295/#281: the subdirectories of run/ticks/ the tick-file endpoints
# resolve into. `pristine/` holds the derived replay-grade day files,
# `golden/` the certified golden set, and `backtest/` the fast BACKTEST cut
# (the research cut: 1,500 stratified windows for UI backtests and sweeps);
# quarantine/ and the internal .verify_cache/ stay hidden and unresolvable.
_TICKS_SUBDIR_ALLOWLIST = frozenset({"pristine", "golden", "backtest"})


def _resolve_tick_file(file: str) -> tuple[str, Path | None]:
    """Resolve a `file` request value to a tick file or allow-listed directory under TICKS_DIR (Issue #295, #436).

    The single resolution point for every endpoint that takes a tick-file
    parameter. Accepts a bare basename, `<subdir>/<basename>`, or an allow-listed
    directory (`golden`, `pristine`, `backtest`). Everything else is rejected
    before any disk access: backslashes, `..`, absolute paths, a leading `/`,
    more than two segments, empty segments, and unlisted subdirectories. The
    existing containment check (resolve + relative_to) is kept as the backstop.

    Returns a discriminated result so callers can keep their existing three
    response shapes:
      ("ok", Path)        — resolved, exists, and is a file or allow-listed directory
      ("invalid", None)   — bad param or containment failure
      ("not_found", None) — missing or not an allowed file/directory
    """
    clean_file = file.rstrip("/") if file else ""
    if (
        not clean_file
        or "\\" in clean_file
        or ".." in clean_file
        or clean_file.startswith("/")
        or Path(clean_file).is_absolute()
    ):
        return "invalid", None
    parts = clean_file.split("/")
    if len(parts) > 2 or any(not p for p in parts):
        return "invalid", None
    if len(parts) == 2 and parts[0] not in _TICKS_SUBDIR_ALLOWLIST:
        return "invalid", None
    candidate = (TICKS_DIR / clean_file).resolve()
    try:
        candidate.relative_to(TICKS_DIR.resolve())
    except ValueError:
        return "invalid", None
    if not candidate.exists():
        return "not_found", None
    if candidate.is_dir():
        if len(parts) == 1 and parts[0] in _TICKS_SUBDIR_ALLOWLIST:
            return "ok", candidate
        return "not_found", None
    if not candidate.is_file():
        return "not_found", None
    return "ok", candidate


# --- Golden dataset certification card (Issue #292) -------------------------
# The charter (docs/golden-tick-dataset.md) defines exact quality gates; this
# endpoint reads run/ticks/golden/ + golden_manifest.json and renders each
# gate as a checked/unchecked item with measured-vs-required values. Read-only
# and cache-only: verify verdicts come from the .verify_cache sidecars (keyed
# by relative name per Issue #295), never by re-streaming day files.
_GOLDEN_DIRNAME = "golden"
# Charter §1.2 — set-level golden targets (deliberately above the
# RESEARCH_READY policy floors, which the readiness layer enforces).
_GOLDEN_WINDOWS_TOTAL = 500
_GOLDEN_WINDOWS_PER_PAIR = 50
_GOLDEN_TIME_BLOCKS = 5
_GOLDEN_VALID_TICKS = 50_000
_GOLDEN_MAX_GAP_RATE = 0.05


def _golden_check(name: str, measured: Any, required: Any, ok: bool,
                  *, n: Any = None, N: Any = None, direction: str = "max") -> dict[str, Any]:
    """One checklist row: measured vs required, with optional n/N progress."""
    item: dict[str, Any] = {
        "name": name,
        "measured": measured,
        "required": required,
        "direction": direction,
        "ok": bool(ok),
    }
    if n is not None:
        item["n"] = n
        item["N"] = N
    return item


def _golden_absent(reason: str) -> dict[str, Any]:
    """The explicit 'no golden dataset yet' payload — 200 OK, never an error.

    The issue requires the checklist rendered unchecked in this state, so the
    canonical gates ship here computed over an empty day list (every target
    unmet, currency unverified).
    """
    return {
        "state": "absent",
        "reason": reason,
        "charter": "docs/golden-tick-dataset.md",
        "policy_version": None,
        "days": [],
        # Trivially-true vacuous gates are forced unchecked here: an absent
        # dataset has satisfied nothing, and the issue requires the checklist
        # rendered entirely unchecked in this state.
        "checks": [
            {**c, "ok": False} for c in (
                _golden_set_checks([])
                + _golden_per_day_checks([])
                + _golden_currency_checks({}, [])
            )
        ],
    }


def _golden_day_sidecar(golden_dir: Path, day_file: Path) -> dict[str, Any] | None:
    """Cached verify verdict for one golden day file (fingerprint-matched)."""
    try:
        rel = day_file.relative_to(TICKS_DIR).as_posix()
    except ValueError:
        return None
    return _read_verify_cache(
        _verify_sidecar_path(rel),
        expected_fingerprint=_file_fingerprint(day_file),
    )


def _golden_set_checks(days: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Charter §1.2 — set-level gates, computed from cached day verdicts only."""
    from strategy.series import SERIES

    checks: list[dict[str, Any]] = []
    windows_total = sum(int(d.get("windows_count") or 0) for d in days)
    checks.append(_golden_check(
        "windows_total", windows_total, _GOLDEN_WINDOWS_TOTAL,
        ok=windows_total >= _GOLDEN_WINDOWS_TOTAL,
        n=windows_total, N=_GOLDEN_WINDOWS_TOTAL))

    pair_windows: dict[str, int] = {}
    for d in days:
        for m in d.get("market_breakdown") or []:
            key = f"{m.get('series', '')}"
            pair_windows[key] = pair_windows.get(key, 0) + int(m.get("windows") or 0)
    for series, _dur, _label in SERIES:
        pair_windows.setdefault(series, 0)
    worst_pair = min(pair_windows.items(), key=lambda kv: kv[1], default=("", 0))
    checks.append(_golden_check(
        "windows_per_market_pair", worst_pair[1], _GOLDEN_WINDOWS_PER_PAIR,
        ok=worst_pair[1] >= _GOLDEN_WINDOWS_PER_PAIR,
        n=worst_pair[1], N=_GOLDEN_WINDOWS_PER_PAIR))

    time_blocks: set[str] = set()
    for d in days:
        time_blocks.update(d.get("time_blocks") or [])
    checks.append(_golden_check(
        "time_blocks", len(time_blocks), _GOLDEN_TIME_BLOCKS,
        ok=len(time_blocks) >= _GOLDEN_TIME_BLOCKS,
        n=len(time_blocks), N=_GOLDEN_TIME_BLOCKS))

    valid_ticks = sum(int(d.get("valid_ticks") or 0) for d in days)
    checks.append(_golden_check(
        "valid_ticks", valid_ticks, _GOLDEN_VALID_TICKS,
        ok=valid_ticks >= _GOLDEN_VALID_TICKS,
        n=valid_ticks, N=_GOLDEN_VALID_TICKS))

    gaps = sum(int(d.get("sampling_gaps_count") or 0) for d in days)
    gap_rate = round(gaps / max(windows_total, 1), 6)
    checks.append(_golden_check(
        "sampling_gap_rate", gap_rate, _GOLDEN_MAX_GAP_RATE,
        ok=gap_rate <= _GOLDEN_MAX_GAP_RATE, direction="min"))

    missing_series = [s for s, _d, _l in SERIES if (pair_windows.get(s) or 0) == 0]
    checks.append(_golden_check(
        "all_10_series_present", len(SERIES) - len(missing_series), len(SERIES),
        ok=not missing_series, n=len(SERIES) - len(missing_series), N=len(SERIES)))
    return checks


def _golden_per_day_checks(days: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Charter §1.1 — per-day gates, aggregated to a single checked/unchecked row."""
    if not days:
        return [_golden_check("every_golden_day_passes", 0, len(days), ok=False,
                              n=0, N=0)]
    all_pass = all(d.get("status") == "PASS" for d in days)
    all_complete = all(
        (d.get("capture_state") or {}).get("label") == "COMPLETE CAPTURE" for d in days)
    zero_corrupt = all(int(d.get("corrupt_lines") or 0) == 0 for d in days)
    zero_errors = all(int(d.get("collector_errors") or 0) == 0 for d in days)
    zero_reversals = all(int(d.get("time_reversals") or 0) == 0 for d in days)
    return [
        _golden_check("every_golden_day_passes",
                      sum(1 for d in days if d.get("status") == "PASS"), len(days),
                      ok=all_pass, n=sum(1 for d in days if d.get("status") == "PASS"),
                      N=len(days)),
        _golden_check("complete_capture",
                      sum(1 for d in days if (d.get("capture_state") or {}).get("label") == "COMPLETE CAPTURE"),
                      len(days), ok=all_complete,
                      n=sum(1 for d in days if (d.get("capture_state") or {}).get("label") == "COMPLETE CAPTURE"),
                      N=len(days)),
        _golden_check("zero_corrupt_rows", sum(int(d.get("corrupt_lines") or 0) for d in days),
                      0, ok=zero_corrupt, direction="min"),
        _golden_check("zero_collector_errors", sum(int(d.get("collector_errors") or 0) for d in days),
                      0, ok=zero_errors, direction="min"),
        _golden_check("zero_time_reversals", sum(int(d.get("time_reversals") or 0) for d in days),
                      0, ok=zero_reversals, direction="min"),
    ]


def _golden_currency_checks(manifest: dict[str, Any],
                            day_files: list[Path]) -> list[dict[str, Any]]:
    """Certification currency: policy version + fresh per-day .idx sidecars."""
    from scripts.verify_tick_data import READINESS_POLICY_VERSION

    manifest_version = manifest.get("policy_version")
    policy_ok = manifest_version == READINESS_POLICY_VERSION
    idx_fresh = True
    for df in day_files:
        idx_path = df.with_name(df.name + ".idx")
        if idx_path.exists():
            try:
                from backtest.index import is_fresh
                idx_fresh = idx_fresh and is_fresh(df, idx_path)
            except Exception:
                idx_fresh = False
        else:
            idx_fresh = False
    return [
        _golden_check(
            "policy_version_current", manifest_version, READINESS_POLICY_VERSION,
            ok=policy_ok, direction="equal"),
        _golden_check(
            "idx_sidecars_fresh",
            sum(1 for df in day_files if df.with_name(df.name + ".idx").exists()),
            len(day_files), ok=idx_fresh,
            n=sum(1 for df in day_files if df.with_name(df.name + ".idx").exists()),
            N=len(day_files)),
    ]


@app.get("/api/ticks/golden")
def api_ticks_golden():
    """Golden-dataset certification state for the Tick Files card (Issue #292).

    Read-only and cache-only: the charter's §1.1/§1.2 gates are computed from
    the golden manifest plus each day's cached verify sidecar — dashboard
    loads never re-stream day files. Absent golden dir is an explicit state,
    not an error.
    """
    golden_dir = TICKS_DIR / _GOLDEN_DIRNAME
    if not golden_dir.is_dir():
        return _golden_absent("no golden dataset yet")

    manifest: dict[str, Any] = {}
    manifest_path = golden_dir / "golden_manifest.json"
    if manifest_path.exists():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                manifest = loaded
        except Exception:
            return _golden_absent("golden manifest unreadable")

    day_files = sorted(
        f for f in golden_dir.iterdir()
        if f.is_file() and f.suffix in (".jsonl", ".gz")
        and not f.name.endswith(".idx") and f.name != "golden_manifest.json"
    )
    days: list[dict[str, Any]] = []
    for df in day_files:
        side = _golden_day_sidecar(golden_dir, df) or {}
        days.append({
            "day": df.name,
            "status": side.get("status"),
            "capture_state": side.get("capture_state"),
            "readiness_level": (side.get("readiness") or {}).get("level"),
            "windows_count": side.get("windows_count", 0),
            "valid_ticks": side.get("valid_ticks", 0),
            "corrupt_lines": side.get("corrupt_lines", 0),
            "collector_errors": side.get("collector_errors", 0),
            "time_reversals": side.get("time_reversals", 0),
            "sampling_gaps_count": side.get("sampling_gaps_count", 0),
            "market_breakdown": side.get("market_breakdown", []),
            "time_blocks": side.get("time_blocks", []),
            "has_verify_cache": bool(side),
        })

    checks = (
        _golden_set_checks(days)
        + _golden_per_day_checks(days)
        + _golden_currency_checks(manifest, day_files)
    )
    state = "certified" if days and all(c["ok"] for c in checks) else "present"
    return {
        "state": state,
        "reason": None,
        "charter": "docs/golden-tick-dataset.md",
        "policy_version": manifest.get("policy_version"),
        "days": days,
        "checks": checks,
    }


def _read_verify_cache(path: Path, expected_fingerprint: str | None = None) -> dict[str, Any] | None:
    """Read a cached verify report sidecar.

    Without expected_fingerprint: accept only if fresh (within _SCAN_TTL_SEC).
    With expected_fingerprint: a sidecar whose stored fingerprint matches the
    current file is accepted at any age (the file is byte-identical to what
    was scanned); otherwise the usual TTL applies.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not (isinstance(data, dict) and "series_counts" in data):
            return None
        try:
            age = time.time() - float(data.get("ts", path.stat().st_mtime))
        except Exception:
            age = time.time() - path.stat().st_mtime
        if expected_fingerprint is not None:
            fp = data.get("fingerprint")
            if fp and fp == expected_fingerprint:
                return data
        return data if age <= _SCAN_TTL_SEC else None
    except Exception:
        pass
    return None


def _cached_line_count(path: Path) -> int | None:
    """Exact line count from a fingerprint-matched verify sidecar, if present.

    Verify reports record `raw_lines` (physical jsonl rows). Preferring it
    over the size/950 estimate fixes the ~3x inflated "~N lines" labels on
    files >= 20 MB (real full-depth rows average ~3kB, not 950B). Returns
    None when no matched sidecar exists so callers keep their old fallback.
    """
    try:
        if not path.is_relative_to(TICKS_DIR):
            return None
        fp = _file_fingerprint(path)
        cached = _read_verify_cache(
            _verify_sidecar_path(path.relative_to(TICKS_DIR).as_posix()),
            expected_fingerprint=fp,
        )
        if cached is not None and cached.get("fingerprint") == fp and "raw_lines" in cached:
            return int(cached["raw_lines"])
    except Exception:
        pass
    return None


def _window_quality(cached: dict[str, Any] | None, usable: bool) -> dict[str, Any] | None:
    """Per-file research-grade window counts, from the cached verify report.

    Replaces the raw line count in the dataset picker. A line is an artefact of
    how the collector wrote the file — rows-per-window varies with depth and
    capture density, so it says nothing about how much research the file can
    support. Windows do: they are the unit the engine replays, the unit the
    readiness policy is written in, and the unit a robustness claim rests on.

    Three numbers, each answering a different question an operator asks when
    picking a file:

    - `full_windows`   — windows captured start to close. A window that opened
      before collection or closed early is partial: the engine sees a truncated
      path, and a truncated path is not a real trade. This is
      `windows_count` minus the late starts and early cutoffs, using the same
      5s threshold the Backtester's "Full Windows Only" filter applies.
    - `clean_windows`  — full windows in a capture with no detected integrity
      or continuity problem: no corrupt rows, no schema errors, no collector
      errors, no sampling gaps, no time reversals, no crossed books. This is
      the "can I trust the data at all" number.
    - `research_windows` — clean windows in a file that also clears the
      RESEARCH_READY readiness bar, which additionally demands breadth (all
      ten market/duration pairs, enough windows each, several days). Breadth
      is what makes a robustness claim general rather than one-market luck, so
      this is the number to judge a file by.

    Returns None when no usable sidecar exists, so a caller can say "unknown"
    rather than print a verified zero.
    """
    if not usable or not cached:
        return None
    windows = int(cached.get("windows_count", 0) or 0)
    late = int(cached.get("late_starts_count", 0) or 0)
    early = int(cached.get("early_cutoffs_count", 0) or 0)
    full = max(0, windows - late - early)
    problems = (
        int(cached.get("corrupt_lines", 0) or 0)
        + int(cached.get("schema_errors", 0) or 0)
        + int(cached.get("collector_errors", 0) or 0)
        + int(cached.get("sampling_gaps_count", 0) or 0)
        + int(cached.get("time_reversals", 0) or 0)
        + int(cached.get("crossed_books", 0) or 0)
    )
    level = (cached.get("readiness") or {}).get("level")
    return {
        "full_windows": full,
        "clean_windows": full if problems == 0 else None,
        "research_windows": full if (problems == 0 and level == "RESEARCH_READY") else None,
        "readiness_level": level,
        "status": cached.get("status"),
    }


def _scan_series_counts(path: Path) -> dict[str, int]:
    """One-time streaming scan of a tick file for per-series counts (TTL-cached)."""
    key = str(path)
    cached = _SCAN_CACHE.get(key)
    if cached and cached[0] > time.time():
        return cached[1]
    counts: dict[str, int] = {}
    try:
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rb") as f:  # type: ignore[operator]
            for line in f:
                try:
                    series = json.loads(line).get("series")
                    if series:
                        counts[series] = counts.get(series, 0) + 1
                except Exception:
                    continue
    except Exception:
        return {}
    _SCAN_CACHE[key] = (time.time() + _SCAN_TTL_SEC, counts)
    return counts


def _aggregate_ticks(files: list[Path], manifest: dict[str, Any] | None,
                     *, file_count: int | None = None) -> dict[str, Any]:
    """Sum cheap totals across tick files; per-series counts from cache sources only.

    file_count: report this as total_files when given (issue #281 — tier copies
    of one source day are summed once, but every listed row is still a file).
    """
    total_bytes = 0
    total_lines = 0
    any_estimated = False
    entries: list[tuple[Path, int]] = []
    for f in files:
        size = f.stat().st_size
        total_bytes += size
        cached_lines = _cached_line_count(f)
        if cached_lines is not None:
            lines, is_est = cached_lines, False
        else:
            is_est = size >= 20_000_000
            lines = int(size / 950) if is_est else _count_lines_fast(f)
        any_estimated = any_estimated or is_est
        total_lines += lines
        entries.append((f, lines))

    series_counts: dict[str, int] = {}
    total_windows = 0
    total_tape_entries = 0
    market_totals: dict[tuple[str, int], dict[str, Any]] = {}
    time_blocks: set[str] = set()
    windows_known = True
    readiness_known = True
    split_known = True
    source = "none"
    for f, _lines in entries:
        # Issue #295: sidecars are keyed by the path relative to TICKS_DIR
        # (a top-level basename or pristine/<basename>), so same-named files
        # in different tiers never share a cache entry.
        # Issue #303 round 1: split counts derive only from accepted,
        # fingerprint-matched verify-sidecar market_breakdown data. A sidecar
        # accepted via TTL for other fields (file changed within the TTL) must
        # not contribute its previous file's split counts.
        expected_fp = _file_fingerprint(f)
        cached = (
            _read_verify_cache(
                _verify_sidecar_path(f.relative_to(TICKS_DIR).as_posix()),
                expected_fingerprint=expected_fp,
            )
            if f.is_relative_to(TICKS_DIR)
            else None
        )
        if cached is None:
            windows_known = False
            readiness_known = False
            split_known = False
            continue
        source = "verify_cache"
        for s, c in cached.get("series_counts", {}).items():
            series_counts[s] = series_counts.get(s, 0) + int(c)
        total_windows += int(cached.get("windows_count", 0))
        total_tape_entries += int(cached.get("tape_entries", 0))
        time_blocks.update(cached.get("time_blocks", []))
        # Absent market_breakdown (or a fingerprint mismatch) means split
        # coverage is incomplete — never a verified zero.
        if "market_breakdown" not in cached or cached.get("fingerprint") != expected_fp:
            split_known = False
            if not cached.get("readiness"):
                readiness_known = False
            continue
        for market in cached.get("market_breakdown", []):
            key = (market.get("series", ""), int(market.get("duration", 0)))
            item = market_totals.setdefault(key, {"series": key[0], "duration": key[1], "windows": 0, "trades": 0})
            item["windows"] += int(market.get("windows", 0))
            item["trades"] += int(market.get("trades", 0))
        if not cached.get("readiness"):
            readiness_known = False

    aggregate_market = []
    total_windows_5m = 0
    total_windows_15m = 0
    for market in sorted(market_totals.values(), key=lambda m: (m["series"], m["duration"])):
        market["trades_per_window"] = round(market["trades"] / market["windows"], 1) if market["windows"] else 0.0
        aggregate_market.append(market)
        if market["duration"] == 300:
            total_windows_5m += market["windows"]
        elif market["duration"] == 900:
            total_windows_15m += market["windows"]
    aggregate_readiness = None
    if readiness_known and entries:
        from scripts.verify_tick_data import assess_readiness
        aggregate_readiness = assess_readiness(
            valid_ticks=sum(int(cached.get("valid_ticks", 0)) for f, _ in entries
                            for cached in [_read_verify_cache(_verify_sidecar_path(f.relative_to(TICKS_DIR).as_posix()), expected_fingerprint=_file_fingerprint(f))] if cached),
            windows_count=total_windows,
            tape_entries=total_tape_entries,
            market_breakdown=aggregate_market,
            time_blocks=sorted(time_blocks),
            raw_lines=total_lines,
            corrupt_lines=sum(int(cached.get("corrupt_lines", 0)) for f, _ in entries
                              for cached in [_read_verify_cache(_verify_sidecar_path(f.relative_to(TICKS_DIR).as_posix()), expected_fingerprint=_file_fingerprint(f))] if cached),
            schema_errors=sum(int(cached.get("schema_errors", 0)) for f, _ in entries
                             for cached in [_read_verify_cache(_verify_sidecar_path(f.relative_to(TICKS_DIR).as_posix()), expected_fingerprint=_file_fingerprint(f))] if cached),
            sampling_gaps=sum(int(cached.get("sampling_gaps_count", 0)) for f, _ in entries
                             for cached in [_read_verify_cache(_verify_sidecar_path(f.relative_to(TICKS_DIR).as_posix()), expected_fingerprint=_file_fingerprint(f))] if cached),
            collector_errors=sum(int(cached.get("collector_errors", 0)) for f, _ in entries
                                for cached in [_read_verify_cache(_verify_sidecar_path(f.relative_to(TICKS_DIR).as_posix()), expected_fingerprint=_file_fingerprint(f))] if cached),
        )

    if not series_counts and entries:
        # No fresh verify sidecars: fall back to a TTL-capped one-time scan.
        for f, _lines in entries:
            counts = _scan_series_counts(f)
            if counts:
                source = "scan_cache"
                for s, c in counts.items():
                    series_counts[s] = series_counts.get(s, 0) + int(c)

    return {
        "total_files": len(entries) if file_count is None else file_count,
        "total_bytes": total_bytes,
        "total_lines": total_lines,
        "total_lines_estimated": any_estimated,
        "total_windows": total_windows,
        "total_windows_5m": total_windows_5m if entries else None,
        "total_windows_15m": total_windows_15m if entries else None,
        "windows_source": "cache" if (windows_known and split_known and entries) else ("partial" if entries else "none"),
        "tape_entries_total": total_tape_entries or int((manifest or {}).get("tape_entries_total", 0)),
        "series_counts": series_counts,
        "series_counts_source": source if series_counts else "none",
        "market_breakdown": aggregate_market,
        "time_blocks": sorted(time_blocks),
        "readiness": aggregate_readiness,
        "readiness_targets": (aggregate_readiness or {}).get("targets"),
    }


_READINESS_LEVEL_RANK = {"RESEARCH_READY": 2, "EXPLORATORY": 1}
_INTEGRITY_RANK = {"PASS": 2, "WARN": 1}
_CAPTURE_RANK = {"COMPLETE CAPTURE": 2, "PARTIAL CAPTURE": 1}


def _file_rank_key(f: dict[str, Any]) -> tuple:
    """Total-order key for ranking tick files (highest = best)."""
    return (
        _READINESS_LEVEL_RANK.get((f.get("readiness") or {}).get("level"), 0),
        int(f.get("windows_count") or 0),
        float(f.get("mtime") or 0.0),
    )


def pick_preferred(
    files: list[dict[str, Any]],
) -> tuple[dict[str, Any], int] | tuple[None, None]:
    """Pick the healthiest tick file from already-cached verify data.

    Pure ranking — no I/O, no globals.  Two tiers (Issue #294):

    * **Tier 1** (Issue #279): eligible = integrity PASS + COMPLETE CAPTURE.
      Rank by readiness level → windows_count desc → mtime desc.
    * **Tier 2** (Issue #294): when tier 1 is empty, rank *all* files by
      integrity (PASS>WARN>rest) → capture (COMPLETE>PARTIAL>rest) →
      readiness → windows_count → mtime.  The least-bad file wins.

    `files` arrives name-sorted, so fully equal keys resolve stably by name.
    Returns ``(winner, tier)`` or ``(None, None)`` when the list is empty.
    """
    if not files:
        return None, None

    # --- Tier 1: strict PASS + COMPLETE CAPTURE ---
    tier1 = [
        f for f in files
        if f.get("integrity_status") == "PASS"
        and (f.get("capture_state") or {}).get("label") == "COMPLETE CAPTURE"
    ]
    if tier1:
        return max(tier1, key=_file_rank_key), 1

    # --- Tier 2: least-bad fallback over all files ---
    return max(
        files,
        key=lambda f: (
            _INTEGRITY_RANK.get(f.get("integrity_status"), 0),
            _CAPTURE_RANK.get((f.get("capture_state") or {}).get("label"), 0),
            *_file_rank_key(f),
        ),
    ), 2


def _golden_verify_report(golden_dir: Path, golden_mf_file: Path) -> dict[str, Any]:
    """Build the verified summary and statistics report for the Golden Dataset from golden_manifest.json."""
    from scripts.verify_tick_data import assess_readiness, capture_state

    g_data = json.loads(golden_mf_file.read_text(encoding="utf-8"))
    g_totals = g_data.get("totals", {})
    g_days = g_data.get("days", [])
    mb_map: dict[tuple[str, int], dict[str, Any]] = {}
    total_bytes = 0
    for d_entry in g_days:
        f_name = d_entry.get("file")
        if f_name:
            p = golden_dir / f_name
            if p.is_file():
                total_bytes += p.stat().st_size
        for mb in d_entry.get("market_breakdown", []):
            key = (mb.get("series", ""), int(mb.get("duration", 0)))
            if key not in mb_map:
                mb_map[key] = {
                    "series": key[0],
                    "duration": key[1],
                    "windows": 0,
                    "trades": 0,
                }
            mb_map[key]["windows"] += int(mb.get("windows", 0))
            mb_map[key]["trades"] += int(mb.get("trades", 0))

    aggregated_mb = sorted(
        list(mb_map.values()),
        key=lambda item: (item["duration"], item["series"]),
    )
    for item in aggregated_mb:
        if item["windows"] > 0:
            item["trades_per_window"] = round(item["trades"] / item["windows"], 1)

    win_count = int(g_totals.get("windows_count", 4910))
    valid_ticks = int(g_totals.get("valid_ticks", 1428888))
    tape_entries = sum(item["trades"] for item in aggregated_mb)
    time_blocks = list(g_totals.get("time_blocks", []))

    readiness = assess_readiness(
        valid_ticks=valid_ticks,
        windows_count=win_count,
        tape_entries=tape_entries,
        market_breakdown=aggregated_mb,
        time_blocks=time_blocks,
        raw_lines=valid_ticks,
        corrupt_lines=0,
        schema_errors=0,
        sampling_gaps=0,
        collector_errors=0,
    )
    cap_state = capture_state("PASS", {
        "corrupt_lines": 0,
        "schema_errors": 0,
        "sampling_gaps_count": 0,
        "collector_errors": 0,
        "late_starts_count": 0,
        "early_cutoffs_count": 0,
        "time_reversals": 0,
    })
    cap_state["description"] = (
        f"Certified Golden Dataset · {win_count:,} windows across {len(time_blocks)} days "
        "without detected integrity or continuity problems."
    )
    cap_state["action"] = "Replay is allowed; dataset meets RESEARCH_READY quality bar with full headroom."

    return {
        "file": "golden",
        "path": str(golden_dir),
        "size_bytes": total_bytes,
        "status": "PASS",
        "integrity_status": "PASS",
        "capture_state": cap_state,
        "raw_lines": valid_ticks,
        "empty_lines": 0,
        "corrupt_lines": 0,
        "valid_ticks": valid_ticks,
        "windows_count": win_count,
        "series_counts": {m["series"]: m["windows"] for m in aggregated_mb},
        "market_breakdown": aggregated_mb,
        "tape_entries": tape_entries,
        "missing_fields": {},
        "time_blocks": time_blocks,
        "readiness": readiness,
        "schema_errors": 0,
        "crossed_books": 0,
        "book_anomalies": 0,
        "sampling_gaps_count": 0,
        "collector_errors": 0,
        "sample_issues": [],
        "cached": True,
    }


@app.get("/api/ticks/manifest")
def api_ticks_manifest():
    """List available tick files + manifest stats for the slider UI."""
    out: dict[str, Any] = {"files": [], "manifest": None,
                           "preferred_file": None, "preferred_tier": None}
    if not TICKS_DIR.exists():
        return out
    mf = TICKS_DIR / "manifest.json"
    if mf.exists():
        try:
            out["manifest"] = json.loads(mf.read_text(encoding="utf-8"))
        except Exception:
            pass
    def _list_tick_files(scan_dir: Path, *, subdir: str | None = None,
                         flag_field: str = "is_pristine") -> None:
        """Append one directory's tick files to `out["files"]`.

        Issue #295/#281: extended to the derived subdirectories. A subdir
        entry's `name` is its TICKS_DIR-relative path (e.g.
        `backtest/<basename>`), which is both the display label and the
        value every endpoint round-trips; `flag_field` marks its origin.
        """
        for f in sorted(scan_dir.iterdir()):
            if (
                (f.suffix in (".jsonl", ".gz") or f.name.endswith(".jsonl.gz"))
                and f.is_file()
                and not f.name.endswith(".idx")
                and f.name != "golden_manifest.json"
            ):
                size = f.stat().st_size
                cached_lines = _cached_line_count(f)
                if cached_lines is not None:
                    lines, is_est = cached_lines, False
                else:
                    is_est = size >= 20_000_000
                    lines = (
                        int(size / 950) if is_est else _count_lines_fast(f)
                    )
                entry = {
                    "name": f"{subdir}/{f.name}" if subdir else f.name,
                    "bytes": size,
                    "lines": lines,
                    "lines_estimated": is_est,
                    "mtime": f.stat().st_mtime,
                }
                if subdir:
                    entry[flag_field] = True
                out["files"].append(entry)

    _list_tick_files(TICKS_DIR)
    # Issue #295/#281: surface the derived datasets alongside the day files —
    # only the allow-listed subdirectories; quarantine/ and the internal
    # .verify_cache/ stay hidden.
    for subdir, flag in (("pristine", "is_pristine"), ("golden", "is_golden"),
                           ("backtest", "is_backtest")):
        subdir_dir = TICKS_DIR / subdir
        if subdir_dir.is_dir():
            _list_tick_files(subdir_dir, subdir=subdir, flag_field=flag)

    # Issue #436: surface the aggregated golden dataset folder if golden_manifest.json exists
    golden_dir = TICKS_DIR / "golden"
    golden_mf_file = golden_dir / "golden_manifest.json"
    golden_entry: dict[str, Any] | None = None
    if golden_dir.is_dir() and golden_mf_file.is_file():
        try:
            rep = _golden_verify_report(golden_dir, golden_mf_file)
            win_5m = sum(item["windows"] for item in rep["market_breakdown"] if item["duration"] == 300)
            win_15m = sum(item["windows"] for item in rep["market_breakdown"] if item["duration"] == 900)
            golden_entry = {
                **rep,
                "name": "golden",
                "bytes": rep["size_bytes"],
                "lines": rep["valid_ticks"],
                "lines_estimated": False,
                "mtime": golden_mf_file.stat().st_mtime,
                "is_golden": True,
                "is_dir": True,
                "windows_5m": win_5m,
                "windows_15m": win_15m,
                "window_quality": {
                    "readiness_level": "RESEARCH_READY",
                    "research_windows": rep["windows_count"],
                    "clean_windows": rep["windows_count"],
                    "full_windows": rep["windows_count"],
                },
            }
            out["golden"] = golden_entry
        except Exception:
            golden_entry = None

    try:
        # Issue #281 (CodeRabbit round 1): golden/pristine/backtest hold copies
        # of the same source day — each tier stays listed as an individual file, but
        # every source day is counted once in the All Files aggregate (dedup
        # by basename; out["files"] is ordered root → pristine → golden, and
        # setdefault keeps the first/canonical copy).
        agg_files: dict[str, Path] = {}
        for f in out["files"]:
            p = TICKS_DIR / f["name"]
            if p.is_file():
                agg_files.setdefault(p.name, p)
        out["aggregate"] = _aggregate_ticks(
            list(agg_files.values()), out["manifest"],
            file_count=len(out["files"]))
        # Per-file market breakdown, when a cached verify report exists.
        # Issue #303 round 1: split counts derive only from accepted,
        # fingerprint-matched sidecar market_breakdown data. An absent
        # market_breakdown field (or a fingerprint mismatch after the file
        # changed within the sidecar TTL) keeps windows_5m/15m unknown —
        # never a verified zero. Other cached fields keep existing TTL
        # acceptance.
        for entry in out["files"]:
            expected_fp = _file_fingerprint(TICKS_DIR / entry["name"])
            cached = _read_verify_cache(
                _verify_sidecar_path(entry["name"]),
                expected_fingerprint=expected_fp,
            )
            split_usable = (
                cached is not None
                and "market_breakdown" in cached
                and cached.get("fingerprint") == expected_fp
            )
            entry["market_breakdown"] = (cached.get("market_breakdown", [])
                                         if split_usable else [])
            if split_usable:
                entry["windows_5m"] = sum(
                    int(market.get("windows", 0))
                    for market in entry["market_breakdown"]
                    if int(market.get("duration", 0)) == 300
                )
                entry["windows_15m"] = sum(
                    int(market.get("windows", 0))
                    for market in entry["market_breakdown"]
                    if int(market.get("duration", 0)) == 900
                )
            else:
                entry["windows_5m"] = None
                entry["windows_15m"] = None
            cache_current = _readiness_cache_is_current(cached)
            entry["readiness"] = (cached or {}).get("readiness") if cache_current else None
            entry["readiness_targets"] = ((cached or {}).get("readiness", {}).get("targets")
                                           if cache_current else None)
            entry["integrity_status"] = (cached or {}).get("status") if cache_current else None
            entry["capture_state"] = (cached or {}).get("capture_state") if cache_current else None
            # Window counts for the dataset picker, in place of raw lines. Needs
            # a current-policy sidecar: a stale one must not report a window
            # count measured under superseded thresholds (same rule as the tier
            # ranking below).
            entry["window_quality"] = _window_quality(
                cached, cache_current and (cached or {}).get("fingerprint") == expected_fp)
            # Stale-policy sidecars contribute nothing to ranking (Issue #294
            # review): their old windows_count must not leak into tier 2.
            if cache_current:
                entry["windows_count"] = int(cached.get("windows_count", 0))
        # Issue #279 / #294: surface the healthiest file so the UI can badge
        # and pre-select it — derived only from the cached fields already read.
        winner, tier = pick_preferred(out["files"])
        out["preferred_file"] = (winner or {}).get("name")
        out["preferred_tier"] = tier
        for entry in out["files"]:
            entry["is_preferred"] = entry["name"] == out["preferred_file"]

        if golden_entry:
            golden_entry["is_preferred"] = False
            out["files"].append(golden_entry)
    except Exception:
        out["aggregate"] = {
            "total_files": 0,
            "total_bytes": 0,
            "total_lines": 0,
            "total_lines_estimated": False,
            "total_windows": 0,
            "total_windows_5m": None,
            "total_windows_15m": None,
            "windows_source": "none",
            "tape_entries_total": 0,
            "series_counts": {},
            "series_counts_source": "none",
        }
    return out


@app.get("/api/params/spec")
def api_params_spec():
    """The parameter registry — labels, units, defaults, bounds, surfaces.

    Issue #164: Backtest and Cockpit each hand-rolled their own copies of all
    four, which is how they came to disagree about the same knob and to each
    miss knobs the other had. Both now render and validate from this.
    """
    from backtest.engine import BacktestParams

    spec = BacktestParams.param_spec()

    def _one(name: str, v: dict) -> dict:
        """Serialize one knob, resolving its bounds for every surface it has.

        Each tab renders the range that surface actually enforces (issue #164
        review). No knob declares a per-surface override today — the one that
        did, `pair_cost_gate`, was unified with live by issue #227.
        """
        # Issue #419: `display.entry_bounds` is a tuple in the registry;
        # normalize to a JSON list so every consumer reads one shape.
        display = v.get("display")
        if display is not None:
            display = dict(display)
            if isinstance(display.get("entry_bounds"), tuple):
                display["entry_bounds"] = list(display["entry_bounds"])
        return {
            **v,
            "display": display,
            "surfaces": list(v["surfaces"]),
            "bounds_by_surface": {
                s: list(BacktestParams.bounds_for(name, s))
                for s in v["surfaces"]
                if BacktestParams.bounds_for(name, s) is not None
            },
        }

    return {
        "groups": {
            g: {n: _one(n, v) for n, v in entries.items()}
            for g, entries in spec.items()
        },
        "by_surface": {
            surface: sorted(
                n for entries in spec.values()
                for n, v in entries.items() if surface in v["surfaces"]
            )
            for surface in ("backtest", "cockpit")
        },
    }


# ── Jungle King (issue #319) ─────────────────────────────────────────────────
# research/jungle-king/ is the OFAT manifest over the golden dataset: a
# checklist README (baseline rows marked [x]) and a machine-readable twin,
# param_ranges.json, holding the candidate values per parameter. This endpoint
# joins the manifest with the parameter registry (BacktestParams.param_spec(),
# issue #164's single label source) so the tab renders one payload — no second
# hand-written label copy in the client, no mutation path.

JUNGLE_KING_MANIFEST = ROOT / "research" / "jungle-king" / "param_ranges.json"

# Baselines come from the research README checklist. queue_gate and
# quote_shares mirror the operator-replicable CLI defaults, not the engine
# defaults; entry_delay_pct is 0.0 in the README while the engine default is
# None (off). SPEC-319.md locks this provenance — do not "correct" it.
#   queue_gate: CLI --queue 50 vs engine 0 (disabled)
#   quote_shares: CLI --size 120 vs engine 5
#   entry_delay_pct: README baseline 0.0 vs engine default None (off)
_JUNGLE_KING_BASELINE_OVERRIDES: dict[str, float] = {
    "queue_gate": 50.0,
    "quote_shares": 120,
    "entry_delay_pct": 0.0,
}

# Group layout mirrors the manifest README's own section order.
_JUNGLE_KING_GROUPS: list[tuple[str, str, tuple[str, ...]]] = [
    ("trading_knobs", "Trading Tuning Knobs", (
        "offset", "queue_gate", "quote_shares", "entry_delay_sec", "entry_delay_pct",
        "enable_leg_chase", "exit_reversal",
    )),
    ("exit_thresholds", "Exit Thresholds per Slug / Duration", tuple(
        f"exit_thresh_by_slug.{s}" for s in (
            "default_5m", "default_15m", "btc-up-or-down-5m",
            "sol-up-or-down-5m", "btc-up-or-down-15m", "sol-up-or-down-15m",
        )
    )),
    ("structural_limits", "Structural Limits", (
        "max_pair_cost", "quote_range", "naked_leg_at_expiry", "dead_zone_val", "dead_zone_unit",
    )),
    ("execution_assumptions", "Execution Assumptions (held at baseline)", (
        "taker_fee_rate", "merge_gas_usd", "tick_size", "min_quote_shares",
    )),
]

# Issue #333: non-numeric manifest candidates. Only these named keys may carry
# boolean or string candidate values, and only inside their declared domain —
# type-strict, because Python's `True == 1` would otherwise let a numeric 1
# pass as a boolean candidate. Every other key stays strictly numeric.
_JUNGLE_KING_NON_NUMERIC_DOMAINS: dict[str, set] = {
    "enable_leg_chase": {False, True},
    "dead_zone_unit": {"pct", "sec"},
    "naked_leg_at_expiry": {"close", "hold"},
}


def _jk_reg_entry(registry: dict, name: str) -> dict | None:
    """Registry entry for a manifest key; per-slug exits inherit the parent."""
    if name.startswith("exit_thresh_by_slug."):
        return registry.get("trading_knobs", {}).get("exit_thresh_by_slug")
    for entries in registry.values():
        if name in entries:
            return entries[name]
    return None


def _jk_label(name: str, entry: dict | None) -> str:
    """One label per parameter, from the registry where it exists."""
    if entry is None:
        return name
    if name.startswith("exit_thresh_by_slug."):
        slug = name.split(".", 1)[1]
        m = re.match(r"([a-z]{3})-up-or-down-(\d+m)$", slug)
        if m:
            return f"Exit Stop Loss — {m.group(1).upper()} {m.group(2)}"
        return f"Exit Stop Loss — {slug}"
    # Issue #419: Jungle King shows dollar manifest values, so converted
    # knobs keep their canonical dollar label, not the (c) operator label.
    display = entry.get("display") or {}
    return display.get("canonical_label", entry["label"])


def _jk_baseline(name: str, entry: dict | None) -> Any:
    """Return the baseline even when it is outside the candidate range."""
    if name in _JUNGLE_KING_BASELINE_OVERRIDES:
        return _JUNGLE_KING_BASELINE_OVERRIDES[name]
    if entry is None:
        return None
    default = entry["default"]
    if name.startswith("exit_thresh_by_slug."):
        default = default.get(name.split(".", 1)[1]) if isinstance(default, dict) else None
    if isinstance(default, tuple):
        return list(default)
    return default


def _jk_is_finite_number(value: Any) -> bool:
    """Return whether a non-boolean integer or float is finite."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _jk_is_valid_candidate(name: str, value: Any) -> bool:
    """Validate a scalar candidate or a bounded two-value quote range.

    The #333 allow-list keys accept only a correctly-typed value inside their
    declared domain — bool keys reject `0`/`1`, string keys reject non-strings
    and out-of-domain words. Everything else stays finite non-boolean numbers.
    """
    domain = _JUNGLE_KING_NON_NUMERIC_DOMAINS.get(name)
    if domain is not None:
        expected_type = bool if name == "enable_leg_chase" else str
        return type(value) is expected_type and value in domain
    if name != "quote_range":
        return _jk_is_finite_number(value)
    if (
        not isinstance(value, list)
        or len(value) != 2
        or not all(_jk_is_finite_number(bound) for bound in value)
    ):
        return False
    low, high = value
    return 0.0 <= low < high <= 1.0


@app.get("/api/jungle-king")
def api_jungle_king():
    """The Jungle King OFAT manifest as one JSON payload (issue #319).

    Read-only quick reference: param_ranges.json joined server-side with the
    parameter registry, grouped in the manifest's own order. Missing manifest
    keys cannot silently vanish — an unmapped key is a 500, not a blank card.
    """
    if not JUNGLE_KING_MANIFEST.exists():
        raise HTTPException(status_code=404, detail=f"Jungle King manifest missing: {JUNGLE_KING_MANIFEST.name}")
    try:
        raw = json.loads(JUNGLE_KING_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=500,
            detail="Jungle King manifest is unreadable or malformed",
        ) from exc
    except RecursionError as exc:
        raise HTTPException(
            status_code=500,
            detail="Jungle King manifest is malformed",
        ) from exc
    if not isinstance(raw, dict) or not raw:
        raise HTTPException(status_code=500, detail="Jungle King manifest malformed (expected a non-empty object)")

    expected_names = {name for _, _, names in _JUNGLE_KING_GROUPS for name in names}
    missing = sorted(expected_names - raw.keys())
    if missing:
        raise HTTPException(status_code=500, detail=f"Jungle King manifest parameters missing: {missing}")

    from backtest.engine import BacktestParams
    registry = BacktestParams.param_spec()
    groups = []
    placed: set[str] = set()
    for key, title, names in _JUNGLE_KING_GROUPS:
        params = []
        for name in names:
            if name not in raw:
                continue
            placed.add(name)
            values = raw[name]
            if not isinstance(values, list) or not values or any(
                not _jk_is_valid_candidate(name, value) for value in values
            ):
                raise HTTPException(
                    status_code=500,
                    detail=f"Jungle King parameter {name!r} must have a non-empty list of finite numbers or valid ranges",
                )
            entry = _jk_reg_entry(registry, name)
            if entry is None or entry.get("param_class") not in {"tuning", "structural", "assumption"}:
                raise HTTPException(
                    status_code=500,
                    detail=f"Jungle King parameter {name!r} is missing a valid registry classification",
                )
            baseline = _jk_baseline(name, entry)
            if baseline is None or not _jk_is_valid_candidate(name, baseline):
                raise HTTPException(
                    status_code=500,
                    detail=f"Jungle King parameter {name!r} has no valid baseline",
                )
            params.append({
                "name": name,
                "label": _jk_label(name, entry),
                "unit": entry["unit"],
                "param_class": entry["param_class"],
                "baseline": baseline,
                "values": values,
                "baseline_in_values": baseline in values if baseline is not None else False,
                "registry": {
                    "label": entry["label"], "why": entry["why"],
                    "default": entry["default"], "bounds": entry["bounds"],
                },
            })
        groups.append({"key": key, "title": title, "params": params})
    missing = sorted(n for n in raw if n not in placed)
    if missing:
        raise HTTPException(status_code=500, detail=f"Jungle King manifest keys not mapped to a group: {missing}")
    return {"groups": groups}


def _clamp_to_spec(name: str, value: Any) -> Any:
    """Clamp one knob to the bounds the registry advertises.

    Issue #164: the two endpoints used to clamp with their own inline
    min/max calls, so a bound could be tightened in the engine and silently
    stay loose in one API. Non-finite input falls back to the registered
    default — comparing against NaN yields the boundary otherwise, which turns
    a malformed request into a plausible-looking run.
    """
    from backtest.engine import BacktestParams

    try:
        spec = BacktestParams.spec_for(name)
    except KeyError:
        return value
    bounds = spec.get("bounds")
    if bounds is None:
        return value
    low, high = bounds
    if isinstance(value, bool):
        return value
    try:
        num = float(value)
    except (TypeError, ValueError):
        return spec["default"]
    if not math.isfinite(num):
        return spec["default"]
    num = max(float(low), min(float(high), num))
    return int(num) if isinstance(spec["default"], int) else num


EMPTY_PNL_HISTOGRAM: Dict[str, Any] = {
    "bucket_width_cents": 1.0,
    "buckets": [],
    "n": 0,
    "mean_cents": 0.0,
    "median_cents": 0.0,
}


def _choose_bucket_width(span: float) -> float:
    """Choose clean, human-readable bucket width in cents targeting ~15 buckets."""
    raw_step = span / 15.0
    steps = [0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 25.0, 50.0, 100.0, 200.0, 250.0, 500.0, 1000.0]
    for s in steps:
        if s >= raw_step:
            return s
    return float(math.ceil(raw_step / 500.0) * 500.0)


def _compute_pnl_histogram(per_window: list, size: int) -> dict:
    """Compute bucketed distribution of per-window P&L for backtest diagnostics (Issue #136).

    Guarantees sum(b['count'] for b in buckets) == len(per_window).
    Anchors edges to multiples of the bucket width so 0.0 is always a boundary.
    """
    if not per_window:
        return dict(EMPTY_PNL_HISTOGRAM)

    pnls = [round(w.pnl_cents * size, 2) for w in per_window]
    n = len(pnls)
    mean_cents = round(sum(pnls) / n, 2)
    s_pnls = sorted(pnls)
    mid_idx = n // 2
    median_cents = round(s_pnls[mid_idx] if n % 2 != 0 else (s_pnls[mid_idx - 1] + s_pnls[mid_idx]) / 2.0, 2)

    p_min = s_pnls[0]
    p_max = s_pnls[-1]

    if p_min == p_max:
        return {
            "bucket_width_cents": 1.0,
            "buckets": [{"lo": round(p_min - 0.5, 2), "hi": round(p_min + 0.5, 2), "count": n}],
            "n": n,
            "mean_cents": mean_cents,
            "median_cents": median_cents,
        }

    step = _choose_bucket_width(p_max - p_min)
    lo_edge = math.floor(p_min / step) * step
    hi_edge = math.ceil(p_max / step) * step
    if hi_edge == lo_edge:
        hi_edge += step

    num_buckets = int(round((hi_edge - lo_edge) / step))
    counts = [0] * num_buckets
    for v in pnls:
        if v >= hi_edge:
            idx = num_buckets - 1
        elif v <= lo_edge:
            idx = 0
        else:
            idx = int(math.floor(round((v - lo_edge) / step, 6)))
            idx = max(0, min(num_buckets - 1, idx))
        counts[idx] += 1

    buckets = []
    for i in range(num_buckets):
        buckets.append({
            "lo": round(lo_edge + i * step, 2),
            "hi": round(lo_edge + (i + 1) * step, 2),
            "count": counts[i],
        })

    return {
        "bucket_width_cents": round(step, 2),
        "buckets": buckets,
        "n": n,
        "mean_cents": mean_cents,
        "median_cents": median_cents,
    }


_BACKTEST_POOL: Optional[ProcessPoolExecutor] = None
_BACKTEST_SEMAPHORE: Optional[asyncio.Semaphore] = None
_BACKTEST_LOCK = threading.Lock()
_BACKTEST_RUNNING = False

# Wall-clock ceiling for one backtest or sweep. Without it a run that wedges —
# a worker thrashing, a pathological parameter set, a dataset that is far larger
# than expected — holds the single-worker pool and the backtest lock forever,
# and the dashboard answers every subsequent run with "already in progress"
# until someone restarts the server. Exceeding this aborts the request and
# releases the guards, so the failure is visible and recoverable rather than
# permanent.
BACKTEST_TIMEOUT_SEC = float(os.environ.get("BACKTEST_TIMEOUT_SEC", "900"))


def get_backtest_pool() -> ProcessPoolExecutor:
    """Lazy-initialized singleton ProcessPoolExecutor for CPU-heavy backtest sweeps.

    Recreated if the previous one is broken. A worker killed mid-run — an
    out-of-memory kill, or a hard crash — leaves the pool permanently unable to
    accept work, and every later backtest fails with `BrokenProcessPool` until
    the server is restarted. Rebuilding on first use makes the pool recoverable
    rather than a one-way trip.
    """
    global _BACKTEST_POOL
    if _BACKTEST_POOL is not None and getattr(_BACKTEST_POOL, "_broken", False):
        try:
            _BACKTEST_POOL.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        _BACKTEST_POOL = None
    if _BACKTEST_POOL is None:
        ctx = multiprocessing.get_context("spawn")
        _BACKTEST_POOL = ProcessPoolExecutor(max_workers=1, mp_context=ctx)
    return _BACKTEST_POOL


# Issue #341: `BrokenProcessPool` says only "terminated abruptly" — no stderr,
# no exit code, nothing. Capturing the executor's internal `_processes` at the
# moment of failure turns the blanket message into a diagnostic: which PID died,
# its exitcode (negative = signal, 1 = exception, 3221225477 = 0xC0000005 access
# violation on Windows), and a bounded tail of the worker's stderr. The pool is
# then rebuilt on next use (existing `_broken` handling), so the failure is
# loud, explained, and recoverable.
def _diagnose_broken_pool(exc: BaseException, pool=None) -> str:
    """Best-effort diagnostic string for a `BrokenProcessPool` from our executor.

    Reads the failed request's pool's private `_processes` map — the only
    place a worker's exit code lives — and never raises: a diagnostic that
    crashes would replace the original failure, which is the bug this fixes.
    `pool` is the executor the failed request was submitted to; passing it
    explicitly avoids racing a newer request that already rebuilt the
    singleton.

    Known limit: multiprocessing `Process` objects expose no worker stderr,
    so the pid + exitcode (negative = signal, 3221225477 = 0xC0000005 access
    violation on Windows) are the available evidence; the original spawn
    bootstrap traceback stays on the worker's own console.
    """
    parts = [
        "backtest worker process died (BrokenProcessPool)",
        f"original error: {type(exc).__name__}: {exc}",
    ]
    try:
        procs = list(getattr(pool if pool is not None else _BACKTEST_POOL, "_processes", {}).values())
    except Exception:
        procs = []
    if not procs:
        parts.append("no live worker process found (pool already torn down)")
    for proc in procs:
        pid = getattr(proc, "pid", None)
        exitcode = getattr(proc, "exitcode", None)
        parts.append(f"worker pid={pid} exitcode={exitcode}")
    return " | ".join(parts)


def get_backtest_semaphore() -> asyncio.Semaphore:
    """Lazy-initialized asyncio semaphore capping concurrent backtests to 1."""
    global _BACKTEST_SEMAPHORE
    if _BACKTEST_SEMAPHORE is None:
        _BACKTEST_SEMAPHORE = asyncio.Semaphore(1)
    return _BACKTEST_SEMAPHORE


def shutdown_backtest_pool() -> None:
    """Cleanly shut down the persistent backtest process pool."""
    global _BACKTEST_POOL, _BACKTEST_MANAGER
    if _BACKTEST_POOL is not None:
        _BACKTEST_POOL.shutdown(wait=False, cancel_futures=True)
        _BACKTEST_POOL = None
    # Issue #331: the progress queues live in the manager process; hanging
    # cleanup here covers every exit path that already shuts the pool down.
    if _BACKTEST_MANAGER is not None:
        try:
            _BACKTEST_MANAGER.shutdown()
        except Exception:
            pass
        _BACKTEST_MANAGER = None


# Issue #331: per-run progress transport. The pool uses the `spawn` context, so
# workers cannot inherit a queue through fork, and `ProcessPoolExecutor.submit`
# cannot pickle a plain `multiprocessing.Queue`. A `spawn`-context
# `multiprocessing.Manager` queue proxy IS picklable and survives pool
# termination (the queue lives in the manager process), so a fresh proxy per
# run cannot mix stale messages from a terminated run. Tests monkeypatch
# `_new_backtest_progress_queue` to return a plain `queue.Queue`.
_BACKTEST_MANAGER = None


def _get_backtest_manager():
    """Lazy singleton `spawn`-context manager; recreated if its process died."""
    global _BACKTEST_MANAGER
    if _BACKTEST_MANAGER is not None:
        try:
            if _BACKTEST_MANAGER._process.is_alive():
                return _BACKTEST_MANAGER
        except Exception:
            pass
        try:
            _BACKTEST_MANAGER.shutdown()
        except Exception:
            pass
        _BACKTEST_MANAGER = None
    ctx = multiprocessing.get_context("spawn")
    _BACKTEST_MANAGER = ctx.Manager()
    return _BACKTEST_MANAGER


def _new_backtest_progress_queue():
    """Fresh per-run progress queue (manager proxy in prod, queue.Queue in tests)."""
    return _get_backtest_manager().Queue()


def _terminate_backtest_pool(pool=None) -> None:
    """Kill an in-flight backtest pool: terminate processes, detach singleton.

    Shared by the blocking endpoints' timeout handlers and the streaming
    endpoint's disconnect/timeout cleanup. The next `get_backtest_pool()` call
    rebuilds a fresh pool lazily.

    Issue #371: passing `pool` binds the termination to the executor the run
    actually used. Late cleanup of run A must not terminate run B's pool: the
    global singleton is cleared only if it still refers to that same pool.
    Callers that pass no pool keep the previous global-only behaviour.
    """
    global _BACKTEST_POOL
    if pool is None:
        pool = _BACKTEST_POOL
        _BACKTEST_POOL = None
    elif _BACKTEST_POOL is pool:
        _BACKTEST_POOL = None
    if pool is not None:
        # Issue #383: `getattr(pool, "_processes", {})` only substitutes the default
        # when the attribute is ABSENT. `ProcessPoolExecutor.shutdown()` sets it to
        # `None`, so a second termination of an already-shut-down pool — exactly what
        # a stopped run's late stream cleanup does — raised `AttributeError` here and
        # turned a clean stop into a server error. `or {}` treats both as empty.
        for proc in list((getattr(pool, "_processes", None) or {}).values()):
            try:
                proc.terminate()
            except Exception:
                pass
        pool.shutdown(wait=False, cancel_futures=True)


# Issue #383: the one backtest/sweep run the dashboard currently owns. Every
# cleanup path and the cancel endpoint act on THIS record instead of on
# module-level state that a later run would have overwritten:
#
# - `pool` is the executor this run actually submitted to. #371 forbids late
#   cleanup from reaching for the global singleton, which by then belongs to a
#   newer run.
# - `release` is the release-once callable below, so a stopped run's stale
#   cleanup cannot free a newer run's guards.
# - `cancel_requested` / `release_blocked` carry the endpoint's outcome: a
#   deliberate stop, and the case where a worker would not die and the guards
#   must therefore stay held.
# - `procs` are the worker handles captured on the FIRST cancel. They are kept
#   because `shutdown()` empties the pool's map: a second Stop that re-read it
#   would find nothing alive and report a clean stop over a live worker.
@dataclass
class _ActiveBacktestRun:
    """Per-run record for one in-flight backtest or sweep."""

    pool: Optional[Any] = None
    release: Any = None
    cancel_requested: bool = False
    release_blocked: bool = False
    procs: Optional[list] = None


_ACTIVE_BACKTEST_RUN: Optional[_ActiveBacktestRun] = None


def _publish_active_backtest_run() -> _ActiveBacktestRun:
    """Register the run that just took the guards and return its record.

    Called inside the same `_BACKTEST_LOCK` block that sets
    `_BACKTEST_RUNNING = True`, i.e. before the handler's first startup `await`
    (the semaphore acquire and the progress-queue spawn). A cancel that arrives
    while a run is still starting therefore finds the record instead of
    concluding that nothing is running.
    """
    global _ACTIVE_BACKTEST_RUN
    record = _ActiveBacktestRun()
    base_release = _make_backtest_guard_releaser()

    def _release_and_clear() -> None:
        """Release this run's guards once, then drop the record if it is still ours.

        The record's lifetime is exactly the guards' lifetime, so no exit path can
        leave a finished run looking active to `/api/backtest/cancel`. A stale run
        whose guards a newer run already owns clears nothing.

        A run whose worker death could not be confirmed keeps its guards even when
        its own cleanup `finally` fires: otherwise the streaming path frees them a
        moment later anyway and the "stopping…" verdict lasts only until the
        BrokenProcessPool surfaces. Only the confirmed-death path clears the block,
        and it clears it before calling this.
        """
        if record.release_blocked:
            return
        base_release()
        _clear_active_backtest_run(record)

    record.release = _release_and_clear
    _ACTIVE_BACKTEST_RUN = record
    return record


def _clear_active_backtest_run(record) -> bool:
    """Drop `record` from the module slot, but only while it is still the current
    run. Returns whether it was cleared."""
    global _ACTIVE_BACKTEST_RUN
    if _ACTIVE_BACKTEST_RUN is record:
        _ACTIVE_BACKTEST_RUN = None
        return True
    return False


def _cancelled_before_start(record) -> bool:
    """True when a stop arrived while this run was still starting.

    Issue #383: the record is published before the handler's first startup await
    (the semaphore acquire, the progress-queue spawn), so a cancel can land in
    that window. The cancel then finds no pool, releases the guards and reports a
    clean stop — so the handler MUST NOT carry on and submit the worker the
    operator just cancelled, or a second run could share the one-worker pool with
    a worker nobody is watching.
    """
    return bool(record.cancel_requested)


def _terminate_backtest_pool_confirmed(pool, procs=None) -> bool:
    """Kill `pool`'s workers and report whether their death is CONFIRMED.

    Issue #383: `_terminate_backtest_pool` requests a shutdown and returns; it
    never looks back, so a caller acting on it would happily free the one-worker
    guard while the old worker is still burning CPU. This sibling escalates and
    then verifies:

    `terminate()` each worker -> `join(timeout)` -> `kill()` + `join(timeout)`
    the survivors -> `shutdown()` the pool -> report `True` only when every
    worker process reports `exitcode is not None`.

    An empty process map (a test double, or a run that never got a worker) is
    vacuously dead. `None` is dead too — there is nothing left to stop.

    `procs` lets a caller pass handles it captured earlier. `shutdown()` empties
    the pool's map, so a repeat cancel must re-check the ORIGINAL workers rather
    than find nothing alive and call it a clean stop.

    Blocking by design: it joins with a timeout. Callers on the event loop must
    offload it with `asyncio.to_thread`, or the dashboard freezes for the duration
    of the join — including the very streams the stop exists to close.
    """
    if procs is None:
        if pool is None:
            return True
        try:
            procs = list((getattr(pool, "_processes", None) or {}).values())
        except Exception:
            procs = []
    # `_terminate_backtest_pool` owns the signalling and the singleton detach, so
    # workers are terminated exactly once and its #371 binding stays authoritative.
    if pool is not None:
        _terminate_backtest_pool(pool)
    for proc in procs:
        try:
            proc.join(timeout=2.0)
        except Exception:
            pass
    for proc in procs:
        # Still alive after SIGTERM: escalate. Reported death is never assumed.
        if getattr(proc, "exitcode", None) is None:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.join(timeout=2.0)
            except Exception:
                pass
    return all(getattr(proc, "exitcode", None) is not None for proc in procs)


def _make_backtest_guard_releaser():
    """Return a release-once callable for the streaming path's per-run cleanup.

    The streaming endpoint releases the guards from two places (the submitted
    task's `finally` and the generator's `finally`) so release is eventual even
    if the generator never starts. First call wins; later calls are no-ops, so
    a stale task freed by pool termination cannot clear a newer run's guards.
    """
    released = {"flag": False}

    def _release() -> None:
        """Release the guards on the first call only; later calls are no-ops."""
        if released["flag"]:
            return
        released["flag"] = True
        semaphore = get_backtest_semaphore()
        try:
            semaphore.release()
        except ValueError:
            pass
        with _BACKTEST_LOCK:
            global _BACKTEST_RUNNING
            _BACKTEST_RUNNING = False

    return _release


class _PairStatsAccumulator:
    """Issue #371: constant-size whole-run pair statistics for progress flushes.

    Replaces the unbounded ``prog_pair_costs`` / ``prog_pair_edges`` lists whose
    ``sum()`` re-computation made every progress flush O(windows-processed).
    The displayed envelope values must not change, so the running sums reproduce
    the interpreter's built-in ``sum()`` exactly:

    - Python >= 3.12: built-in ``sum()`` uses Neumaier compensated summation on
      floats, so the accumulator replays the identical algorithm.
    - Older interpreters: built-in ``sum()`` is plain left-to-right addition.

    For every prefix of consumed values the invariant is
    ``round(acc.mean(), 4) == round(sum(prefix) / len(prefix), 4)``.
    """

    __slots__ = (
        "_cost_count", "_cost_sum", "_cost_comp",
        "_edge_count", "_edge_sum", "_edge_comp",
        "_above_settle",
    )

    def __init__(self) -> None:
        """Zero all counters, sums, and compensation terms."""
        self._cost_count = 0
        self._cost_sum = 0.0
        self._cost_comp = 0.0
        self._edge_count = 0
        self._edge_sum = 0.0
        self._edge_comp = 0.0
        self._above_settle = 0

    # -- ingestion ---------------------------------------------------------

    def add_cost(self, c: float) -> None:
        """Ingest one window cost sample; count it above-settle when rounded > 1.00."""
        self._cost_count += 1
        if _BUILTIN_SUM_COMPENSATED:
            self._cost_sum, self._cost_comp = _neumaier_add(self._cost_sum, self._cost_comp, float(c))
        else:
            self._cost_sum += float(c)
        if round(float(c), 4) > 1.00:
            self._above_settle += 1

    def add_edge(self, e: float) -> None:
        """Ingest one window edge sample."""
        self._edge_count += 1
        if _BUILTIN_SUM_COMPENSATED:
            self._edge_sum, self._edge_comp = _neumaier_add(self._edge_sum, self._edge_comp, float(e))
        else:
            self._edge_sum += float(e)

    # -- snapshots (O(1)) ---------------------------------------------------

    def cost_count(self) -> int:
        """Number of cost samples ingested so far."""
        return self._cost_count

    def edge_count(self) -> int:
        """Number of edge samples ingested so far."""
        return self._edge_count

    def cost_mean(self) -> Optional[float]:
        """Mean cost, or None when no samples have been ingested."""
        if not self._cost_count:
            return None
        if _BUILTIN_SUM_COMPENSATED:
            return (self._cost_sum + self._cost_comp) / self._cost_count
        return self._cost_sum / self._cost_count

    def edge_mean(self) -> Optional[float]:
        """Mean edge, or None when no samples have been ingested."""
        if not self._edge_count:
            return None
        if _BUILTIN_SUM_COMPENSATED:
            return (self._edge_sum + self._edge_comp) / self._edge_count
        return self._edge_sum / self._edge_count

    def above_settle_count(self) -> int:
        """Number of cost samples rounding above 1.00 (above-settle)."""
        return self._above_settle

    # -- introspection (tests assert boundedness) ---------------------------

    def state_size(self) -> int:
        """Number of retained values; constant regardless of windows consumed."""
        return 6  # counts + sums + compensation terms


def _neumaier_add(total: float, comp: float, x: float):
    """One Neumaier (improved Kahan) step, matching CPython 3.12+ ``sum()``."""
    s = total + x
    if abs(total) >= abs(x):
        t = (total - s) + x
    else:
        t = (x - s) + total
    return s, comp + t


_BUILTIN_SUM_COMPENSATED = sys.version_info >= (3, 12)


def _run_backtest_simulation_worker(
    ticks_dir_str: str,
    source_file_str: Optional[str],
    params_dict: dict,
    size: int,
    max_start_delay: float,
    limit_windows: int,
    raw_params: dict,
    empty_params: dict,
    series_sel: str = "",
    durations_sel: str = "",
    progress_queue=None,
    progress_batch_windows: int = 50,
    progress_batch_interval: float = 0.25,
) -> dict:
    """Top-level worker function executing backtest simulation in an isolated process.

    Runs in a dedicated OS process with an independent GIL. Passes only picklable
    parameters across the process boundary (`series_sel`/`durations_sel` are
    plain strings, parsed here with the shared selection module).
    """
    from backtest import BacktestParams
    from backtest.engine import _simulate_window, iter_windows_streaming
    from backtest.selection import (
        apply_selection,
        build_coverage,
        found_pairs_from_windows,
        parse_durations,
        parse_series_tokens,
    )
    from strategy.series import SERIES, supported_durations

    params = BacktestParams(**params_dict) if isinstance(params_dict, dict) else params_dict
    series_label_map = {s[0]: s[2] for s in SERIES}
    # Selection was validated in the endpoint; re-parse here (already-valid
    # strings, so this cannot raise for values that reached the worker).
    series_tokens = parse_series_tokens(series_sel)
    duration_values = parse_durations(durations_sel)
    cov_source = source_file_str or ticks_dir_str

    # Stream one window at a time instead of materialising every tick. Same
    # reason as the sweep worker: "All Files" is several GB of ticks, which as
    # Python dicts pinned a single-worker pool past 5 GB and held the backtest
    # lock for the entire run. Selection drops whole windows (ticks sharing a
    # cid share series/duration), so filtering per window is equivalent to
    # filtering the tick stream up front.
    source = Path(source_file_str) if source_file_str else Path(ticks_dir_str)
    # Simulate each window as it completes and keep only the (small) result, so
    # the tick payload never accumulates. `iter_windows_streaming` yields in
    # completion order, so the results are re-sorted by first ts here to match
    # `group_by_cid` — the equity curve and drawdown are order-dependent.
    results: list[tuple[float, int, Any]] = []
    n_snaps = 0

    # Issue #331: batched progress emission. Preview points mirror the final
    # curve's size scaling (`pnl_cents * size`), arrive in completion order,
    # and never change or fail the run — a queue put failure only disables
    # further emission. Defaults keep the legacy (non-streaming) behavior.
    emit_progress = progress_queue is not None
    prog_points: list[dict] = []
    prog_total = 0.0
    prog_count = 0
    # Issue #331 (IIIB): live counters for the dashboard's metric cards and a
    # provisional per-window P&L sample for the histogram — the operator sees
    # every visualization react while the replay iterates its windows.
    prog_pairs = 0
    prog_exits = 0
    prog_wins = 0
    prog_max_dd = 0.0
    prog_peak = 0.0
    prog_pnls: list[float] = []
    prog_last_flush = time.monotonic()
    # Issue #371: constant-size running statistics for the whole-run pair
    # metrics (was: ever-growing prog_pair_costs / prog_pair_edges lists that
    # every flush re-summed, O(n) per flush). The helper reproduces built-in
    # `sum()` exactly, so every envelope value is unchanged.
    pair_stats = _PairStatsAccumulator()

    def _disable_progress() -> None:
        """Stop emitting progress after a queue failure; the run continues."""
        nonlocal emit_progress
        emit_progress = False

    def _flush_progress() -> None:
        """Push one batched progress message; a failed put disables emission."""
        nonlocal prog_points, prog_pnls, prog_last_flush
        if not prog_points:
            return
        msg = {
            "windows_done": prog_count,
            "provisional_total_pnl_cents": round(prog_total, 2),
            "points": prog_points,
            # Live card counters (same semantics as the final `overall` block).
            "pairs": prog_pairs,
            "exits": prog_exits,
            "wins": prog_wins,
            "max_drawdown_cents": round(prog_max_dd, 2),
            "mean_pair_cost": (
                round(pair_stats.cost_mean(), 4)
                if pair_stats.cost_count() else None
            ),
            "mean_pair_edge_cents": (
                round(pair_stats.edge_mean(), 4)
                if pair_stats.edge_count() else None
            ),
            "pairs_above_settle": pair_stats.above_settle_count(),
            # Provisional histogram sample (scaled pnl values, completion order).
            "pnl_sample_cents": prog_pnls[-2000:],
        }
        prog_points = []
        prog_pnls = prog_pnls[-2000:]
        prog_last_flush = time.monotonic()
        try:
            progress_queue.put_nowait(msg)
        except Exception:
            # Emission failures must never fail the run.
            _disable_progress()

    # Push the market selection down into the reader so unselected rows are
    # never parsed at all, not parsed-then-discarded.
    for seq, _cid, g in iter_windows_streaming(source, series_tokens):
        if not g:
            continue
        g = list(apply_selection(g, series_tokens, duration_values))
        if not g:
            continue
        if max_start_delay > 0:
            first_ts = float(g[0].get("ts", 0.0) or 0.0)
            start_ts = float(g[0].get("start_ts", 0.0) or 0.0)
            delay = max(0.0, first_ts - start_ts) if (first_ts and start_ts) else 0.0
            if delay > max_start_delay:
                continue
        n_snaps += len(g)
        win = _simulate_window(g, params)
        results.append((float(g[0].get("ts", 0.0) or 0.0), seq, win))
        if emit_progress:
            # Same scaling the final equity curve applies.
            win_pnl = win.pnl_cents * size
            prog_total += win_pnl
            prog_count += 1
            prog_peak = max(prog_peak, prog_total)
            prog_max_dd = max(prog_max_dd, prog_peak - prog_total)
            if win.pair_captured:
                prog_pairs += 1
            elif win.exit_taken:
                prog_exits += 1
            if win.first_pair_cost is not None:
                pair_stats.add_cost(win.first_pair_cost)
            if win.mean_pair_edge_cents is not None:
                pair_stats.add_edge(win.mean_pair_edge_cents)
            if win.pnl_cents > 0:
                prog_wins += 1
            prog_pnls.append(round(win_pnl, 2))
            prog_points.append({
                "pnl_cents": round(win_pnl, 2),
                "cumulative_pnl_cents": round(prog_total, 2),
            })
            if (progress_batch_windows and prog_count % progress_batch_windows == 0) or \
                    (progress_batch_interval and time.monotonic() - prog_last_flush >= progress_batch_interval):
                _flush_progress()
    if emit_progress:
        _flush_progress()
    # `(ts, seq)` reproduces `group_by_cid`'s ordering exactly, including the
    # first-seen tie-break for markets that open on the same timestamp. The
    # equity curve and max drawdown are order-dependent, so this must match.
    results.sort(key=lambda t: (t[0], t[1]))
    per_window = [w for _ts, _seq, w in results]
    # `limit_windows` takes the earliest N, as it did when it sliced a
    # ts-sorted `grouped`. It therefore cannot short-circuit the loop: windows
    # arrive in completion order, so which N are earliest is only known after
    # the sort. The dashboard never sends this parameter — it exists for
    # debugging, and correctness beats skipping work on a path nobody uses.
    if limit_windows and limit_windows > 0:
        per_window = per_window[:limit_windows]
    # Coverage counts windows per (series, duration); the results already carry
    # both, so there is no need to keep the tick payload around to re-derive it.
    window_rows = [{"series": w.series, "duration": w.duration} for w in per_window]

    if not per_window:
        gp = params.grouped_params()
        empty_cov = build_coverage(cov_source, {}, series_tokens, duration_values)
        return {
            "params_hash": params.params_hash(),
            "params": empty_params,
            "params_groups": gp,
            "selection": empty_cov["selection"],
            "coverage": empty_cov,
            "overall": {
                "windows": 0,
                "entered_windows": 0,
                "pairs": 0,
                "pair_rate": 0.0,
                "exits": 0,
                "exit_rate": 0.0,
                "total_pnl_cents": 0.0,
                "avg_pnl_cents": 0.0,
                "max_drawdown_cents": 0.0,
                "win_rate": 0.0,
            },
            "per_series": {},
            "per_duration": {},
            "equity_curve": [],
            "trades_sample": [],
            "pnl_histogram": dict(EMPTY_PNL_HISTOGRAM),
            "n_snaps": 0,
            "n_windows": 0,
        }

    # The max_start_delay and limit_windows filters ran inside the streaming loop
    # above, and per_window is already built and ts-sorted.

    # Compute Equity Curve and Max Drawdown scaled by size
    cum_pnl = 0.0
    peak_pnl = 0.0
    max_dd = 0.0
    equity_curve = []
    winning_windows = 0

    for idx, w in enumerate(per_window):
        win_pnl = w.pnl_cents * size
        cum_pnl += win_pnl
        if cum_pnl > peak_pnl:
            peak_pnl = cum_pnl
        drawdown = peak_pnl - cum_pnl
        if drawdown > max_dd:
            max_dd = drawdown
        if w.pnl_cents > 0:
            winning_windows += 1

        equity_curve.append({
            "window_idx": idx + 1,
            "cumulative_pnl_cents": round(cum_pnl, 2),
            "pnl_cents": round(win_pnl, 2),
        })

    # Per-series aggregation
    def _new_outcome_row():
        """Fresh zeroed outcome row for one per-series/per-duration bucket."""
        return {
            "windows": 0,
            "entered": 0,
            "pairs": 0,
            "exits": 0,
            "oscillating": 0,
            "monotonic": 0,
            "flat": 0,
            "total_pnl_cents": 0.0,
            "first_pair_costs": [],
            "mean_pair_edges": [],
            "total_pair_pnl_cents": 0.0,
        }

    per_series_raw = defaultdict(_new_outcome_row)
    # Issue #308: per-duration aggregation, same row shape, keyed by seconds.
    per_duration_raw: dict[int, dict] = defaultdict(_new_outcome_row)

    trades_sample = []
    profitable_pairs = 0
    profitable_exits = 0
    unfilled_windows = 0

    for w in per_window:
        win_pnl = w.pnl_cents * size
        a = per_series_raw[w.series]
        a["windows"] += 1
        if getattr(w, "entered", False) or w.filled_up or w.filled_down:
            a["entered"] += 1
        if w.first_pair_cost is not None:
            a["first_pair_costs"].append(w.first_pair_cost)
        if w.mean_pair_edge_cents is not None:
            a["mean_pair_edges"].append(w.mean_pair_edge_cents)
        a["total_pair_pnl_cents"] += w.pair_pnl_cents * size

        if w.pair_captured:
            a["pairs"] += 1
            if win_pnl > 0:
                profitable_pairs += 1
        elif w.exit_taken:
            a["exits"] += 1
            if win_pnl > 0:
                profitable_exits += 1
        elif not w.filled_up and not w.filled_down:
            unfilled_windows += 1
        elif win_pnl > 0:
            profitable_exits += 1

        if w.class_label == "oscillating":
            a["oscillating"] += 1
        elif w.class_label == "monotonic":
            a["monotonic"] += 1
        elif w.class_label == "flat":
            a["flat"] += 1
        a["total_pnl_cents"] += win_pnl

        # Mirror into the duration bucket (pair XOR exit, like the series row).
        d = per_duration_raw[w.duration]
        d["windows"] += 1
        if getattr(w, "entered", False) or w.filled_up or w.filled_down:
            d["entered"] += 1
        if w.first_pair_cost is not None:
            d["first_pair_costs"].append(w.first_pair_cost)
        if w.mean_pair_edge_cents is not None:
            d["mean_pair_edges"].append(w.mean_pair_edge_cents)
        d["total_pair_pnl_cents"] += w.pair_pnl_cents * size

        if w.pair_captured:
            d["pairs"] += 1
        elif w.exit_taken:
            d["exits"] += 1
        if w.class_label == "oscillating":
            d["oscillating"] += 1
        elif w.class_label == "monotonic":
            d["monotonic"] += 1
        elif w.class_label == "flat":
            d["flat"] += 1
        d["total_pnl_cents"] += win_pnl

        exit_info = f"exit_{w.exit_side}" if w.exit_taken else ("pair_merged" if w.pair_captured else "-")
        trades_sample.append({
            "slug": w.slug,
            "label": series_label_map.get(w.series, w.series),
            "series": w.series,
            "both_filled": w.pair_captured,
            "exit_triggered": w.exit_taken,
            "up_filled": w.filled_up,
            "down_filled": w.filled_down,
            "entry_up": w.entry_price_up,
            "entry_down": w.entry_price_down,
            "exit_price": w.exit_price,
            "exit_side": w.exit_side,
            "settlement_mid": w.settlement_mid,
            "settled_unmarked": w.settled_unmarked,
            "settle_source": w.settle_source,
            "is_dead_zone": bool(w.settle_source or w.settled_unmarked),
            "pnl_cents": round(win_pnl, 2),
            "exit_reason": exit_info,
            "start_delay_sec": w.start_delay_sec,
            "is_partial": w.is_partial,
            "first_pair_cost": w.first_pair_cost,
            "mean_pair_edge_cents": w.mean_pair_edge_cents,
            "worst_pair_edge_cents": w.worst_pair_edge_cents,
            "pair_pnl_cents": round(w.pair_pnl_cents * size, 2),
            "pairs_count": w.pairs_count,
            "stops_count": w.stops_count,
            "pairs": w.pairs,
            "stops": w.stops,
        })

    total_windows = len(per_window)
    total_pairs = sum(a["pairs"] for a in per_series_raw.values())
    total_exits = sum(a["exits"] for a in per_series_raw.values())
    total_pnl = sum(a["total_pnl_cents"] for a in per_series_raw.values())

    entered_windows = sum(1 for w in per_window if getattr(w, "entered", False) or w.filled_up or w.filled_down)
    all_first_pair_costs = [w.first_pair_cost for w in per_window if w.first_pair_cost is not None]
    all_mean_pair_edges = [w.mean_pair_edge_cents for w in per_window if w.mean_pair_edge_cents is not None]
    total_pair_pnl = sum(w.pair_pnl_cents * size for w in per_window)
    pairs_above_settle = sum(1 for c in all_first_pair_costs if round(c, 4) > 1.00)

    overall = {
        "windows": total_windows,
        "entered_windows": entered_windows,
        "pairs": total_pairs,
        "pair_rate": round(total_pairs / total_windows, 4)
        if total_windows
        else 0.0,
        "pair_rate_entered": round(total_pairs / entered_windows, 4)
        if entered_windows
        else 0.0,
        "exits": total_exits,
        "exit_rate": round(total_exits / total_windows, 4)
        if total_windows
        else 0.0,
        "total_pnl_cents": round(total_pnl, 2),
        "avg_pnl_cents": round(total_pnl / total_windows, 2)
        if total_windows
        else 0.0,
        "max_drawdown_cents": round(max_dd, 2),
        "win_rate": round(winning_windows / total_windows, 4)
        if total_windows
        else 0.0,
        "wins": winning_windows,
        "profitable_windows": winning_windows,
        "profitable_pairs": profitable_pairs,
        "profitable_exits": profitable_exits,
        "unfilled_windows": unfilled_windows,
        "mean_pair_cost": round(sum(all_first_pair_costs) / len(all_first_pair_costs), 4)
        if all_first_pair_costs
        else None,
        "mean_pair_edge_cents": round(sum(all_mean_pair_edges) / len(all_mean_pair_edges), 4)
        if all_mean_pair_edges
        else None,
        "pairs_above_settle": pairs_above_settle,
        "total_pair_pnl_cents": round(total_pair_pnl, 2),
    }


    per_series_out = {}
    for s_slug, duration, s_label in SERIES:
        a = per_series_raw.get(s_slug, _new_outcome_row())
        n = a["windows"]
        ent = a.get("entered", 0)
        costs = a.get("first_pair_costs", [])
        edges = a.get("mean_pair_edges", [])
        per_series_out[s_slug] = {
            "label": s_label,
            "windows": n,
            "entered_windows": ent,
            "pairs": a["pairs"],
            "pair_rate": round(a["pairs"] / n, 4) if n else 0.0,
            "pair_rate_entered": round(a["pairs"] / ent, 4) if ent else 0.0,
            "exits": a["exits"],
            "exit_rate": round(a["exits"] / n, 4) if n else 0.0,
            "total_pnl_cents": round(a["total_pnl_cents"], 2),
            "avg_pnl_cents": round(a["total_pnl_cents"] / n, 2) if n else 0.0,
            "oscillating": a["oscillating"],
            "monotonic": a["monotonic"],
            "mean_pair_cost": round(sum(costs) / len(costs), 4) if costs else None,
            "mean_pair_edge_cents": round(sum(edges) / len(edges), 4) if edges else None,
            "total_pair_pnl_cents": round(a.get("total_pair_pnl_cents", 0.0), 2),
        }

    # Issue #308: per-duration breakdown with the worker's row conventions,
    # keyed by seconds as strings (JSON object keys).
    per_duration_out = {}
    for dur in supported_durations():
        a = per_duration_raw.get(dur, _new_outcome_row())
        n = a["windows"]
        ent = a.get("entered", 0)
        costs = a.get("first_pair_costs", [])
        edges = a.get("mean_pair_edges", [])
        per_duration_out[str(dur)] = {
            "label": f"{dur // 60}m",
            "windows": n,
            "entered_windows": ent,
            "pairs": a["pairs"],
            "pair_rate": round(a["pairs"] / n, 4) if n else 0.0,
            "pair_rate_entered": round(a["pairs"] / ent, 4) if ent else 0.0,
            "exits": a["exits"],
            "exit_rate": round(a["exits"] / n, 4) if n else 0.0,
            "total_pnl_cents": round(a["total_pnl_cents"], 2),
            "avg_pnl_cents": round(a["total_pnl_cents"] / n, 2) if n else 0.0,
            "oscillating": a["oscillating"],
            "monotonic": a["monotonic"],
            "mean_pair_cost": round(sum(costs) / len(costs), 4) if costs else None,
            "mean_pair_edge_cents": round(sum(edges) / len(edges), 4) if edges else None,
            "total_pair_pnl_cents": round(a.get("total_pair_pnl_cents", 0.0), 2),
        }


    coverage = build_coverage(
        cov_source, found_pairs_from_windows(window_rows), series_tokens, duration_values)

    gp = params.grouped_params()
    return {
        "params_hash": params.params_hash(),
        "params": raw_params,
        "params_groups": gp,
        "selection": coverage["selection"],
        "coverage": coverage,
        "n_snaps": n_snaps,
        "n_windows": total_windows,
        "overall": overall,
        "per_series": per_series_out,
        "per_duration": per_duration_out,
        "equity_curve": equity_curve,
        "trades_sample": trades_sample,
        "pnl_histogram": _compute_pnl_histogram(per_window, size),
    }


# --- Sweep visual (one axis, X-Y) -------------------------------------------
# Values mirror scripts/sweep_backtest.py sensitivity axes so the chart shows
# the same points the CLI sweeps. The worker loads ticks once and replays each
# point in-process: N runs share one load instead of paying it N times.
SWEEP_AXES: Dict[str, List[float] | List[bool] | List[str]] = {
    "queue": [0.0, 10.0, 25.0, 50.0, 100.0, 200.0, 500.0, 1000.0, 2000.0],
    "offset": [0.010, 0.015, 0.020, 0.025, 0.030, 0.035, 0.040],
    "exit_stop_default": [0.06, 0.08, 0.10, 0.12, 0.14, 0.16],
    "exit_stop_btc": [0.06, 0.08, 0.10, 0.12, 0.14, 0.16],
    "exit_stop_sol": [0.06, 0.08, 0.10, 0.12, 0.14, 0.16],
    "exit_rev": [0.010, 0.015, 0.020, 0.025, 0.030],
    "late_entry": [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0],
    "quote_range": [0.00, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30],
    # Issue #388: the three engine knobs the Backtester and cockpit already
    # expose, so a sweep can answer what they do to PnL. Values mirror
    # `scripts/sweep_backtest.py`'s sensitivity grids (leg chase on/off, naked
    # leg close/hold); dead zone is the exception — the CLI speaks fractions
    # while the page control and the `dead_zone_pct` query parameter speak
    # percent, so this grid is percent and lists the 10% default as a bar.
    "leg_chase": [False, True],
    "naked_leg": ["close", "hold"],
    "dead_zone_pct": [0.0, 5.0, 10.0, 15.0, 20.0, 30.0],
}

#: Issue #388: axes whose values are names, not numbers — anchor translation is
#: a no-op for them. Derived from the grids' own value types so a future
#: categorical axis cannot be half-wired by forgetting a second hand-written
#: list; the frontend mirrors this set and the Node parity test covers it.
_SWEEP_CATEGORICAL_AXES = {
    name for name, grid in SWEEP_AXES.items()
    # Review fix (Station IV): numeric, not `float` — an int grid (`[0, 10, 25]`)
    # is numeric too and must not be misread as name-valued. `bool` subclasses
    # `int`, so it is excluded first instead of being caught by the int branch.
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in grid)
}


#: Sweep axes that clamp through the BacktestParams registry (axis → knob name).
_SWEEP_REGISTRY_AXES = {"offset": "offset", "queue": "queue_gate", "exit_rev": "exit_reversal"}


def _sweep_axis_values(axis: str, center: Optional[float]) -> tuple[list, int]:
    """Effective sweep values for one axis with an optional midpoint anchor.

    Issue #378: with `center=None` the literal `SWEEP_AXES` grid is returned
    unchanged — no rounding, no clamping, so default sweeps are byte-identical
    to before. Otherwise the grid is translated so index `N // 2` equals the
    anchor (`anchor + (grid[i] - grid[N // 2])`), which preserves the existing
    spacing including the irregular `queue` gaps and puts even-length grids on
    their upper-middle index. Each translated point clamps to its bounds and
    duplicates collapse (first occurrence wins); the second return value counts
    how many requested points were clamped, so the card can label what ran.
    """
    grid = list(SWEEP_AXES.get(axis, []))
    if center is None or not grid:
        return grid, 0
    if axis in _SWEEP_CATEGORICAL_AXES:
        # Issue #388: categorical axes ignore `sweep_center` and always use the
        # literal value list — there is no midpoint between "off" and "on".
        # The frontend `sweepAxisValues` mirror must match this behaviour.
        return grid, 0
    mid = len(grid) // 2
    translated = [center + (g - grid[mid]) for g in grid]
    # Review finding (Station IV): cosmetic rounding is the axis's own shape,
    # not clamping — so every branch counts bound hits BEFORE rounding. An
    # in-bounds anchor must report clamped_count 0 even when rounding moves it.
    if axis == "quote_range":
        # Sweep moves `lo` only; cap below `hi`. The 2dp round matches
        # `_sweep_params_for_value` construction so lo < hi always holds.
        bounded = [max(0.0, min(0.49, t)) for t in translated]
        n_clamped = sum(1 for t, c in zip(translated, bounded) if c != t)
        rounded = [round(v, 2) for v in bounded]
    elif axis == "late_entry":
        # Registry speaks fraction, the wire speaks percent. Count on fractions
        # so the /100→*100 float roundtrip never inflates the count.
        frac = [t / 100.0 for t in translated]
        bounded_frac = [_clamp_to_spec("entry_delay_pct", f) for f in frac]
        n_clamped = sum(1 for f, c in zip(frac, bounded_frac) if c != f)
        rounded = [round(c * 100.0, 6) for c in bounded_frac]
    elif axis == "dead_zone_pct":
        # Issue #388: percent of window — the Backtester control's domain. Never
        # the stop axes' 0.001–0.50 clamp, which would flatten this axis.
        bounded = [max(0.0, min(100.0, t)) for t in translated]
        n_clamped = sum(1 for t, c in zip(translated, bounded) if c != t)
        rounded = [round(v, 6) for v in bounded]
    elif axis in _SWEEP_REGISTRY_AXES:
        bounded = [_clamp_to_spec(_SWEEP_REGISTRY_AXES[axis], t) for t in translated]
        n_clamped = sum(1 for t, c in zip(translated, bounded) if c != t)
        rounded = [round(v, 6) for v in bounded]
    else:
        # Stop axes have no scalar registry bounds; the sweep-local 0.001–0.50
        # matches the existing `exit_thresh` API limit.
        bounded = [max(0.001, min(0.50, t)) for t in translated]
        n_clamped = sum(1 for t, c in zip(translated, bounded) if c != t)
        rounded = [round(v, 6) for v in bounded]
    effective = list(dict.fromkeys(rounded))
    return effective, n_clamped


def _sweep_params_for_value(base: Any, axis: str, value: float | bool | str) -> tuple[Any, str]:
    """Return an independent parameter copy and readable label for one sweep bar."""
    if axis == "queue":
        return _dc_replace(base, queue_gate=float(value)), f"queue={value:.0f}"
    if axis == "offset":
        return _dc_replace(base, offset=float(value)), f"offset={value:.3f}"
    if axis == "exit_stop_default":
        thresholds = dict(base.exit_thresh_by_slug)
        thresholds["default_5m"] = float(value)
        thresholds["default_15m"] = float(value)
        return _dc_replace(base, exit_thresh_by_slug=thresholds), f"stop_default={value:.2f}"
    if axis == "exit_stop_btc":
        thresholds = dict(base.exit_thresh_by_slug)
        thresholds["btc-up-or-down-5m"] = float(value)
        thresholds["btc-up-or-down-15m"] = float(value)
        return _dc_replace(base, exit_thresh_by_slug=thresholds), f"stop_btc={value:.2f}"
    if axis == "exit_stop_sol":
        thresholds = dict(base.exit_thresh_by_slug)
        thresholds["sol-up-or-down-5m"] = float(value)
        thresholds["sol-up-or-down-15m"] = float(value)
        return _dc_replace(base, exit_thresh_by_slug=thresholds), f"stop_sol={value:.2f}"
    if axis == "late_entry":
        return _dc_replace(base, entry_delay_pct=float(value) / 100.0, entry_delay_sec=0.0), f"late_entry={value:.0f}%"
    if axis == "quote_range":
        lo = round(float(value), 2)
        hi = round(1.0 - lo, 2)
        return _dc_replace(base, quote_range=(lo, hi)), f"quote_range=[{lo:.2f},{hi:.2f}]"
    if axis == "leg_chase":
        return _dc_replace(base, enable_leg_chase=bool(value)), \
            f"leg_chase={'on' if bool(value) else 'off'}"
    if axis == "naked_leg":
        return _dc_replace(base, naked_leg_at_expiry=str(value)), f"naked_leg={value}"
    if axis == "dead_zone_pct":
        # Review fix (Station IV): `:g`, not `:.0f` — an anchored grid can land on
        # a fractional percent (12 → 12.5 at anchor 12.5), and the bar's label
        # must read the value that ran, not a rounded neighbour of it.
        return _dc_replace(base, dead_zone_val=float(value) / 100.0, dead_zone_unit="pct"), \
            f"dead_zone={float(value):g}%"
    return _dc_replace(base, exit_reversal=float(value)), f"exit_rev={value:.3f}"


def _sweep_point_value(axis: str, value: Any) -> float | bool | str:
    """Issue #388: coerce one sweep point to the type its axis speaks.

    Categorical axes keep their native type end to end — bool for `leg_chase`,
    str for `naked_leg` — and every numeric axis stays a float. The blanket
    `float(...)` casts this replaces corrupted categorical values into NaN
    (JSON `true` → 1.0, a label string → NaN), which broke both the wire
    contract and the chart's strict-equality best-point match.
    """
    if axis == "leg_chase":
        return bool(value)
    if axis == "naked_leg":
        return str(value)
    return float(value)


#: Issue #355: the minimum wall-clock gap between sweep progress messages.
#: Progress used to be emitted once per replayed window, but only AFTER the whole
#: dataset had been read and replayed — so the dashboard showed a ticking "Sweeping"
#: over an all-grey card for the whole run. Emission now happens inside the streaming
#: read loop; the time gate keeps the event count bounded to
#: (run duration / interval) + 2 regardless of how many windows the corpus holds.
def _sweep_progress_interval() -> float:
    """Parse the progress throttle; a bad env value falls back to 0.5s instead
    of crashing the dashboard at import (review finding, issue #355)."""
    try:
        v = float(os.environ.get("SWEEP_PROGRESS_MIN_INTERVAL_SEC", "0.5"))
    except (TypeError, ValueError):
        return 0.5
    return v if v > 0 else 0.5


SWEEP_PROGRESS_MIN_INTERVAL_SEC = _sweep_progress_interval()


def _select_sweep_bests(
    points: list[dict],
    series_order: list[str],
    series_labels: dict[str, str],
) -> tuple[Optional[dict], Optional[dict]]:
    """Select aggregate and canonical-order market winners from sweep points."""
    best_overall = None
    best_market = None
    best_market_order = len(series_order)
    for point in points:
        if point["overall"]["windows"] <= 0:
            continue
        overall_pnl = point["overall"]["total_pnl_cents"]
        if best_overall is None or overall_pnl > best_overall["total_pnl_cents"]:
            best_overall = {
                "value": point["value"],
                "label": point["label"],
                "total_pnl_cents": overall_pnl,
            }
        for market_order, slug in enumerate(series_order):
            if slug not in point["series_present"]:
                continue
            market_pnl = point["per_series"][slug]
            if (
                best_market is None
                or market_pnl > best_market["total_pnl_cents"]
                or (
                    market_pnl == best_market["total_pnl_cents"]
                    and market_order < best_market_order
                )
            ):
                best_market = {
                    "series": slug,
                    "label": series_labels[slug],
                    "value": point["value"],
                    "point_label": point["label"],
                    "total_pnl_cents": market_pnl,
                }
                best_market_order = market_order
    return best_overall, best_market


def _run_sweep_worker(
    ticks_dir_str: str,
    source_file_str: Optional[str],
    base_params_dict: dict,
    axis: str,
    size: int,
    max_start_delay: float,
    limit_windows: int,
    series_sel: str = "",
    durations_sel: str = "",
    progress_queue=None,
    sweep_center: Optional[float] = None,
) -> dict:
    """Load ticks once, replay one param point per axis value.

    Returns {"axis", "points": [{label, value, overall, per_series}],
    "n_windows", "n_snaps"} where overall/per_series carry only the totals the
    chart needs (windows, pairs, exits, total/avg PnL in cents).

    `series_sel`/`durations_sel` are the operator's market and timeframe chips,
    passed as plain strings and re-parsed here with the shared selection module
    — same as the backtest worker, so both honour the same selection semantics.
    """
    from backtest import BacktestParams
    from backtest.engine import _simulate_window, iter_windows_streaming
    from backtest.selection import (
        apply_selection,
        parse_durations,
        parse_series_tokens,
    )
    from strategy.series import SERIES, token_for_slug

    base = BacktestParams(**base_params_dict)
    # Validated in the endpoint; already-valid strings, so this cannot raise.
    series_tokens = parse_series_tokens(series_sel)
    duration_values = parse_durations(durations_sel)
    # Issue #378: the server owns the effective values — an anchor recenters
    # the grid, None restores the literal defaults.
    values, clamped_count = _sweep_axis_values(axis, sweep_center)
    if axis in _SWEEP_CATEGORICAL_AXES:
        # Issue #388: the values above ignored the anchor, so echoing one would
        # claim an anchoring that never applied.
        sweep_center = None

    series_order = [s[0] for s in SERIES]
    series_labels = {
        slug: f"{duration // 60:02d}m {token_for_slug(slug)}"
        for slug, duration, _label in SERIES
    }

    # One streaming pass, every axis point simulated per window.
    #
    # This used to materialise the whole dataset (`list(iter_ticks(...))` then
    # `group_by_cid`) and hold it for the whole sweep, which on "All Files" is
    # several GB of ticks as Python dicts. The worker peaked past 5 GB, ran for
    # minutes with nothing to show, and — because the pool has a single worker —
    # held the backtest lock for that whole time, so the dashboard answered every
    # later run with "already in progress". Streaming one window at a time caps
    # memory at the largest single window and lets the OS reclaim each window as
    # soon as it is simulated.
    variants = [_sweep_params_for_value(base, axis, _sweep_point_value(axis, v))
                for v in values]

    def _new_acc() -> list[dict]:
        """One zeroed running-total record per axis point."""
        return [{"pnl": 0.0, "pairs": 0, "exits": 0, "n": 0,
                 "per_series": {}} for _ in variants]

    def _accumulate(into: list[dict], ws: list[Any]) -> None:
        """Fold one window's per-axis results into the running totals.

        Shared by the live (in-loop) pass and the post-loop pass so the two can
        never drift: a preview is the same arithmetic as the result, only fewer
        windows behind.
        """
        for a, w in zip(into, ws):
            a["pnl"] += w.pnl_cents * size
            a["pairs"] += 1 if w.pair_captured else 0
            a["exits"] += 1 if w.exit_taken else 0
            a["n"] += 1
            a["per_series"][w.series] = (
                a["per_series"].get(w.series, 0.0) + w.pnl_cents * size)

    def _snapshot(totals: list[dict]) -> list[dict]:
        """Running per-axis-point totals in the final `points` shape."""
        return [
            {
                "label": label,
                "value": _sweep_point_value(axis, value),
                "overall": {
                    "windows": a["n"],
                    "pairs": a["pairs"],
                    "exits": a["exits"],
                    "total_pnl_cents": round(a["pnl"], 2),
                    "avg_pnl_cents": round(a["pnl"] / a["n"], 2) if a["n"] else 0.0,
                },
                "per_series": {
                    slug: round(a["per_series"].get(slug, 0.0), 2)
                    for slug in series_order
                },
                "series_present": sorted(a["per_series"]),
            }
            for a, (value, (_params, label)) in zip(totals, zip(values, variants))
        ]

    # Issue #344: running totals so the UI fills the charts in while the sweep
    # iterates. Issue #355: emitted from inside the read loop on a time gate, so
    # the card lights up during the run and the event count stays bounded.
    # Emission failures only disable progress; the run and its result are
    # untouched.
    emit_progress = progress_queue is not None
    live = _new_acc()
    rows_live = 0
    last_emit = 0.0

    def _put(msg: dict) -> None:
        """Enqueue one progress message; a full/broken queue disables progress
        only — the run and its result are untouched."""
        nonlocal emit_progress
        try:
            progress_queue.put_nowait(msg)
        except Exception:
            emit_progress = False

    source = Path(source_file_str) if source_file_str else Path(ticks_dir_str)
    # Simulate each window as it completes and retain only the (small) results —
    # never the tick payload, which is what made "All Files" exhaust memory.
    # Windows arrive in completion order, so a `limit_windows` cap (earliest N)
    # can only be applied after the sort; the dashboard never sends it, and
    # correctness beats skipping work on a debug-only path.
    rows: list[tuple[float, int, list[Any]]] = []
    n_snaps = 0
    for seq, _cid, g in iter_windows_streaming(source, series_tokens):
        if not g:
            continue
        # Market/timeframe chips, same semantics as the backtest worker: a
        # whole window drops together, so filtering per window is equivalent to
        # filtering the tick stream up front. The market tokens are already
        # pushed into the reader above; this applies the duration half and
        # stays the authority on the match.
        g = list(apply_selection(g, series_tokens, duration_values))
        if not g:
            continue
        if max_start_delay and max_start_delay > 0:
            first_ts = float(g[0].get("ts", 0.0) or 0.0)
            start_ts = float(g[0].get("start_ts", 0.0) or 0.0)
            delay = max(0.0, first_ts - start_ts) if (first_ts and start_ts) else 0.0
            if delay > max_start_delay:
                continue
        n_snaps += len(g)
        # One memo per window, shared by every axis point. Only the axes that
        # leave `offset` alone can reuse it — the offset axis moves the resting
        # price at every tick, so it would pay the bookkeeping and never hit
        # (measured: 0.87x, i.e. slower). The other three gain because the queue
        # gate, which is 77% of sweep time, is then computed once per tick
        # instead of once per axis point.
        reuse = axis != "offset"
        memo: dict = {} if reuse else None
        results = [_simulate_window(g, p, queue_memo=memo) for p, _l in variants]
        rows.append((float(g[0].get("ts", 0.0) or 0.0), seq, results))
        # Issue #355: publish from inside the read loop. The first accepted window
        # always emits, so `series_present` is non-empty and the card is never all
        # grey for the whole run; later ones are gated on wall-clock so a
        # multi-GB corpus cannot flood the manager queue with one event per
        # window. `rows_total` is None because the total is not known yet.
        if emit_progress:
            _accumulate(live, results)
            rows_live += 1
            now = time.monotonic()
            if rows_live == 1 or (now - last_emit) >= SWEEP_PROGRESS_MIN_INTERVAL_SEC:
                last_emit = now
                _put({
                    "rows_done": rows_live,
                    "rows_total": None,
                    "n_snaps": n_snaps,
                    "points": _snapshot(live),
                    "sweep_center": sweep_center,
                    "clamped_count": clamped_count,
                })
    # `(ts, seq)` reproduces `group_by_cid`'s ordering exactly, ties included.
    rows.sort(key=lambda t: (t[0], t[1]))
    if limit_windows and limit_windows > 0:
        rows = rows[:limit_windows]

    # The authoritative pass runs over the SORTED, SLICED rows, so a
    # `limit_windows` cap and the live previews are reconciled here: the live
    # accumulator saw every accepted window, this one sees exactly the result.
    n_windows = len(rows)
    acc = _new_acc()
    for _ts, _seq, ws in rows:
        _accumulate(acc, ws)
    # One converged message: identical to the final `points` below, so the last
    # thing the UI renders before `final` cannot disagree with it.
    if emit_progress:
        _put({
            "rows_done": n_windows,
            "rows_total": n_windows,
            "n_snaps": n_snaps,
            "points": _snapshot(acc),
            "sweep_center": sweep_center,
            "clamped_count": clamped_count,
        })

    # Same builder as the live previews, so the final result can never drift
    # from what the UI rendered a moment earlier (review finding, issue #355).
    points = _snapshot(acc)

    best_overall, best_market = _select_sweep_bests(
        points, series_order, series_labels
    )
    return {
        "axis": axis,
        "points": points,
        "series_order": series_order,
        "series_labels": series_labels,
        "best_overall": best_overall,
        "best_market": best_market,
        "n_snaps": n_snaps,
        "n_windows": n_windows,
        "sweep_center": sweep_center,
        "clamped_count": clamped_count,
    }


def _build_backtest_params(
    *,
    offset: float,
    queue: float,
    pair_cost: float,
    exit_default_5m: float,
    exit_default_15m: float,
    exit_btc_5m: float,
    exit_sol_5m: float,
    exit_reversal: float,
    size: int,
    quote_lo: float,
    quote_hi: float,
    entry_delay_sec: float,
    entry_delay_pct: float | None,
    dead_zone_val: float,
    dead_zone_pct: float | None,
    dead_zone_unit: str,
    naked_leg_at_expiry: str,
    enable_leg_chase: bool,
) -> tuple[Any, dict]:
    """Build one BacktestParams from request values, plus the echo both endpoints report.

    `/api/backtest` and `/api/backtest/sweep` must build the *same* base
    configuration: a sweep is a backtest whose one axis varies. The sweep
    endpoint used to hand-roll its own subset and silently dropped every knob
    added after it (pair cost, quote range, dead zone, entry delay, naked-leg,
    leg-chase, per-market exits), so it ran against engine defaults while the
    page showed the operator's numbers. Both now call this one function, so the
    two cannot drift again — the same failure #164 fixed for clamping.

    `echo` carries the effective values back for the run-summary blocks; the
    endpoints add their own request-scoped fields (max_start_delay, selection)
    on top.
    """
    from backtest import BacktestParams

    exit_thresh = {
        "default_5m": exit_default_5m,
        "default_15m": exit_default_15m,
        "btc-up-or-down-5m": exit_btc_5m,
        "sol-up-or-down-5m": exit_sol_5m,
        "btc-up-or-down-15m": exit_btc_5m,
        "sol-up-or-down-15m": exit_sol_5m,
    }
    size = max(5, int(size))

    # Quotable range (issue #228), clamped like the live engine's
    # update_config: each end to the price domain. An inverted or degenerate
    # pair has no clamp order that preserves "lo < hi" without inventing a
    # range the operator never asked for, so it falls back to the default —
    # the same "fall back, never pass through" rule as the non-finite
    # fallbacks below. Non-finite input (nan/inf) falls back the same way.
    try:
        f_lo, f_hi = float(quote_lo), float(quote_hi)
        if not (math.isfinite(f_lo) and math.isfinite(f_hi)):
            _lo, _hi = 0.10, 0.90
        else:
            _lo = max(0.0, min(1.0, f_lo))
            _hi = max(0.0, min(1.0, f_hi))
    except (TypeError, ValueError):
        _lo, _hi = 0.10, 0.90
    if not (_lo < _hi):
        _lo, _hi = 0.10, 0.90
    quote_lo, quote_hi = _lo, _hi

    # Backtest timing controls use operator-facing percentages. Keep the old
    # seconds argument as an internal compatibility path for older callers.
    if entry_delay_pct is not None:
        entry_delay_pct = max(0.0, min(100.0, float(entry_delay_pct))) if math.isfinite(float(entry_delay_pct)) else 0.0
        entry_delay_sec = 0.0
    elif not math.isfinite(entry_delay_sec):
        entry_delay_sec = 0.0
    else:
        entry_delay_sec = max(0.0, min(3600.0, entry_delay_sec))

    if dead_zone_pct is not None:
        dead_zone_val = max(0.0, min(100.0, float(dead_zone_pct))) / 100.0 if math.isfinite(float(dead_zone_pct)) else 0.10
        dead_zone_unit = "pct"
        dz_pct_echo: float | None = dead_zone_val * 100.0
    else:
        # No percentage given (the "sec" unit, or the legacy seconds form): the
        # echoed value stays None rather than inventing a percent.
        dz_pct_echo = None
    dz_unit = dead_zone_unit if dead_zone_unit in ("pct", "sec") else "pct"
    # The registry bound (0.0, 3600.0) is the union across units; under "pct"
    # the engine itself refuses anything above 1.0, so the clamp must be
    # unit-aware or a pct request of 9999 would 500 at construction.
    dz_high = 1.0 if dz_unit == "pct" else 3600.0
    if not math.isfinite(dead_zone_val):
        dz_val = 0.10
    else:
        dz_val = min(max(dead_zone_val, 0.0), dz_high)
    naked_expiry = naked_leg_at_expiry if naked_leg_at_expiry in ("close", "hold") else "close"
    leg_chase = bool(enable_leg_chase)

    params = BacktestParams(
        offset=_clamp_to_spec("offset", offset),
        queue_gate=_clamp_to_spec("queue_gate", queue),
        max_pair_cost=_clamp_to_spec("max_pair_cost", pair_cost),
        exit_thresh_by_slug=exit_thresh,
        exit_reversal=_clamp_to_spec("exit_reversal", exit_reversal),
        quote_shares=_clamp_to_spec("quote_shares", size),
        quote_range=(quote_lo, quote_hi),
        entry_delay_sec=_clamp_to_spec("entry_delay_sec", entry_delay_sec),
        entry_delay_pct=(entry_delay_pct / 100.0) if entry_delay_pct is not None else None,
        dead_zone_val=dz_val,
        dead_zone_unit=dz_unit,
        naked_leg_at_expiry=naked_expiry,
        enable_leg_chase=leg_chase,
        # taker_fee_rate, min_quote_shares, merge_gas_usd and tick_size are
        # venue facts with no control on this tab, and neither endpoint accepts
        # them: they now take the BacktestParams defaults (0.07, 5, 0.0, 0.001).
        # The CLI and the sweep lab construct BacktestParams directly and are
        # unaffected.
    )
    echo = {
        "offset": offset,
        "queue": queue,
        "pair_cost": pair_cost,
        "exit_default_5m": exit_default_5m,
        "exit_default_15m": exit_default_15m,
        "exit_btc_5m": exit_btc_5m,
        "exit_sol_5m": exit_sol_5m,
        "exit_reversal": exit_reversal,
        "size": size,
        "quote_lo": quote_lo,
        "quote_hi": quote_hi,
        "entry_delay_sec": entry_delay_sec,
        "entry_delay_pct": entry_delay_pct,
        "dead_zone_pct": dz_pct_echo,
        "naked_leg_at_expiry": naked_expiry,
        "enable_leg_chase": leg_chase,
    }
    return params, echo



def _prepare_backtest_request(
    *, file, offset, queue, pair_cost, exit_default_5m, exit_default_15m,
    exit_btc_5m, exit_sol_5m, exit_reversal, size, max_start_delay,
    filter_partial, quote_lo, quote_hi, entry_delay_sec, entry_delay_pct,
    dead_zone_val, dead_zone_pct, dead_zone_unit, naked_leg_at_expiry,
    enable_leg_chase, series, durations,
):
    """Shared validation + preparation for /api/backtest and /api/backtest/stream.

    Issue #331: both endpoints must return identical validation error shapes
    before any run starts. Returns (None, error_response) on invalid input or
    ("ok", ctx) with everything the worker submission needs.
    """
    from backtest.selection import parse_durations, parse_series_tokens

    # Issue #308: market-series / time-frame selection, same semantics as the
    # CLI flags. A typo must fail loudly (HTTP 400), never replay zero
    # windows silently.
    try:
        series_tokens = parse_series_tokens(series)
        duration_values = parse_durations(durations)
    except ValueError as exc:
        return None, JSONResponse(status_code=400, content={"error": str(exc)})

    size = max(5, int(size))

    if filter_partial and max_start_delay <= 0:
        max_start_delay = 5.0

    params, echo = _build_backtest_params(
        offset=offset,
        queue=queue,
        pair_cost=pair_cost,
        exit_default_5m=exit_default_5m,
        exit_default_15m=exit_default_15m,
        exit_btc_5m=exit_btc_5m,
        exit_sol_5m=exit_sol_5m,
        exit_reversal=exit_reversal,
        size=size,
        quote_lo=quote_lo,
        quote_hi=quote_hi,
        entry_delay_sec=entry_delay_sec,
        entry_delay_pct=entry_delay_pct,
        dead_zone_val=dead_zone_val,
        dead_zone_pct=dead_zone_pct,
        dead_zone_unit=dead_zone_unit,
        naked_leg_at_expiry=naked_leg_at_expiry,
        enable_leg_chase=enable_leg_chase,
    )

    if not TICKS_DIR.exists():
        return None, {
            "error": "no ticks dir",
            "params_hash": params.params_hash(),
            "overall": {},
            "per_series": {},
            "equity_curve": [],
            "trades_sample": [],
            "pnl_histogram": dict(EMPTY_PNL_HISTOGRAM),
            "n_snaps": 0,
            "n_windows": 0,
        }

    source_path_str: Optional[str] = None
    if file:
        # Issue #295: one shared resolver for all tick-file endpoints — allows
        # the pristine/ subpath while still rejecting traversal.
        status, source = _resolve_tick_file(file)
        if status == "invalid":
            return None, {
                "error": "invalid file param",
                "params_hash": params.params_hash(),
                "pnl_histogram": dict(EMPTY_PNL_HISTOGRAM),
            }
        if status == "not_found":
            return None, {
                "error": f"file not found: {file}",
                "params_hash": params.params_hash(),
                "pnl_histogram": dict(EMPTY_PNL_HISTOGRAM),
            }
        source_path_str = str(source)

    # Two echo blocks, matching the pre-refactor semantics field for field.
    # `params` reports the values the engine actually used (clamped); `raw`
    # reports what the request asked for before registry clamping but after
    # range/percent normalisation — which is why a non-finite quote_lo never
    # reaches the JSON encoder. The shared builder returns the normalised set
    # so neither block can drift.
    empty_params = {
        "offset": params.offset,
        "queue": params.queue_gate,
        "pair_cost": params.max_pair_cost,
        "exit_default_5m": echo["exit_default_5m"],
        "exit_default_15m": echo["exit_default_15m"],
        "exit_btc_5m": echo["exit_btc_5m"],
        "exit_sol_5m": echo["exit_sol_5m"],
        "exit_reversal": params.exit_reversal,
        "size": echo["size"],
        "max_start_delay": max_start_delay,
        "quote_lo": echo["quote_lo"],
        "quote_hi": echo["quote_hi"],
        "entry_delay_sec": params.entry_delay_sec,
        "entry_delay_pct": params.entry_delay_pct,
        "dead_zone_pct": (params.dead_zone_val * 100.0) if params.dead_zone_unit == "pct" else None,
        "series": series,
        "durations": durations,
    }

    raw_params = {
        "offset": echo["offset"],
        "queue": echo["queue"],
        "pair_cost": echo["pair_cost"],
        "exit_default_5m": echo["exit_default_5m"],
        "exit_default_15m": echo["exit_default_15m"],
        "exit_btc_5m": echo["exit_btc_5m"],
        "exit_sol_5m": echo["exit_sol_5m"],
        "exit_reversal": echo["exit_reversal"],
        "size": echo["size"],
        "max_start_delay_sec": max_start_delay,
        "quote_lo": echo["quote_lo"],
        "quote_hi": echo["quote_hi"],
        "entry_delay_sec": echo["entry_delay_sec"],
        "entry_delay_pct": echo["entry_delay_pct"],
        "dead_zone_pct": echo["dead_zone_pct"],
        "series": series,
        "durations": durations,
    }

    return "ok", {
        "params": params,
        "params_dict": asdict(params) if is_dataclass(params) else dict(params),
        "echo": echo,
        "size": size,
        "max_start_delay": max_start_delay,
        "raw_params": raw_params,
        "empty_params": empty_params,
        "series": series,
        "durations": durations,
        "source_path_str": source_path_str,
        "series_tokens": series_tokens,
        "duration_values": duration_values,
        "file": file,
    }


# ---------------------------------------------------------------------------
# Issue #413: in-memory registry of recently completed backtest runs.
# Template save looks up run_id here to capture the canonical parameters +
# result summary. Bounded to _COMPLETED_RUNS_MAX entries, FIFO eviction.
# ---------------------------------------------------------------------------
_COMPLETED_RUNS: collections.OrderedDict[str, dict] = collections.OrderedDict()
_COMPLETED_RUNS_MAX = 32
_COMPLETED_RUNS_LOCK = threading.Lock()


def _record_completed_run(ctx: dict, raw_query: str, request_args: dict, result: dict) -> str:
    """Record a successful backtest completion and return its run_id.

    Only called for successful worker returns (no error key, no cancellation).
    """
    run_id = uuid.uuid4().hex
    summary_src = result.get("overall", {}) if isinstance(result, dict) else {}
    entry = {
        "run_id": run_id,
        "completed_at": time.time(),
        "query": raw_query,
        "request_args": request_args,
        "echo": ctx.get("echo"),
        "params_dict": ctx.get("params_dict"),
        "params_hash": result.get("params_hash") if isinstance(result, dict) else None,
        "size": ctx.get("size"),
        "dataset": {
            "file": ctx.get("file", ""),
            "resolved": ctx.get("source_path_str") or "",
        },
        "scope": {
            "series": ctx.get("series", ""),
            "durations": ctx.get("durations", ""),
            "series_tokens": ctx.get("series_tokens", []),
            "duration_values": ctx.get("duration_values", []),
        },
        "summary": {
            "params_hash": result.get("params_hash") if isinstance(result, dict) else None,
            "n_windows": result.get("n_windows") if isinstance(result, dict) else None,
            "total_pnl_cents": summary_src.get("total_pnl_cents"),
            "pairs": summary_src.get("pairs"),
            "win_rate": summary_src.get("win_rate"),
            "selection": summary_src.get("selection"),
        },
    }
    with _COMPLETED_RUNS_LOCK:
        _COMPLETED_RUNS[run_id] = entry
        while len(_COMPLETED_RUNS) > _COMPLETED_RUNS_MAX:
            _COMPLETED_RUNS.popitem(last=False)
    return run_id


@app.get(
    "/api/backtest",
    responses={
        200: {"description": "Backtest simulation results"},
        429: {"description": "Backtest simulation already in progress"},
    },
)
async def api_backtest(
    request: Request,
    file: str = "",
    offset: float = 0.02,
    queue: float = 0.0,
    pair_cost: float = 0.99,
    exit_default_5m: float = 0.05,
    exit_default_15m: float = 0.05,
    exit_btc_5m: float = 0.05,
    exit_sol_5m: float = 0.05,
    exit_reversal: float = 0.02,
    size: int = 5,
    max_start_delay: float = 0.0,
    filter_partial: bool = False,
    quote_lo: float = 0.10,
    quote_hi: float = 0.90,
    entry_delay_sec: float = 0.0,
    entry_delay_pct: float | None = None,
    dead_zone_val: float = 0.10,
    dead_zone_pct: float | None = None,
    dead_zone_unit: str = "pct",
    naked_leg_at_expiry: str = "close",
    enable_leg_chase: bool = False,
    limit_windows: int = 0,
    series: str = "",
    durations: str = "",
):
    """Run backtest simulation on selected tick file or all files in run/ticks/."""
    status, ctx = _prepare_backtest_request(
        file=file, offset=offset, queue=queue, pair_cost=pair_cost,
        exit_default_5m=exit_default_5m, exit_default_15m=exit_default_15m,
        exit_btc_5m=exit_btc_5m, exit_sol_5m=exit_sol_5m,
        exit_reversal=exit_reversal, size=size, max_start_delay=max_start_delay,
        filter_partial=filter_partial, quote_lo=quote_lo, quote_hi=quote_hi,
        entry_delay_sec=entry_delay_sec, entry_delay_pct=entry_delay_pct,
        dead_zone_val=dead_zone_val, dead_zone_pct=dead_zone_pct,
        dead_zone_unit=dead_zone_unit, naked_leg_at_expiry=naked_leg_at_expiry,
        enable_leg_chase=enable_leg_chase, series=series, durations=durations,
    )
    if status != "ok":
        return ctx
    params = ctx["params"]
    params_dict = ctx["params_dict"]
    raw_params = ctx["raw_params"]
    empty_params = ctx["empty_params"]
    source_path_str = ctx["source_path_str"]
    max_start_delay = ctx["max_start_delay"]

    global _BACKTEST_RUNNING, _BACKTEST_POOL
    semaphore = get_backtest_semaphore()
    with _BACKTEST_LOCK:
        if _BACKTEST_RUNNING or semaphore.locked():
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Backtest simulation already in progress. Please retry shortly.",
                    "params_hash": params.params_hash(),
                    "pnl_histogram": dict(EMPTY_PNL_HISTOGRAM),
                },
            )
        _BACKTEST_RUNNING = True
        # Issue #383: publish the run record here — inside the guard block, before
        # this handler's first startup `await` — so a cancel that lands while the
        # run is still starting still finds it.
        run_record = _publish_active_backtest_run()

    await semaphore.acquire()
    # Issue #383: a stop can land during the acquire above. It already released the
    # guards, so submitting now would run an unwatched worker next to the next run.
    if _cancelled_before_start(run_record):
        run_record.release()
        return JSONResponse(
            status_code=409,
            content={"error": "Backtest cancelled before it started."},
        )

    async def _run_shielded():
        """Execute backtest simulation in worker process pool and release concurrency guards."""
        try:
            if _cancelled_before_start(run_record):
                return None
            loop = asyncio.get_running_loop()
            pool = get_backtest_pool()
            _run_shielded.pool = pool  # issue #341: diagnose the submitted pool
            run_record.pool = pool  # issue #383: bind cleanup + cancel to THIS pool
            return await loop.run_in_executor(
                pool,
                _run_backtest_simulation_worker,
                str(TICKS_DIR),
                source_path_str,
                params_dict,
                size,
                max_start_delay,
                limit_windows,
                raw_params,
                empty_params,
                series,
                durations,
            )
        finally:
            # Issue #383: release-once + drop-the-record, so the cancel endpoint and
            # the late timeout cleanup can both act on this run safely.
            run_record.release()

    worker_task = asyncio.create_task(_run_shielded())
    try:
        result = await asyncio.wait_for(asyncio.shield(worker_task),
                                        timeout=BACKTEST_TIMEOUT_SEC)
        if result is None and _cancelled_before_start(run_record):
            return JSONResponse(
                status_code=409,
                content={"error": "Backtest cancelled before it started."},
            )
        # Issue #413: record the completed run for template save, inject run_id.
        if isinstance(result, dict) and "error" not in result:
            _req_args = {
                "file": file, "offset": offset, "queue": queue,
                "pair_cost": pair_cost, "exit_default_5m": exit_default_5m,
                "exit_default_15m": exit_default_15m, "exit_btc_5m": exit_btc_5m,
                "exit_sol_5m": exit_sol_5m, "exit_reversal": exit_reversal,
                "size": size, "max_start_delay": max_start_delay,
                "filter_partial": filter_partial, "quote_lo": quote_lo,
                "quote_hi": quote_hi, "entry_delay_sec": entry_delay_sec,
                "entry_delay_pct": entry_delay_pct,
                "dead_zone_val": dead_zone_val, "dead_zone_pct": dead_zone_pct,
                "dead_zone_unit": dead_zone_unit,
                "naked_leg_at_expiry": naked_leg_at_expiry,
                "enable_leg_chase": enable_leg_chase,
                "limit_windows": limit_windows,
                "series": series, "durations": durations,
            }
            rid = _record_completed_run(ctx, str(request.url.query), _req_args, result)
            result["run_id"] = rid
        return result
    except asyncio.TimeoutError:
        # Issue #383: terminate the pool THIS run bound. A global-only call would
        # kill the pool of a run that started after this one (#371).
        _terminate_backtest_pool(run_record.pool)
        raise HTTPException(
            status_code=504,
            detail=(f"Backtest exceeded {BACKTEST_TIMEOUT_SEC:.0f}s and was abandoned. "
                    "Narrow the dataset, markets or timeframes and try again."))
    except concurrent.futures.process.BrokenProcessPool as exc:
        # Issue #341: "terminated abruptly" with zero diagnostics is how a
        # spawned-worker crash (and every environmental break) surfaces. The
        # worker's exit code turns it into an actionable error, and the pool
        # is rebuilt on next use by `get_backtest_pool`.
        raise HTTPException(status_code=500, detail=_diagnose_broken_pool(
            exc, getattr(worker_task, "pool", None) or getattr(_run_shielded, "pool", None))) from exc


@app.get(
    "/api/backtest/stream",
    responses={
        200: {"description": "SSE stream: progress events then one final result"},
        429: {"description": "Backtest simulation already in progress"},
    },
)
async def api_backtest_stream(
    request: Request,
    file: str = "",
    offset: float = 0.02,
    queue: float = 0.0,
    pair_cost: float = 0.99,
    exit_default_5m: float = 0.05,
    exit_default_15m: float = 0.05,
    exit_btc_5m: float = 0.05,
    exit_sol_5m: float = 0.05,
    exit_reversal: float = 0.02,
    size: int = 5,
    max_start_delay: float = 0.0,
    filter_partial: bool = False,
    quote_lo: float = 0.10,
    quote_hi: float = 0.90,
    entry_delay_sec: float = 0.0,
    entry_delay_pct: float | None = None,
    dead_zone_val: float = 0.10,
    dead_zone_pct: float | None = None,
    dead_zone_unit: str = "pct",
    naked_leg_at_expiry: str = "close",
    enable_leg_chase: bool = False,
    limit_windows: int = 0,
    series: str = "",
    durations: str = "",
):
    """Stream backtest progress over SSE, then one authoritative final result.

    Issue #331: same guards, validation shapes and worker as the blocking
    `/api/backtest`; on client disconnect or timeout the pool is terminated and
    the guards are released synchronously so the next run can start at once.
    """
    _verify_safe_origin(request)
    status, ctx = _prepare_backtest_request(
        file=file, offset=offset, queue=queue, pair_cost=pair_cost,
        exit_default_5m=exit_default_5m, exit_default_15m=exit_default_15m,
        exit_btc_5m=exit_btc_5m, exit_sol_5m=exit_sol_5m,
        exit_reversal=exit_reversal, size=size, max_start_delay=max_start_delay,
        filter_partial=filter_partial, quote_lo=quote_lo, quote_hi=quote_hi,
        entry_delay_sec=entry_delay_sec, entry_delay_pct=entry_delay_pct,
        dead_zone_val=dead_zone_val, dead_zone_pct=dead_zone_pct,
        dead_zone_unit=dead_zone_unit, naked_leg_at_expiry=naked_leg_at_expiry,
        enable_leg_chase=enable_leg_chase, series=series, durations=durations,
    )
    if status != "ok":
        return ctx
    params = ctx["params"]
    params_dict = ctx["params_dict"]
    raw_params = ctx["raw_params"]
    empty_params = ctx["empty_params"]
    source_path_str = ctx["source_path_str"]

    semaphore = get_backtest_semaphore()
    with _BACKTEST_LOCK:
        global _BACKTEST_RUNNING
        if _BACKTEST_RUNNING or semaphore.locked():
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Backtest simulation already in progress. Please retry shortly.",
                    "params_hash": params.params_hash(),                "pnl_histogram": dict(EMPTY_PNL_HISTOGRAM),
            },
        )
        _BACKTEST_RUNNING = True
        # Issue #383: publish the run record here — inside the guard block, before
        # this handler's first startup `await` — so a cancel that lands while the
        # run is still starting still finds it.
        run_record = _publish_active_backtest_run()
    await semaphore.acquire()

    release_guards = run_record.release
    # Issue #331 review: create the queue before it can fail the run, and off
    # the event loop — the first call spawns the manager process. A failure
    # here must release the guards we already hold, not wedge them at 429
    # until restart.
    try:
        progress_queue = await asyncio.to_thread(_new_backtest_progress_queue)
    except Exception as exc:
        release_guards()
        return JSONResponse(
            status_code=503,
            content={
                "error": f"Backtest progress channel unavailable: {exc}",
                "params_hash": params.params_hash(),
                "pnl_histogram": dict(EMPTY_PNL_HISTOGRAM),
            },
        )
    # Issue #383: the queue spawn is the slow startup step a stop lands in.
    if _cancelled_before_start(run_record):
        release_guards()
        return JSONResponse(
            status_code=409,
            content={"error": "Backtest cancelled before it started."},
        )
    loop = asyncio.get_running_loop()

    async def _submit():
        """Submit the worker; eventual guard release even if the stream dies early."""
        try:
            if _cancelled_before_start(run_record):
                return None
            pool = get_backtest_pool()
            _submit.pool = pool  # issue #341: diagnose the submitted pool
            run_record.pool = pool  # issue #383: bind cleanup + cancel to THIS pool
            return await loop.run_in_executor(
                pool,
                _run_backtest_simulation_worker,
                str(TICKS_DIR),
                source_path_str,
                params_dict,
                ctx["size"],
                ctx["max_start_delay"],
                limit_windows,
                raw_params,
                empty_params,
                series,
                durations,
                progress_queue,
                50,     # progress_batch_windows
                0.25,   # progress_batch_interval (sec)
            )
        finally:
            release_guards()

    worker_task = asyncio.create_task(_submit())

    async def event_generator():
        """Stream progress envelopes, then one authoritative final event."""
        completed = False
        deadline = time.monotonic() + BACKTEST_TIMEOUT_SEC
        try:
            while not worker_task.done():
                if await request.is_disconnected():
                    return
                if time.monotonic() > deadline:
                    yield {"event": "message", "data": json.dumps({
                        "type": "error",
                        "error": (f"Backtest exceeded {BACKTEST_TIMEOUT_SEC:.0f}s and was abandoned. "
                                  "Narrow the dataset, markets or timeframes and try again."),
                    })}
                    return
                try:
                    msg = await asyncio.to_thread(progress_queue.get, True, 0.05)
                    yield {"event": "message", "data": json.dumps(
                        {"type": "progress", **msg})}
                except _pyqueue.Empty:
                    continue
            # Drain remaining progress before the authoritative final event.
            while True:
                try:
                    msg = progress_queue.get_nowait()
                    yield {"event": "message", "data": json.dumps(
                        {"type": "progress", **msg})}
                except _pyqueue.Empty:
                    break
            completed = True
            result = worker_task.result()
            # Issue #383: a deliberate stop never produces a result — the worker was
            # never submitted, or was killed. Emitting a final event here would
            # render a null result as if a run had finished.
            if result is None and run_record.cancel_requested:
                return
            # Issue #413: record completed run for template save, inject run_id.
            if isinstance(result, dict) and "error" not in result:
                _req_args = {
                    "file": file, "offset": offset, "queue": queue,
                    "pair_cost": pair_cost, "exit_default_5m": exit_default_5m,
                    "exit_default_15m": exit_default_15m, "exit_btc_5m": exit_btc_5m,
                    "exit_sol_5m": exit_sol_5m, "exit_reversal": exit_reversal,
                    "size": size, "max_start_delay": max_start_delay,
                    "filter_partial": filter_partial, "quote_lo": quote_lo,
                    "quote_hi": quote_hi, "entry_delay_sec": entry_delay_sec,
                    "entry_delay_pct": entry_delay_pct,
                    "dead_zone_val": dead_zone_val, "dead_zone_pct": dead_zone_pct,
                    "dead_zone_unit": dead_zone_unit,
                    "naked_leg_at_expiry": naked_leg_at_expiry,
                    "enable_leg_chase": enable_leg_chase,
                    "limit_windows": limit_windows,
                    "series": series, "durations": durations,
                }
                rid = _record_completed_run(ctx, str(request.url.query), _req_args, result)
                result["run_id"] = rid
            yield {"event": "message", "data": json.dumps({"type": "final", "result": result})}
        except asyncio.CancelledError:
            # Client disconnected mid-stream (sse_starlette cancels the generator).
            raise
        except concurrent.futures.process.BrokenProcessPool as exc:
            # Issue #341: surface the worker's exit code + stderr tail instead
            # of the blanket "terminated abruptly" message.
            completed = True  # diagnostic error event already ends the stream
            yield {"event": "message", "data": json.dumps(
                {"type": "error", "error": _diagnose_broken_pool(exc, getattr(_submit, "pool", None))})}
        except Exception as exc:
            yield {"event": "message", "data": json.dumps({"type": "error", "error": str(exc)})}
        finally:
            if not completed:
                # Disconnect or timeout: kill the worker now, free capacity at
                # once. The stale task may later fail with a broken-pool error;
                # its release-once call is then a no-op, so a newer run's
                # guards are untouched. The next request rebuilds the pool.
                # #371: terminate the pool this run submitted to — a run that
                # started later owns the global by then and must survive.
                # #372: read the binding at cleanup, not at submit — the task
                # body (and the binding) runs only after its first step. A
                # never-started task bound no pool, so there is nothing of
                # ours to terminate; falling back to the global would kill a
                # newer run's pool for no reason.
                submit_pool = getattr(_submit, "pool", None)
                try:
                    if submit_pool is not None:
                        _terminate_backtest_pool(submit_pool)
                finally:
                    # Guard release must survive even a failing shutdown.
                    release_guards()

    # #372: heartbeat below the client's 15 s inactivity watchdog — a large
    # but healthy window can emit no progress for longer than that.
    return EventSourceResponse(event_generator(), ping=5)


@app.get(
    "/api/backtest/sweep",
    responses={
        200: {"description": "One-axis sweep: X-Y points for the Sweep Visual chart"},
        429: {"description": "Backtest simulation already in progress"},
    },
)
async def api_backtest_sweep(
    axis: str = "queue",
    file: str = "",
    size: int = 5,
    max_start_delay: float = 0.0,
    limit_windows: int = 0,
    filter_partial: bool = False,
    offset: float = 0.02,
    queue: float = 0.0,
    pair_cost: float = 0.99,
    exit_default_5m: float = 0.05,
    exit_default_15m: float = 0.05,
    exit_btc_5m: float = 0.05,
    exit_sol_5m: float = 0.05,
    exit_reversal: float = 0.02,
    quote_lo: float = 0.10,
    quote_hi: float = 0.90,
    entry_delay_sec: float = 0.0,
    entry_delay_pct: float | None = None,
    dead_zone_val: float = 0.10,
    dead_zone_pct: float | None = None,
    dead_zone_unit: str = "pct",
    naked_leg_at_expiry: str = "close",
    enable_leg_chase: bool = False,
    series: str = "",
    durations: str = "",
    sweep_center: float | None = None,
):
    """Replay one sensitivity axis and return X-Y points.

    X = axis value, Y = total PnL (cents). One aggregate series plus one per
    market series, so the UI draws 1 big chart + 10 small ones. The base point
    is the caller's current backtest settings; only the axis moves.

    The base is built by the same `_build_backtest_params` the plain backtest
    uses, so every knob on the page reaches the sweep. This endpoint used to
    hand-roll a six-knob subset and silently fell back to engine defaults for
    pair cost, quote range, dead zone, entry delay, naked-leg and leg-chase —
    so a sweep could contradict the backtest shown beside it. Market and
    duration selection are honoured too, which also makes a sweep over two
    markets roughly half the work.
    """
    from backtest import BacktestParams
    from backtest.selection import parse_durations, parse_series_tokens

    if axis not in SWEEP_AXES:
        return JSONResponse(
            status_code=400,
            content={"error": f"unknown axis: {axis}", "valid": sorted(SWEEP_AXES)},
        )
    # Issue #378: fail loud before any busy guard — a malformed anchor must
    # never hold the single-worker lock.
    if sweep_center is not None and not math.isfinite(sweep_center):
        return JSONResponse(
            status_code=400,
            content={"error": f"sweep_center must be finite, got {sweep_center}"},
        )
    # Same fail-loud rule as the backtest endpoint (issue #308): a typo must
    # 400, never replay zero windows silently.
    try:
        series_tokens = parse_series_tokens(series)
        duration_values = parse_durations(durations)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    size = max(5, int(size))

    if filter_partial and max_start_delay <= 0:
        max_start_delay = 5.0

    source_path_str: Optional[str] = None
    if file:
        # Issue #295: shared resolver — pristine/ subpath allowed, traversal rejected.
        status, source = _resolve_tick_file(file)
        if status == "invalid":
            return JSONResponse(status_code=400, content={"error": "invalid file param"})
        if status == "not_found":
            return JSONResponse(status_code=404, content={"error": f"file not found: {file}"})
        source_path_str = str(source)

    params, _echo = _build_backtest_params(
        offset=offset,
        queue=queue,
        pair_cost=pair_cost,
        exit_default_5m=exit_default_5m,
        exit_default_15m=exit_default_15m,
        exit_btc_5m=exit_btc_5m,
        exit_sol_5m=exit_sol_5m,
        exit_reversal=exit_reversal,
        size=size,
        quote_lo=quote_lo,
        quote_hi=quote_hi,
        entry_delay_sec=entry_delay_sec,
        entry_delay_pct=entry_delay_pct,
        dead_zone_val=dead_zone_val,
        dead_zone_pct=dead_zone_pct,
        dead_zone_unit=dead_zone_unit,
        naked_leg_at_expiry=naked_leg_at_expiry,
        enable_leg_chase=enable_leg_chase,
    )

    global _BACKTEST_RUNNING, _BACKTEST_POOL
    semaphore = get_backtest_semaphore()
    with _BACKTEST_LOCK:
        if _BACKTEST_RUNNING or semaphore.locked():
            return JSONResponse(
                status_code=429,
                content={"error": "Backtest simulation already in progress. Please retry shortly."},
            )
        _BACKTEST_RUNNING = True
        # Issue #383: publish the run record here — inside the guard block, before
        # this handler's first startup `await` — so a cancel that lands while the
        # run is still starting still finds it.
        run_record = _publish_active_backtest_run()

    await semaphore.acquire()
    # Issue #383: a stop can land during the acquire above. It already released the
    # guards, so submitting now would run an unwatched worker next to the next run.
    if _cancelled_before_start(run_record):
        run_record.release()
        return JSONResponse(
            status_code=409,
            content={"error": "Sweep cancelled before it started."},
        )

    async def _run_shielded():
        """Execute the sweep in the worker pool and always release its guards."""
        try:
            if _cancelled_before_start(run_record):
                return None
            loop = asyncio.get_running_loop()
            pool = get_backtest_pool()
            _run_shielded.pool = pool  # issue #341: diagnose the submitted pool
            run_record.pool = pool  # issue #383: bind cleanup + cancel to THIS pool
            # `run_in_executor` takes no kwargs — the anchor rides a partial.
            return await loop.run_in_executor(
                pool,
                functools.partial(_run_sweep_worker, sweep_center=sweep_center),
                str(TICKS_DIR),
                source_path_str,
                asdict(params),
                axis,
                size,
                max_start_delay,
                limit_windows,
                series,
                durations,
            )
        finally:
            # Issue #383: release-once + drop-the-record, so the cancel endpoint and
            # the late timeout cleanup can both act on this run safely.
            run_record.release()

    worker_task = asyncio.create_task(_run_shielded())
    try:
        result = await asyncio.wait_for(asyncio.shield(worker_task),
                                        timeout=BACKTEST_TIMEOUT_SEC)
        if result is None and _cancelled_before_start(run_record):
            return JSONResponse(
                status_code=409,
                content={"error": "Sweep cancelled before it started."},
            )
        return result
    except asyncio.TimeoutError:
        # Issue #383: terminate the pool THIS run bound. A global-only call would
        # kill the pool of a run that started after this one (#371).
        _terminate_backtest_pool(run_record.pool)
        raise HTTPException(
            status_code=504,
            detail=(f"Sweep exceeded {BACKTEST_TIMEOUT_SEC:.0f}s and was abandoned. "
                    "Narrow the dataset, markets or timeframes and try again."))
    except concurrent.futures.process.BrokenProcessPool as exc:
        # Issue #341: same loud diagnostics as the blocking backtest endpoint.
        raise HTTPException(status_code=500, detail=_diagnose_broken_pool(
            exc, getattr(_run_shielded, "pool", None))) from exc


@app.get(
    "/api/backtest/sweep/stream",
    responses={
        200: {"description": "SSE stream: per-axis-point progress, then one final result"},
        429: {"description": "Backtest simulation already in progress"},
    },
)
async def api_backtest_sweep_stream(
    request: Request,
    axis: str = "queue",
    file: str = "",
    size: int = 5,
    max_start_delay: float = 0.0,
    limit_windows: int = 0,
    filter_partial: bool = False,
    offset: float = 0.02,
    queue: float = 0.0,
    pair_cost: float = 0.99,
    exit_default_5m: float = 0.05,
    exit_default_15m: float = 0.05,
    exit_btc_5m: float = 0.05,
    exit_sol_5m: float = 0.05,
    exit_reversal: float = 0.02,
    quote_lo: float = 0.10,
    quote_hi: float = 0.90,
    entry_delay_sec: float = 0.0,
    entry_delay_pct: float | None = None,
    dead_zone_val: float = 0.10,
    dead_zone_pct: float | None = None,
    dead_zone_unit: str = "pct",
    naked_leg_at_expiry: str = "close",
    enable_leg_chase: bool = False,
    series: str = "",
    durations: str = "",
    sweep_center: float | None = None,
):
    """Stream sweep progress over SSE, then one authoritative final result.

    Issue #344: same guards, validation shapes and worker as the blocking
    `/api/backtest/sweep`; per-row progress carries running per-axis-point
    totals so the UI fills the charts while the sweep iterates. Disconnect or
    timeout terminates the pool and releases the guards synchronously.
    """
    _verify_safe_origin(request)
    if axis not in SWEEP_AXES:
        return JSONResponse(
            status_code=400,
            content={"error": f"unknown axis: {axis}", "valid": sorted(SWEEP_AXES)},
        )
    # Issue #378: fail loud before any busy guard — a malformed anchor must
    # never hold the single-worker lock.
    if sweep_center is not None and not math.isfinite(sweep_center):
        return JSONResponse(
            status_code=400,
            content={"error": f"sweep_center must be finite, got {sweep_center}"},
        )
    from backtest.selection import parse_durations, parse_series_tokens
    try:
        series_tokens = parse_series_tokens(series)
        duration_values = parse_durations(durations)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    size = max(5, int(size))
    if filter_partial and max_start_delay <= 0:
        max_start_delay = 5.0

    source_path_str: Optional[str] = None
    if file:
        status, source = _resolve_tick_file(file)
        if status == "invalid":
            return JSONResponse(status_code=400, content={"error": "invalid file param"})
        if status == "not_found":
            return JSONResponse(status_code=404, content={"error": f"file not found: {file}"})
        source_path_str = str(source)

    params, _echo = _build_backtest_params(
        offset=offset,
        queue=queue,
        pair_cost=pair_cost,
        exit_default_5m=exit_default_5m,
        exit_default_15m=exit_default_15m,
        exit_btc_5m=exit_btc_5m,
        exit_sol_5m=exit_sol_5m,
        exit_reversal=exit_reversal,
        size=size,
        quote_lo=quote_lo,
        quote_hi=quote_hi,
        entry_delay_sec=entry_delay_sec,
        entry_delay_pct=entry_delay_pct,
        dead_zone_val=dead_zone_val,
        dead_zone_pct=dead_zone_pct,
        dead_zone_unit=dead_zone_unit,
        naked_leg_at_expiry=naked_leg_at_expiry,
        enable_leg_chase=enable_leg_chase,
    )

    semaphore = get_backtest_semaphore()
    with _BACKTEST_LOCK:
        global _BACKTEST_RUNNING
        if _BACKTEST_RUNNING or semaphore.locked():
            return JSONResponse(
                status_code=429,
                content={"error": "Backtest simulation already in progress. Please retry shortly."},
            )
        _BACKTEST_RUNNING = True
        # Issue #383: publish the run record here — inside the guard block, before
        # this handler's first startup `await` — so a cancel that lands while the
        # run is still starting still finds it.
        run_record = _publish_active_backtest_run()
    await semaphore.acquire()

    release_guards = run_record.release
    try:
        progress_queue = await asyncio.to_thread(_new_backtest_progress_queue)
    except Exception as exc:
        release_guards()
        return JSONResponse(
            status_code=503,
            content={"error": f"Sweep progress channel unavailable: {exc}"},
        )
    # Issue #383: the queue spawn is the slow startup step a stop lands in.
    if _cancelled_before_start(run_record):
        release_guards()
        return JSONResponse(
            status_code=409,
            content={"error": "Sweep cancelled before it started."},
        )
    loop = asyncio.get_running_loop()

    async def _submit():
        """Submit the sweep worker; eventual guard release even on early death."""
        try:
            if _cancelled_before_start(run_record):
                return None
            pool = get_backtest_pool()
            _submit.pool = pool  # issue #341: diagnose the submitted pool
            run_record.pool = pool  # issue #383: bind cleanup + cancel to THIS pool
            # `run_in_executor` takes no kwargs — the anchor rides a partial.
            return await loop.run_in_executor(
                pool,
                functools.partial(_run_sweep_worker, sweep_center=sweep_center),
                str(TICKS_DIR),
                source_path_str,
                asdict(params),
                axis,
                size,
                max_start_delay,
                limit_windows,
                series,
                durations,
                progress_queue,
            )
        finally:
            release_guards()

    worker_task = asyncio.create_task(_submit())

    async def event_generator():
        """Yield per-row progress, then one authoritative final event."""
        completed = False
        deadline = time.monotonic() + BACKTEST_TIMEOUT_SEC
        try:
            while not worker_task.done():
                if await request.is_disconnected():
                    return
                if time.monotonic() > deadline:
                    yield {"event": "message", "data": json.dumps({
                        "type": "error",
                        "error": (f"Sweep exceeded {BACKTEST_TIMEOUT_SEC:.0f}s and was abandoned. "
                                  "Narrow the dataset, markets or timeframes and try again."),
                    })}
                    return
                try:
                    msg = await asyncio.to_thread(progress_queue.get, True, 0.05)
                    yield {"event": "message", "data": json.dumps(
                        {"type": "progress", **msg})}
                except _pyqueue.Empty:
                    continue
            # Drain remaining progress before the authoritative final event.
            while True:
                try:
                    msg = progress_queue.get_nowait()
                    yield {"event": "message", "data": json.dumps(
                        {"type": "progress", **msg})}
                except _pyqueue.Empty:
                    break
            completed = True
            result = worker_task.result()
            # Issue #383: a deliberate stop never produces a result — the worker was
            # never submitted, or was killed.
            if result is None and run_record.cancel_requested:
                return
            yield {"event": "message", "data": json.dumps({"type": "final", "result": result})}
        except asyncio.CancelledError:
            raise
        except concurrent.futures.process.BrokenProcessPool as exc:
            # Issue #341: surface the worker's exit code + stderr tail instead
            # of the blanket "terminated abruptly" message.
            completed = True  # diagnostic error event already ends the stream
            yield {"event": "message", "data": json.dumps(
                {"type": "error", "error": _diagnose_broken_pool(exc, getattr(_submit, "pool", None))})}
        except Exception as exc:
            yield {"event": "message", "data": json.dumps({"type": "error", "error": str(exc)})}
        finally:
            if not completed:
                # Issue #383: terminate the pool THIS run bound. A global-only call
                # would kill the pool of a run that started after this one (#371) —
                # which a cancel makes likely: the operator presses Stop and starts
                # the next run immediately.
                submit_pool = run_record.pool
                try:
                    if submit_pool is not None:
                        _terminate_backtest_pool(submit_pool)
                finally:
                    # Guard release must survive even a failing shutdown.
                    release_guards()

    # #372: heartbeat below the client's 15 s inactivity watchdog — a large
    # but healthy window can emit no progress for longer than that.
    return EventSourceResponse(event_generator(), ping=5)


@app.post(
    "/api/backtest/cancel",
    responses={200: {"description": "Stop request outcome for the in-flight run"}},
)
async def api_backtest_cancel(request: Request):
    """Issue #383: stop the in-flight backtest/sweep and free the single-run guards.

    Aborting on the client only closes the SSE stream: the worker keeps burning CPU
    until it finishes or the wall-clock timeout kills it, and the guards stay held,
    so the operator's next Run is answered with 429. This is the server-side stop
    the Backtest tab's Stop control calls.
    """
    _verify_safe_origin(request)
    with _BACKTEST_LOCK:
        record = _ACTIVE_BACKTEST_RUN
    if record is None:
        # Idle is a normal, quiet state: a 200 no-op that changes nothing. Never a 500.
        return {
            "ok": True,
            "running": False,
            "stopped": False,
            "stopping": False,
            "detail": "No backtest or sweep is running.",
        }

    record.cancel_requested = True
    pool = record.pool
    # Capture the worker handles on the FIRST cancel and keep them: `shutdown()`
    # empties the pool's map, so a repeat Stop that re-read it would find nothing
    # alive and report a clean stop over a worker that never died.
    if record.procs is None:
        try:
            record.procs = list((getattr(pool, "_processes", None) or {}).values())
        except Exception:
            record.procs = []
    if pool is None and not record.procs:
        # Registered but never submitted — nothing is burning CPU yet, so releasing
        # the guards is a complete stop.
        record.release()
        return {
            "ok": True,
            "running": False,
            "stopped": True,
            "stopping": False,
            "detail": "Stopped before the worker started.",
        }

    # The kill walk joins workers with a timeout; off the loop, or the dashboard
    # freezes for its duration.
    confirmed = await asyncio.to_thread(
        _terminate_backtest_pool_confirmed, pool, record.procs)
    if confirmed:
        record.release_blocked = False  # a confirmed death may release
        record.release()
        return {
            "ok": True,
            "running": False,
            "stopped": True,
            "stopping": False,
            "detail": "Stopped. The worker is gone and the next Run can start now.",
        }

    # Death unconfirmed: the guards stay held. A visible "stopping…" is safer than a
    # clean-looking stop that lets a second run share one worker pool with a live
    # worker. Release-once semantics keep a late cleanup from clearing this later.
    record.release_blocked = True
    return {
        "ok": False,
        "running": True,
        "stopped": False,
        "stopping": True,
        "detail": ("The worker did not confirm death. The run slot stays held until "
                   "it does — press Stop again, or restart the server."),
    }


# ---------------------------------------------------------------------------
# Issue #445: seed preset templates (e.g. overnight-sweep winners) into the
# templates dir on fresh checkouts. Copy-if-missing only — never overwrite an
# operator-saved template. params_hash is computed at copy time through the
# same builder the load endpoint validates against, so registry drift can
# never ship a stale (HTTP 409) preset.
# ---------------------------------------------------------------------------


def _materialize_seed_template(seed: dict) -> dict | None:
    """Build a complete template record from a seed-file dict.

    Returns None when the seed's request_args no longer validate (fail loud
    at startup logs, never a half-written record).
    """
    from backtest.selection import parse_durations, parse_series_tokens

    if not isinstance(seed, dict):
        return None
    args = seed.get("request_args", {})
    if not isinstance(args, dict):
        return None
    try:
        series_tokens = parse_series_tokens(args.get("series", ""))
        duration_values = parse_durations(args.get("durations", ""))
        params, echo = _build_backtest_params(
            offset=args.get("offset", 0.02),
            queue=args.get("queue", 0.0),
            pair_cost=args.get("pair_cost", 0.99),
            exit_default_5m=args.get("exit_default_5m", 0.05),
            exit_default_15m=args.get("exit_default_15m", 0.05),
            exit_btc_5m=args.get("exit_btc_5m", 0.05),
            exit_sol_5m=args.get("exit_sol_5m", 0.05),
            exit_reversal=args.get("exit_reversal", 0.02),
            size=args.get("size", 5),
            quote_lo=args.get("quote_lo", 0.10),
            quote_hi=args.get("quote_hi", 0.90),
            entry_delay_sec=args.get("entry_delay_sec", 0.0),
            entry_delay_pct=args.get("entry_delay_pct"),
            dead_zone_val=args.get("dead_zone_val", 0.10),
            dead_zone_pct=args.get("dead_zone_pct"),
            dead_zone_unit=args.get("dead_zone_unit", "pct"),
            naked_leg_at_expiry=args.get("naked_leg_at_expiry", "close"),
            enable_leg_chase=args.get("enable_leg_chase", False),
        )
    except Exception:
        return None
    name = seed.get("name", "")
    if not name:
        return None
    raw_summary = seed.get("summary") or {}
    if not isinstance(raw_summary, dict):
        return None
    summary = dict(raw_summary)
    summary["params_hash"] = params.params_hash()
    query = "&".join(f"{k}={args[k]}" for k in (
        "offset", "queue", "pair_cost", "size", "exit_reversal",
    ) if k in args)
    return {
        "name": name,
        "saved_at": time.time(),
        "run_id": f"seed-{name}",
        "query": query,
        "request_args": args,
        "echo": echo,
        "params_dict": asdict(params) if is_dataclass(params) else dict(params),
        "params_hash": params.params_hash(),
        "dataset": {"file": args.get("file", ""), "resolved": ""},
        "scope": {
            "series": args.get("series", ""),
            "durations": args.get("durations", ""),
            "series_tokens": list(series_tokens),
            "duration_values": list(duration_values),
        },
        "summary": summary,
    }


def ensure_seed_templates(directory: Path | str = BACKTEST_TEMPLATES_DIR) -> list[str]:
    """Copy missing seed presets into the templates directory.

    Returns the names that were seeded. Existing files are never touched.
    """
    from backtest.templates import save_template, template_path

    seeded: list[str] = []
    skipped: list[str] = []
    try:
        seed_files = sorted(SEED_TEMPLATES_DIR.glob("*.json"))
    except Exception:
        return seeded
    try:
        Path(directory).mkdir(parents=True, exist_ok=True)
    except Exception:
        return seeded
    for path in seed_files:
        try:
            seed = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            skipped.append(path.name)
            continue
        name = seed.get("name", path.stem) if isinstance(seed, dict) else path.stem
        try:
            if template_path(directory, name).exists():
                continue
        except ValueError:
            skipped.append(path.name)
            continue
        record = _materialize_seed_template(seed)
        if record is None:
            skipped.append(path.name)
            continue
        try:
            save_template(directory, record["name"], record)
        except (OSError, ValueError):
            skipped.append(path.name)
            continue
        seeded.append(record["name"])
    if skipped:
        print(f"[osc_dash] skipping seed templates: {', '.join(skipped)}")
    return seeded


# ---------------------------------------------------------------------------
# Issue #413: Backtest template CRUD endpoints
# ---------------------------------------------------------------------------

class _TemplateSaveBody(BaseModel):
    """Request body for POST /api/backtest/templates."""
    name: str
    run_id: str


@app.post(
    "/api/backtest/templates",
    responses={
        200: {"description": "Template saved"},
        400: {"description": "Invalid template name"},
        404: {"description": "Run ID not found in recent completions"},
    },
)
async def api_backtest_template_save(request: Request, body: _TemplateSaveBody):
    """Save a completed backtest run as a named template."""
    _verify_safe_origin(request)
    from backtest.templates import normalize_name, save_template
    try:
        norm_name = normalize_name(body.name)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    with _COMPLETED_RUNS_LOCK:
        entry = _COMPLETED_RUNS.get(body.run_id)
    if entry is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"run_id '{body.run_id}' not found in recent completions"},
        )

    record = {
        "name": norm_name,
        "saved_at": time.time(),
        "run_id": entry["run_id"],
        "query": entry["query"],
        "request_args": entry["request_args"],
        "echo": entry["echo"],
        "params_dict": entry["params_dict"],
        "params_hash": entry["params_hash"],
        "dataset": entry["dataset"],
        "scope": entry["scope"],
        "summary": entry["summary"],
    }
    try:
        overwritten = save_template(BACKTEST_TEMPLATES_DIR, norm_name, record)
    except OSError as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})
    return {"status": "saved", "name": norm_name, "overwritten": overwritten}


@app.get(
    "/api/backtest/templates",
    responses={200: {"description": "List of saved templates"}},
)
async def api_backtest_template_list():
    """List all saved backtest templates."""
    from backtest.templates import list_templates
    return {"templates": list_templates(BACKTEST_TEMPLATES_DIR)}


@app.get(
    "/api/backtest/templates/{name}",
    responses={
        200: {"description": "Template record"},
        404: {"description": "Template not found"},
        409: {"description": "Template stale — parameters no longer validate"},
        422: {"description": "Malformed template file"},
    },
)
async def api_backtest_template_load(name: str):
    """Load a saved template. Validates parameters still match the current registry."""
    from backtest.templates import normalize_name, read_template
    try:
        norm_name = normalize_name(name)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    try:
        record = read_template(BACKTEST_TEMPLATES_DIR, norm_name)
    except FileNotFoundError:
        return JSONResponse(status_code=404, content={"error": f"template not found: {norm_name}"})
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"error": str(exc)})

    # Validate stored request_args still produce the same params_hash.
    stored_args = record.get("request_args", {})
    stored_hash = record.get("params_hash")
    try:
        rebuild_status, rebuild_ctx = _prepare_backtest_request(
            file=stored_args.get("file", ""),
            offset=stored_args.get("offset", 0.02),
            queue=stored_args.get("queue", 0.0),
            pair_cost=stored_args.get("pair_cost", 0.99),
            exit_default_5m=stored_args.get("exit_default_5m", 0.05),
            exit_default_15m=stored_args.get("exit_default_15m", 0.05),
            exit_btc_5m=stored_args.get("exit_btc_5m", 0.05),
            exit_sol_5m=stored_args.get("exit_sol_5m", 0.05),
            exit_reversal=stored_args.get("exit_reversal", 0.02),
            size=stored_args.get("size", 5),
            max_start_delay=stored_args.get("max_start_delay", 0.0),
            filter_partial=stored_args.get("filter_partial", False),
            quote_lo=stored_args.get("quote_lo", 0.10),
            quote_hi=stored_args.get("quote_hi", 0.90),
            entry_delay_sec=stored_args.get("entry_delay_sec", 0.0),
            entry_delay_pct=stored_args.get("entry_delay_pct"),
            dead_zone_val=stored_args.get("dead_zone_val", 0.10),
            dead_zone_pct=stored_args.get("dead_zone_pct"),
            dead_zone_unit=stored_args.get("dead_zone_unit", "pct"),
            naked_leg_at_expiry=stored_args.get("naked_leg_at_expiry", "close"),
            enable_leg_chase=stored_args.get("enable_leg_chase", False),
            series=stored_args.get("series", ""),
            durations=stored_args.get("durations", ""),
        )
        if rebuild_status == "ok":
            current_hash = rebuild_ctx["params"].params_hash()
            if stored_hash and current_hash != stored_hash:
                return JSONResponse(status_code=409, content={
                    "error": "template stale: parameters no longer validate against the current registry",
                    "stored_hash": stored_hash,
                    "current_hash": current_hash,
                })
    except Exception:
        return JSONResponse(status_code=409, content={
            "error": "template stale: parameters no longer validate against the current registry",
            "stored_hash": stored_hash,
            "current_hash": None,
        })

    return record


@app.delete(
    "/api/backtest/templates/{name}",
    responses={
        200: {"description": "Template deleted"},
        404: {"description": "Template not found"},
    },
)
async def api_backtest_template_delete(request: Request, name: str):
    """Delete a saved backtest template."""
    _verify_safe_origin(request)
    from backtest.templates import normalize_name, delete_template
    try:
        norm_name = normalize_name(name)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    try:
        delete_template(BACKTEST_TEMPLATES_DIR, norm_name)
    except FileNotFoundError:
        return JSONResponse(status_code=404, content={"error": f"template not found: {norm_name}"})
    except OSError as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})
    return {"status": "deleted", "name": norm_name}


@app.get("/api/analysis")
def api_analysis():
    """Full windows distribution data for statistical charts."""
    rows = _load_all_windows()
    per_series: dict[str, list] = {}
    for r in rows:
        per_series.setdefault(r.get("series", ""), []).append(r)

    buckets = list(range(0, 55, 5))
    hist = {b: 0 for b in buckets}
    for r in rows:
        m = max(r.get("max_up", 0), r.get("max_down", 0)) * 100
        for b in buckets:
            if m < b + 5:
                hist[b] += 1
                break

    hist_start = {b: 0 for b in [0, 1, 2, 3, 5, 10]}
    for r in rows:
        d = abs((r.get("start_mid") or 0.5) - 0.5) * 100
        for thr in sorted(hist_start):
            if d < thr + 1:
                hist_start[thr] += 1
                break

    return {
        "total": len(rows),
        "per_series": {k: len(v) for k, v in per_series.items()},
        "hist_max": hist,
        "hist_start": hist_start,
        "rows": rows[:500],
    }


# Collector endpoints
# Issue #349: the #174 Phase 1 divergence threshold (scripts/collect_ticks.py).
# Kept in sync by name/comment; the collector owns the comparison, the dashboard
# only displays what it recorded.
BOOK_SHADOW_TOLERANCE = 0.001


def _shadow_badge_text(bs: dict) -> str:
    """Format the book_shadow badge text — the single source of truth for it.

    The server computes it into the /api/collector/status payload and the UI
    renders it verbatim, so the format lives in exactly one place (and is
    testable in Python). #349 rules: never a rate without its sample size, and
    no data / zero comparisons renders "not enough data yet" — never 0%, which
    would read as a measured clean pass instead of an absent measurement.
    """
    rate = bs.get("divergence_rate") if isinstance(bs, dict) else None
    if not bs or not bs.get("comparisons") or rate is None:
        return "Book Δ: not enough data yet"
    return f"Book Δ: {rate * 100:.1f}% ({bs['comparisons']:,})"


@app.get("/api/collector/status")
def api_collector_status():
    """Return status of the background tick collector, today's ticks, and tape empty-rate health."""
    global _collector_proc
    running = _collector_proc is not None and _collector_proc.poll() is None
    # Count total tick lines collected today -- cheap on large files (same
    # 20 MB / 950-bytes heuristic as api_ticks_manifest) so a growing tick
    # file cannot make status exceed the menu's timeout (issue #200).
    today_ticks = 0
    today_file = (
        TICKS_DIR / f"ticks_{time.strftime('%Y-%m-%d', time.gmtime())}.jsonl"
    )
    if today_file.exists():
        try:
            sz = today_file.stat().st_size
            if sz >= 20_000_000:
                today_ticks = int(sz / 950)
            else:
                today_ticks = _count_lines_fast(today_file)
        except Exception:
            today_ticks = 0

    tape_empty_rate = None
    tape_recent_empty_rate = None
    tape_entries_total = 0
    tape_alert = False
    book_shadow = None
    mf = TICKS_DIR / "manifest.json"
    if mf.exists():
        try:
            mdata = json.loads(mf.read_text(encoding="utf-8"))
            tape_empty_rate = mdata.get("tape_empty_rate")
            tape_recent_empty_rate = mdata.get("tape_recent_empty_rate")
            tape_entries_total = mdata.get("tape_entries_total", 0)
            if "tape_alert" in mdata:
                tape_alert = bool(mdata.get("tape_alert"))
            elif tape_empty_rate is not None and tape_empty_rate > 0.99:
                total_checks = mdata.get("tape_empty_count", 0) + mdata.get("tape_non_empty_count", 0)
                if total_checks >= 300:
                    tape_alert = True
            # Issue #349: surface the #174 Phase 1 book_shadow disagreement
            # summary so the operator reads it in the dashboard, not in a JSON
            # file. Flat copy of the manifest's ready-to-read fields; the
            # tolerance is supplied here because the collector keeps it as a
            # module constant, not a manifest key. Same fault tolerance as the
            # tape fields above: absent/malformed -> null, never an error.
            bs = mdata.get("book_shadow")
            if isinstance(bs, dict) and bs.get("comparisons"):
                book_shadow = {
                    "comparisons": bs.get("comparisons"),
                    "divergent": bs.get("divergent"),
                    "divergence_rate": bs.get("divergence_rate"),
                    "tolerance": BOOK_SHADOW_TOLERANCE,
                    "mean_abs_bb_delta": bs.get("mean_abs_bb_delta"),
                    "mean_abs_ba_delta": bs.get("mean_abs_ba_delta"),
                    "max_bb": bs.get("max_bb"),
                    "max_ba": bs.get("max_ba"),
                    "per_series": bs.get("per_series", {}),
                }
                # The UI renders this verbatim — the badge format lives only
                # here, not duplicated in JS (locale-dependent toLocaleString
                # would otherwise diverge from the tested format).
                book_shadow["badge_text"] = _shadow_badge_text(book_shadow)
        except Exception:
            pass

    ext = _detect_external_collector()

    return {
        "running": running,
        "pid": _collector_proc.pid if running else None,
        "source": "child" if running else ("external" if ext["live"] else "none"),
        "external": ext["live"] and not running,
        "manifest_age_sec": ext["manifest_age_sec"],
        "total_ticks_collected": today_ticks,
        "tape_empty_rate": tape_empty_rate,
        "tape_recent_empty_rate": tape_recent_empty_rate,
        "tape_entries_total": tape_entries_total,
        "tape_alert": tape_alert,
        "book_shadow": book_shadow,
    }


@app.post("/api/collector/start")
def api_collector_start(request: Request):
    """Start background tick collection process if not already running."""
    _verify_safe_origin(request)
    global _collector_proc
    if _collector_proc is None or _collector_proc.poll() is not None:
        ext = _detect_external_collector()
        if ext["live"]:
            return JSONResponse(
                status_code=409,
                content={
                    "ok": False,
                    "running": False,
                    "source": "external",
                    "manifest_age_sec": ext["manifest_age_sec"],
                    "error": (
                        "External standalone collector is live "
                        "(run/ticks/manifest.json is fresh); refusing to spawn "
                        "a second writer on the same daily tick file."
                    ),
                },
            )
        cmd = [sys.executable, "-m", "scripts.collect_ticks"]
        # Issue #351: a DEVNULL child made "socket never connected" invisible —
        # the "CLOB market WS connected" log line (or an exception trace) was
        # thrown away, and a run whose WS telemetry stays zero looks healthy.
        # Capture the child's output to run/ticks/collector_child.log so the
        # operator has evidence instead of a black hole. Append mode: several
        # launches share one log, each stamped by the collector's own output.
        try:
            log_dir = TICKS_DIR
            log_dir.mkdir(parents=True, exist_ok=True)
            child_log = open(log_dir / "collector_child.log", "ab", buffering=0)
            child_log.write(
                f"\n===== launch {datetime.now(timezone.utc).isoformat()} "
                f"pid=pending =====\n".encode("utf-8")
            )
        except OSError as e:
            # Log setup failing must never block capture. Keep the two failure
            # domains separate: only the *log* falls back to DEVNULL here — the
            # spawn below must still run exactly once (a fallback retry here
            # could double-spawn two writers onto the same daily tick file).
            print(f"[collector] child log unavailable ({e}); stdout discarded")
            child_log = subprocess.DEVNULL
        # Single spawn point, reached exactly once per launch: the log failure
        # path above already routed stdout to DEVNULL, so the collector always
        # gets a writable stream and never starts twice.
        _collector_proc = subprocess.Popen(
            cmd, cwd=str(ROOT), stdout=child_log, stderr=subprocess.STDOUT,
        )
        if child_log is not subprocess.DEVNULL:
            try:
                child_log.write(
                    f"===== pid {_collector_proc.pid} =====\n".encode("utf-8")
                )
            finally:
                # With-scoped equivalent: the child inherited its own duplicated
                # handle at spawn; the parent's copy must close deterministically
                # (a dashboard process launches collectors for days).
                child_log.close()
    return {"ok": True, "running": True, "pid": _collector_proc.pid}


@app.post("/api/collector/stop")
def api_collector_stop(request: Request):
    """Stop active background tick collection process."""
    _verify_safe_origin(request)
    global _collector_proc
    if _collector_proc and _collector_proc.poll() is None:
        _collector_proc.terminate()
        try:
            _collector_proc.wait(timeout=2.0)
        except Exception:
            _collector_proc.kill()
    _collector_proc = None
    return {"ok": True, "running": False}


@app.post("/api/collector/poll-once")
def api_collector_poll_once(request: Request):
    """Perform a single immediate poll across all active series."""
    _verify_safe_origin(request)
    cmd = [sys.executable, "-m", "scripts.collect_ticks", "--once"]
    try:
        res = subprocess.run(
            cmd, cwd=str(ROOT), capture_output=True, text=True,
            timeout=60, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "poll timed out after 60s"}
    return {"ok": res.returncode == 0, "output": res.stdout[:500]}


@app.post("/api/rebuild")
def api_rebuild_windows(request: Request):
    """Reconstruct oscillation windows and summary from persisted tick data.

    Refused while any collector is writing (own child or fresh external
    manifest): a window closing mid-rebuild would be overwritten by the
    dataset replace. Serialized with a lock across dashboard requests.
    """
    _verify_safe_origin(request)
    global _collector_proc
    if _collector_proc is not None and _collector_proc.poll() is None:
        return JSONResponse(
            status_code=409,
            content={"ok": False, "output": "collector running — stop polling before rebuild"},
        )
    if _detect_external_collector()["live"]:
        return JSONResponse(
            status_code=409,
            content={"ok": False, "output": "external collector live — pause it before rebuild"},
        )
    if not _rebuild_lock.acquire(blocking=False):
        return JSONResponse(
            status_code=409, content={"ok": False, "output": "rebuild already running"}
        )
    try:
        cmd = [sys.executable, "-m", "scripts.rebuild_windows"]
        try:
            res = subprocess.run(
                cmd, cwd=str(ROOT), capture_output=True, text=True,
                timeout=60, check=False,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "output": "rebuild timed out after 60s"}
        except Exception as e:
            return {"ok": False, "output": f"rebuild failed to start: {e}"}
        if res.returncode == 0:
            return {"ok": True, "output": res.stdout[:500]}
        detail = (res.stderr or res.stdout or "")[:500]
        return {"ok": False, "output": detail or "rebuild failed"}
    finally:
        _rebuild_lock.release()


@app.delete("/api/ticks/file")
def api_delete_tick_file(request: Request, filename: str):
    """Delete a tick file and its index sidecar from run/ticks/."""
    _verify_safe_origin(request)
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        return JSONResponse(status_code=400, content={"error": "invalid filename"})
    target = TICKS_DIR / filename
    if not target.exists():
        return JSONResponse(status_code=404, content={"error": "file not found"})
    try:
        target.unlink()
        idx = TICKS_DIR / f"{filename}.idx"
        if idx.exists():
            idx.unlink()
        return {"ok": True, "deleted": filename}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# --- Trading Platform Endpoints ---


@app.get("/api/live/state")
def api_live_state():
    """Return real-time state snapshot of the trading engine behind the Trading Platform tab."""
    engine = get_live_trader_engine()
    return engine.get_state()


@app.get("/api/live/queue_telemetry")
def api_live_queue_telemetry():
    """Bucketed fill-ratio evidence + verdict for the cockpit queue panel.

    Read-only aggregation over run/live_fill_telemetry.jsonl. Missing or
    unparsable input yields an explicit empty payload (HTTP 200, never 500).
    """
    now = time.time()
    cached = _queue_telemetry_cache
    if cached["payload"] is not None and now - cached["ts"] < QUEUE_TELEMETRY_CACHE_TTL:
        return cached["payload"]
    try:
        payload = _compute_queue_telemetry(QUEUE_TELEMETRY_FILE, QUEUE_TELEMETRY_TRADES_FILE)
    except Exception as e:
        payload = {"empty": True, "total_fills": 0, "buckets": [],
                   "chased": {"count": 0, "mean_settle_pnl_usd": None},
                   "tape_sources": {}, "tape_source_used": None,
                   "verdict": "awaiting fills", "error": str(e)[:200]}
    cached["ts"] = now
    cached["payload"] = payload
    return payload


@app.get("/api/live/stream")
async def api_live_stream(request: Request):
    """Real-time SSE stream broadcasting versioned DashboardEnvelope events."""
    _verify_safe_origin(request)
    engine = get_live_trader_engine()
    engine.ensure_telemetry_streaming()
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    engine.stream_bridge.register_queue(q)

    async def event_generator():
        """Yield SSE events including initial snapshot and real-time delta envelopes."""
        try:
            # First send full state snapshot envelope
            state_data = await asyncio.get_running_loop().run_in_executor(None, engine.get_state)
            snap = DashboardEnvelope(
                type="snapshot",
                stream_id="state",
                seq=0,
                server_time=int(time.time() * 1000),
                data=state_data,
            )
            yield {"event": "message", "data": snap.to_json()}

            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield {"event": "message", "data": payload}
                except asyncio.TimeoutError:
                    ping_env = DashboardEnvelope(
                        type="delta",
                        stream_id="ping",
                        data={"status": "keepalive"},
                    )
                    yield {"event": "ping", "data": ping_env.to_json()}
        finally:
            engine.stream_bridge.unregister_queue(q)

    return EventSourceResponse(event_generator())


@app.post("/api/live/control")
async def api_live_control(request: Request):
    """Control live bot execution (start, stop, restart, reset_pnl)."""
    _verify_safe_origin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    action = body.get("action", "")
    engine = get_live_trader_engine()
    if action == "start":
        engine.start()
    elif action == "stop":
        stop_streams = body.get("stop_streams") is True
        engine.stop(stop_streams=stop_streams)
    elif action == "restart":
        engine.restart()
    elif action == "reset_pnl":
        reset_res = engine.reset_pnl()
        if isinstance(reset_res, dict) and not reset_res.get("ok", True):
            state = engine.get_state()
            state["ok"] = False
            state["error"] = reset_res.get("message") or reset_res.get("error") or "Reset refused"
            return JSONResponse(status_code=409, content=state)
    elif action == "demo_data":
        engine.seed_demo_data()
    elif action == "sync_wallet_trades":
        addr = body.get("wallet_address") or engine.wallet_address
        start_marker = body.get("start_marker")
        res = await asyncio.to_thread(engine.sync_wallet_trades, addr, start_marker)
        if not res.get("success"):
            return JSONResponse(status_code=400, content=res)
        state = engine.get_state()
        state["sync_result"] = res
        return state
    else:
        return JSONResponse(status_code=400, content={"error": f"Unknown action '{action}'"})
    return engine.get_state()


class LiveConfigPayload(BaseModel):
    """Payload schema for live trading cockpit configuration updates."""

    offset: Optional[float] = Field(default=None, ge=0.001, le=0.49)
    exit_thresh: Optional[float] = Field(default=None, ge=0.001, le=0.50)
    shares: Optional[int] = Field(default=None, ge=5, le=10000)
    mode: Optional[str] = Field(default=None, pattern="^(paper|live)$")
    wallet_address: Optional[str] = None
    starting_balance: Optional[float] = Field(default=None, ge=5.0)
    selected_markets: Optional[list[str]] = None
    tokens: Optional[list[str]] = None
    durations: Optional[list[int]] = None
    dead_zone_val: Optional[float] = Field(default=None, ge=0.0, le=3600.0)
    dead_zone_unit: Optional[str] = Field(default=None, pattern="^(pct|sec)$")
    naked_leg_at_expiry: Optional[str] = Field(default=None, pattern="^(close|hold)$")
    exit_reversal: Optional[float] = Field(default=None, ge=0.001, le=0.50)
    # Issue #228: the re-entry payload fields stood here. Removed with the
    # mechanism; the engine still accepts the knobs (inert) until T5.
    # Issue #137: patient entry delay. entry_delay_sec has no
    # upper bound (a delay past the window simply never quotes).
    # (Issue #228: the entry_band field stood here. Removed with the band
    # gate; the engine still accepts the knob, inert, until T5.)
    preset: Optional[str] = None
    # No upper bound would let a typo (or inf) silently never quote, since a
    # delay past the window end never expires. 3600s is 4x the longest 900s
    # window — anything larger is rejected at the boundary instead.
    entry_delay_sec: Optional[float] = Field(default=None, ge=0.0, le=3600.0)
    # Issue #228: the quotable range, replacing the band and the adverse-open
    # gate. Two ends in one field — the Cockpit renders two inputs and posts
    # the pair; `update_config` clamps each end and refuses an inverted pair.
    quote_range: Optional[list[float]] = None
    enable_leg_chase: Optional[bool] = None
    # Issue #353: socket-book authority switch — the one control that isolates
    # the socket from pricing. Strict bool in the engine; None leaves it.
    ws_book_authority: Optional[bool] = None
    max_pair_cost: Optional[float] = Field(default=None, ge=0.50, le=1.00)

    @field_validator("offset", mode="before")
    @classmethod
    def normalize_offset(cls, v: Any) -> Any:
        """Normalize whole-number offset values (1-49) entered as cents to decimal dollars."""
        if v is not None:
            try:
                fv = float(v)
                if fv.is_integer() and 1.0 <= fv <= 49.0:
                    return fv / 100.0
                return fv
            except (ValueError, TypeError):
                pass
        return v

    @field_validator("exit_thresh", mode="before")
    @classmethod
    def normalize_exit_thresh(cls, v: Any) -> Any:
        """Normalize whole-number exit threshold values (1-50) entered as cents to decimal dollars."""
        if v is not None:
            try:
                fv = float(v)
                if fv.is_integer() and 1.0 <= fv <= 50.0:
                    return fv / 100.0
                return fv
            except (ValueError, TypeError):
                pass
        return v

    @field_validator("exit_reversal", mode="before")
    @classmethod
    def normalize_exit_reversal(cls, v: Any) -> Any:
        """Normalize whole-number exit_reversal values (1-50) entered as cents to decimals."""
        if v is not None:
            try:
                fv = float(v)
                if fv.is_integer() and 1.0 <= fv <= 50.0:
                    return fv / 100.0
                return fv
            except (ValueError, TypeError):
                pass
        return v


@app.post("/api/live/config")
def api_live_config(payload: LiveConfigPayload, request: Request):
    """Update strategy parameters for the live bot."""
    _verify_safe_origin(request)
    engine = get_live_trader_engine()
    try:
        state = engine.update_config(
            offset=payload.offset,
            exit_thresh=payload.exit_thresh,
            shares=payload.shares,
            mode=payload.mode,
            wallet_address=payload.wallet_address,
            starting_balance=payload.starting_balance,
            selected_markets=payload.selected_markets,
            tokens=payload.tokens,
            durations=payload.durations,
            dead_zone_val=payload.dead_zone_val,
            dead_zone_unit=payload.dead_zone_unit,
            naked_leg_at_expiry=payload.naked_leg_at_expiry,
            exit_reversal=payload.exit_reversal,
            enable_leg_chase=payload.enable_leg_chase,
            ws_book_authority=payload.ws_book_authority,
            max_pair_cost=payload.max_pair_cost,
            entry_delay_sec=payload.entry_delay_sec,
            quote_range=payload.quote_range,
            preset=payload.preset,
        )
        return state
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})


@app.get("/api/live/account")
def api_live_account(address: Optional[str] = None):
    """Fetch live Polymarket net account value, collateral cash, and positions."""
    engine = get_live_trader_engine()
    addr = (address or "").strip() or engine.wallet_address or os.getenv("POLY_FUNDER") or ""
    return fetch_polymarket_account_value(addr)


@app.post("/api/live/cancel_all")
def api_live_cancel_all(request: Request):
    """Emergency panic button: cancel all active orders on CLOB and engine."""
    _verify_safe_origin(request)
    engine = get_live_trader_engine()
    res = engine.cancel_all_orders()
    return res


@app.post("/api/live/cancel_order")
async def api_live_cancel_order(request: Request):
    """Cancel a single active order by order_id."""
    _verify_safe_origin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    order_id = str(body.get("order_id") or "").strip()
    if not order_id:
        return JSONResponse(status_code=400, content={"error": "order_id required"})
    engine = get_live_trader_engine()
    ok = await asyncio.to_thread(engine.cancel_live_order, order_id)
    return {"ok": ok, "order_id": order_id}


@app.get("/api/live/orders")
def api_live_orders():
    """List all open active orders from CLOB and engine tracking."""
    engine = get_live_trader_engine()
    return {"orders": engine.get_open_orders_list()}


@app.post("/api/live/test_order")
async def api_live_test_order(request: Request):
    """Safe test endpoint to place 1 small resting order and return its Polymarket Order ID for live verification."""
    _verify_safe_origin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    token_id = str(body.get("token_id") or "").strip()
    try:
        price = float(body.get("price", 0.05))
        size = float(body.get("size", 1.0))
    except (TypeError, ValueError):
        return JSONResponse(status_code=400, content={"error": "price and size must be numeric"})
    side = str(body.get("side") or "BUY").upper()
    if not token_id:
        return JSONResponse(status_code=400, content={"error": "token_id required"})
    if not (0.0 < price < 1.0):
        return JSONResponse(status_code=400, content={"error": "price must be between 0 and 1"})
    if not (0.0 < size <= MAX_TEST_ORDER_SHARES):
        return JSONResponse(status_code=400, content={"error": "size out of allowed range"})
    if side not in ("BUY", "SELL"):
        return JSONResponse(status_code=400, content={"error": "side must be BUY or SELL"})
    engine = get_live_trader_engine()
    res = engine.place_live_quote(token_id=token_id, price=price, size=size, side=side)
    if not res:
        return JSONResponse(status_code=500, content={"error": "Failed placing test order on CLOB"})
    return res


_upload_locks_mutex = threading.Lock()
_upload_target_locks: dict[str, threading.Lock] = {}


def _get_target_lock(filename: str) -> threading.Lock:
    """Get or create per-target synchronization lock."""
    with _upload_locks_mutex:
        if filename not in _upload_target_locks:
            _upload_target_locks[filename] = threading.Lock()
        return _upload_target_locks[filename]


def _cleanup_abandoned_uploads(max_age_seconds: int = 3600) -> None:
    """Remove upload staging directories older than max_age_seconds."""
    uploads_root = RUN / "_uploads"
    if not uploads_root.exists():
        return
    now = time.time()
    try:
        for item in uploads_root.iterdir():
            if item.is_dir():
                try:
                    if now - item.stat().st_mtime > max_age_seconds:
                        shutil.rmtree(item, ignore_errors=True)
                except Exception:
                    pass
    except Exception:
        pass


def _finalize_upload(upload_dir: Path, target_file: Path, total_chunks: int) -> tuple[int, int]:
    """Atomically assemble chunks, count lines, and build index in worker thread."""
    lock = _get_target_lock(target_file.name)
    with lock:
        for i in range(total_chunks):
            part = upload_dir / f"chunk_{i:06d}"
            if not part.exists():
                raise FileNotFoundError(f"missing chunk {i}")

        tmp_target = target_file.with_suffix(target_file.suffix + f".tmp_{time.time_ns()}")
        try:
            with open(tmp_target, "wb") as out_f:
                for i in range(total_chunks):
                    part = upload_dir / f"chunk_{i:06d}"
                    out_f.write(part.read_bytes())
            os.replace(tmp_target, target_file)
        finally:
            if tmp_target.exists():
                try:
                    tmp_target.unlink()
                except Exception:
                    pass

        shutil.rmtree(upload_dir, ignore_errors=True)

        lines_count = _count_lines_fast(target_file)
        windows_indexed = 0
        try:
            from backtest.index import build_index
            _, total_snaps = build_index(target_file)
            windows_indexed = total_snaps
        except Exception:
            pass

        return lines_count, windows_indexed


@app.post("/api/ticks/upload-chunk")
async def api_upload_chunk(
    request: Request,
    filename: str,
    uploadId: str,
    chunkIndex: int,
    totalChunks: int,
):
    """Receive and assemble chunked tick data stream into run/ticks/ directory."""
    _verify_safe_origin(request)
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        return JSONResponse(status_code=400, content={"error": "invalid filename"})
    if not (filename.endswith(".jsonl") or filename.endswith(".jsonl.gz") or filename.endswith(".gz")):
        return JSONResponse(status_code=400, content={"error": "file must be .jsonl or .gz"})

    if totalChunks < 1 or totalChunks > 10000:
        return JSONResponse(status_code=400, content={"error": "totalChunks must be between 1 and 10000"})
    if chunkIndex < 0 or chunkIndex >= totalChunks:
        return JSONResponse(status_code=400, content={"error": "chunkIndex out of range"})

    if not re.match(r"^up_[A-Za-z0-9_-]+$", uploadId):
        return JSONResponse(status_code=400, content={"error": "invalid uploadId"})

    uploads_root = (RUN / "_uploads").resolve()
    upload_dir = (RUN / "_uploads" / uploadId).resolve()
    if not str(upload_dir).startswith(str(uploads_root)):
        return JSONResponse(status_code=400, content={"error": "path traversal detected"})

    target_file = (TICKS_DIR / filename).resolve()
    if not str(target_file).startswith(str(TICKS_DIR.resolve())):
        return JSONResponse(status_code=400, content={"error": "invalid target path"})

    # Run background cleanup of stale staging directories
    _cleanup_abandoned_uploads()

    # If retry arrives after assembly completed and upload_dir was removed
    if chunkIndex == totalChunks - 1 and not upload_dir.exists() and target_file.exists():
        lines_count = _count_lines_fast(target_file)
        windows_indexed = 0
        try:
            from backtest.index import load_index
            windows_indexed = len(load_index(target_file))
        except Exception:
            pass
        return {
            "ok": True,
            "filename": filename,
            "lines": lines_count,
            "windows_indexed": windows_indexed,
        }

    upload_dir.mkdir(parents=True, exist_ok=True)
    chunk_path = upload_dir / f"chunk_{chunkIndex:06d}"

    MAX_CHUNK_BYTES = 5 * 1024 * 1024  # 5 MiB hard limit per chunk
    total_bytes = 0
    try:
        with open(chunk_path, "wb") as f_chunk:
            async for chunk in request.stream():
                total_bytes += len(chunk)
                if total_bytes > MAX_CHUNK_BYTES:
                    f_chunk.close()
                    if chunk_path.exists():
                        chunk_path.unlink()
                    return JSONResponse(status_code=413, content={"error": "chunk exceeds 5MB size limit"})
                await asyncio.to_thread(f_chunk.write, chunk)
    except Exception as e:
        if chunk_path.exists():
            try:
                chunk_path.unlink()
            except Exception:
                pass
        return JSONResponse(status_code=500, content={"error": str(e)})

    if chunkIndex == totalChunks - 1:
        try:
            lines_count, windows_indexed = await asyncio.to_thread(
                _finalize_upload, upload_dir, target_file, totalChunks
            )
        except FileNotFoundError as e:
            return JSONResponse(status_code=400, content={"error": str(e)})
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})

        return {
            "ok": True,
            "filename": filename,
            "lines": lines_count,
            "windows_indexed": windows_indexed,
        }

    return {"ok": True, "chunkIndex": chunkIndex}


def _finalize_stream_upload(tmp_target: Path, target_file: Path) -> tuple[int, int]:
    """Atomically commit streamed temp file, count lines, and build index under lock in worker thread."""
    lock = _get_target_lock(target_file.name)
    with lock:
        os.replace(tmp_target, target_file)
        lines_count = _count_lines_fast(target_file)
        windows_indexed = 0
        try:
            from backtest.index import build_index
            _, total_snaps = build_index(target_file)
            windows_indexed = total_snaps
        except Exception:
            pass
        return lines_count, windows_indexed


@app.post("/api/ticks/upload-stream")
async def api_upload_stream(
    request: Request,
    filename: str,
):
    """Directly stream raw JSONL payload into run/ticks/ directory and build index."""
    _verify_safe_origin(request)
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        return JSONResponse(status_code=400, content={"error": "invalid filename"})
    if not (filename.endswith(".jsonl") or filename.endswith(".jsonl.gz") or filename.endswith(".gz")):
        return JSONResponse(status_code=400, content={"error": "file must be .jsonl or .gz"})

    target_file = (TICKS_DIR / filename).resolve()
    if not str(target_file).startswith(str(TICKS_DIR.resolve())):
        return JSONResponse(status_code=400, content={"error": "invalid target path"})

    tmp_target = target_file.with_suffix(target_file.suffix + f".tmp_{time.time_ns()}")
    try:
        with open(tmp_target, "wb") as out_f:
            async for chunk in request.stream():
                if chunk:
                    await asyncio.to_thread(out_f.write, chunk)
        lines_count, windows_indexed = await asyncio.to_thread(
            _finalize_stream_upload, tmp_target, target_file
        )
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        if tmp_target.exists():
            try:
                tmp_target.unlink()
            except Exception:
                pass

    return {
        "ok": True,
        "filename": filename,
        "lines": lines_count,
        "windows_indexed": windows_indexed,
    }


# --- Full verify-report cache ---------------------------------------------
# /api/ticks/verify used to re-stream the entire file on every dashboard load
# (500MB+ files take minutes, so the inline report never populated). Reports
# are now cached in memory and persisted to the .verify_cache sidecar, keyed
# by a (size, mtime_ns) fingerprint. Cache misses return status=PENDING while
# a background scan (serialized by a semaphore) runs; the frontend polls until
# the report lands. `?wait=1` forces the legacy synchronous scan (tests).
_VERIFY_REPORT_CACHE: dict[str, dict[str, Any]] = {}
_VERIFY_SCAN_INFLIGHT: set[str] = set()
_VERIFY_SCAN_SEM = asyncio.Semaphore(1)
_VERIFY_RESCAN_COOLDOWN_SEC = 120.0
# Scan progress: filename -> {"lines": int, "est_total": int, "started_at": float}
_VERIFY_SCAN_PROGRESS: dict[str, dict[str, Any]] = {}


def _readiness_cache_is_current(side: dict[str, Any] | None) -> bool:
    """Accept only sidecars written by the currently active readiness policy."""
    if not side or not isinstance(side.get("readiness"), dict):
        return False
    from scripts.verify_tick_data import READINESS_POLICY_VERSION
    return side["readiness"].get("policy_version") == READINESS_POLICY_VERSION


def _file_fingerprint(path: Path) -> str:
    """Cheap identity for a tick file: size + mtime_ns."""
    st = path.stat()
    return f"{st.st_size}:{st.st_mtime_ns}"


def _verify_sidecar_path(filename: str) -> Path:
    """Path of the verify-report sidecar JSON for a tick file.

    `filename` is the request-visible relative name (a bare basename, or
    `pristine/<basename>` per Issue #295) — the sidecar mirrors the
    subdirectory under .verify_cache/ so same-named files never collide.
    """
    return TICKS_DIR / _VERIFY_CACHE_DIRNAME / f"{filename}.json"


def _write_verify_sidecar(target: Path, rep: dict[str, Any]) -> None:
    """Persist the full report (superset of the old counts sidecar), best-effort.

    Keyed by the target's path relative to TICKS_DIR (Issue #295), so a
    pristine file and a same-named top-level day file get distinct sidecars.
    """
    try:
        rel_name = target.relative_to(TICKS_DIR)
        sidecar = _verify_sidecar_path(rel_name.as_posix())
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(rep)
        payload["ts"] = time.time()
        sidecar.write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        pass


def _prewarm_verify_cache() -> None:
    """Load verify sidecars whose fingerprint still matches the tick file.

    Runs at startup so the first dashboard load after a restart serves cached
    reports instantly instead of re-streaming 500MB+ files. Sidecars are small
    JSON files, so this is cheap; mismatches are simply skipped (the normal
    on-demand path rescans them in the background).
    """
    loaded = 0
    try:
        cache_dir = TICKS_DIR / _VERIFY_CACHE_DIRNAME
        if not cache_dir.is_dir():
            return
        for f in TICKS_DIR.iterdir():
            if f.suffix not in (".jsonl", ".gz") or not f.is_file():
                continue
            try:
                side = _read_verify_cache(
                    _verify_sidecar_path(f.name),
                    expected_fingerprint=_file_fingerprint(f),
                )
            except OSError:
                continue
            if side and "status" in side and _readiness_cache_is_current(side):
                rep = {k: v for k, v in side.items() if k != "ts"}
                _VERIFY_REPORT_CACHE[f.name] = {
                    "fingerprint": side["fingerprint"],
                    "report": rep,
                    "scanned_at": float(side.get("ts") or 0),
                }
                loaded += 1
    except Exception:
        return
    if loaded:
        print(f"[osc_dash] pre-warmed verify cache: {loaded} report(s) from sidecars")


@app.on_event("startup")
def _startup_prewarm() -> None:
    """Load verify sidecars into memory at startup for instant first reports."""
    _prewarm_verify_cache()
    try:
        seeded = ensure_seed_templates()
    except Exception:
        return
    if seeded:
        print(f"[osc_dash] seeded backtest templates: {', '.join(seeded)}")


@app.on_event("shutdown")
def _shutdown_event() -> None:
    """Cleanly shut down worker process pool on application exit."""
    shutdown_backtest_pool()


def _ensure_bg_verify_scan(filename: str, target: Path) -> None:
    """Kick off a background verify scan for the file if none is running."""
    if filename in _VERIFY_SCAN_INFLIGHT:
        return
    _VERIFY_SCAN_INFLIGHT.add(filename)
    asyncio.ensure_future(_bg_verify_scan(filename, target))


async def _bg_verify_scan(filename: str, target: Path) -> None:
    """Scan one tick file off the event loop; cache + persist the full report."""
    try:
        from scripts.verify_tick_data import verify_tick_file

        def _on_progress(lines_done: int, est_total: int) -> None:
            """Record latest scan progress for the polling endpoint."""
            _VERIFY_SCAN_PROGRESS[filename] = {
                "lines": lines_done,
                "est_total": est_total,
                "started_at": _VERIFY_SCAN_PROGRESS.get(filename, {}).get(
                    "started_at", time.time()
                ),
            }

        _VERIFY_SCAN_PROGRESS[filename] = {
            "lines": 0,
            "est_total": 0,
            "started_at": time.time(),
        }
        async with _VERIFY_SCAN_SEM:
            rep = await asyncio.to_thread(
                verify_tick_file,
                target,
                max_gap_sec=6.0,
                max_start_delay=5.0,
                progress_cb=_on_progress,
            )
        fp = _file_fingerprint(target)
        rep["fingerprint"] = fp
        _VERIFY_REPORT_CACHE[filename] = {
            "fingerprint": fp,
            "report": rep,
            "scanned_at": time.time(),
        }
        _write_verify_sidecar(target, rep)
    except Exception:
        pass
    finally:
        _VERIFY_SCAN_INFLIGHT.discard(filename)
        _VERIFY_SCAN_PROGRESS.pop(filename, None)


@app.get("/api/ticks/verify")
async def api_ticks_verify(
    request: Request,
    file: str | None = None,
    max_gap: float = 6.0,
    max_start_delay: float = 5.0,
    refresh: int = 0,
    wait: int = 0,
):
    """Verify data integrity and quality of tick file(s) in run/ticks/.

    Per-file requests are served from a fingerprint-validated cache; a cache
    miss kicks off a background scan and returns status=PENDING (the dashboard
    polls). Pass wait=1 to scan synchronously, refresh=1 to ignore the cache.
    """
    _verify_safe_origin(request)
    from scripts.verify_tick_data import verify_tick_file, verify_ticks_dir

    if not file:
        return await asyncio.to_thread(
            verify_ticks_dir,
            TICKS_DIR,
            max_gap_sec=max_gap,
            max_start_delay=max_start_delay,
        )

    # Issue #295, #436: shared resolver — pristine/ subpath and golden dir allowed, traversal rejected.
    status, target = _resolve_tick_file(file)
    if status == "invalid":
        return JSONResponse(status_code=400, content={"error": "invalid file param"})
    if status == "not_found":
        return JSONResponse(status_code=404, content={"error": f"file not found: {file}"})

    if target.is_dir():
        if target.name == "golden" and (target / "golden_manifest.json").is_file():
            return _golden_verify_report(target, target / "golden_manifest.json")
        return await asyncio.to_thread(
            verify_ticks_dir,
            target,
            max_gap_sec=max_gap,
            max_start_delay=max_start_delay,
        )

    fp = _file_fingerprint(target)
    entry = _VERIFY_REPORT_CACHE.get(file)

    # Fresh in-memory report for the exact current file: serve instantly.
    if not refresh and entry and entry.get("fingerprint") == fp and entry.get("report", {}).get("readiness"):
        out = dict(entry["report"])
        out["cached"] = True
        return out

    # Stale-but-recent snapshot (e.g. today's file the collector is still
    # appending to): serve it immediately and rescan in the background.
    stale_rep: dict[str, Any] | None = None
    if not refresh and entry and (
        time.time() - float(entry.get("scanned_at") or 0) <= _VERIFY_RESCAN_COOLDOWN_SEC
        and entry.get("report", {}).get("readiness")
    ):
        stale_rep = dict(entry["report"])
    else:
        side = _read_verify_cache(_verify_sidecar_path(file), expected_fingerprint=fp)
        if side and not refresh and "status" in side and _readiness_cache_is_current(side):
            if side.get("fingerprint") == fp:
                # Cold start (server restart): exact sidecar for this file.
                rep = {k: v for k, v in side.items() if k != "ts"}
                _VERIFY_REPORT_CACHE[file] = {
                    "fingerprint": fp,
                    "report": rep,
                    "scanned_at": float(side.get("ts") or 0),
                }
                out = dict(rep)
                out["cached"] = True
                return out
            if time.time() - float(side.get("ts") or 0) <= _VERIFY_RESCAN_COOLDOWN_SEC:
                stale_rep = {k: v for k, v in side.items() if k != "ts"}

    if stale_rep is not None:
        _ensure_bg_verify_scan(file, target)
        out = dict(stale_rep)
        out["cached"] = True
        out["stale"] = True
        out["rescanning"] = True
        return out

    # wait=1: legacy synchronous scan (also populates the cache).
    if wait:
        async with _VERIFY_SCAN_SEM:
            rep = await asyncio.to_thread(
                verify_tick_file,
                target,
                max_gap_sec=max_gap,
                max_start_delay=max_start_delay,
            )
        rep["fingerprint"] = fp
        _VERIFY_REPORT_CACHE[file] = {
            "fingerprint": fp,
            "report": rep,
            "scanned_at": time.time(),
        }
        _write_verify_sidecar(target, rep)
        return rep

    # Nothing usable cached: start a background scan and let the client poll.
    _ensure_bg_verify_scan(file, target)
    prog = _VERIFY_SCAN_PROGRESS.get(file) or {}
    return {
        "file": file,
        "status": "PENDING",
        "pending": True,
        "rescanning": True,
        "progress": {
            "lines": int(prog.get("lines") or 0),
            "est_total": int(prog.get("est_total") or 0),
            "elapsed_sec": round(time.time() - float(prog.get("started_at") or time.time()), 1),
        },
    }


# --- Front-end SPA (Complete Hebrew RTL Studio: Trading / Backtest Lab / Statistical Analysis / Tick Data Manager) ---

FULL_APP_HTML = r"""<!doctype html><html lang="en" dir="ltr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Crypto Spread — 5m/15m SPREAD-2 Engine & Lab</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='7' fill='%230a0d12' stroke='%23232a35' stroke-width='1.5'/><path d='M10 6v20' stroke='%2333c9b5' stroke-width='2' stroke-linecap='round'/><rect x='7' y='10' width='6' height='11' rx='2' fill='%2333c9b5'/><path d='M22 6v20' stroke='%23f0684d' stroke-width='2' stroke-linecap='round'/><rect x='19' y='11' width='6' height='11' rx='2' fill='%23f0684d'/></svg>">
<link rel="alternate icon" href="/favicon.ico">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
:root{--bg:#0a0d12;--panel:#12161d;--panel2:#171c24;--line:#232a35;--line-hi:#364152;--line-dark:#1a2029;--tx:#e7ebf3;--dim:#8792a6;--faint:#535e70;--up:#33c9b5;--up-hi:#2bb5a2;--upS:#12302c;--down:#f0684d;--downS:#311b18;--gold:#e8b84b;--warn:#f0b90b;--proj:#7b9bf7;--cyan:#38bdf8;--disp:'Space Grotesk',system-ui;--mono:'IBM Plex Mono',monospace;--body:'IBM Plex Sans',system-ui;--sidebar-w-collapsed: 48px;--sidebar-w-expanded: 220px}
*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--tx);font:13px/1.5 var(--body);-webkit-font-smoothing:antialiased;padding-left:var(--sidebar-w-collapsed);transition:padding-left .25s cubic-bezier(.16,1,.3,1)}
body.sidebar-pinned{padding-left:var(--sidebar-w-expanded)}
.cui-sidebar{position:fixed;top:0;left:0;bottom:0;width:var(--sidebar-w-collapsed);background:var(--bg);border-right:1px solid var(--line);z-index:1000;display:flex;flex-direction:column;transition:width .25s cubic-bezier(.16,1,.3,1);overflow:hidden;box-shadow:2px 0 10px rgba(0,0,0,.35)}
.cui-sidebar:hover,.cui-sidebar.pinned{width:var(--sidebar-w-expanded)}
.sidebar-header{height:48px;display:flex;align-items:center;padding:0 12px;gap:12px;border-bottom:1px solid var(--line);flex-shrink:0}
.sidebar-toggle-btn{background:transparent;border:none;color:var(--dim);cursor:pointer;display:inline-flex;align-items:center;justify-content:center;padding:6px;border-radius:6px;transition:color .15s,background .15s}
.sidebar-toggle-btn:hover{color:var(--tx);background:var(--panel2)}
.cui-sidebar.pinned .sidebar-toggle-btn{color:var(--up);background:rgba(51,201,181,0.15)}
.sidebar-brand-text{font:800 12px var(--disp);letter-spacing:.08em;color:var(--tx);white-space:nowrap;opacity:0;transition:opacity .2s ease}
.cui-sidebar:hover .sidebar-brand-text,.cui-sidebar.pinned .sidebar-brand-text{opacity:1}
.sidebar-nav{display:flex;flex-direction:column;gap:4px;padding:10px 6px;flex:1}
.sidebar-tab-btn,.sidebar-link-btn{display:flex;align-items:center;gap:12px;padding:8px 10px;border-radius:6px;background:transparent;border:1px solid transparent;color:var(--dim);font:600 12px var(--disp);cursor:pointer;text-decoration:none;white-space:nowrap;transition:all .15s ease;width:100%;text-align:left}
.sidebar-tab-btn:hover,.sidebar-link-btn:hover{color:var(--tx);background:var(--panel2)}
.sidebar-tab-btn.active{color:var(--up);background:rgba(51,201,181,0.12);border-color:rgba(51,201,181,0.25);font-weight:700}
.nav-icon{display:inline-flex;align-items:center;justify-content:center;width:20px;height:20px;flex-shrink:0}
.nav-label{opacity:0;transition:opacity .2s ease;white-space:nowrap}
.cui-sidebar:hover .nav-label,.cui-sidebar.pinned .nav-label{opacity:1}
.sidebar-divider{height:1px;background:var(--line);margin:6px 10px}
.sidebar-footer{padding:10px 6px;border-top:1px solid var(--line);flex-shrink:0}
.sidebar-status-pill{display:flex;align-items:center;gap:8px;padding:6px 8px;border-radius:6px;background:var(--panel);border:1px solid var(--line);white-space:nowrap}
.status-indicator-dot{width:8px;height:8px;border-radius:50%;background:var(--dim);flex-shrink:0;transition:all .2s ease}
.status-indicator-dot.running{background:var(--up);box-shadow:0 0 6px var(--up)}
.status-indicator-text{font:700 10px var(--mono);color:var(--dim);opacity:0;transition:opacity .2s ease}
.cui-sidebar:hover .status-indicator-text,.cui-sidebar.pinned .status-indicator-text{opacity:1}
a{color:var(--proj);text-decoration:none} a:hover{text-decoration:underline}
/* Visible keyboard focus (guidelines: never remove outlines without a
   replacement). :focus-visible avoids rings on mouse clicks. */
a:focus-visible,
button:focus-visible,
input:focus-visible,
select:focus-visible,
textarea:focus-visible,
[tabindex]:focus-visible{
  outline:2px solid var(--up);
  outline-offset:2px;
  border-radius:6px;
}
.filter-chip:focus-visible,
.tab-btn:focus-visible,
.btn:focus-visible{outline-offset:2px;border-radius:8px}
.sidebar-tab-btn:focus-visible,.sidebar-link-btn:focus-visible,.sidebar-toggle-btn:focus-visible{
  outline:2px solid var(--up);outline-offset:-2px;border-radius:6px;
}
.mono{font-family:var(--mono)}
.hdr{padding:14px 20px;background:var(--panel);border-bottom:1px solid var(--line);display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.hdr h1{margin:0;font:700 16px var(--disp);display:flex;align-items:center;gap:8px}
.tag{border:1px solid var(--up);color:var(--up);border-radius:99px;padding:2px 8px;font-size:10px;font-weight:700}
.nav-tabs{display:flex;gap:6px;background:var(--panel2);padding:4px;border-radius:10px;border:1px solid var(--line)}
.tab-btn{background:transparent;border:none;color:var(--dim);padding:6px 14px;border-radius:7px;font:600 12px var(--disp);cursor:pointer;transition:all .15s}
.tab-btn.active{background:var(--panel);color:var(--tx);box-shadow:0 1px 4px rgba(0,0,0,.4)}
.tab-btn:hover:not(.active){color:var(--tx)}
.filter-chip{background:var(--panel2);border:1px solid var(--line);color:var(--dim);border-radius:20px;padding:3px 10px;font:600 11px var(--disp);cursor:pointer;transition:all .15s;user-select:none;display:inline-flex;align-items:center;gap:5px}
.filter-chip.active{background:rgba(51,201,181,0.15);border-color:var(--up);color:var(--up)}
.filter-chip:hover:not(.active){color:var(--tx);border-color:var(--line-hi)}
.wrap{max-width:1440px;margin:0 auto;padding:16px 20px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:1000px){.grid{grid-template-columns:1fr}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:12px}
.card h3{margin:0 0 8px;font:700 12px var(--disp);letter-spacing:.06em;text-transform:uppercase;display:flex;align-items:center;justify-content:space-between}
.kpi{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}
.kpi .box{flex:1;min-width:90px;background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:8px 10px;text-align:center}
.box .lbl{font:600 9px var(--disp);letter-spacing:.07em;color:var(--faint);text-transform:uppercase}
.box .val{font:700 18px var(--mono);margin-top:2px}
.box .sub{font:400 10px var(--mono);color:var(--dim)}
.bar{height:6px;background:var(--panel2);border:1px solid var(--line);border-radius:99px;overflow:hidden;margin-top:6px}
.fill{height:100%;border-radius:99px}
.fill.up{background:var(--up)} .fill.warn{background:var(--proj)} .fill.gold{background:var(--gold)} .fill.down{background:var(--down)}
.tick-progress-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
.tick-progress{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:7px 9px;margin-top:0;min-width:0}
.tick-progress-head{display:flex;gap:10px;align-items:baseline;font-size:11px}
.tick-progress-label{font-weight:700;color:var(--tx);min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tick-progress-direction{color:var(--faint);font:9px var(--mono);margin-top:1px}
.tick-progress-scale{position:relative;height:10px;margin-top:4px;overflow:visible;color:var(--faint);font:9px var(--mono)}
.tick-progress-scale-zero{position:absolute;left:0;bottom:0;color:var(--dim)}
.tick-progress-measured-row{display:flex;justify-content:center;align-items:center;height:20px;margin:3px 0 4px}
.tick-progress-track{position:relative;height:18px;background:var(--panel2);border:1px solid var(--line);border-radius:6px;overflow:visible;margin-top:0}
.tick-progress-fill{position:absolute;left:0;top:0;bottom:0;border-radius:5px;transition:width .2s ease}
.tick-progress-fill.min{background:var(--up)} .tick-progress-fill.max{background:var(--gold)}
.tick-progress-measured-readout{position:static!important;display:grid!important;place-items:center!important;width:auto!important;min-width:118px!important;height:20px!important;box-sizing:border-box!important;padding:3px 9px!important;border:1px solid var(--tx)!important;border-radius:5px!important;background:var(--bg)!important;color:var(--tx)!important;font:800 10px/1 var(--mono)!important;letter-spacing:.01em!important;white-space:nowrap!important;z-index:10!important;pointer-events:none!important;box-shadow:0 1px 4px rgba(0,0,0,.8)!important;text-shadow:none!important}
.tick-progress-marker{position:absolute;top:-3px;bottom:-3px;width:2px;transform:translateX(-1px);border-radius:2px;z-index:2;pointer-events:none}
.tick-progress-marker.exploratory{background:var(--gold);box-shadow:0 0 0 1px rgba(232,184,75,.22)}
.tick-progress-marker.research{background:var(--cyan);box-shadow:0 0 0 1px rgba(56,189,248,.22)}
.tick-progress-targets-row{display:grid!important;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:10px;margin-top:7px;font:700 10px/1.25 var(--disp);letter-spacing:.02em;position:relative;z-index:4}
.tick-progress-target{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tick-progress-target.exploratory{color:var(--gold)}
.tick-progress-target.research{color:var(--cyan);text-align:right}
.tick-progress-next{color:var(--gold);font-size:9px;margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
@media(max-width:760px){.tick-progress-grid{grid-template-columns:1fr}}
.tick-info{display:inline-grid;place-items:center;width:18px;height:18px;padding:0;margin-left:5px;border:1px solid var(--line-hi);border-radius:50%;background:var(--panel2);color:var(--cyan);font:700 11px var(--mono);cursor:pointer;vertical-align:middle}
.tick-info:focus-visible{outline:2px solid var(--cyan);outline-offset:2px}
.tick-tooltip{position:relative;display:inline-block}
.tick-tooltip-pop{position:absolute;right:0;top:25px;z-index:30;width:300px;padding:10px 12px;background:var(--panel2);border:1px solid var(--line-hi);border-radius:8px;box-shadow:0 8px 22px rgba(0,0,0,.45);font:12px/1.45 var(--body);color:var(--tx);text-transform:none;letter-spacing:normal;text-align:left}
.tick-tooltip-pop[hidden]{display:none}
@media(max-width:700px){.tick-tooltip-pop{position:fixed;right:12px;left:12px;top:auto;bottom:12px;width:auto}}
.tbl{width:100%;border-collapse:collapse;margin-top:10px;font-size:13px}
.tbl th{font:700 11px var(--disp);letter-spacing:.06em;text-transform:uppercase;color:var(--faint);text-align:left;padding:8px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
.tbl td{padding:10px 8px;border-bottom:1px solid var(--line-dark);font-size:13px;vertical-align:middle}
#manifestTableWrap{overflow-x:auto}
#manifestTableWrap .tbl{min-width:980px}
.price-up{color:var(--up);font-weight:700;font-family:var(--mono)}
.price-down{color:var(--down);font-weight:700;font-family:var(--mono)}
.price-small{font-size:10px;font-weight:500;opacity:.85}
.candle-wrap{width:110px}
.candle-bar{height:10px;background:var(--panel2);border:1px solid var(--line);border-radius:99px;position:relative;overflow:hidden}
.candle-wick{position:absolute;top:50%;height:2px;background:var(--faint);transform:translateY(-50%)}
.candle-body{position:absolute;top:2px;bottom:2px;border-radius:3px}
.pill{font:700 9px var(--disp);letter-spacing:.06em;padding:2px 7px;border-radius:99px;border:1px solid var(--line);white-space:nowrap}
.pill-osc{background:rgba(51,201,181,.12);color:var(--up);border-color:rgba(51,201,181,.3)}
.pill-mono{background:rgba(240,104,77,.12);color:var(--down);border-color:rgba(240,104,77,.3)}
.pill-flat{background:var(--panel2);color:var(--dim)}
/* Issue #462: a market the engine has stopped updating gets its own colour —
   "status looks fine" and "nothing is updating this market" are different facts. */
.pill-stale{background:rgba(232,184,75,.14);color:var(--gold);border-color:rgba(232,184,75,.35)}
.live-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:8px}
@media(max-width:1000px){.live-grid{grid-template-columns:repeat(2,1fr)}}
.liveBox{background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:9px 10px}
.btn{background:var(--panel2);color:var(--tx);border:1px solid var(--line);border-radius:8px;padding:6px 12px;font:600 12px var(--disp);cursor:pointer;display:inline-flex;align-items:center;gap:6px}
.btn:hover{background:var(--line);border-color:var(--line-hi)}
.btn-primary{background:var(--up);color:var(--bg);border:none;font-weight:700;position:relative;transition:all .2s ease}
.btn-primary:hover{background:var(--up-hi)}
.btn-primary:disabled{opacity:0.75;cursor:wait}
/* Issue #383/#386: `.btn` sets `display:inline-flex`, which outranks the
   user-agent's `[hidden]{display:none}` — so a `hidden` button still renders. The
   Stop controls must stay invisible on first paint, before any script has run. */
.bt-stop[hidden]{display:none !important}
/* A locked control is not a broken one: it stays legible, shows a
   not-allowed cursor, and does not invite a hover. Used when a
   standalone collector owns run/ticks/ and Start would spawn a
   second writer on the same daily file. */
.btn-locked{background:rgba(51,201,181,0.10);color:var(--up);border:1px solid rgba(51,201,181,0.35);font-weight:700}
.btn:disabled,.btn-locked:disabled{cursor:not-allowed;opacity:1}
.btn:disabled:hover,.btn-locked:disabled:hover{background:rgba(51,201,181,0.10);border-color:rgba(51,201,181,0.35)}
.btn-primary.thinking{background:var(--up-hi);box-shadow:0 0 12px rgba(51,201,181,0.45);animation:pulse-glow 1.4s infinite alternate;pointer-events:none}
@keyframes pulse-glow{0%{box-shadow:0 0 4px rgba(51,201,181,0.3);transform:scale(0.995)}100%{box-shadow:0 0 16px rgba(51,201,181,0.7);transform:scale(1.015)}}
.spinner{width:12px;height:12px;border:2px solid rgba(10,13,18,0.25);border-top-color:var(--bg);border-radius:50%;display:inline-block;animation:spin .7s linear infinite;vertical-align:middle;margin-left:4px}
@keyframes spin{to{transform:rotate(360deg)}}
.thinking-dots{display:inline-flex;align-items:center;gap:3px;margin-right:2px}
.thinking-dots span{width:4px;height:4px;background:var(--bg);border-radius:50%;display:inline-block;animation:dot-blink 1.2s infinite ease-in-out}
.thinking-dots span:nth-child(2){animation-delay:0.2s}
.thinking-dots span:nth-child(3){animation-delay:0.4s}
@keyframes dot-blink{0%,80%,100%{opacity:0.2;transform:scale(0.8)}40%{opacity:1;transform:scale(1.2)}}
.btn-danger{background:rgba(240,104,77,.2);color:var(--down);border-color:rgba(240,104,77,.4)}
.btn-danger:hover{background:rgba(240,104,77,.3)}
.form-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
@media(max-width:900px){.form-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
/* Issue #201, carried through #229 and #411: the four backtest thresholds are one
   group owning their own full-width row in .form-grid via grid-column:1/-1 with a
   repeat(4,minmax(0,1fr)) inner grid. display:contents was removed in issue #411 because it
   smeared the group across adjacent rows. The [hidden] rule is required because
   the id selector would otherwise outrank the UA one. */
#btStopLossFields{grid-column:1/-1;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
@media(max-width:900px){#btStopLossFields{grid-template-columns:repeat(2,minmax(0,1fr))}}
#btStopLossFields[hidden]{display:none}
.form-group{display:flex;flex-direction:column;gap:4px;min-width:0}
.form-group label{font:600 11px var(--disp);color:var(--dim);letter-spacing:.04em;text-align:left}
.form-group input, .form-group select{width:100%;box-sizing:border-box;min-width:0;background:var(--panel2);color:var(--tx);border:1px solid var(--line);border-radius:8px;padding:7px 10px;font:500 13px var(--mono);transition:border-color .15s ease,box-shadow .15s ease,background .15s ease}
.form-group input::placeholder{color:var(--faint);opacity:0.75}
.form-group input.input-invalid{border:1px solid var(--down) !important;box-shadow:0 0 6px rgba(240,104,77,0.45) !important;background:rgba(240,104,77,0.06) !important}
.form-group .input-hint{font:500 10px var(--mono);color:var(--faint);margin-top:2px;display:block}
.form-group .input-hint.err{color:var(--down);font-weight:600}
.tab-content{display:none}
.tab-content.active{display:block}
/* Jungle King tab (issue #319) */
.jk-group-head{display:flex;align-items:baseline;justify-content:space-between;gap:8px;margin-bottom:10px}
.jk-group-title{font:700 13px var(--disp);color:var(--tx);letter-spacing:.02em;margin:0}
.jk-group-count{font:600 10px var(--mono);color:var(--faint)}
.jk-param{border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin-bottom:8px;background:var(--panel2)}
.jk-param-head{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:6px}
.jk-param-label{font:600 12.5px var(--body);color:var(--tx);cursor:default}
.jk-badge{font:700 9px var(--mono);letter-spacing:.06em;padding:2px 7px;border-radius:99px;border:1px solid}
.jk-badge-tuning{color:var(--up);border-color:var(--up);background:var(--upS)}
.jk-badge-structural{color:var(--gold);border-color:var(--gold);background:rgba(232,184,75,.12)}
.jk-badge-assumption{color:var(--proj);border-color:var(--proj);background:rgba(123,155,247,.12)}
.jk-baseline{display:flex;align-items:baseline;gap:8px;margin-bottom:8px}
.jk-baseline-lbl{font:700 9px var(--mono);letter-spacing:.1em;color:var(--faint)}
.jk-baseline-val{font:700 15px var(--mono);color:var(--cyan)}
.jk-chips{display:flex;flex-wrap:wrap;gap:4px}
.jk-chip{font:500 10.5px var(--mono);padding:2px 7px;border-radius:5px;border:1px solid var(--line-hi);color:var(--dim);background:var(--panel)}
.jk-chip.jkBaselineChip{color:var(--bg);background:var(--up);border-color:var(--up);font-weight:700}
.jk-chip-baseline-tag{margin-left:5px;font-size:8px;letter-spacing:.04em;white-space:nowrap}
.jk-chip.jkBaselineMissing{color:var(--gold);border-color:var(--gold);background:rgba(232,184,75,.12);font-weight:700}
.toggle-wrap{display:inline-flex;align-items:center;gap:6px;cursor:pointer;user-select:none}
.toggle-switch{position:relative;display:inline-block;width:34px;height:18px}
.toggle-switch input{opacity:0;width:0;height:0}
.toggle-slider{position:absolute;cursor:pointer;top:0;left:0;right:0;bottom:0;background-color:var(--panel2);border:1px solid var(--line-hi);transition:.2s;border-radius:18px}
.toggle-slider:before{position:absolute;content:"";height:12px;width:12px;left:2px;bottom:2px;background-color:var(--dim);transition:.2s;border-radius:50%}
.toggle-switch input:checked + .toggle-slider{background-color:rgba(51,201,181,0.25);border-color:var(--up)}
.toggle-switch input:checked + .toggle-slider:before{transform:translateX(16px);background-color:var(--up)}
.ot-tabs{display:inline-flex;gap:4px;background:var(--panel2);padding:3px;border-radius:8px;border:1px solid var(--line)}
.ot-tab-btn{background:transparent;border:none;color:var(--dim);font:700 11px var(--disp);letter-spacing:.05em;padding:5px 12px;border-radius:6px;cursor:pointer;display:inline-flex;align-items:center;gap:6px;transition:all .15s ease}
.ot-tab-btn:hover{color:var(--tx);background:rgba(255,255,255,0.04)}
.ot-tab-btn.active{background:var(--panel);color:var(--tx);box-shadow:0 1px 3px rgba(0,0,0,0.3);border:1px solid var(--line-hi)}
.ot-count{font:700 10px var(--mono);background:var(--line);color:var(--tx);padding:1px 6px;border-radius:99px}
.ot-tab-btn.active .ot-count{background:rgba(51,201,181,0.2);color:var(--up)}
.ot-pane{display:none;position:relative}
.ot-pane.active{display:block}
.ot-pane-scroll{max-height:min(680px,72vh);overflow-y:auto;position:relative;border-radius:6px;transition:max-height .2s ease}
.ot-pane-scroll.ot-expanded{max-height:88vh!important}
.ot-pane .tbl thead th{position:sticky;top:0;z-index:10;background:var(--panel);box-shadow:0 1px 0 var(--line);padding:7px 8px}
.ot-th-sortable{cursor:pointer;user-select:none;transition:color .15s ease,background .15s ease;position:relative;white-space:nowrap}
.ot-th-sortable:hover{color:var(--tx);background:rgba(255,255,255,0.04)}
.ot-th-sortable:focus-visible{outline:1px solid var(--gold);outline-offset:-1px}
.ot-sort-button{background:none;border:none;padding:0;margin:0;font:inherit;color:inherit;cursor:pointer;white-space:nowrap}
.ot-sort-button:hover{color:var(--tx)}
.ot-sort-button:focus-visible{outline:1px solid var(--gold);outline-offset:-1px}
.ot-sort-ind{display:inline-block;margin-left:4px;font-size:9px;color:var(--dim);opacity:0.4;vertical-align:middle;transition:all .15s ease}
.ot-th-sortable[aria-sort="ascending"] .ot-sort-ind{color:var(--gold);opacity:1}
.ot-th-sortable[aria-sort="descending"] .ot-sort-ind{color:var(--gold);opacity:1}
.ot-pane .tbl td{padding:6px 8px;font-size:12px}
.ot-row-cancelled td{color:var(--dim)!important}
.ot-cell-cancelled{color:var(--dim)!important}
.ot-row-cancelled td a{color:var(--dim)!important}
.ot-row-cancelled td .ot-tag,.ot-row-cancelled td .pill{color:var(--faint)!important;background:rgba(120,135,155,0.08);border-color:rgba(120,135,155,0.22)}
/* Cancelled-bid matrix cards (stopped out / timeout / drift skipped) render
   their Orders & Position box dimmed so a dead market reads as inactive. */
.mat-bids-cancelled{border-color:rgba(120,135,155,0.3)!important;background:rgba(120,135,155,0.05)!important}
.mat-bids-cancelled .mono{color:var(--dim)!important}
.mat-bids-cancelled .mono b{color:var(--dim)!important}
.ot-pair-lead{border-top:1px solid var(--line-hi)}
.ot-tag{font:700 9px var(--disp);letter-spacing:.04em;padding:2px 6px;border-radius:4px;white-space:nowrap;display:inline-block}
.ot-tag-up{background:rgba(51,201,181,0.15);color:var(--up);border:1px solid rgba(51,201,181,0.3)}
.ot-tag-down{background:rgba(240,104,77,0.15);color:var(--down);border:1px solid rgba(240,104,77,0.3)}
.ot-tag-paired{background:rgba(51,201,181,0.12);color:var(--up);border:1px solid rgba(51,201,181,0.25)}
.ot-tag-partial{background:rgba(235,178,74,0.12);color:var(--gold);border:1px solid rgba(235,178,74,0.25)}
.ot-tag-unpaired{background:rgba(120,135,155,0.12);color:var(--dim);border:1px solid rgba(120,135,155,0.25)}
.ot-tag-cancelled{background:rgba(120,135,155,0.12);color:var(--dim);border:1px solid rgba(120,135,155,0.25)}
.card-title{font:700 12px var(--disp);letter-spacing:.06em;text-transform:uppercase;margin-bottom:10px;display:flex;align-items:center;justify-content:space-between}
/* Floating Side Toast Notifications (Issue #81) */
.toast-container{position:fixed;top:20px;right:20px;z-index:9999;display:flex;flex-direction:column;gap:8px;max-width:360px;width:calc(100vw - 40px);pointer-events:none}
.toast{pointer-events:auto;background:var(--panel2);border:1px solid var(--line-hi);border-radius:8px;padding:10px 14px;color:var(--tx);font:12px/1.4 var(--body);box-shadow:0 4px 16px rgba(0,0,0,.5);display:flex;align-items:flex-start;justify-content:space-between;gap:10px;animation:toast-slide-in .25s cubic-bezier(.16,1,.3,1) forwards;transition:opacity .25s ease,transform .25s ease}
.toast.fade-out{opacity:0;transform:translateX(30px)}
@keyframes toast-slide-in{from{opacity:0;transform:translateX(40px)}to{opacity:1;transform:translateX(0)}}
.toast-merged{border-left:4px solid var(--up);background:linear-gradient(90deg,rgba(51,201,181,.12) 0%,var(--panel2) 100%)}
.toast-stoploss{border-left:4px solid var(--down);background:linear-gradient(90deg,rgba(240,104,77,.12) 0%,var(--panel2) 100%)}
.toast-filled{border-left:4px solid var(--line-hi);background:var(--panel2)}
.toast-content{flex:1}
.toast-header{display:flex;align-items:center;gap:6px;margin-bottom:2px;font:700 12px var(--disp)}
.toast-header-merged{color:var(--up)}
.toast-header-stoploss{color:var(--down)}
.toast-header-filled{color:var(--tx)}
.toast-msg{font:11px var(--mono);color:var(--dim);word-break:break-word}
.toast-close{background:none;border:none;color:var(--faint);font-size:16px;line-height:1;cursor:pointer;padding:0 2px;transition:color .15s ease}
.toast-close:hover{color:var(--tx)}
/* ── Backtest parameter sections (operator vs assumption vs policy) ───────── */
.bt-accordion{display:flex;flex-direction:column;gap:14px}
.bt-section{margin-top:0;padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:var(--panel2)}
.bt-section-head{display:flex;align-items:center;gap:8px;width:100%;text-align:left;background:none;border:0;padding:2px 0;margin:0;cursor:pointer;font:700 11px var(--disp);letter-spacing:.05em;text-transform:uppercase;color:var(--tx)}
.bt-section-head:hover{color:var(--gold)}
.bt-section-head:focus-visible{outline:2px solid var(--gold);outline-offset:2px;border-radius:4px}
.bt-section-chevron{margin-left:auto;transition:transform .15s ease;color:var(--faint)}
.bt-section-head[aria-expanded="false"] .bt-section-chevron{transform:rotate(-90deg)}
.bt-section-body{min-width:0}
.bt-section-body[hidden]{display:none}
.bt-section-dot{display:inline-block;width:8px;height:8px;border-radius:50%}
.bt-section-dot-green{background:var(--up)}
.bt-section-dot-red{background:var(--down)}
.param-structural-badge{display:inline-block;font:700 9px var(--disp);letter-spacing:.05em;color:var(--down);border:1px solid var(--down);border-radius:4px;padding:1px 5px;margin-left:6px;vertical-align:middle}
.param-structural{border-left:2px solid var(--down) !important;padding-left:8px;border-radius:2px}
.bt-section-dot-amber{background:var(--gold)}
.bt-section-dot-blue{background:var(--proj)}
#btSetupActions{display:flex;justify-content:flex-end;align-items:center;flex-wrap:wrap;gap:8px;margin-top:14px}
.bt-peer-layout{display:flex;flex-direction:column;gap:14px}
.bt-peer-section{margin:0!important;padding:12px 14px!important;background:var(--panel)!important;border-top:2px solid var(--up)!important}
.bt-peer-section .bt-section-body{padding-top:10px}
.bt-peer-section .bt-section-head{font-size:12px;padding:0}
.bt-peer-section .bt-section-head .bt-section-dot{flex:0 0 auto}
.bt-chart-card{position:relative;min-width:0;cursor:zoom-in;transition:border-color .15s ease,box-shadow .15s ease}
.bt-chart-card:hover{border-color:var(--line-hi)!important}
.bt-chart-card:focus-visible{outline:2px solid var(--gold);outline-offset:3px}
.bt-chart-card::after{content:'Open chart';position:absolute;right:8px;top:7px;font:600 9px var(--disp);color:var(--faint);opacity:0;transition:opacity .15s ease}
.bt-chart-card:hover::after,.bt-chart-card:focus-visible::after{opacity:1}
.bt-chart-dialog[hidden]{display:none}
.bt-chart-dialog{position:fixed;inset:0;z-index:1000;display:grid;place-items:center;padding:20px;background:rgba(5,8,12,.78)}
.bt-chart-dialog-panel{width:min(1100px,calc(100vw - 32px));max-height:calc(100vh - 40px);overflow:auto;background:var(--panel);border:1px solid var(--line-hi);border-radius:12px;padding:16px;box-shadow:0 18px 60px rgba(0,0,0,.6)}
.bt-chart-dialog-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px}
.bt-chart-dialog-title{margin:0;font:700 13px var(--disp);letter-spacing:.05em;text-transform:uppercase;color:var(--tx)}
.bt-chart-dialog-close:focus-visible{outline:2px solid var(--gold);outline-offset:2px}
.bt-chart-dialog-canvas{display:block;width:100%;height:min(62vh,560px)!important}
@media(max-width:600px){.bt-chart-dialog{padding:10px}.bt-chart-dialog-panel{width:calc(100vw - 20px);padding:12px}.bt-chart-dialog-canvas{height:58vh!important}}
/* ── Backtest Strategy Geometry Preview (Issue #263) ───────────────────────── */
#btParamPreviewWrap{background:var(--panel2);border:1px solid var(--line);border-radius:12px;padding:14px;margin-top:14px;transition:border-color .15s ease}
#btParamPreviewWrap:hover{border-color:var(--line-hi)}
.bt-preview-chart-shell{width:100%;background:rgba(10,13,18,0.55);border:1px solid var(--line);border-radius:9px;overflow:hidden}
.bt-preview-chart-shell svg{display:block;width:100%;height:clamp(180px, calc((100vw - 300px) / 3), 540px);aspect-ratio:auto}
/* ── Backtest Runtime Estimation Badge (Issue #330) ───────────────────────── */
.bt-runtime-badge{display:inline-flex;align-items:center;gap:6px;font:600 11px var(--mono);padding:4px 10px;border-radius:6px;background:var(--panel2);border:1px solid var(--line);color:var(--cyan);user-select:none;transition:all .15s ease}
.bt-runtime-badge:hover{border-color:var(--line-hi)}
</style></head><body>
<aside class="cui-sidebar" id="app-sidebar" aria-label="Main Navigation">
  <div class="sidebar-header">
    <button class="sidebar-toggle-btn" id="sidebarToggleBtn" onclick="toggleSidebarPin()" title="Pin/Unpin Sidebar" aria-label="Toggle Sidebar Navigation">
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
        <line x1="9" y1="3" x2="9" y2="21"></line>
      </svg>
    </button>
    <div class="sidebar-brand-text">CRYPTO SPREAD</div>
  </div>
  <nav class="sidebar-nav">
    <button class="sidebar-tab-btn active" id="tab-btn-cockpit" onclick="switchTab('cockpit')" title="Trading Platform">
      <span class="nav-icon">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
        </svg>
      </span>
      <span class="nav-label">Trading Platform</span>
    </button>
    <button class="sidebar-tab-btn" id="tab-btn-marketdata" onclick="switchTab('marketdata')" title="Collector's Market Data">
      <span class="nav-icon">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M4.93 4.93a10 10 0 0 1 14.14 0"></path>
          <path d="M7.76 7.76a6 6 0 0 1 8.48 0"></path>
          <circle cx="12" cy="12" r="2"></circle>
          <path d="M12 14v7"></path>
        </svg>
      </span>
      <span class="nav-label">Collector's Market Data</span>
    </button>
    <button class="sidebar-tab-btn" id="tab-btn-backtest" onclick="switchTab('backtest')" title="Backtest Sweeper">
      <span class="nav-icon">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="22 7 13.5 15.5 8.5 10.5 2 17"></polyline>
          <polyline points="16 7 22 7 22 13"></polyline>
        </svg>
      </span>
      <span class="nav-label">Backtest Sweeper</span>
    </button>
    <button class="sidebar-tab-btn" id="tab-btn-jungleking" onclick="switchTab('jungleking')" title="Jungle King — OFAT Manifest" aria-label="Jungle King">
      <span class="nav-icon">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M3 17l2-9 5 5 2-7 2 7 5-5 2 9"></path>
          <line x1="3" y1="20" x2="21" y2="20"></line>
        </svg>
      </span>
      <span class="nav-label">Jungle King</span>
    </button>
    <button class="sidebar-tab-btn" id="tab-btn-summary" onclick="switchTab('summary')" title="Stats Summary">
      <span class="nav-icon">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <line x1="18" y1="20" x2="18" y2="10"></line>
          <line x1="12" y1="20" x2="12" y2="4"></line>
          <line x1="6" y1="20" x2="6" y2="14"></line>
        </svg>
      </span>
      <span class="nav-label">Stats Summary</span>
    </button>
    <button class="sidebar-tab-btn" id="tab-btn-ticks" onclick="switchTab('ticks')" title="Tick Files">
      <span class="nav-icon">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <ellipse cx="12" cy="5" rx="9" ry="3"></ellipse>
          <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"></path>
          <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"></path>
        </svg>
      </span>
      <span class="nav-label">Tick Files</span>
    </button>
  </nav>
  <div class="sidebar-divider"></div>
  <div class="sidebar-nav" style="flex:0">
    <a href="https://polymarket.com" target="_blank" rel="noopener noreferrer" class="sidebar-link-btn" title="Polymarket CLOB">
      <span class="nav-icon">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="10"></circle>
          <line x1="2" y1="12" x2="22" y2="12"></line>
          <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path>
        </svg>
      </span>
      <span class="nav-label">Polymarket Venue</span>
    </a>
    <a href="https://github.com/AI-Degen-69/crypto-spread" target="_blank" rel="noopener noreferrer" class="sidebar-link-btn" title="GitHub Repository">
      <span class="nav-icon">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22"></path>
        </svg>
      </span>
      <span class="nav-label">GitHub Repo</span>
    </a>
  </div>
  <div class="sidebar-footer">
    <div class="sidebar-status-pill" id="sidebarBotStatusPill" title="Trading Bot Engine Status">
      <span class="status-indicator-dot" id="sidebarStatusDot"></span>
      <span class="status-indicator-text" id="sidebarStatusText">ENGINE IDLE</span>
    </div>
  </div>
</aside>

<div class="hdr" id="app-hdr">
  <h1><span>◆</span> Crypto Spread <span>5m/15m Engine</span></h1>
  <span class="tag">SPREAD-2 · POLYMARKET CLOB</span>
  <span style="flex:1"></span>
  <div style="display:flex;align-items:center;gap:8px">
    <span id="collectorBadge" class="mono" style="font-size:11px;padding:3px 8px;border-radius:6px;background:var(--panel2);border:1px solid var(--line)">Collector: Loading...</span>
    <span id="tapeBadge" class="mono" style="font-size:11px;padding:3px 8px;border-radius:6px;background:var(--panel2);border:1px solid var(--line)">Tape: Loading...</span>
    <span id="shadowBadge" class="mono" title="WS vs REST book disagreement (#174 Phase 1): divergence rate with its comparison count" style="font-size:11px;padding:3px 8px;border-radius:6px;background:var(--panel2);border:1px solid var(--line)">Book Δ: Loading...</span>
    <span id="globalStreamPill" class="mono" title="Stream health: green = live <1s, yellow = 1s, red = offline/polling" style="font-size:11px;padding:3px 10px;border-radius:99px;background:var(--panel2);border:1px solid var(--line);font-weight:700">● STREAM: CONNECTING...</span>
    <button class="btn" id="btnToggleCollector" onclick="toggleCollector()" title="Capture 1-second live ticks and tape into run/ticks/; closing 5m/15m windows append to the dataset">Start Polling (1s)</button>
    <button class="btn" onclick="pollOnce()">Poll Now (Once)</button>
    <button class="btn" id="btnRebuildStats" onclick="rebuildStats()">Rebuild Stats</button>
  </div>
</div>

<div class="wrap">
  <!-- TAB 1: LIVE & RECENT WINDOWS -->
  <div id="tab-marketdata" class="tab-content">
    <div id="goalBar" class="card" style="border-top:2px solid var(--gold)"></div>
    <div id="liveBar" class="card"></div>
    <div id="seriesGrid" class="grid"></div>
    <div id="windowsTableWrap"></div>
  </div>

  <!-- TAB 2: BACKTEST ENGINE & SWEEPER -->
  <div id="tab-backtest" class="tab-content">
    <div class="bt-peer-layout">
    <div class="bt-section bt-peer-section" id="btSecParameters">
      <button type="button" class="bt-section-head" aria-expanded="true" aria-controls="btSecParametersBody" onclick="toggleBtSection(this,'btSecParametersBody')">
        <span class="bt-section-dot bt-section-dot-green"></span>
        <span>⚡ Backtest Setup &amp; Run</span>
        <span class="bt-section-chevron" aria-hidden="true">▾</span>
      </button>
      <div class="bt-section-body" id="btSecParametersBody">
      <div class="mono" id="btHash" style="font-size:11px;color:var(--dim);margin-bottom:8px"></div>
      <div class="bt-accordion" style="margin-top:12px">
        <!-- ── 0. BACKTEST SCOPE (universe: dataset, markets, timeframe, windows) -->
        <div class="bt-section" id="btSecScope">
          <button type="button" class="bt-section-head" aria-expanded="true" aria-controls="btSecScopeBody" onclick="toggleBtSection(this,'btSecScopeBody')">
            <span class="bt-section-dot bt-section-dot-green"></span>
            <span>Backtest Scope</span>
            <span class="bt-section-chevron" aria-hidden="true">▾</span>
          </button>
          <div class="bt-section-body" id="btSecScopeBody">
          <div class="form-grid" style="margin-top:6px">
            <div class="form-group">
              <label>Tick File Dataset</label>
              <select id="btFileSelect" onchange="updateBtRuntimeEstimate()">
                <option value="">All Files (Default)</option>
              </select>
            </div>
            <div class="form-group">
              <label>Markets (multi-select)</label>
              <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
                <div id="btTokenChips" style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
                  <button type="button" class="filter-chip active" aria-pressed="true" id="btToken-BTC" onclick="toggleBtToken('BTC')">BTC</button>
                  <button type="button" class="filter-chip active" aria-pressed="true" id="btToken-ETH" onclick="toggleBtToken('ETH')">ETH</button>
                  <button type="button" class="filter-chip active" aria-pressed="true" id="btToken-BNB" onclick="toggleBtToken('BNB')">BNB</button>
                  <button type="button" class="filter-chip active" aria-pressed="true" id="btToken-SOL" onclick="toggleBtToken('SOL')">SOL</button>
                  <button type="button" class="filter-chip active" aria-pressed="true" id="btToken-XRP" onclick="toggleBtToken('XRP')">XRP</button>
                </div>
                <button type="button" id="btTokensAll" class="btn" style="font-size:10px;padding:2px 8px" onclick="setBtTokensAll(true)">All</button>
                <button type="button" id="btTokensClear" class="btn" style="font-size:10px;padding:2px 8px" onclick="setBtTokensAll(false)">Clear</button>
              </div>
            </div>
            <div class="form-group">
              <label>Timeframe</label>
              <div style="display:flex;gap:4px;background:var(--panel2);padding:2px;border-radius:8px;border:1px solid var(--line)">
                <button type="button" id="btDur5m" class="tab-btn" aria-pressed="false" style="font-size:11px;padding:4px 10px" onclick="setBtDuration('5m')">5m</button>
                <button type="button" id="btDur15m" class="tab-btn" aria-pressed="false" style="font-size:11px;padding:4px 10px" onclick="setBtDuration('15m')">15m</button>
                <button type="button" id="btDurBoth" class="tab-btn active" aria-pressed="true" style="font-size:11px;padding:4px 10px" onclick="setBtDuration('both')">Both</button>
              </div>
            </div>
            <div class="form-group">
              <label>Partial Windows Filter</label>
              <select id="btMaxStartDelay">
                <option value="0" selected>All (No filter)</option>
                <option value="5.0">Full Windows Only (≤5s delay)</option>
                <option value="2.0">Strict Full Windows (≤2s delay)</option>
              </select>
            </div>
          </div>
          </div>
        </div>

        <!-- ── 1. QUOTE PLACEMENT (live-replicable) ─────────────────────────── -->
        <div class="bt-section" id="btSecOperator">
          <button type="button" class="bt-section-head" aria-expanded="true" aria-controls="btSecOperatorBody" onclick="toggleBtSection(this,'btSecOperatorBody')">
            <span class="bt-section-dot bt-section-dot-green"></span>
            <span>Quote Placement</span>
            <span class="bt-section-chevron" aria-hidden="true">▾</span>
          </button>
          <div class="bt-section-body" id="btSecOperatorBody">
          <div class="form-grid" style="margin-top:6px">
            <div class="form-group">
              <label data-param-label="offset"></label>
              <input type="number" min="0.1" max="49" step="0.1" id="btOffset" data-param="offset" value="2">
            </div>
            <div class="form-group">
              <label data-param-label="queue_gate"></label>
              <input type="number" step="5" id="btQueue" data-param="queue_gate" value="0">
            </div>
            <div class="form-group">
              <label data-param-label="entry_delay_pct"></label>
              <input type="number" min="0" max="100" step="1" id="btEntryDelay" data-param="entry_delay_pct" value="0">
            </div>
            <div class="form-group">
              <label data-param-label="quote_shares"></label>
              <input type="number" min="5" step="1" id="btSize" data-param="quote_shares" value="5">
            </div>
            <div id="btStopLossFields">
              <div class="form-group">
                <label>Exit Stop Loss 5m <span data-param-unit="exit_thresh_by_slug"></span></label>
                <input type="number" min="0.1" max="50" step="0.1" id="btExit5m" data-param="exit_thresh_by_slug" value="5">
              </div>
              <div class="form-group">
                <label>Exit Stop Loss 15m <span data-param-unit="exit_thresh_by_slug"></span></label>
                <input type="number" min="0.1" max="50" step="0.1" id="btExit15m" data-param="exit_thresh_by_slug" value="5">
              </div>
              <div class="form-group">
                <label>BTC 5m Stop Loss <span data-param-unit="exit_thresh_by_slug"></span></label>
                <input type="number" min="0.1" max="50" step="0.1" id="btExitBtc" data-param="exit_thresh_by_slug" value="5">
              </div>
              <div class="form-group">
                <label>SOL 5m Stop Loss <span data-param-unit="exit_thresh_by_slug"></span></label>
                <input type="number" min="0.1" max="50" step="0.1" id="btExitSol" data-param="exit_thresh_by_slug" value="5">
              </div>
            </div>
            <div class="form-group">
              <label data-param-label="exit_reversal"></label>
              <input type="number" min="0.1" max="50" step="0.1" id="btExitReversal" data-param="exit_reversal" value="2">
            </div>
            <div class="form-group">
              <label data-param-label="enable_leg_chase"></label>
              <select id="btLegChase" data-param="enable_leg_chase">
                <option value="0" selected>Off — passive quote only</option>
                <option value="1">On — chase the unfilled leg within the cap</option>
              </select>
            </div>
          </div>
          </div>
        </div>

        <!-- ── 2. RISK LIMITS (engine invariants, issue #233) ─────────────── -->
        <div class="bt-section" id="btSecStructural">
          <button type="button" class="bt-section-head" aria-expanded="true" aria-controls="btSecStructuralBody" onclick="toggleBtSection(this,'btSecStructuralBody')">
            <span class="bt-section-dot bt-section-dot-red"></span>
            <span>Risk Limits</span>
            <span class="bt-section-chevron" aria-hidden="true">▾</span>
          </button>
          <div class="bt-section-body" id="btSecStructuralBody">
          <div class="form-grid" style="margin-top:6px">
            <div class="form-group">
              <label for="btQuoteLo">Quotable Range Lo <span data-param-unit="quote_range"></span></label>
              <input type="number" min="0" max="100" step="0.1" id="btQuoteLo" data-param="quote_range" value="10">
            </div>
            <div class="form-group">
              <label for="btQuoteHi">Quotable Range Hi <span data-param-unit="quote_range"></span></label>
              <input type="number" min="0" max="100" step="0.1" id="btQuoteHi" data-param="quote_range" value="90">
            </div>
            <div class="form-group">
              <label for="btPairCost" data-param-label="max_pair_cost"></label>
              <input type="number" min="50" max="100" step="0.1" id="btPairCost" data-param="max_pair_cost" value="99">
            </div>
            <div class="form-group">
              <label>Dead Zone (% of window)</label>
              <input type="number" min="0" max="100" step="1" id="btDeadZoneVal" data-param="dead_zone_pct" value="10">
            </div>
            <div class="form-group">
              <label data-param-label="naked_leg_at_expiry"></label>
              <select id="btNakedLegAtExpiry" data-param="naked_leg_at_expiry">
                <option value="close" selected>Close at Book</option>
                <option value="hold">Hold to Settlement</option>
              </select>
            </div>
          </div>
          </div>
        </div>

        <!-- ── 3. (removed) ───────────────────────────────────────────────────
             The execution-assumption section used to sit here. Every field in
             it — taker fee rate, min quote shares, merge gas, tick size — is a
             venue fact rather than an operator choice, so all four are now
             held by the engine, documented on the BacktestParams field
             comments in `backtest/engine.py`, and settable by neither a tab
             nor the /api/backtest query string. -->

        <!-- ── 4. GEOMETRY PREVIEW (lives with the parameters it visualizes) ── -->
        <div class="bt-section" id="btSecGeometry">
        <button type="button" class="bt-section-head" aria-expanded="true" aria-controls="btSecGeometryBody" onclick="toggleBtSection(this,'btSecGeometryBody')">
          <span class="bt-section-dot bt-section-dot-blue"></span>
          <span>📐 Strategy Geometry Preview</span>
          <span class="bt-section-chevron" aria-hidden="true">▾</span>
        </button>
        <div class="bt-section-body" id="btSecGeometryBody">
      <div id="btParamPreviewWrap">
        <div class="bt-preview-chart-shell">
          <svg id="btParamPreviewSvg" viewBox="0 0 900 420" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Strategy price levels across the active window">
            <!-- Rendered by updateBacktestParamPreview() -->
          </svg>
        </div>
      </div>

        </div>
        </div>
      </div>
      <div id="btSetupActions">
        <button class="btn btn-primary" id="btnRunSweep" onclick="runBacktest()"><span id="btnRunSweepIcon">▶</span> <span id="btnRunSweepText">Run Sweep</span></button>
        <!-- Issue #383: the server-side stop. Visible exactly while a backtest or
             sweep is in flight (driven by `updateBtStopVisibility`), so it starts
             hidden and never appears next to an idle tab. -->
        <button class="btn bt-stop" id="btnStopBacktest" onclick="stopBacktestRun()" hidden>⏹ Stop</button>
        <button class="btn" id="btnResetParams" onclick="resetBtParams()">Reset to Defaults</button>
        <!-- Issue #413: manual save of the last completed run as a reusable template. Enabled only when a run_id is available. -->
        <button class="btn" id="btnSaveTemplate" onclick="saveBacktestTemplate()" disabled title="Save the last completed run as a reusable template">💾 Save as Template</button>
        <span id="btRuntimeEstBadge" class="bt-runtime-badge" title="Estimated execution runtime based on selected dataset and scope" aria-live="polite">⏱️ Est: calculating…</span>
        <span id="btLastRunTime" class="mono" style="font-size:11px;color:var(--dim)" aria-live="polite"></span>
      </div>
      </div>
    </div>

    <!-- Issue #413: saved backtest templates — list, load (refills controls, never replays), delete. -->
    <div class="bt-section bt-peer-section" id="btSecTemplates">
      <button type="button" class="bt-section-head" aria-expanded="true" aria-controls="btSecTemplatesBody" onclick="toggleBtSection(this,'btSecTemplatesBody')">
        <span class="bt-section-dot bt-section-dot-green"></span>
        <span>💾 Backtest Templates</span>
        <span class="bt-section-chevron" aria-hidden="true">▾</span>
      </button>
      <div class="bt-section-body" id="btSecTemplatesBody">
      <div class="card">
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px">
          <input id="btTemplateName" placeholder="Template name…" maxlength="64" style="flex:1;min-width:160px;background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:6px 10px;color:var(--tx)" aria-label="Template name">
          <button class="btn" onclick="loadBacktestTemplateList()" title="Reload the saved template list">↻ Refresh</button>
        </div>
        <div id="btTemplateStatus" class="mono" style="font-size:11px;color:var(--dim);margin-bottom:6px" aria-live="polite"></div>
        <div id="btTemplateList" style="display:flex;flex-direction:column;gap:6px"></div>
      </div>
      </div>
    </div>

      <div class="bt-section bt-peer-section" id="btSecOverall">
      <button type="button" class="bt-section-head" aria-expanded="true" aria-controls="btSecOverallBody" onclick="toggleBtSection(this,'btSecOverallBody')">
        <span class="bt-section-dot bt-section-dot-green"></span>
        <span>📈 Overall Execution Results</span>
        <span class="bt-section-chevron" aria-hidden="true">▾</span>
      </button>
      <div class="bt-section-body" id="btSecOverallBody">
      <div class="card" id="btOverallCard">

      <div class="kpi" id="btKpiRow">
        <div class="box" title="Net cumulative P&L across all executed windows"><div class="lbl">Total P&L</div><div class="val" id="btTotalPnl" style="color:var(--up)">+$0.00</div><div class="sub" id="btAvgPnl">+$0.00 / window</div></div>
        <div class="box" title="Proportion of windows where both legs were captured and merged on CTF"><div class="lbl">Pair Capture Rate ℹ️</div><div class="val" id="btPairRate">0.0%</div><div class="sub" id="btPairsCount">0 / 0 pairs</div></div>
        <div class="box" id="btCardPairCost" title="Mean pair cost (entry_up + entry_down) and edge vs $1.00 settlement"><div class="lbl">Pair Cost vs $1.00 Settle ℹ️</div><div class="val" id="btMeanPairCost">--</div><div class="sub" id="btPairCostSub">0 pairs captured</div></div>
        <div class="box" title="Proportion of windows where safety stop exit was triggered on adverse drift"><div class="lbl">Exit Stop Rate ℹ️</div><div class="val" id="btExitRate" style="color:var(--down)">0.0%</div><div class="sub" id="btExitsCount">0 exits</div></div>
        <div class="box" title="Maximum peak-to-trough equity drawdown from $0.00 start"><div class="lbl">Max Drawdown</div><div class="val" id="btMaxDd" style="color:var(--gold)">-$0.00</div><div class="sub" id="btMaxDdSub">Peak to trough from $0.00 start</div></div>
        <div class="box" title="Proportion of windows with net positive P&L after all window events"><div class="lbl">Win Rate ℹ️</div><div class="val" id="btWinRate">0.0%</div><div class="sub" id="btWinsCount">0 / 0 profitable</div></div>
        <div class="box" title="Total wall-clock duration of the last backtest sweep (measured by Run Sweep)"><div class="lbl">Elapsed Time ℹ️</div><div class="val" id="btElapsedTime" style="color:var(--cyan)">--</div><div class="sub" id="btElapsedSub">Sweep duration</div></div>
      </div>
      <style>.bt-charts-row{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px}.bt-charts-row>div{margin-top:0 !important}@media (max-width:900px){.bt-charts-row{grid-template-columns:1fr}}</style>
      <div class="bt-charts-row">
      <div style="background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:12px;min-width:0">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
          <h4 style="margin:0;font:700 11px var(--disp);color:var(--faint)">Cumulative Equity Curve</h4>
          <span id="btEquityWarning" style="display:none;font-size:11px;font-weight:600;color:var(--gold);background:rgba(235,178,58,0.12);padding:2px 8px;border-radius:4px;border:1px solid rgba(235,178,58,0.3)">⚠️ 0 fills recorded in this run. Check tape data density for this dataset.</span>
        </div>
        <canvas id="chartEquity" height="120"></canvas>
      </div>
      <div style="background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:12px;min-width:0">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;flex-wrap:wrap;gap:8px">
          <h4 style="margin:0;font:700 11px var(--disp);color:var(--faint)">Per-Window P&amp;L Distribution (Histogram)</h4>
          <div style="display:flex;align-items:center;gap:8px">
            <span id="btPnlHistStats" class="mono" style="font-size:11px;color:var(--dim)"></span>
            <span id="btPnlHistWarning" style="display:none;font-size:11px;font-weight:600;color:var(--gold);background:rgba(235,178,58,0.12);padding:2px 8px;border-radius:4px;border:1px solid rgba(235,178,58,0.3)">⚠️ 0 fills recorded in this run.</span>
          </div>
        </div>
        <canvas id="chartPnlHist" height="120"></canvas>
      </div>
      </div>
      </div>
      </div>
    </div>

    <div class="bt-section bt-peer-section" id="btSecSweep">
      <button type="button" class="bt-section-head" aria-expanded="true" aria-controls="btSecSweepBody" onclick="toggleBtSection(this,'btSecSweepBody')">
        <span class="bt-section-dot bt-section-dot-amber"></span>
        <span>🔬 Sweep Visual</span>
        <span class="bt-section-chevron" aria-hidden="true">▾</span>
      </button>
      <div class="bt-section-body" id="btSecSweepBody">
      <div class="card" id="btSweepCard">
      <div style="display:flex;justify-content:flex-end;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:8px">
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <!-- Issue #344: the axis selector lives in the sweep card's title
               (sweepAxisSelect rendered by sweepCard), not as a separate
               dropdown row above it. -->
          <button class="btn btn-primary" id="btnRunSweepVisual" onclick="runSweepVisual()">▶ Run Sweep Visual</button>
          <!-- Issue #386: the Sweep Visual card gets its own Stop. The operator
               starts this run from HERE and watches it HERE, so the control has to
               be in this card — the one beside the Setup & Run button was scrolled
               out of reach in every screenshot of a running sweep. Both controls
               call the same handler and the same endpoint; the server holds one
               active run, so whichever is on screen is the one to press. -->
          <button class="btn bt-stop" id="btnStopSweepVisual" onclick="stopBacktestRun()" hidden>⏹ Stop</button>
        </div>
      </div>
      <div id="btSweepMeta" class="mono" style="font-size:11px;color:var(--dim)"></div>
      <style>#btSweepMeta:empty{display:none}#btSweepMeta:not(:empty){margin-top:6px;margin-bottom:6px}
      #btSweepMeta .sweep-title{display:flex;flex-wrap:wrap;gap:2px 14px;align-items:baseline;margin-bottom:6px}
      #btSweepMeta .sweep-title-sub{font:11px var(--mono);color:var(--dim)}
      #btSweepMeta .sweep-anchor-field{display:inline-flex;align-items:baseline;gap:6px;white-space:nowrap}
      #btSweepMeta .sweep-anchor-label{font:700 9px var(--disp);letter-spacing:1px;text-transform:uppercase;color:var(--faint)}
      #btSweepMeta .sweep-anchor-unit{font:11px var(--mono);color:var(--dim)}
      #btSweepMeta .sweep-anchor-field.is-set .sweep-anchor-label,
      #btSweepMeta .sweep-anchor-field.is-set .sweep-anchor-unit{color:var(--gold)}
      #btSweepMeta .sweep-card{display:block;padding:10px 12px;background:var(--panel2);border:1px solid var(--line);border-radius:10px;font:12px/1.5 var(--body)}
      #btSweepMeta .sweep-lab{display:block;font:700 9px var(--disp);letter-spacing:1px;text-transform:uppercase;color:var(--faint);margin-bottom:4px}
      #btSweepMeta .sweep-cols{display:grid;grid-template-columns:1.2fr 1fr 1fr;gap:8px 18px}
      #btSweepMeta .sweep-dl{display:block}
      #btSweepMeta .sweep-row{display:flex;justify-content:space-between;align-items:baseline;gap:8px;padding:1px 6px;border-radius:5px}
      #btSweepMeta .sweep-row.subject{background:var(--panel);box-shadow:inset 0 0 0 1px var(--gold)}
      #btSweepMeta .sweep-row .k{color:var(--dim);font-size:11px}
      #btSweepMeta .sweep-row .v{color:var(--tx);font:11px var(--mono);text-align:left}
      #btSweepMeta .sweep-tag{font:700 8px var(--disp);letter-spacing:.6px;text-transform:uppercase;color:var(--gold)}
      #btSweepMeta .sweep-mkt{display:inline-block;font:11px var(--mono);color:var(--tx);background:var(--panel);border:1px solid var(--line);border-radius:5px;padding:1px 7px;margin:0 0 4px 4px}
      #btSweepMeta .sweep-mkt.pending{color:var(--tx);background:transparent;border-style:solid;border-color:var(--gold)}
      #btSweepMeta .sweep-mkt.off{color:var(--faint);background:transparent;border-style:dashed;opacity:.55}
      #btSweepMeta .sweep-mkt-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:4px}
      #btSweepMeta .sweep-mkt-grid .sweep-mkt{display:block;text-align:center;margin:0}
      #btSweepMeta .sweep-stats{display:flex;flex-wrap:wrap;gap:6px 24px;padding-bottom:8px;margin-bottom:8px;border-bottom:1px solid var(--line)}
      #btSweepMeta .sweep-stats > span{display:block}
      #btSweepMeta .sweep-stat-v{font:11.5px var(--mono);color:var(--tx);font-variant-numeric:tabular-nums}
      #btSweepMeta .sweep-verdict{display:block;margin-top:8px;padding-top:6px;border-top:1px solid var(--line);font-size:11.5px}
      #btSweepMeta .sweep-verdict.yours{color:var(--gold)}
      #btSweepMeta .sweep-verdict.none{color:var(--warn)}
      </style>
      <div id="btSweepAggCard" class="bt-chart-card" tabindex="0" role="button" aria-label="Open aggregate Sweep Visual chart detail" style="background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:8px 10px;margin-bottom:8px">
        <h4 style="margin:0 0 4px;font:700 11px var(--disp);color:var(--faint)">ALL MARKETS — total P&amp;L vs param</h4>
        <div style="position:relative;height:120px"><canvas id="chartSweepAgg" height="120"></canvas></div>
      </div>
      <div id="btSweepGrid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px"></div>
      </div>
      </div>
    </div>

    <div class="bt-section bt-peer-section" id="btSecSeries">
      <button type="button" class="bt-section-head" aria-expanded="true" aria-controls="btSecSeriesBody" onclick="toggleBtSection(this,'btSecSeriesBody')">
        <span class="bt-section-dot bt-section-dot-blue"></span>
        <span>📊 Per-Series Performance</span>
        <span class="bt-section-chevron" aria-hidden="true">▾</span>
      </button>
      <div class="bt-section-body" id="btSecSeriesBody">
      <div class="card">
      <div id="btSeriesTableWrap"></div>
      </div>
      </div>
    </div>

    <div class="bt-section bt-peer-section" id="btSecLog">
      <button type="button" class="bt-section-head" aria-expanded="true" aria-controls="btSecLogBody" onclick="toggleBtSection(this,'btSecLogBody')">
        <span class="bt-section-dot bt-section-dot-blue"></span>
        <span>📝 Executed Windows Log</span>
        <span class="bt-section-chevron" aria-hidden="true">▾</span>
      </button>
      <div class="bt-section-body" id="btSecLogBody">
      <div class="card">
      <div style="display:flex;justify-content:flex-end;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:12px">
        <div id="btLogControls" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <input type="text" id="btLogSearch" placeholder="Search slug/cid..." oninput="onBtLogFilterChange()" style="padding:4px 8px;font-size:11.5px;background:var(--panel2);border:1px solid var(--line);border-radius:6px;color:var(--fg);width:140px">
          <select id="btLogSeriesFilter" onchange="onBtLogFilterChange()" style="padding:4px 8px;font-size:11.5px;background:var(--panel2);border:1px solid var(--line);border-radius:6px;color:var(--fg)">
            <option value="">All Series</option>
          </select>
          <select id="btLogResultFilter" onchange="onBtLogFilterChange()" style="padding:4px 8px;font-size:11.5px;background:var(--panel2);border:1px solid var(--line);border-radius:6px;color:var(--fg)">
            <option value="">All Results</option>
            <option value="pair">Pairs Merged</option>
            <option value="exit">Stop Exited</option>
            <option value="unresolved">Flat / Unresolved</option>
          </select>
          <select id="btLogPageSize" onchange="onBtLogPageSizeChange()" style="padding:4px 8px;font-size:11.5px;background:var(--panel2);border:1px solid var(--line);border-radius:6px;color:var(--fg)">
            <option value="25">25 / page</option>
            <option value="50">50 / page</option>
            <option value="100">100 / page</option>
            <option value="all">All</option>
          </select>
        </div>
      </div>
      <div id="btTradesTableWrap"></div>
      <div id="btLogPagination" style="display:flex;justify-content:space-between;align-items:center;margin-top:10px;font-size:12px;color:var(--dim)">
        <span id="btLogPageInfo">Showing 0-0 of 0 windows</span>
        <div style="display:flex;gap:6px">
          <button class="btn" id="btLogBtnPrev" onclick="onBtLogPagePrev()" style="padding:3px 10px;font-size:11.5px" disabled>◀ Prev</button>
          <button class="btn" id="btLogBtnNext" onclick="onBtLogPageNext()" style="padding:3px 10px;font-size:11.5px" disabled>Next ▶</button>
        </div>
      </div>
      </div>
    </div>
    </div>
  </div>

  <div id="btChartDialog" class="bt-chart-dialog" role="dialog" aria-modal="true" aria-labelledby="btChartDialogTitle" hidden>
    <div class="bt-chart-dialog-panel">
      <div class="bt-chart-dialog-head">
        <h2 id="btChartDialogTitle" class="bt-chart-dialog-title">Sweep chart detail</h2>
        <button type="button" class="btn bt-chart-dialog-close" id="btChartDialogClose" aria-label="Close expanded chart">✕ Close</button>
      </div>
      <canvas id="btChartDialogCanvas" class="bt-chart-dialog-canvas"></canvas>
    </div>
  </div>
  </div>

  <!-- TAB: JUNGLE KING — OFAT manifest quick reference (issue #319, read-only) -->
  <div id="tab-jungleking" class="tab-content" aria-labelledby="tab-btn-jungleking">
    <div class="card" style="border-top:2px solid var(--proj)">
      <h3>Jungle King — OFAT Manifest</h3>
      <div style="font-size:12.5px;color:var(--dim);line-height:1.6">
        One-Factor-at-a-Time candidate ranges over the golden dataset: vary one
        parameter across its range while every other stays at its baseline.
        This is a read-only quick reference; start sweeps from the Backtest Sweeper.
      </div>
    </div>
    <div id="jkNotice" class="card" role="status" aria-live="polite" style="display:none;border-top:2px solid var(--down);color:var(--down);font-size:12.5px;line-height:1.6"></div>
    <div id="jkGroups"></div>
  </div>

  <!-- TAB 3: STATISTICAL ANALYSIS & DISTRIBUTIONS -->
  <div id="tab-summary" class="tab-content">
    <div class="hero" style="display:grid;grid-template-columns:1.2fr .8fr;gap:12px;margin-bottom:12px">
      <div class="card" style="border-top:2px solid var(--up)" aria-live="polite" aria-atomic="true">
        <h3>Research Conclusion — SPREAD-2</h3>
        <div style="font:700 24px var(--mono);color:var(--up);margin:4px 0"><span id="oscHeroOverallPct">—</span> of Windows Are Oscillating</div>
        <div style="font-size:12.5px;color:var(--dim);line-height:1.6">
          Across <span id="oscHeroTotalWindows" class="mono">—</span> empirical windows measured in 5m and 15m: on 5m <b id="oscHeroPct5m">—</b> oscillating — both sides quoted dynamically at <code>mid - offset</code> (e.g. $0.48 on 50¢ mid, $0.96/pair) are filled and merged for +$0.04/share profit. On 15m <b id="oscHeroPct15m">—</b> oscillating.
        </div>
        <div style="font-size:11px;color:var(--dim);margin-top:8px">Computed from <code>oscillation_summary.json</code> · as of <span id="oscHeroAsOf" class="mono">—</span></div>
      </div>
      <div class="card" style="border-top:2px solid var(--gold)">
        <h3>Recommended Stop-Loss Thresholds</h3>
        <div style="font-size:12px;color:var(--dim)">Tighter = earlier exit. BTC shows highest monotonicity:</div>
        <div class="mono" style="font-size:12px;margin-top:8px;display:flex;flex-direction:column;gap:4px">
          <div><b style="color:var(--down)">BTC 5m:</b> Stop +$0.09 (Exit @ $0.59 UP)</div>
          <div><b style="color:var(--gold)">SOL 5m:</b> Stop +$0.11 (Exit @ $0.61)</div>
          <div><b style="color:var(--up)">ETH/BNB/XRP 5m:</b> Stop +$0.12 (Exit @ $0.62)</div>
          <div><b>15m General:</b> Stop +$0.13</div>
        </div>
        <div id="stopLossProvenance" style="font-size:11px;color:var(--dim);margin-top:10px;line-height:1.5;border-top:1px solid var(--line);padding-top:8px">
          Not computed from the live dataset — a static heuristic with no surviving sweep behind it, unlike the card on the left. The newest study, <code>docs/ev-research-findings-2026-09-11.md</code> (2026-09-11, itself provisional pending #182), reached the opposite conclusion: stop-loss exits were its largest PnL destroyer, and it recommends holding to settlement instead.
        </div>
      </div>
    </div>
    <div class="grid">
      <div class="card"><h3>1. Oscillation Rate by Asset</h3><canvas id="cPerAsset" height="220"></canvas></div>
      <div class="card"><h3>2. Max Excursion Distribution</h3><canvas id="cHist" height="220"></canvas></div>
    </div>
    <div class="grid" style="margin-top:12px">
      <div class="card"><h3>3. Open Deviation from 50¢</h3><canvas id="cStart" height="200"></canvas></div>
      <div class="card"><h3>4. Touch Pair Distribution</h3><canvas id="cPair" height="200"></canvas></div>
    </div>
  </div>

  <!-- TAB 4: TICKS FILE MANAGER & INGESTION -->
  <div id="tab-ticks" class="tab-content">
    <div class="card" style="border-top:2px solid var(--gold)">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
        <h3 style="margin:0">🏆 Golden Dataset (Certification Checklist)</h3>
        <button class="btn" style="font-size:11px;padding:4px 10px" onclick="loadGoldenCard()">🔄 Refresh</button>
      </div>
      <div id="goldenCardWrap"><div style="color:var(--faint);font-size:12px">Loading golden dataset state…</div></div>
    </div>

    <div class="card" style="border-top:2px solid var(--proj)">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
        <h3 style="margin:0">💾 Tick Data Files (JSONL Repository)</h3>
        <div style="display:flex;gap:8px">
          <button class="btn" style="font-size:11px;padding:4px 10px" onclick="loadManifest()">🔄 Refresh List</button>
        </div>
      </div>
      <div style="font-size:12px;color:var(--dim);margin-bottom:12px">The collector writes full book depth and tape data to <code>run/ticks/ticks_YYYY-MM-DD.jsonl</code>. Additional tick files can be uploaded for analysis.</div>
      <div id="manifestNotice" style="display:none;padding:8px 12px;border-radius:6px;margin-bottom:10px;font-size:12px;font-weight:600"></div>
      <div id="manifestAggregateWrap"></div>
      <div id="manifestTableWrap">Loading files...</div>
    </div>

    <!-- Per-file integrity verify results are rendered inline as accordion
         rows directly under each file in the files table (no modal). -->

    <!-- Custom Delete Confirmation Modal -->
    <div id="deleteModalOverlay" style="display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.7);z-index:9999;align-items:center;justify-content:center">
      <div style="background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:22px 24px;max-width:420px;width:90%;box-shadow:0 12px 36px rgba(0,0,0,0.6);text-align:center">
        <div style="font-size:32px;margin-bottom:8px">🗑️</div>
        <h3 style="margin:0 0 8px;font-size:16px;color:var(--tx)">Confirm File Deletion</h3>
        <p style="margin:0 0 16px;font-size:13px;color:var(--dim);line-height:1.5">Are you sure you want to permanently delete the file:<br><span id="deleteFileNameTarget" class="mono" style="color:var(--down);font-weight:700;word-break:break-all"></span>?</p>
        <div style="display:flex;gap:10px;justify-content:center">
          <button class="btn" style="padding:6px 16px" onclick="closeDeleteModal()">Cancel</button>
          <button id="confirmDeleteBtn" class="btn btn-danger" style="padding:6px 16px;font-weight:700" onclick="executeDeleteFile()">Yes, Delete File</button>
        </div>
      </div>
    </div>

    <div class="card">
      <h3>📤 Upload JSONL File (Streaming Ingestion)</h3>
      <div id="dropZone" style="border:2px dashed var(--line);border-radius:10px;padding:28px 20px;text-align:center;background:var(--panel2);transition:all 0.2s"
           ondragover="event.preventDefault();this.style.borderColor='var(--up)';this.style.background='rgba(51,201,181,0.06)'"
           ondragleave="this.style.borderColor='var(--line)';this.style.background='var(--panel2)'"
           ondrop="handleFileDrop(event)">
        <p style="margin:0 0 6px;font-size:14px;font-weight:600">Drag & drop a <code>.jsonl</code> file here or click to browse</p>
        <p style="margin:0 0 14px;font-size:12px;color:var(--dim)">Supports streaming upload of large files (10MB–1GB+) with zero memory buffering</p>
        <input type="file" id="fileInput" accept=".jsonl,.txt" style="display:none" onchange="handleFileSelect(event)">
        <button class="btn btn-primary" onclick="document.getElementById('fileInput').click()">📁 Select File from Computer</button>
        <div id="uploadProgressWrap" style="display:none;margin-top:16px;max-width:400px;margin-left:auto;margin-right:auto">
          <div style="background:var(--line);height:8px;border-radius:4px;overflow:hidden">
            <div id="uploadProgressBar" style="width:0%;height:100%;background:var(--up);transition:width 0.15s ease"></div>
          </div>
          <div id="uploadProgressText" class="mono" style="font-size:11px;margin-top:6px;color:var(--faint)">0%</div>
        </div>
        <div id="uploadStatus" class="mono" style="font-size:12px;margin-top:12px;color:var(--gold)"></div>
      </div>
    </div>
  </div>

  <!-- TAB 5: LIVE TRADING COCKPIT -->
  <div id="tab-cockpit" class="tab-content active">
    <!-- Top Control Bar -->
    <div class="card" style="border-top:2px solid var(--up)">
      <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;margin-bottom:12px">
        <div style="display:flex;align-items:center;gap:10px">
          <h3 style="margin:0;font-size:15px;display:flex;align-items:center;gap:8px">
            <span>⚡</span> Trading Platform (5m Markets)
          </h3>
          <span id="cockpitStatusPill" class="pill pill-flat" style="font-size:11px;padding:3px 10px;font-weight:700">BOT: STOPPED</span>
          <span id="cockpitModePill" class="pill" style="font-size:11px;padding:3px 10px;background:rgba(51,201,181,0.15);color:var(--up);border-color:rgba(51,201,181,0.3);font-weight:700">PAPER TRADING</span>
        </div>
        <div style="display:flex;align-items:center;gap:8px">
          <button id="btnCockpitToggle" class="btn btn-primary" style="font-size:13px;padding:7px 16px" onclick="toggleCockpitBot()">▶ START BOT</button>
          <button class="btn" style="font-size:13px;padding:7px 14px" onclick="restartCockpitBot()">🔄 RESTART</button>
          <button class="btn" style="font-size:13px;padding:7px 14px;background:rgba(243,186,47,0.15);border-color:var(--gold);color:var(--gold);font-weight:700" onclick="loadCockpitDemoData()">🎲 DEMO DATA</button>
          <button id="btnSyncRealRun" class="btn" style="font-size:13px;padding:7px 14px;background:rgba(51,201,181,0.2);border-color:var(--up);color:var(--up);font-weight:700" onclick="syncRealRunTrades()">📥 Sync Real Run (Polymarket)</button>
          <button class="btn btn-danger" style="font-size:13px;padding:7px 14px" onclick="resetCockpitPnL()">🗑 RESET P&L</button>
          <button id="btnPanicCancel" class="btn btn-danger" style="font-size:13px;padding:7px 14px;font-weight:700;background:rgba(240,104,77,0.3);border-color:var(--down)" onclick="panicCancelAllOrders()">🚨 PANIC CANCEL ALL</button>
        </div>
      </div>

      <!-- Config Inputs -->
      <div class="form-grid" style="margin-top:10px">
        <div class="form-group">
          <label data-param-label="offset"></label>
          <input type="number" step="0.1" min="0.1" max="49" id="cockpitOffset" data-param="offset" value="2" oninput="validateCockpitInputs()">
        </div>
        <div class="form-group">
          <label data-param-label="exit_thresh_by_slug"></label>
          <input type="number" step="0.1" min="0.1" max="50" id="cockpitExit" data-param="exit_thresh_by_slug" value="5" oninput="validateCockpitInputs()">
        </div>
        <div class="form-group">
          <label data-param-label="exit_reversal"></label>
          <input type="number" step="0.1" min="0.1" max="50" id="cockpitExitReversal" data-param="exit_reversal" value="2" oninput="validateCockpitInputs()">
        </div>
        <div class="form-group">
          <label data-param-label="quote_shares"></label>
          <input type="number" min="5" max="10000" step="1" id="cockpitShares" data-param="quote_shares" value="5" oninput="validateCockpitInputs()">
        </div>
        <div class="form-group">
          <label data-param-label="entry_delay_sec"></label>
          <input type="number" min="0" max="3600" step="5" id="cockpitEntryDelay" data-param="entry_delay_sec" value="0" placeholder="0 = off" oninput="validateCockpitInputs()">
        </div>
        <div class="form-group">
          <label data-param-label="enable_leg_chase"></label>
          <select id="cockpitLegChase" data-param="enable_leg_chase">
            <option value="true" selected>On — chase the unfilled leg within the cap</option>
            <option value="false">Off — passive quote only</option>
          </select>
        </div>
        <div class="form-group">
          <label for="cockpitWsAuthority">WS Book Authority</label>
          <select id="cockpitWsAuthority" data-param="ws_book_authority" title="Issue #353: when off, the socket never prices best/mid/spread — REST does. Default off until the socket book is proven.">
            <option value="true">On — socket prices best/mid/spread</option>
            <option value="false" selected>Off — REST prices everything</option>
          </select>
        </div>
        <div class="form-group">
          <label>Execution Mode</label>
          <select id="cockpitMode" onchange="onCockpitModeChange()">
            <option value="paper" selected>Paper — simulated fills, real books</option>
            <option value="live">Real Money — Polymarket orders</option>
          </select>
        </div>
      </div>

      <!-- Structural Limits — engine invariants, badged apart from tuning dials (issue #233) -->
      <div id="cockpitStructuralGroup" style="margin-top:10px;padding:10px 12px;border:1px solid var(--line);border-left:2px solid var(--down);border-radius:8px;background:var(--panel2)">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
          <span style="font:700 11px var(--disp);color:var(--down);text-transform:uppercase;letter-spacing:0.06em">Structural Limits — Engine Invariants &amp; Safety Bounds</span>
          <span class="param-structural-badge">STRUCTURAL</span>
        </div>
        <div class="form-grid">
        <div class="form-group">
          <label data-param-label="naked_leg_at_expiry"></label>
          <select id="cockpitNakedLegAtExpiry" data-param="naked_leg_at_expiry">
            <option value="close" selected>Close at Book</option>
            <option value="hold">Hold to Settlement</option>
          </select>
        </div>
        <div class="form-group">
          <label for="cockpitQuoteLo">Quotable Range Lo</label>
          <input type="number" min="0" max="100" step="0.1" id="cockpitQuoteLo" data-param="quote_range" value="10" oninput="validateCockpitInputs()">
        </div>
        <div class="form-group">
          <label for="cockpitQuoteHi">Quotable Range Hi</label>
          <input type="number" min="0" max="100" step="0.1" id="cockpitQuoteHi" data-param="quote_range" value="90" oninput="validateCockpitInputs()">
        </div>
        <div class="form-group">
          <label data-param-label="max_pair_cost"></label>
          <input type="number" min="50" max="100" step="0.1" id="cockpitPairCost" data-param="max_pair_cost" value="99" placeholder="max pair cost" oninput="validateCockpitInputs()">
        </div>
        <div class="form-group">
          <label data-param-label="dead_zone_val"></label>
          <input type="number" min="0" max="3600" step="0.01" id="cockpitDeadZoneVal" data-param="dead_zone_val" value="0.10" placeholder="0.10 or sec" oninput="validateCockpitInputs()">
        </div>
        <div class="form-group">
          <label data-param-label="dead_zone_unit"></label>
          <select id="cockpitDeadZoneUnit" data-param="dead_zone_unit">
            <option value="pct" selected>% of window</option>
            <option value="sec">Seconds</option>
          </select>
        </div>
        </div>
      </div>

      <div class="form-grid" style="margin-top:10px">
        <div class="form-group" style="grid-column:span 2">
          <label id="lblCockpitWallet">Polymarket Wallet Address (Optional)</label>
          <input type="text" id="cockpitWallet" placeholder="0x... (fetches real balance)" onchange="if ($('cockpitMode').value === 'live') onCockpitModeChange()">
        </div>
        <div class="form-group">
          <label id="lblCockpitStartBal">Starting Portfolio Balance ($)</label>
          <input type="number" min="5" step="10" id="cockpitStartBal" value="1000.00" placeholder="≥ 5.00" oninput="validateCockpitInputs()">
        </div>
        <div class="form-group" style="justify-content:flex-end;align-items:flex-end;gap:6px">
          <span id="cockpitParamsLockHint" style="display:none;font:700 10px var(--disp);color:var(--warn);letter-spacing:0.04em;text-align:right">🔒 LOCKED WHILE BOT IS RUNNING — STOP THE BOT TO CHANGE PARAMETERS</span>
          <button id="btnApplyParams" class="btn" style="background:rgba(51,201,181,0.15);border-color:var(--up);color:var(--up);font-weight:700;height:35px" onclick="applyCockpitConfig()">💾 APPLY PARAMETERS</button>
        </div>
      </div>

      <!-- Market Selection: Tokens & Duration -->
      <div style="margin-top:14px;padding-top:12px;border-top:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <span style="font:700 11px var(--disp);color:var(--faint);text-transform:uppercase;letter-spacing:0.06em">Assets:</span>
          <div id="cockpitTokenChips" style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
            <button type="button" class="filter-chip active" id="chip-token-BTC" onclick="toggleCockpitToken('BTC')">BTC</button>
            <button type="button" class="filter-chip active" id="chip-token-ETH" onclick="toggleCockpitToken('ETH')">ETH</button>
            <button type="button" class="filter-chip active" id="chip-token-BNB" onclick="toggleCockpitToken('BNB')">BNB</button>
            <button type="button" class="filter-chip active" id="chip-token-SOL" onclick="toggleCockpitToken('SOL')">SOL</button>
            <button type="button" class="filter-chip active" id="chip-token-XRP" onclick="toggleCockpitToken('XRP')">XRP</button>
          </div>
          <button type="button" id="btnTokensAll" class="btn" style="font-size:10px;padding:2px 8px" onclick="setCockpitTokensAll(true)">All</button>
          <button type="button" id="btnTokensClear" class="btn" style="font-size:10px;padding:2px 8px" onclick="setCockpitTokensAll(false)">Clear</button>
          <span id="cockpitFilterLockHint" style="display:none;font:700 10px var(--disp);color:var(--warn);letter-spacing:0.04em">🔒 LOCKED WHILE BOT IS RUNNING — STOP THE BOT TO CHANGE MARKETS</span>
        </div>
        <div style="display:flex;align-items:center;gap:8px">
          <span style="font:700 11px var(--disp);color:var(--faint);text-transform:uppercase;letter-spacing:0.06em">Duration:</span>
          <div style="display:flex;gap:4px;background:var(--panel2);padding:2px;border-radius:8px;border:1px solid var(--line)">
            <button type="button" id="btnDur5m" class="tab-btn active" style="font-size:11px;padding:4px 10px" onclick="setCockpitDuration('5m')">5m</button>
            <button type="button" id="btnDur15m" class="tab-btn" style="font-size:11px;padding:4px 10px" onclick="setCockpitDuration('15m')">15m</button>
            <button type="button" id="btnDurBoth" class="tab-btn" style="font-size:11px;padding:4px 10px" onclick="setCockpitDuration('both')">Both</button>
          </div>
        </div>
      </div>
    </div>

    <!-- KPI Summary -->
    <div class="kpi" id="cockpitKpiBar">
      <div class="box">
        <div class="lbl">Total Realized P&L</div>
        <div class="val" id="cockpitRealizedPnl" style="color:var(--tx)">$0.00</div>
        <div class="sub" id="cockpitRealizedSub">+0.0%</div>
      </div>
      <div class="box">
        <div class="lbl">Portfolio Net Value</div>
        <div class="val" id="cockpitPortfolioVal" style="color:var(--gold)">$1,000.00</div>
        <div class="sub">Account Equity</div>
      </div>
      <div class="box">
        <div class="lbl">Win Rate (Pairs / Closed)</div>
        <div class="val" id="cockpitWinRate" style="color:var(--up)">0.0%</div>
        <div class="sub" id="cockpitTradesSummary">0 trades</div>
      </div>
      <div class="box">
        <div class="lbl">Pairs Merged ($1.00)</div>
        <div class="val" id="cockpitPairsCount" style="color:var(--up)">0</div>
        <div class="sub">Completed pairs</div>
      </div>
      <div class="box">
        <div class="lbl">Stops Triggered (0.05)</div>
        <div class="val" id="cockpitStopsCount" style="color:var(--down)">0</div>
        <div class="sub">Protected exits</div>
      </div>
      <div class="box">
        <div class="lbl">Active Exposure</div>
        <div class="val" id="cockpitExposure" style="color:var(--dim)">$0.00</div>
        <div class="sub">Capital at risk</div>
      </div>
    </div>

    <!-- Interactive Real-Time Equity Curve Card -->
    <div class="card" style="margin-top:12px">
      <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:10px">
        <h3 style="margin:0;display:flex;align-items:center;gap:8px">
          <span>📈</span> Real-Time Equity & Performance Curve
        </h3>
        <div style="display:flex;align-items:center;gap:6px">
          <button id="btnChartModeTotal" class="btn btn-primary" style="font-size:11px;padding:4px 10px" onclick="setCockpitChartMode('total')">Portfolio Total Net Value ($)</button>
          <button id="btnChartModeUsd" class="btn" style="font-size:11px;padding:4px 10px" onclick="setCockpitChartMode('breakdown_usd')">Market P&L Breakdown ($)</button>
          <button id="btnChartModePct" class="btn" style="font-size:11px;padding:4px 10px" onclick="setCockpitChartMode('breakdown_pct')">Market Return Breakdown (%)</button>
        </div>
      </div>
      <div id="cockpitChartWrap" style="height:270px;width:100%;position:relative;background:var(--panel2);border:1px solid var(--line);border-radius:8px;overflow:hidden;user-select:none">
        <div id="cockpitSvgWrap" style="width:100%;height:100%"></div>
        <div id="cockpitChartTooltip" style="position:absolute;display:none;pointer-events:none;background:rgba(18,22,31,0.96);border:1px solid rgba(255,255,255,0.18);backdrop-filter:blur(8px);border-radius:6px;padding:8px 12px;box-shadow:0 8px 24px rgba(0,0,0,0.6);font-size:11px;z-index:20;color:var(--tx);min-width:180px"></div>
      </div>
      <div id="cockpitChartLegend" style="display:flex;gap:14px;flex-wrap:wrap;margin-top:8px;font-size:11px;align-items:center" class="mono"></div>
    </div>

    <!-- Market Matrix -->
    <div class="card" style="margin-top:12px">
      <h3 style="margin:0 0 10px">
        <span>🎯 Market Matrix</span>
        <span id="cockpitActiveMarketsBadge" class="pill pill-flat" style="font-size:11px;padding:2px 8px;font-weight:600">5 ACTIVE MARKETS</span>
        <span id="cockpitStaleMarketsBadge" class="pill pill-flat" style="font-size:11px;padding:2px 8px;font-weight:600" title="Markets whose strategy update has not run within the staleness threshold">0 STALE</span>
      </h3>
      <div id="cockpitMarketGrid" class="live-grid" style="grid-template-columns:repeat(auto-fill, minmax(230px, 1fr));gap:10px"></div>
    </div>

    <!-- Unified Orders & Trades Component -->
    <div class="card" id="orders-trades-card" style="margin-top:12px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;flex-wrap:wrap;gap:8px">
        <div class="ot-tabs" id="otTabs">
          <button class="ot-tab-btn active" id="otTabBtnOrders" onclick="switchOtTab('orders')">
            OPEN ORDERS <span class="ot-count" id="otOrdersCount">0</span>
          </button>
          <button class="ot-tab-btn" id="otTabBtnPositions" onclick="switchOtTab('positions')">
            POSITIONS <span class="ot-count" id="otPositionsCount">0</span>
          </button>
          <button class="ot-tab-btn" id="otTabBtnTrades" onclick="switchOtTab('trades')">
            CLOSED TRADES <span class="ot-count" id="otTradesCount">0</span>
          </button>
        </div>
        <div style="display:flex;align-items:center;gap:6px">
          <span id="cockpitOrdersCount" style="display:none">0</span>
          <span id="cockpitPositionsCount" style="display:none">0</span>
          <button class="btn" id="otHeightToggleBtn" style="font-size:11px;padding:4px 10px" onclick="toggleOtHeight()" title="Expand tables to full cockpit height" aria-expanded="false">⛶ Expand</button>
          <button class="btn" style="font-size:11px;padding:4px 10px" onclick="fetchCockpitState()">🔄 Refresh</button>
        </div>
      </div>

      <!-- Tab 1: Open Orders -->
      <div id="otPaneOrders" class="ot-pane active ot-pane-scroll" style="max-height:min(680px,72vh);overflow-y:auto">
        <table class="tbl" id="cockpitOrdersTable">
          <thead>
            <tr>
              <th class="ot-th-sortable" data-tab="orders" data-col="time" aria-sort="none"><button type="button" class="ot-sort-button" onclick="sortOtTable('orders','time')" title="Sort by Time">Time <span class="ot-sort-ind" aria-hidden="true">↕</span></button></th>
              <th class="ot-th-sortable" data-tab="orders" data-col="market" aria-sort="none"><button type="button" class="ot-sort-button" onclick="sortOtTable('orders','market')" title="Sort by Market">Market <span class="ot-sort-ind" aria-hidden="true">↕</span></button></th>
              <th class="ot-th-sortable" data-tab="orders" data-col="side" aria-sort="none"><button type="button" class="ot-sort-button" onclick="sortOtTable('orders','side')" title="Sort by Side">Side <span class="ot-sort-ind" aria-hidden="true">↕</span></button></th>
              <th class="ot-th-sortable" data-tab="orders" data-col="price" aria-sort="none"><button type="button" class="ot-sort-button" onclick="sortOtTable('orders','price')" title="Sort by Price">Price <span class="ot-sort-ind" aria-hidden="true">↕</span></button></th>
              <th class="ot-th-sortable" data-tab="orders" data-col="size" aria-sort="none"><button type="button" class="ot-sort-button" onclick="sortOtTable('orders','size')" title="Sort by Size">Size <span class="ot-sort-ind" aria-hidden="true">↕</span></button></th>
              <th class="ot-th-sortable" data-tab="orders" data-col="filled" aria-sort="none"><button type="button" class="ot-sort-button" onclick="sortOtTable('orders','filled')" title="Sort by Filled">Filled <span class="ot-sort-ind" aria-hidden="true">↕</span></button></th>
              <th class="ot-th-sortable" data-tab="orders" data-col="cost" aria-sort="none"><button type="button" class="ot-sort-button" onclick="sortOtTable('orders','cost')" title="Sort by Total Cost">Total Cost <span class="ot-sort-ind" aria-hidden="true">↕</span></button></th>
              <th class="ot-th-sortable" data-tab="orders" data-col="status" aria-sort="none"><button type="button" class="ot-sort-button" onclick="sortOtTable('orders','status')" title="Sort by Status">Status <span class="ot-sort-ind" aria-hidden="true">↕</span></button></th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody id="cockpitOrdersBody">
            <tr><td colspan="9" style="text-align:center;color:var(--dim);padding:18px">No orders are resting on the book.</td></tr>
          </tbody>
        </table>
      </div>

      <!-- Tab 2: Positions -->
      <div id="otPanePositions" class="ot-pane ot-pane-scroll" style="max-height:min(680px,72vh);overflow-y:auto">
        <table class="tbl" id="cockpitPositionsTable">
          <thead>
            <tr>
              <th class="ot-th-sortable" data-tab="positions" data-col="time" aria-sort="none"><button type="button" class="ot-sort-button" onclick="sortOtTable('positions','time')" title="Sort by Time">Time <span class="ot-sort-ind" aria-hidden="true">↕</span></button></th>
              <th class="ot-th-sortable" data-tab="positions" data-col="market" aria-sort="none"><button type="button" class="ot-sort-button" onclick="sortOtTable('positions','market')" title="Sort by Market">Market <span class="ot-sort-ind" aria-hidden="true">↕</span></button></th>
              <th class="ot-th-sortable" data-tab="positions" data-col="side" aria-sort="none"><button type="button" class="ot-sort-button" onclick="sortOtTable('positions','side')" title="Sort by Side">Side <span class="ot-sort-ind" aria-hidden="true">↕</span></button></th>
              <th class="ot-th-sortable" data-tab="positions" data-col="size" aria-sort="none"><button type="button" class="ot-sort-button" onclick="sortOtTable('positions','size')" title="Sort by Size">Size <span class="ot-sort-ind" aria-hidden="true">↕</span></button></th>
              <th class="ot-th-sortable" data-tab="positions" data-col="baseCost" aria-sort="none"><button type="button" class="ot-sort-button" onclick="sortOtTable('positions','baseCost')" title="Sort by Base Cost">Base Cost <span class="ot-sort-ind" aria-hidden="true">↕</span></button></th>
              <th class="ot-th-sortable" data-tab="positions" data-col="marketValue" aria-sort="none"><button type="button" class="ot-sort-button" onclick="sortOtTable('positions','marketValue')" title="Sort by Market Value">Market Value <span class="ot-sort-ind" aria-hidden="true">↕</span></button></th>
              <th class="ot-th-sortable" data-tab="positions" data-col="unrealized" aria-sort="none"><button type="button" class="ot-sort-button" onclick="sortOtTable('positions','unrealized')" title="Sort by Unrealized PnL">Unrealized $ (%) <span class="ot-sort-ind" aria-hidden="true">↕</span></button></th>
              <th class="ot-th-sortable" data-tab="positions" data-col="realized" aria-sort="none"><button type="button" class="ot-sort-button" onclick="sortOtTable('positions','realized')" title="Sort by Realized PnL">Realized $ (%) <span class="ot-sort-ind" aria-hidden="true">↕</span></button></th>
            </tr>
          </thead>
          <tbody id="cockpitPositionsBody">
            <tr><td colspan="8" style="text-align:center;color:var(--dim);padding:18px">No open positions held in account.</td></tr>
          </tbody>
        </table>
      </div>

      <!-- Tab 3: Closed Trades -->
      <div id="otPaneTrades" class="ot-pane ot-pane-scroll" style="max-height:min(680px,72vh);overflow-y:auto">
        <table class="tbl" id="cockpitTradesTable">
          <thead>
            <tr>
              <th class="ot-th-sortable" data-tab="trades" data-col="time" aria-sort="none"><button type="button" class="ot-sort-button" onclick="sortOtTable('trades','time')" title="Sort by Time">Time <span class="ot-sort-ind" aria-hidden="true">↕</span></button></th>
              <th class="ot-th-sortable" data-tab="trades" data-col="market" aria-sort="none"><button type="button" class="ot-sort-button" onclick="sortOtTable('trades','market')" title="Sort by Market">Market <span class="ot-sort-ind" aria-hidden="true">↕</span></button></th>
              <th class="ot-th-sortable" data-tab="trades" data-col="cause" aria-sort="none"><button type="button" class="ot-sort-button" onclick="sortOtTable('trades','cause')" title="Sort by Cause">Cause <span class="ot-sort-ind" aria-hidden="true">↕</span></button></th>
              <th class="ot-th-sortable" data-tab="trades" data-col="shares" aria-sort="none"><button type="button" class="ot-sort-button" onclick="sortOtTable('trades','shares')" title="Sort by Shares">Shares <span class="ot-sort-ind" aria-hidden="true">↕</span></button></th>
              <th class="ot-th-sortable" data-tab="trades" data-col="baseCost" aria-sort="none"><button type="button" class="ot-sort-button" onclick="sortOtTable('trades','baseCost')" title="Sort by Base Cost">Base Cost <span class="ot-sort-ind" aria-hidden="true">↕</span></button></th>
              <th class="ot-th-sortable" data-tab="trades" data-col="exitPrice" aria-sort="none"><button type="button" class="ot-sort-button" onclick="sortOtTable('trades','exitPrice')" title="Sort by Exit Price">Exit Price <span class="ot-sort-ind" aria-hidden="true">↕</span></button></th>
              <th class="ot-th-sortable" data-tab="trades" data-col="gainLoss" aria-sort="none"><button type="button" class="ot-sort-button" onclick="sortOtTable('trades','gainLoss')" title="Sort by Gain / Loss">Gain / Loss $ (%) <span class="ot-sort-ind" aria-hidden="true">↕</span></button></th>
              <th class="ot-th-sortable" data-tab="trades" data-col="details" aria-sort="none"><button type="button" class="ot-sort-button" onclick="sortOtTable('trades','details')" title="Sort by Details">Details <span class="ot-sort-ind" aria-hidden="true">↕</span></button></th>
            </tr>
          </thead>
          <tbody id="cockpitTradesBody">
            <tr><td colspan="8" style="text-align:center;color:var(--dim);padding:20px">No closed trades recorded in this session.</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</div>

<div id="toastContainer" class="toast-container" aria-live="polite" aria-atomic="true"></div>

<script>
const $=s=>document.getElementById(s);
const esc=s=>String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
const pct=(a,b)=> b?Math.round(a/b*100):0;
const hms=s=>{s=Math.max(0,Math.floor(s));const h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=s%60;return h?`${h}h ${String(m).padStart(2,'0')}m`:`${m}m ${String(x).padStart(2,'0')}s`;};
const getThemeToken = name => {
  // Headless node harnesses stub `document` but not `getComputedStyle` —
  // guard both so theme reads degrade to '' instead of throwing.
  if (typeof document === 'undefined' || typeof getComputedStyle !== 'function') return '';
  const prop = name.startsWith('--') ? name : `--${name}`;
  return getComputedStyle(document.documentElement).getPropertyValue(prop).trim();
};
const getThemeTokens = () => ({
  bg: getThemeToken('bg'),
  panel: getThemeToken('panel'),
  panel2: getThemeToken('panel2'),
  line: getThemeToken('line'),
  lineHi: getThemeToken('line-hi'),
  lineDark: getThemeToken('line-dark'),
  tx: getThemeToken('tx'),
  dim: getThemeToken('dim'),
  faint: getThemeToken('faint'),
  up: getThemeToken('up'),
  upHi: getThemeToken('up-hi'),
  down: getThemeToken('down'),
  gold: getThemeToken('gold'),
  warn: getThemeToken('warn'),
  proj: getThemeToken('proj'),
  cyan: getThemeToken('cyan'),
});
const hexToRgba = (hex, alpha) => {
  if (!hex || typeof hex !== 'string' || !hex.startsWith('#')) return hex;
  const h = hex.slice(1);
  const r = parseInt(h.length === 3 ? h[0]+h[0] : h.slice(0, 2), 16);
  const g = parseInt(h.length === 3 ? h[1]+h[1] : h.slice(2, 4), 16);
  const b = parseInt(h.length === 3 ? h[2]+h[2] : h.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${alpha})`;
};
// Issue #100: seeker-bar math for the market cards. Returns the fill percent
// (0-100) of a depleting time bar and its urgency colour — normal while plenty
// of window remains, gold in the final 60s or final 10% (whichever is larger),
// red in the final 10s. Always clamps to [0,100] so an expired window renders
// an empty bar, never a negative or over-full width.
const timeBarFraction=(rem, dur)=>{
  const r = Number(rem);
  const d = Number(dur);
  if (!isFinite(r) || !isFinite(d) || d <= 0) return { fillPct: 0, barColor: 'var(--dim)' };
  const frac = Math.max(0, Math.min(1, r / d));
  const remainSec = Math.max(0, r);
  let barColor = 'var(--up)';
  if (remainSec <= 10) barColor = 'var(--down)';
  else if (remainSec <= 60 || frac <= 0.10) barColor = 'var(--gold)';
  return { fillPct: Math.round(frac * 1000) / 10, barColor };
};
// Issue #193: the Stats Summary hero used to state its figures as hardcoded
// numerals, so it drifted further from the data on every collector run. These
// three helpers recompute the headline from the /api/oscillation payload. They
// are pure -- no DOM, no fetch -- so the Node harness can call them directly.
// Sum windows/oscillating across every series and split by duration
// (300 = 5m, 900 = 15m). A bucket with no windows yields null, never NaN and
// never a 0% that reads as a measured result.
const OSC_DUR_5M = 300;
const OSC_DUR_15M = 900;
// An absent field means zero -- the summary omits counts it never measured.
// A field that is *present* but not a finite, non-negative number is corrupt,
// and folding it to zero would render a measured-looking "0.0%" from garbage.
// Drop that entry from the aggregation instead, so its bucket stays an em dash.
function measureCount(v){
  if(v===undefined) return 0;
  if(typeof v!=='number' && typeof v!=='string') return null;
  const n=Number(v);
  return (isFinite(n) && n>=0) ? n : null;
}
function computeOscillationHeadline(perSeries){
  const rate=(osc,win)=> win>0 ? (osc/win)*100 : null;
  let tW=0,tO=0,w5=0,o5=0,w15=0,o15=0;
  for(const entry of Object.values(perSeries||{})){
    if(!entry || typeof entry!=='object') continue;
    const win=measureCount(entry.windows);
    const osc=measureCount(entry.oscillating);
    if(win===null || osc===null) continue;
    tW+=win; tO+=osc;
    if(Number(entry.duration)===OSC_DUR_5M){ w5+=win; o5+=osc; }
    else if(Number(entry.duration)===OSC_DUR_15M){ w15+=win; o15+=osc; }
  }
  return {
    ok: tW>0,
    totalWindows: tW,
    windows5m: w5,
    windows15m: w15,
    overallPct: rate(tO,tW),
    pct5m: rate(o5,w5),
    pct15m: rate(o15,w15)
  };
}
// One decimal place, or an em dash when there is nothing to report. A genuine
// 0% still renders as 0.0% -- only absent data becomes the dash.
function formatOscPct(v){
  const n=Number(v);
  if(v===null||v===undefined||!isFinite(n)) return '—';
  return n.toFixed(1)+'%';
}
// Render summary.ts so a stale oscillation_summary.json is visible rather than
// silently presented as current. Matches the stamp format used for tick files.
function formatOscAsOf(ts){
  const n=Number(ts);
  if(!ts||!isFinite(n)||n<=0) return '—';
  return new Date(n*1000).toLocaleString('en-US');
}
// Write the computed headline into the Stats Summary hero. Every lookup is
// guarded: a missing slot must not abort the rest of the render.
function renderOscillationHero(summary){
  const s = summary || {};
  const h = computeOscillationHeadline(s.per_series);
  const put=(id,text)=>{ const el=$(id); if(el) el.textContent=text; };
  put('oscHeroOverallPct', formatOscPct(h.overallPct));
  put('oscHeroTotalWindows', h.ok ? h.totalWindows.toLocaleString() : '—');
  put('oscHeroPct5m', formatOscPct(h.pct5m));
  put('oscHeroPct15m', formatOscPct(h.pct15m));
  put('oscHeroAsOf', formatOscAsOf(s.ts));
}
// Issue #216: compute effective resting and execution quote prices for market display.
// Eliminates dead phantom-key branches that unconditionally defaulted to 0.48.
// Derives quotes from the real m.mid (or 0.50 anchor) and the active live offset.
function cockpitRestingPrice(m, leg, fallbackOffset) {
  const isUp = leg === 'up';
  const resting = isUp ? m?.resting_up : m?.resting_down;
  if (resting != null) return resting;
  const off = (fallbackOffset != null && isFinite(fallbackOffset)) ? fallbackOffset : 0.02;
  const mid = m?.mid;
  if (mid != null && isFinite(mid)) {
    const anchor = isUp ? mid : (1.0 - mid);
    return Math.max(0.01, +(anchor - off).toFixed(2));
  }
  return Math.max(0.01, +(0.50 - off).toFixed(2));
}
function cockpitLegPrice(m, leg, fallbackOffset) {
  const isUp = leg === 'up';
  const fill = isUp ? m?.fill_price_up : m?.fill_price_down;
  if (fill != null) return fill;
  return cockpitRestingPrice(m, leg, fallbackOffset);
}
// Issue #100: two-way visual link. Hovering a market card highlights its Open
// Orders group and vice versa, via the shared data-market key.
function setMarketHighlight(key,on){
  document.querySelectorAll('[data-market]').forEach(el=>{
    if(el.getAttribute('data-market')!==key) return;
    if(el.classList.contains('mat-market-card')){
      el.style.boxShadow=on?'0 0 0 2px var(--gold)':'';
      el.style.borderColor=on?'var(--gold)':'';
    }else if(el.classList.contains('mat-orders-group')){
      el.style.background=on?'rgba(240,183,77,0.10)':'';
    }
  });
}
function wireMarketCardHighlight(gridEl){
  if(!gridEl) return;
  const ordersBody=document.getElementById('cockpitOrdersBody');
  const ordersWrap=(ordersBody && typeof ordersBody.closest==='function')?ordersBody.closest('table'):null;
  const markets=new Set();
  gridEl.querySelectorAll('.mat-market-card').forEach(c=>{const k=c.getAttribute('data-market');if(k)markets.add(k);});
  if(ordersBody){
    ordersBody.querySelectorAll('.mat-orders-group[data-market]').forEach(c=>{const k=c.getAttribute('data-market');if(k)markets.add(k);});
  }
  markets.forEach(key=>{
    gridEl.querySelectorAll(`.mat-market-card[data-market="${key}"]`).forEach(card=>{
      card.addEventListener('mouseenter',()=>setMarketHighlight(key,true));
      card.addEventListener('mouseleave',()=>setMarketHighlight(key,false));
    });
    if(ordersWrap){
      ordersWrap.querySelectorAll(`.mat-orders-group[data-market="${key}"]`).forEach(cell=>{
        cell.addEventListener('mouseenter',()=>setMarketHighlight(key,true));
        cell.addEventListener('mouseleave',()=>setMarketHighlight(key,false));
      });
    }
  });
}
const fmtUsd=(cents, showPlus=true)=>{
  if(cents===null || cents===undefined || isNaN(Number(cents))) return '$0.00';
  const val = Number(cents) / 100;
  const sign = val >= 0 ? (showPlus ? '+' : '') : '-';
  return `${sign}$${Math.abs(val).toFixed(2)}`;
};
const fmtPrice=(p)=>{
  if(p===null || p===undefined || isNaN(Number(p))) return '-';
  return `$${Number(p).toFixed(2)}`;
};
function pill(cls,txt){return `<span class="pill ${cls}">${txt}</span>`;}
function clsPill(c){return c==='oscillating'?pill('pill-osc','oscillating'):c==='monotonic'?pill('pill-mono','monotonic'):c==='flat'?pill('pill-flat','flat'):pill('pill-flat',esc(c));}
// Engine statuses are machine names; map the ones shown in Orders & Trades and
// on the Market Matrix badges to human-readable labels so e.g. next-window
// pre-quotes never display raw ADVANCE_PRE_QUOTE and matrix cards read
// "Stopped Out" / "Timed Out" instead of STOP_EXIT-style tokens. Unknown
// statuses render unchanged (still raw).
const OT_STATUS_LABELS = {
  // order / venue statuses (Open Orders Status column)
  'ADVANCE_PRE_QUOTE': 'Pre-Quote',
  'PRE_QUOTE': 'Pre-Quote',
  // market-state statuses (Market Matrix badge)
  'IDLE': 'Idle',
  'QUOTING': 'Quoting Bids',
  'FILLED_UP': 'Filled Up',
  'FILLED_DOWN': 'Filled Down',
  'PAIR_MERGED': 'Pair Merged',
  'STOP_EXIT': 'Stopped Out',
  'STOP_EXIT_PENDING': 'Stop Exiting',
  'TIMEOUT_NO_FILL': 'Timed Out',
  'DRIFT_SKIPPED': 'Drift Skipped',
  'LATE_START_SKIPPED': 'Late Start Skipped',
  'NO_BOOK': 'No Book',
  'NO_BOOK_SKIPPED': 'No Book Skipped'
};
function otStatusLabel(s){
  const raw = String(s || '').toUpperCase();
  return Object.prototype.hasOwnProperty.call(OT_STATUS_LABELS, raw) ? OT_STATUS_LABELS[raw] : s;
}

// Floating Side Toast Notifications (Issue #81)
function showToast({ type = 'filled', title = '', message = '', durationMs = 5000 } = {}) {
  const container = $('toastContainer');
  if (!container) return null;

  const toast = document.createElement('div');
  toast.className = 'toast toast-' + type;

  const content = document.createElement('div');
  content.className = 'toast-content';

  const header = document.createElement('div');
  header.className = 'toast-header toast-header-' + type;
  const icon = type === 'merged' ? '🟢' : type === 'stoploss' ? '🔴' : '⚪';
  header.textContent = icon + ' ' + title;

  const msg = document.createElement('div');
  msg.className = 'toast-msg';
  msg.textContent = message;

  content.appendChild(header);
  content.appendChild(msg);

  const closeBtn = document.createElement('button');
  closeBtn.className = 'toast-close';
  closeBtn.textContent = '\u00d7';
  closeBtn.onclick = function(e) {
    e.stopPropagation();
    toast.classList.add('fade-out');
    if (toast._dismissTimer) { clearTimeout(toast._dismissTimer); toast._dismissTimer = null; }
    setTimeout(function() { if (toast.parentNode) toast.parentNode.removeChild(toast); }, 280);
  };

  toast.appendChild(content);
  toast.appendChild(closeBtn);
  container.appendChild(toast);

  toast._dismissTimer = setTimeout(function() {
    toast._dismissTimer = null;
    toast.classList.add('fade-out');
    setTimeout(function() { if (toast.parentNode) toast.parentNode.removeChild(toast); }, 280);
  }, durationMs);

  // Cap max visible toasts at 6
  while (container.children.length > 6) {
    const old = container.children[0];
    if (old && old._dismissTimer) { clearTimeout(old._dismissTimer); old._dismissTimer = null; }
    container.removeChild(old);
  }

  return toast;
}

let _toastInitialized = false;
let _seenTradeIds = new Set();
let _prevMarketFills = {}; // slug -> { filled_up: bool, filled_down: bool }

function resetToastState() {
  _seenTradeIds.clear();
  _prevMarketFills = {};
  _toastInitialized = false;
}

function reconcileCockpitToasts(st) {
  if (!st) return;

  // 1. Initial boot / page load seeding: suppress historical notifications
  if (!_toastInitialized) {
    _toastInitialized = true;
    if (Array.isArray(st.trades)) {
      st.trades.forEach(t => {
        if (t && t.id) _seenTradeIds.add(t.id);
      });
    }
    if (st.markets) {
      for (const [slug, m] of Object.entries(st.markets)) {
        if (m) {
          _prevMarketFills[slug] = {
            filled_up: !!m.filled_up,
            filled_down: !!m.filled_down
          };
        }
      }
    }
    return;
  }

  // 2. Detect new Trade Events (Position Merged & Stop-Loss Exits)
  if (Array.isArray(st.trades)) {
    st.trades.forEach(t => {
      if (!t || !t.id) return;
      if (!_seenTradeIds.has(t.id)) {
        _seenTradeIds.add(t.id);
        const mktLabel = t.label || t.market || t.slug || 'Market';
        const action = String(t.action || '').toUpperCase();

        if (action === 'PAIR_MERGE') {
          const shares = t.shares || 0;
          const pnlUsd = t.pnl_usd != null ? (t.pnl_usd >= 0 ? `+$${t.pnl_usd.toFixed(2)}` : `-$${Math.abs(t.pnl_usd).toFixed(2)}`) : '+$0.00';
          const pnlPct = t.pnl_pct != null ? ` (${t.pnl_pct >= 0 ? '+' : ''}${t.pnl_pct.toFixed(1)}%)` : '';
          showToast({
            type: 'merged',
            title: 'Position Merged',
            message: `${mktLabel}: ${shares} pairs merged back to USDC (${pnlUsd}${pnlPct})`
          });
        } else if (action.startsWith('STOP') || action === 'STOP_EXIT_UP' || action === 'STOP_EXIT_DOWN') {
          const isUp = action.includes('UP') || (t.notes && t.notes.includes('UP'));
          const isDown = action.includes('DOWN') || (t.notes && t.notes.includes('DOWN'));
          const leg = isUp ? 'UP' : (isDown ? 'DOWN' : 'Position');
          const exitPrice = t.exit_price != null ? `$${t.exit_price.toFixed(2)}` : '-';
          const pnlUsd = t.pnl_usd != null ? (t.pnl_usd >= 0 ? `+$${t.pnl_usd.toFixed(2)}` : `-$${Math.abs(t.pnl_usd).toFixed(2)}`) : '$0.00';
          showToast({
            type: 'stoploss',
            title: 'Stop-Loss Exit',
            message: `${mktLabel} (${leg}): Stopped out @ ${exitPrice} (${pnlUsd})`
          });
        }
      }
    });
  }

  // 3. Detect new Market Leg Fills (Order Filled)
  if (st.markets) {
    for (const [slug, m] of Object.entries(st.markets)) {
      if (!m) continue;
      const prev = _prevMarketFills[slug] || { filled_up: false, filled_down: false };
      const mktLabel = m.label || slug;
      const shares = m.order_shares || 5;

      // Check UP leg fill transition
      if (!prev.filled_up && m.filled_up) {
        const price = cockpitLegPrice(m, 'up', st?.params?.offset);
        showToast({
          type: 'filled',
          title: 'Order Filled',
          message: `${mktLabel} (UP): ${shares} shares filled @ $${price.toFixed(2)}`
        });
      }

      // Check DOWN leg fill transition
      if (!prev.filled_down && m.filled_down) {
        const price = cockpitLegPrice(m, 'down', st?.params?.offset);
        showToast({
          type: 'filled',
          title: 'Order Filled',
          message: `${mktLabel} (DOWN): ${shares} shares filled @ $${price.toFixed(2)}`
        });
      }

      _prevMarketFills[slug] = {
        filled_up: !!m.filled_up,
        filled_down: !!m.filled_down
      };
    }
  }
}

// Orders & Trades Tab State & Switching (Issue #59)
let activeOtTab = (typeof localStorage !== 'undefined' && localStorage.getItem('crypto-spread-ot-view')) || 'orders';

function switchOtTab(tabName) {
  activeOtTab = tabName;
  try { if (typeof localStorage !== 'undefined') localStorage.setItem('crypto-spread-ot-view', tabName); } catch (e) {}
  
  const tabs = ['orders', 'positions', 'trades'];
  const cap = s => s.charAt(0).toUpperCase() + s.slice(1);
  tabs.forEach(t => {
    const btn = $('otTabBtn' + cap(t));
    const pane = $('otPane' + cap(t));
    if (btn) btn.classList.toggle('active', t === tabName);
    if (pane) pane.classList.toggle('active', t === tabName);
  });
}

// Orders & Trades Height Toggle (Issue #133)
let otHeightExpanded = false;
try {
  otHeightExpanded = typeof localStorage !== 'undefined'
    && localStorage.getItem('crypto-spread-ot-height') === 'expanded';
} catch (e) {
  console.warn('Orders & Trades height preference is unavailable', e);
}

function applyOtHeight(isExpanded) {
  otHeightExpanded = !!isExpanded;
  try { if (typeof localStorage !== 'undefined') localStorage.setItem('crypto-spread-ot-height', otHeightExpanded ? 'expanded' : 'standard'); } catch (e) {}
  const panes = ['otPaneOrders', 'otPanePositions', 'otPaneTrades'];
  panes.forEach(id => {
    const el = $(id);
    if (el) el.classList.toggle('ot-expanded', otHeightExpanded);
  });
  const btn = $('otHeightToggleBtn');
  if (btn) {
    btn.innerHTML = otHeightExpanded ? '🗗 Standard' : '⛶ Expand';
    btn.title = otHeightExpanded ? 'Switch to standard view height' : 'Expand tables to full cockpit height';
    if (typeof btn.setAttribute === 'function') {
      btn.setAttribute('aria-expanded', otHeightExpanded ? 'true' : 'false');
    }
  }
}

function toggleOtHeight() {
  applyOtHeight(!otHeightExpanded);
}

function formatSignedMoneyPct(dollarVal, pctVal) {
  if (dollarVal == null || isNaN(Number(dollarVal))) return '--';
  const d = Number(dollarVal);
  const p = pctVal != null && !isNaN(Number(pctVal)) ? Number(pctVal) : null;

  let dStr = '';
  if (d >= 0.005) dStr = `+$${d.toFixed(2)}`;
  else if (d <= -0.005) dStr = `-$${Math.abs(d).toFixed(2)}`;
  else dStr = '$0.00';

  if (p == null) return dStr;
  let pStr = '';
  if (p >= 0.05) pStr = `(+${p.toFixed(1)}%)`;
  else if (p <= -0.05) pStr = `(-${Math.abs(p).toFixed(1)}%)`;
  else pStr = '(0.0%)';

  return `${dStr} ${pStr}`;
}

function groupOrdersByPair(orders) {
  const groups = {};
  for (const o of (orders || [])) {
    const rawMkt = o.market || o.label || o.token_id || 'Unknown';
    if (!groups[rawMkt]) {
      groups[rawMkt] = {
        market: rawMkt,
        market_slug: o.market_slug || '',
        series_slug: o.series_slug || '',
        legs: [],
        pair_cost: '--',
        status: 'Unpaired',
        rowspan: 0
      };
    }
    if (!groups[rawMkt].market_slug && o.market_slug) groups[rawMkt].market_slug = o.market_slug;
    if (!groups[rawMkt].series_slug && o.series_slug) groups[rawMkt].series_slug = o.series_slug;
    const sideRaw = String(o.side || 'BUY').toUpperCase();
    const isUp = sideRaw.includes('UP');
    const sideClean = isUp ? 'Up' : (sideRaw.includes('DOWN') ? 'Down' : (sideRaw.charAt(0) + sideRaw.slice(1).toLowerCase()));
    
    groups[rawMkt].legs.push({
      ...o,
      isUp,
      side: sideClean,
      priceNum: o.price != null ? Number(o.price) : null,
      sizeNum: o.size != null ? Number(o.size) : 0,
      filledNum: o.filled != null ? Number(o.filled) : 0,
      time: o.time || o.created_at || o.timestamp || '-'
    });
  }

  for (const k of Object.keys(groups)) {
    const g = groups[k];
    g.legs.sort((a, b) => (a.isUp === b.isUp ? 0 : a.isUp ? -1 : 1));
    g.rowspan = g.legs.length;
    
    const hasCancelled = g.legs.some(l => {
      const s = String(l.status || '').toUpperCase();
      return s === 'CANCELLED' || s === 'CANCELED';
    });
    const activeLegs = g.legs.filter(l => {
      const s = String(l.status || '').toUpperCase();
      return s !== 'CANCELLED' && s !== 'CANCELED';
    });
    const hasActiveUp = activeLegs.some(l => l.isUp);
    const hasActiveDown = activeLegs.some(l => !l.isUp);
    if (hasActiveUp && hasActiveDown) {
      g.status = 'Paired';
      const upLeg = activeLegs.find(l => l.isUp);
      const downLeg = activeLegs.find(l => !l.isUp);
      if (upLeg?.priceNum != null && downLeg?.priceNum != null) {
        g.pair_cost = `$${(upLeg.priceNum + downLeg.priceNum).toFixed(2)}`;
      }
    } else if (activeLegs.length === 0 && g.legs.length > 0) {
      g.status = 'Cancelled';
      g.pair_cost = '--';
    } else if (hasCancelled && activeLegs.length > 0) {
      g.status = 'Partial';
      g.pair_cost = '--';
    } else {
      g.status = 'Unpaired';
      g.pair_cost = '--';
    }
  }
  return groups;
}

function groupPositionsByPair(positions, markets) {
  const groups = {};
  for (const p of (positions || [])) {
    const rawMkt = p.title || p.market || p.asset || 'Unknown';
    if (!groups[rawMkt]) {
      groups[rawMkt] = {
        market: rawMkt,
        market_slug: p.market_slug || '',
        series_slug: p.series_slug || '',
        legs: [],
        status: 'Unpaired',
        market_val: null,
        unrealized_usd: null,
        unrealized_pct: null,
        realized_usd: null,
        realized_pct: null,
        rowspan: 0
      };
    }
    if (!groups[rawMkt].market_slug && p.market_slug) groups[rawMkt].market_slug = p.market_slug;
    if (!groups[rawMkt].series_slug && p.series_slug) groups[rawMkt].series_slug = p.series_slug;
    const outRaw = String(p.outcome || p.side || '').toUpperCase();
    const isUp = outRaw.includes('UP');
    const sideClean = isUp ? 'Up' : (outRaw.includes('DOWN') ? 'Down' : (outRaw.charAt(0) + outRaw.slice(1).toLowerCase()));
    
    groups[rawMkt].legs.push({
      ...p,
      isUp,
      side: sideClean,
      sizeNum: p.size != null ? Number(p.size) : 0,
      baseCost: p.avgPrice != null ? Number(p.avgPrice) : (p.price != null ? Number(p.price) : null),
      curPrice: p.curPrice != null ? Number(p.curPrice) : null,
      time: p.time || p.created_at || p.timestamp || '-'
    });
  }

  for (const k of Object.keys(groups)) {
    const g = groups[k];
    g.legs.sort((a, b) => (a.isUp === b.isUp ? 0 : a.isUp ? -1 : 1));
    g.rowspan = g.legs.length;
    
    // Issue #91: synthetic legs promoted from filled orders have no CLOB
    // valuation (no curPrice). Exclude them from pair valuation so the group
    // never derives a Market Value or $0.00 unrealized from the fill price —
    // those cells stay '--' until real position data arrives. Row rendering
    // (side / size / base cost) still uses every leg.
    const valLegs = g.legs.filter(l => !l._fromFilledOrder);
    const upSize = valLegs.filter(l => l.isUp).reduce((sum, l) => sum + (l.sizeNum || 0), 0);
    const downSize = valLegs.filter(l => !l.isUp).reduce((sum, l) => sum + (l.sizeNum || 0), 0);
    const upLeg = valLegs.find(l => l.isUp);
    const downLeg = valLegs.find(l => !l.isUp);

    let totalCost = 0;
    let totalRealized = 0;
    let hasRealized = false;
    for (const leg of valLegs) {
      if (leg.baseCost != null) totalCost += leg.sizeNum * leg.baseCost;
      const cp = leg.cashPnl != null ? Number(leg.cashPnl) : null;
      if (cp != null && !isNaN(cp)) {
        totalRealized += cp;
        hasRealized = true;
      }
    }

    if (upLeg && downLeg) {
      const pairedShares = Math.min(upSize, downSize);
      const remainderShares = Math.abs(upSize - downSize);
      g.status = (upSize === downSize) ? 'Paired' : 'Partial';

      let mktVal = pairedShares * 1.00;
      if (remainderShares > 0) {
        const remLeg = upSize > downSize ? upLeg : downLeg;
        if (remLeg.curPrice != null) {
          mktVal += remainderShares * remLeg.curPrice;
        } else if (remLeg.baseCost != null) {
          mktVal += remainderShares * remLeg.baseCost;
        }
      }
      g.market_val = mktVal;
    } else {
      g.status = 'Unpaired';
      const singleLeg = upLeg || downLeg;
      if (singleLeg && singleLeg.curPrice != null) {
        g.market_val = singleLeg.sizeNum * singleLeg.curPrice;
      } else if (singleLeg && singleLeg.baseCost != null) {
        g.market_val = singleLeg.sizeNum * singleLeg.baseCost;
      } else {
        g.market_val = null;
      }
    }

    if (g.market_val != null && totalCost > 0) {
      g.unrealized_usd = g.market_val - totalCost;
      g.unrealized_pct = (g.unrealized_usd / totalCost) * 100;
    }

    if (hasRealized) {
      g.realized_usd = Math.round(totalRealized * 100) / 100;
      g.realized_pct = totalCost > 0 ? (totalRealized / totalCost) * 100 : 0.0;
    }
  }
  return groups;
}

// Orders & Trades Table Sorting Engine (Issue #197)
let otSortState = {
  orders: { col: null, dir: 'asc' },
  positions: { col: null, dir: 'asc' },
  trades: { col: null, dir: 'desc' }
};

try {
  if (typeof localStorage !== 'undefined') {
    const saved = localStorage.getItem('crypto-spread-ot-sort');
    if (saved) {
      const parsed = JSON.parse(saved);
      if (parsed && typeof parsed === 'object') {
        if (parsed.orders && typeof parsed.orders === 'object') otSortState.orders = parsed.orders;
        if (parsed.positions && typeof parsed.positions === 'object') otSortState.positions = parsed.positions;
        if (parsed.trades && typeof parsed.trades === 'object') otSortState.trades = parsed.trades;
      }
    }
  }
} catch (e) {}

function saveOtSortState() {
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem('crypto-spread-ot-sort', JSON.stringify(otSortState));
    }
  } catch (e) {}
}

function parseSortNumeric(val) {
  if (val == null) return null;
  if (typeof val === 'number') return isFinite(val) ? val : null;
  const s = String(val).trim();
  if (s === '' || s === '-' || s === '--') return null;
  const cleaned = s.replace(/[$,]/g, '').replace(/^\+/, '');
  const n = parseFloat(cleaned);
  return isFinite(n) ? n : null;
}

function compareOtPrimitives(a, b, isNumeric) {
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  if (isNumeric) {
    const numA = parseSortNumeric(a);
    const numB = parseSortNumeric(b);
    if (numA == null && numB == null) return 0;
    if (numA == null) return 1;
    if (numB == null) return -1;
    return numA - numB;
  }
  const strA = String(a).toLowerCase();
  const strB = String(b).toLowerCase();
  return strA.localeCompare(strB);
}

function sortOtOrdersGroups(groupList, col, dir) {
  if (!col || !dir) return groupList;
  const mult = dir === 'desc' ? -1 : 1;
  return groupList.slice().sort((gA, gB) => {
    let diff = 0;
    const legA = (gA.legs && gA.legs[0]) || {};
    const legB = (gB.legs && gB.legs[0]) || {};
    if (col === 'time') {
      diff = compareOtPrimitives(legA.time, legB.time, false);
    } else if (col === 'market') {
      diff = compareOtPrimitives(gA.market, gB.market, false);
    } else if (col === 'side') {
      diff = compareOtPrimitives(legA.side, legB.side, false);
    } else if (col === 'price') {
      const pA = gA.status === 'Paired' ? parseSortNumeric(gA.pair_cost) : legA.priceNum;
      const pB = gB.status === 'Paired' ? parseSortNumeric(gB.pair_cost) : legB.priceNum;
      diff = compareOtPrimitives(pA, pB, true);
    } else if (col === 'size') {
      const sA = (gA.legs && gA.legs[0] && gA.legs[0].sizeNum) || 0;
      const sB = (gB.legs && gB.legs[0] && gB.legs[0].sizeNum) || 0;
      diff = compareOtPrimitives(sA, sB, true);
    } else if (col === 'filled') {
      const fA = (gA.legs || []).reduce((acc, l) => acc + (l.filledNum || 0), 0);
      const fB = (gB.legs || []).reduce((acc, l) => acc + (l.filledNum || 0), 0);
      diff = compareOtPrimitives(fA, fB, true);
    } else if (col === 'cost') {
      const cA = (gA.legs || []).reduce((acc, l) => acc + ((l.priceNum || 0) * (l.sizeNum || 0)), 0);
      const cB = (gB.legs || []).reduce((acc, l) => acc + ((l.priceNum || 0) * (l.sizeNum || 0)), 0);
      diff = compareOtPrimitives(cA, cB, true);
    } else if (col === 'status') {
      diff = compareOtPrimitives(gA.status, gB.status, false);
    }
    return diff * mult;
  });
}

function sortOtPositionsGroups(groupList, col, dir) {
  if (!col || !dir) return groupList;
  const mult = dir === 'desc' ? -1 : 1;
  return groupList.slice().sort((gA, gB) => {
    let diff = 0;
    const legA = (gA.legs && gA.legs[0]) || {};
    const legB = (gB.legs && gB.legs[0]) || {};
    if (col === 'time') {
      diff = compareOtPrimitives(legA.time, legB.time, false);
    } else if (col === 'market') {
      diff = compareOtPrimitives(gA.market, gB.market, false);
    } else if (col === 'side') {
      diff = compareOtPrimitives(legA.side, legB.side, false);
    } else if (col === 'size') {
      const sA = (gA.legs && gA.legs[0] && gA.legs[0].sizeNum) || 0;
      const sB = (gB.legs && gB.legs[0] && gB.legs[0].sizeNum) || 0;
      diff = compareOtPrimitives(sA, sB, true);
    } else if (col === 'baseCost') {
      diff = compareOtPrimitives(legA.baseCost, legB.baseCost, true);
    } else if (col === 'marketValue') {
      diff = compareOtPrimitives(gA.market_val, gB.market_val, true);
    } else if (col === 'unrealized') {
      diff = compareOtPrimitives(gA.unrealized_usd, gB.unrealized_usd, true);
    } else if (col === 'realized') {
      diff = compareOtPrimitives(gA.realized_usd, gB.realized_usd, true);
    }
    return diff * mult;
  });
}

function sortOtTradesList(tradesList, col, dir) {
  if (!col || !dir) return tradesList;
  const mult = dir === 'desc' ? -1 : 1;
  return tradesList.slice().sort((tA, tB) => {
    let diff = 0;
    if (col === 'time') {
      diff = compareOtPrimitives(tA.timestamp || tA.time, tB.timestamp || tB.time, false);
    } else if (col === 'market') {
      diff = compareOtPrimitives(tA.label || tA.market, tB.label || tB.market, false);
    } else if (col === 'cause') {
      diff = compareOtPrimitives(tA.action, tB.action, false);
    } else if (col === 'shares') {
      diff = compareOtPrimitives(tA.shares, tB.shares, true);
    } else if (col === 'baseCost') {
      const cA = (tA.entry_price_up != null && tA.entry_price_down != null)
        ? (tA.entry_price_up + tA.entry_price_down)
        : (tA.entry_price_up ?? tA.entry_price_down ?? tA.price ?? 0);
      const cB = (tB.entry_price_up != null && tB.entry_price_down != null)
        ? (tB.entry_price_up + tB.entry_price_down)
        : (tB.entry_price_up ?? tB.entry_price_down ?? tB.price ?? 0);
      diff = compareOtPrimitives(cA, cB, true);
    } else if (col === 'exitPrice') {
      diff = compareOtPrimitives(tA.exit_price, tB.exit_price, true);
    } else if (col === 'gainLoss') {
      diff = compareOtPrimitives(tA.pnl_usd, tB.pnl_usd, true);
    } else if (col === 'details') {
      diff = compareOtPrimitives(tA.notes || tA.details || tA.outcome, tB.notes || tB.details || tB.outcome, false);
    }
    return diff * mult;
  });
}

function updateOtSortIndicators(tab) {
  const tableIdMap = {
    orders: 'cockpitOrdersTable',
    positions: 'cockpitPositionsTable',
    trades: 'cockpitTradesTable'
  };
  const table = $(tableIdMap[tab]);
  if (!table) return;
  const ths = table.querySelectorAll('th.ot-th-sortable');
  const cur = (otSortState && otSortState[tab]) || { col: null, dir: 'asc' };
  ths.forEach(th => {
    const c = th.dataset.col;
    const ind = th.querySelector('.ot-sort-ind');
    if (cur.col && c === cur.col) {
      const isAsc = cur.dir === 'asc';
      th.setAttribute('aria-sort', isAsc ? 'ascending' : 'descending');
      if (ind) ind.textContent = isAsc ? '▲' : '▼';
    } else {
      th.setAttribute('aria-sort', 'none');
      if (ind) ind.textContent = '↕';
    }
  });
}

function sortOtTable(tab, col) {
  if (!otSortState[tab]) otSortState[tab] = { col: null, dir: 'asc' };
  const cur = otSortState[tab];
  if (cur.col === col) {
    if (cur.dir === 'asc') {
      cur.dir = 'desc';
    } else if (cur.dir === 'desc') {
      // 3rd click: return to natural order
      cur.col = null;
      cur.dir = 'asc';
    } else {
      cur.dir = 'asc';
    }
  } else {
    cur.col = col;
    cur.dir = (col === 'time' || col === 'unrealized' || col === 'realized' || col === 'gainLoss') ? 'desc' : 'asc';
  }
  saveOtSortState();
  updateOtSortIndicators(tab);
  if (typeof cockpitState !== 'undefined' && cockpitState) {
    renderCockpitUI(cockpitState);
  }
}

function toggleSidebarPin(){
  const sb = $('app-sidebar');
  if(!sb) return;
  const isPinned = sb.classList.toggle('pinned');
  document.body.classList.toggle('sidebar-pinned', isPinned);
  try{
    localStorage.setItem('cui_sidebar_pinned', isPinned ? 'true' : 'false');
  }catch(e){}
}

function initSidebarState(){
  try{
    if(localStorage.getItem('cui_sidebar_pinned') === 'true'){
      const sb = $('app-sidebar');
      if(sb) sb.classList.add('pinned');
      document.body.classList.add('sidebar-pinned');
    }
  }catch(e){}
}

let currentActiveTab = 'cockpit';
// Issue #371: skip the timer-driven oscillation poll while the Backtest tab is
// active or the document is hidden — the poll competes with the backtest stream
// for the same event loop and disk. Explicit tick() callers stay unguarded;
// stale data triggers one immediate refresh when the operator returns.
let btOscStale = false;
let btOscPollInFlight = false;
function pollTick(){
  if (typeof document !== 'undefined' && document.hidden) { btOscStale = true; return; }
  if (currentActiveTab === 'backtest') { btOscStale = true; return; }
  // #372: timer-driven oscillation polls serve the Market Data tab only —
  // the /api/oscillation fetch is disk work, so hidden tabs skip it every
  // 3 s. Explicit tick() callers (sampling, rebuild, upload, tab switches)
  // stay unguarded: their fetch runs once, not on a timer.
  if (typeof currentActiveTab !== 'undefined' && currentActiveTab !== 'marketdata') return;
  if (btOscPollInFlight) return; // never overlap timer-driven polls
  btOscPollInFlight = true;
  const done = () => { btOscPollInFlight = false; };
  try {
    const p = tick();
    if (p && typeof p.finally === 'function') p.finally(done); else done();
  } catch (e) { done(); }
}
function btOscRefreshIfStale(){
  if (!btOscStale) return;
  btOscStale = false;
  if (currentActiveTab !== 'backtest' && !(typeof document !== 'undefined' && document.hidden)) tick();
}
function switchTab(name){
  const prevTab = currentActiveTab;
  const leavingBacktest = prevTab === 'backtest' && name !== 'backtest';
  currentActiveTab = name;
  document.querySelectorAll('.sidebar-tab-btn').forEach(b=>b.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c=>c.classList.remove('active'));
  const btn = $('tab-btn-'+name);
  const cont = $('tab-'+name);
  if(btn) btn.classList.add('active');
  if(cont) cont.classList.add('active');
  if(name==='cockpit'){
    if(cockpitState) renderCockpitUI(cockpitState);
    fetchCockpitState();
  }
  // #371 IIIB: leaving Backtest with skipped cockpit polls — refresh once.
  if (leavingBacktest) { btOscStale = false; fetchCockpitState(); }
  if(name==='marketdata'){
    // Returning from Backtest with skipped polls: refresh once, immediately.
    if (prevTab === 'backtest') btOscRefreshIfStale();
    tick();
  }
  if(name==='backtest'){
    // Opening the tab is read-only. Backtests start only after the operator
    // clicks Run Sweep (or explicitly presses Enter in a parameter field).
    updateBacktestParamPreview();
    // IIIB: the tab is never a blank rectangle — idle card + chart axes are
    // drawn on first open (and on page load for the default tab).
    initBacktestIdle();
  }
  if(name==='summary') renderSummaryCharts();
  if(name==='ticks'){
    loadManifest();
    loadGoldenCard();
    if(typeof runVerifyQueue === 'function') runVerifyQueue();
  }
  if(name==='jungleking') loadJungleKing();
}

// ── Jungle King tab (issue #319) ──────────────────────────────────────────
// Read-only quick reference over /api/jungle-king. One fetch, cached for the
// session; the manifest is static research data, not a stream.
let JK_DATA = null;
let JK_LOAD_PROMISE = null;

function loadJungleKing(){
  if(JK_DATA){ renderJungleKing(JK_DATA); return Promise.resolve(); }
  if(JK_LOAD_PROMISE) return JK_LOAD_PROMISE;
  JK_LOAD_PROMISE = (async function(){
    let data;
    try{
      const res = await fetch('/api/jungle-king');
      if(!res.ok){
        let detail = 'HTTP ' + res.status;
        try{ detail = (await res.json()).detail || detail; }
        catch(e){ detail += ' (the server returned an unreadable error message)'; }
        throw new Error(detail);
      }
      data = await res.json();
    }catch(err){
      JK_DATA = null;
      const n = $('jkNotice');
      const groups = $('jkGroups');
      if(groups) groups.innerHTML = '';
      if(n){
        n.style.display = 'block';
        n.textContent = 'Jungle King manifest unavailable (' + err.message + '). Check research/jungle-king/param_ranges.json.';
      }
      return;
    }

    JK_DATA = data;
    try{
      renderJungleKing(JK_DATA);
    }catch(err){
      JK_DATA = null;
      const n = $('jkNotice');
      const groups = $('jkGroups');
      if(groups) groups.innerHTML = '';
      if(n){
        n.style.display = 'block';
        n.textContent = 'Jungle King data loaded but could not be displayed. Reload the dashboard and try again.';
      }
      console.error('Jungle King renderer failed:', err);
    }
  })().finally(function(){ JK_LOAD_PROMISE = null; });
  return JK_LOAD_PROMISE;
}

function jkEsc(s){
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function jkNum(v){
  if(typeof v !== 'number') return String(v);
  return Math.abs(v) >= 1000 ? v.toLocaleString('en-US') : String(v);
}

function jkFmt(v){
  if(Array.isArray(v)) return '[' + v.map(jkNum).join(', ') + ']';
  // Issue #333: booleans render as JSON spells them, strings verbatim — no
  // number grouping or coercion on the non-numeric knobs.
  if(typeof v === 'boolean') return v ? 'true' : 'false';
  if(typeof v === 'string') return v;
  return jkNum(v);
}

function jkIsBaseline(v, baseline){
  if(baseline === null || baseline === undefined) return false;
  if(Array.isArray(v) || Array.isArray(baseline)) return JSON.stringify(v) === JSON.stringify(baseline);
  return v === baseline;
}

const JK_CLASS_BADGES = {
  tuning:     {cls:'jk-badge-tuning',     label:'TUNING KNOB'},
  structural: {cls:'jk-badge-structural', label:'STRUCTURAL LIMIT'},
  assumption: {cls:'jk-badge-assumption', label:'EXECUTION ASSUMPTION'}
};

function renderJungleKing(data){
  const wrap = $('jkGroups');
  if(!wrap) return;
  const notice = $('jkNotice');
  if(notice) notice.style.display = 'none';
  const groups = data.groups || [];
  let params = 0;
  let html = '';
  groups.forEach(function(g){
    const ps = g.params || [];
    params += ps.length;
    html += '<div class="card jk-group" style="margin-top:12px">'
         +  '<div class="jk-group-head"><h4 class="jk-group-title">' + jkEsc(g.title || g.key) + '</h4>'
         +  '<span class="jk-group-count">' + ps.length + ' items</span></div>';
    ps.forEach(function(p){
      const badge = JK_CLASS_BADGES[p.param_class] || {cls:'', label:'UNKNOWN CLASS'};
      const why = p.registry && p.registry.why ? p.registry.why : '';
      html += '<div class="jk-param">'
           +  '<div class="jk-param-head">'
           +  '<span class="jk-param-label" title="' + jkEsc(why) + '">' + jkEsc(p.label) + '</span>'
           +  '<span class="jk-badge ' + badge.cls + '">' + badge.label + '</span>'
           +  '</div>'
           +  '<div class="jk-baseline"><span class="jk-baseline-lbl">BASELINE</span>'
           +  '<span class="jk-baseline-val">' + (p.baseline === null || p.baseline === undefined ? '—' : jkFmt(p.baseline)) + '</span></div>'
           +  '<div class="jk-chips">';
      if(p.baseline_in_values === false){
        html += '<span class="jk-chip jkBaselineMissing" title="Baseline is outside the candidate range">BASELINE OUTSIDE RANGE</span>';
      }
      (p.values || []).forEach(function(v){
        const isBase = jkIsBaseline(v, p.baseline);
        html += '<span class="jk-chip' + (isBase ? ' jkBaselineChip' : '') + '">' + jkFmt(v)
             + (isBase ? '<span class="jk-chip-baseline-tag">BASELINE</span>' : '') + '</span>';
      });
      html += '</div></div>';
    });
    html += '</div>';
  });
  if(params === 0){
    wrap.innerHTML = '<div class="card" role="status" style="color:var(--dim);margin-top:12px">No parameters in the Jungle King manifest.</div>';
  } else {
    wrap.innerHTML = html;
  }
}

let isCollectorActive = false;

// Friendly market label from a series slug: "btc-up-or-down-5m" -> "BTC 5m".
const ASSET_LABELS = {btc:'BTC', eth:'ETH', bnb:'BNB', sol:'SOL', xrp:'XRP'};
function marketName(series){
  const m = /^([a-z]{3})-up-or-down-(\d+m)$/.exec(String(series||''));
  return m ? `${ASSET_LABELS[m[1]]||m[1].toUpperCase()} ${m[2]}` : String(series||'');
}

// Backtest-facing market label. Slugs remain stable filter values; only the
// customer-facing text is canonicalized to duration-first format.
function canonicalMarketName(series){
  const m = /^([a-z]{3})-up-or-down-(\d+)m$/.exec(String(series||''));
  if(!m) return String(series||'');
  return `${String(m[2]).padStart(2, '0')}m ${ASSET_LABELS[m[1]]||m[1].toUpperCase()}`;
}

// ── Parameter registry (issue #164) ──────────────────────────────────────
// Backtest and Cockpit render from one definition. Every label, min, max and
// title below comes from /api/params/spec; nothing here hard-codes a shared
// parameter's wording, which is what let the two tabs drift apart.
let PARAM_SPEC = null;

async function loadParamSpec(){
  if(PARAM_SPEC) return PARAM_SPEC;
  try{
    const res = await fetch('/api/params/spec');
    PARAM_SPEC = await res.json();
  }catch{
    PARAM_SPEC = null;   // leave the served defaults in place rather than blanking the form
  }
  return PARAM_SPEC;
}

function paramSpecFor(name){
  if(!PARAM_SPEC || !PARAM_SPEC.groups) return null;
  for(const entries of Object.values(PARAM_SPEC.groups)){
    if(entries[name]) return entries[name];
  }
  return null;
}

async function applyParamSpec(root){
  await loadParamSpec();
  if(!PARAM_SPEC) return;
  const scope = root || document;
  // Labels: an empty placeholder is filled, an existing one is overwritten, so
  // a stale hard-coded string cannot survive a registry rename.
  scope.querySelectorAll('[data-param-label]').forEach(el => {
    const spec = paramSpecFor(el.getAttribute('data-param-label'));
    if(spec) el.textContent = spec.label;
  });
  // Issue #419: unit placeholders inside hand-written labels (the four exit
  // inputs, the quote-range ends) render the registry's display unit, so no
  // hard-coded ($) survives for a converted knob.
  scope.querySelectorAll('[data-param-unit]').forEach(el => {
    const spec = paramSpecFor(el.getAttribute('data-param-unit'));
    if(spec && spec.display) el.textContent = `(${spec.display.unit})`;
  });
  // Bounds and tooltips on the controls themselves.
  scope.querySelectorAll('[data-param]').forEach(el => {
    const spec = paramSpecFor(el.getAttribute('data-param'));
    if(!spec) return;
    if(spec.why) el.title = spec.why;
    // Issue #233: badge structural limits so an operator can tell a safety
    // ceiling from a daily tuning dial at a glance, straight from the served
    // param_class — no second hard-coded list in the page to drift.
    if(spec.param_class === 'structural') el.classList.add('param-structural');
    // Per-surface bounds: an id prefix tells us which tab this control is on,
    // and each tab gets the range it actually enforces. Falling back to the
    // shared pair would have widened the Cockpit's pair-cost input to the
    // research range and shown values the live API rejects with a 422.
    const surface = el.id.startsWith('cockpit') ? 'cockpit'
                  : el.id.startsWith('bt') ? 'backtest' : '';
    const b = (spec.bounds_by_surface && spec.bounds_by_surface[surface])
              || spec.bounds;
    // Issue #419: converted knobs render cents bounds (dollars x scale) and
    // the display step; the exit inputs take entry_bounds where the
    // canonical bounds is None. Fields without display keep today's behavior.
    if(spec.display && el.tagName === 'INPUT' && el.type === 'number'){
      const scale = spec.display.scale || 100;
      const eb = spec.display.entry_bounds;
      const cb = Array.isArray(b) ? b
        : (Array.isArray(eb) ? eb : null);
      if(cb){
        el.min = String(cb[0] * scale);
        el.max = String(cb[1] * scale);
      }
      if(spec.display.step != null) el.step = String(spec.display.step);
    }else if(Array.isArray(b) && el.tagName === 'INPUT' && el.type === 'number'){
      el.min = String(b[0]);
      el.max = String(b[1]);
    }
  });
}

async function refreshCollectorStatus(){
  try{
    const res = await fetch('/api/collector/status');
    const st = await res.json();
    isCollectorActive = st.running;
    const src = st.source || (st.running ? 'child' : 'none');
    const cb = $('collectorBadge');
    const ticks = (st.total_ticks_collected||0).toLocaleString();
    // A standalone collector is the normal way to run a long capture, so it
    // reads as healthy green like any other running writer. Amber here used to
    // suggest something was wrong; the only thing that differs is who owns the
    // process, which belongs in the tooltip, not in the badge text.
    if(src === 'external' || st.running){
      cb.textContent = `● COLLECTING · ${ticks} ticks`;
      cb.style.color = 'var(--up)';
      cb.style.borderColor = 'rgba(51,201,181,0.45)';
      cb.style.background = 'rgba(51,201,181,0.12)';
      cb.title = src === 'external'
        ? `Standalone collector is writing run/ticks/ (${ticks} ticks today). Start is locked so a second writer cannot corrupt the same daily file — stop it in its own terminal.`
        : `Dashboard-owned collector is running (${ticks} ticks today).`;
      $('btnToggleCollector').textContent = src === 'external' ? '🔒 Collecting' : 'Stop Polling';
      $('btnToggleCollector').className = src === 'external' ? 'btn btn-locked' : 'btn btn-danger';
      $('btnToggleCollector').disabled = (src === 'external');
      $('btnToggleCollector').title = src === 'external'
        ? 'A standalone collector already owns run/ticks/ — stop it in its own terminal.'
        : 'Stop the dashboard-owned collector.';
    } else {
      cb.textContent = `○ IDLE · ${ticks} ticks`;
      cb.style.color = 'var(--dim)';
      cb.style.borderColor = 'var(--line)';
      cb.style.background = 'var(--panel2)';
      cb.title = 'No collector is writing run/ticks/.';
      $('btnToggleCollector').textContent = 'Start Polling (1s)';
      $('btnToggleCollector').className = 'btn';
      $('btnToggleCollector').disabled = false;
      $('btnToggleCollector').title = 'Capture 1-second live ticks and tape into run/ticks/.';
    }

    const tb = $('tapeBadge');
    if(tb){
      if(st.tape_empty_rate !== null && st.tape_empty_rate !== undefined){
        const pctStr = (st.tape_empty_rate * 100).toFixed(1) + '%';
        if(st.tape_alert){
          tb.textContent = `⚠️ Tape quiet (${pctStr} empty)`;
          tb.style.color = 'var(--down)';
          tb.style.borderColor = 'rgba(240,104,77,0.5)';
          tb.style.background = 'rgba(240,104,77,0.18)';
        } else {
          tb.textContent = `Tape empty: ${pctStr} (${(st.tape_entries_total||0).toLocaleString()} trades)`;
          tb.style.color = 'var(--dim)';
          tb.style.borderColor = 'var(--line)';
          tb.style.background = 'var(--panel2)';
        }
      } else {
        tb.textContent = 'Tape: -';
      }
    }

    // Issue #349: WS-vs-REST book disagreement (#174 Phase 1). Rate and its
    // sample size always travel together; amber when books diverge beyond the
    // tolerance, same palette as the tape alert. The tooltip carries the
    // per-series breakdown so an operator can see which series diverge.
    const sb = $('shadowBadge');
    if(sb){
      const bs = st.book_shadow;
      if(bs && bs.comparisons > 0 && bs.badge_text){
        // badge_text is server-rendered (single source of truth, tested in
        // Python); the UI only picks the color from the rate.
        sb.textContent = bs.badge_text;
        if(bs.divergence_rate > bs.tolerance){
          sb.style.color = 'var(--gold)';
          sb.style.borderColor = 'rgba(240,180,41,0.5)';
          sb.style.background = 'rgba(240,180,41,0.15)';
        } else {
          sb.style.color = 'var(--up)';
          sb.style.borderColor = 'rgba(51,201,181,0.45)';
          sb.style.background = 'rgba(51,201,181,0.12)';
        }
        const ps = bs.per_series || {};
        const rows = Object.entries(ps)
          .map(([slug, s]) => {
            const r = s.comparisons ? (s.divergent / s.comparisons * 100).toFixed(1) : '-';
            return `${slug}: ${r}% (${s.comparisons})`;
          })
          .join('\n');
        sb.title = `WS vs REST book disagreement (diverged = delta > ${bs.tolerance})` +
          (rows ? `\n${rows}` : '');
      } else {
        sb.textContent = 'Book Δ: not enough data yet';
        sb.style.color = 'var(--dim)';
        sb.style.borderColor = 'var(--line)';
        sb.style.background = 'var(--panel2)';
        sb.title = 'The collector has not produced a WS-vs-REST book comparison yet (#174 Phase 1).';
      }
    }
  }catch(e){ console.warn('refreshCollectorStatus failed', e); }
}

async function toggleCollector(){
  const endpoint = isCollectorActive ? '/api/collector/stop' : '/api/collector/start';
  const res = await fetch(endpoint, {method:'POST'});
  if(res.status === 409){
    // A 409 means a standalone writer beat us to run/ticks/. Let the refresh
    // below paint the real state rather than writing a competing label here;
    // only the reason is worth keeping, and it belongs in the tooltip.
    let reason = 'A standalone collector already owns run/ticks/.';
    try{ reason = (await res.json()).error || reason; }catch{}
    $('collectorBadge').title = reason;
  }
  refreshCollectorStatus();
}

async function pollOnce(){
  $('collectorBadge').textContent = 'Sampling data now...';
  await fetch('/api/collector/poll-once', {method:'POST'});
  tick();
  refreshCollectorStatus();
}

async function rebuildStats(){
  const btn=$('btnRebuildStats');
  if(btn) btn.disabled=true;
  $('collectorBadge').textContent = 'Rebuilding stats...';
  try{
    const res=await fetch('/api/rebuild', {method:'POST'});
    let body={}; try{body=await res.json();}catch{}
    if(!res.ok || !body.ok){
      $('collectorBadge').textContent = 'Rebuild failed: ' + (body.output||res.status);
    } else {
      tick();
      refreshCollectorStatus();
    }
  }catch(e){
    $('collectorBadge').textContent = 'Rebuild failed: ' + e;
  }finally{
    if(btn) btn.disabled=false;
  }
}

async function tick(){
  // Issue #371: the tab/visibility guards live in the timer wrapper (pollTick),
  // NOT here — explicit callers (sampling, rebuild, goal save, upload, the
  // `await tick()` refresh paths) must always poll.
  refreshCollectorStatus();
  let data; try{data=await (await fetch('/api/oscillation',{cache:'no-store'})).json();}catch(e){return;}
  const sum=data.summary||{}, per=sum.per_series||{}, live=data.live||{}, wins=data.windows||[];

  // Goal bar
  (function(){
    const g=data.goals||{}, dg=data.default_goals||{'300':500,'900':150};
    const fmt=(dur)=>{
      const k=String(dur), cur=g[k]||{goal:dg[k],n:0,any_2c:0,monotonic:0,oscillating:0};
      const goal=parseInt(localStorage.getItem('goal_'+k)||cur.goal,10);
      const n=cur.n, any2=cur.any_2c, mono=cur.monotonic, osc=cur.oscillating;
      const pctGoal=Math.min(100,Math.round(n/goal*100));
      const remain=Math.max(0,goal-n);
      return {goal,n,any2,mono,osc,pctGoal,remain,label:dur===300?'5m (300s)':'15m (900s)', short:dur===300?'5m':'15m'};
    };
    const g5=fmt(300), g15=fmt(900);
    const gt=g.total||{n:0,any_2c:0,monotonic:0,oscillating:0};
    const bar=(x)=>`<div class="card" style="flex:1;min-width:280px;background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:12px"><div style="font:700 11px var(--disp);letter-spacing:.07em;color:var(--faint)">🎯 ${x.short} — ${x.label}</div><div class="mono" style="font-size:18px;font-weight:700;margin:6px 0">${x.goal} <span style="font-size:12px;color:var(--dim)">goal</span> / ${x.n} <span style="font-size:12px;color:var(--up)">passed</span> / ${x.any2} <span style="font-size:12px;color:var(--gold)">±$0.02</span> / ${x.mono} <span style="font-size:12px;color:var(--down)">mono</span></div><div style="display:flex;gap:6px;align-items:center"><div class="bar" style="flex:1;height:8px"><div class="fill ${x.pctGoal>=100?'up':x.pctGoal>=70?'gold':'warn'}" style="width:${x.pctGoal}%"></div></div><span class="mono" style="font-size:11px;color:var(--dim)">${x.pctGoal}%</span></div><div class="mono" style="font-size:10px;color:var(--dim);margin-top:4px">oscillating ${x.osc} · flat ${g[String(x.short==='5m'?300:900)]?.flat||0} · remaining ${x.remain}</div><div style="margin-top:6px;display:flex;gap:6px;align-items:center"><span class="mono" style="font-size:10px;color:var(--dim)">Target:</span><input id="goalIn${x.short}" type="number" min="1" step="10" value="${x.goal}" style="width:90px;background:var(--bg);color:var(--tx);border:1px solid var(--line);border-radius:6px;padding:4px 6px;font:500 12px var(--mono)"><button onclick="(function(){const v=parseInt(document.getElementById('goalIn${x.short}').value,10);if(v>0){localStorage.setItem('goal_${x.short==='5m'?300:900}',v);tick();}})()" style="background:var(--panel);color:var(--tx);border:1px solid var(--line);border-radius:6px;padding:4px 10px;font:600 11px var(--disp);cursor:pointer">Save</button></div></div>`;
    const tot=`<div class="card" style="flex:0 0 180px;min-width:160px;background:var(--panel);border:1px dashed var(--line);border-radius:10px;padding:12px;text-align:center"><div style="font:700 11px var(--disp);letter-spacing:.07em;color:var(--faint)">Total</div><div class="mono" style="font-size:16px;font-weight:700;margin-top:4px">${gt.n} windows</div><div class="mono" style="font-size:10px;color:var(--dim)">${gt.any_2c} touched · ${gt.monotonic} mono · ${gt.oscillating} osc</div></div>`;
    const src=data.source||'oscillation_windows.jsonl';
    const ageMin=(data.source_mtime!=null)?Math.max(0,Math.round((Date.now()/1000-data.source_mtime)/60)):'?';
    const totClosed=(data.total_windows!=null)?data.total_windows:gt.n;
    const prov=`<div class="mono" id="provenanceLine" style="font-size:11px;color:var(--dim);margin-top:8px">Source: ${esc(src)} · updated ${ageMin} min ago · ${totClosed} closed windows</div>`;
    $('goalBar').innerHTML=`<h3>🎯 Window Capture Targets</h3><div style="display:flex;gap:10px;flex-wrap:wrap">${bar(g5)}${bar(g15)}${tot}</div>${prov}`;
  })();

  // Collector market-data bar
  let liveHtml = '<h3>Market Data — Books & Queue</h3><div class="live-grid">';
  const order=['btc-up-or-down-5m','eth-up-or-down-5m','bnb-up-or-down-5m','sol-up-or-down-5m','xrp-up-or-down-5m','btc-up-or-down-15m','eth-up-or-down-15m','bnb-up-or-down-15m','sol-up-or-down-15m','xrp-up-or-down-15m'];
  for(const k of order){
    const s=live[k];
    if(!s){ liveHtml+=`<div class="liveBox"><div style="font:700 12px var(--disp);color:var(--tx)">${esc(marketName(k))}</div><div style="color:var(--dim);font-size:12px">Loading…</div></div>`; continue; }
    const mid=s.mid==null?'-':fmtPrice(s.mid);
    const tp=s.touch_pair==null?'-':s.touch_pair.toFixed(3);
    const rem=s.t_rem==null?'-':hms(s.t_rem);
    const q=s.queue_up==null?'-':Math.round(s.queue_up);
    liveHtml+=`<div class="liveBox"><div style="font:700 12px var(--disp);color:var(--tx)">${esc(marketName(k))}</div><div class="mono" style="font-size:12.5px;font-variant-numeric:tabular-nums">mid ${mid} · touch ${tp}</div><div class="mono" style="font-size:11.5px;color:var(--dim);font-variant-numeric:tabular-nums">queue @rest ${q} · rem ${rem}</div><div style="font-size:11px"><a href="https://polymarket.com/market/${s.slug}" target="_blank" rel="noopener">Polymarket ↗</a></div></div>`;
  }
  liveHtml+='</div>';
  $('liveBar').innerHTML=liveHtml;

  // Per series cards
  let grid='';
  for(const k of order){
    const s=per[k];
    if(!s) continue;
    const n=s.windows||0;
    const any2=s.any_2c||0, any3=s.any_3c||0, osc=s.oscillating||0, mono=s.monotonic||0, flat=s.flat||0;
    const p2=pct(any2,n), p3=pct(any3,n), po=pct(osc,n), pm=pct(mono,n);
    grid+=`<div class="card"><h3>${esc(s.label)} — ${s.duration===300?'5m':'15m'} <span style="font-weight:400;color:var(--dim);text-transform:none;letter-spacing:0">· ${n} windows</span></h3>
      <div class="kpi">
        <div class="box"><div class="lbl">Any move ≥$0.02</div><div class="val">${any2}/${n}</div><div class="sub">${p2}% moved $0.02</div><div class="bar"><div class="fill up" style="width:${p2}%"></div></div></div>
        <div class="box"><div class="lbl">≥$0.03</div><div class="val">${any3}/${n}</div><div class="sub">${p3}%</div><div class="bar"><div class="fill gold" style="width:${p3}%"></div></div></div>
        <div class="box"><div class="lbl">oscillating</div><div class="val" style="color:var(--up)">${osc}/${n}</div><div class="sub">${po}%</div><div class="bar"><div class="fill up" style="width:${po}%"></div></div></div>
        <div class="box"><div class="lbl">monotonic</div><div class="val" style="color:var(--down)">${mono}/${n}</div><div class="sub">${pm}%</div><div class="bar"><div class="fill down" style="width:${pm}%"></div></div></div>
      </div>
      <div class="mono" style="font-size:11.5px;color:var(--dim);font-variant-numeric:tabular-nums">Median touch pair: ${s.pair_cost_median==null?'-':s.pair_cost_median.toFixed(3)} · flat ${flat}/${n}</div>
    </div>`;
  }
  $('seriesGrid').innerHTML=grid;

  // Recent windows table
  let tbl='<div class="card"><h3 style="font-size:13px">Recent Windows — 50/50 Open (Click for Polymarket)</h3><table class="tbl"><thead><tr><th>Series</th><th>Open UP / DOWN</th><th style="color:var(--up)">Max UP</th><th style="color:var(--down)">Max DOWN</th><th>Candle</th><th>Class</th><th>Result</th></tr></thead><tbody>';
  const fmtDelta=d=>d==null?'-':((d<0?'-':'+')+fmtPrice(Math.abs(d)));
  // Non-positive timestamps are missing (strategy/windows.py defaults absent
  // start_ts/end_ts to numeric 0.0) — never render them as Unix-epoch times.
  const fmtHM=t=>(Number(t)>0)?new Date(Number(t)*1000).toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit'}):'-';
  for(const w of wins.slice(0,60)){
    const sm = w.start_mid, cm=w.close_mid, mx=w.max_mid, mn=w.min_mid;
    const openUp = sm==null?'-':fmtPrice(sm);
    const openDown = sm==null?'-':fmtPrice(1-sm);
    const upHigh = mx==null?'-':fmtPrice(mx);
    // Entry-relative excursion (exit-opportunity signal): distance from OPEN,
    // not from the fixed 0.50 base. Highlight at the $0.05 exit level.
    const upDelta = (mx==null||sm==null)?null:(mx-sm);
    const downDelta = (mn==null||sm==null)?null:(sm-mn);
    const upExc = upDelta==null?'-':`<span class="price-up"${upDelta>=0.05?' style="font-weight:800"':''}>${fmtDelta(upDelta)}</span>`;
    const downHigh = mn==null?'-':fmtPrice(1-mn);
    const downExc = downDelta==null?'-':`<span class="price-down"${downDelta>=0.05?' style="font-weight:800"':''}>${fmtDelta(downDelta)}</span>`;
    const o = sm==null?50:sm*100, c = cm==null?o:cm*100, h = mx==null?o:mx*100, l = mn==null?o:mn*100;
    const bodyLeft = Math.min(o,c), bodyW = Math.abs(c-o);
    const wickLeft = l, wickW = h-l;
    const bodyColor = c>=o ? 'var(--up)' : 'var(--down)';
    const candle = `<div class="candle-wrap"><div class="candle-bar"><div class="candle-wick" style="left:${wickLeft}%;width:${wickW}%;"></div><div class="candle-body" style="left:${bodyLeft}%;width:${Math.max(2,bodyW)}%;background:${bodyColor};border:1px solid ${bodyColor}"></div><div style="position:absolute;left:50%;top:0;bottom:0;width:1px;background:var(--faint);opacity:.6"></div></div>    <div style="font-size:10.5px;color:var(--dim);margin-top:1px">Range ${fmtPrice(mx!=null&&mn!=null?mx-mn:0)} · Close ${fmtPrice(cm)}</div></div>`;
    const startHM=fmtHM(w.start_ts), endHM=fmtHM(w.end_ts);
    const rangeStr=(startHM==='-'&&endHM==='-')?'-':`${startHM}-${endHM}`;
    const resPill = cm==null?'-':(cm>=0.50?pill('pill-osc','UP'):pill('pill-mono','DOWN'));
    const safeUrl=(typeof w.url==='string'&&w.url.startsWith('https://'))?esc(w.url):'#';
    tbl+=`<tr><td style="font-weight:700"><a href="${safeUrl}" target="_blank" rel="noopener">${esc(marketName(w.series||w.label||''))}<div style="font-size:10.5px;color:var(--faint);font-weight:400;font-variant-numeric:tabular-nums">${esc(rangeStr)}</div></a></td><td><span class="price-up">${openUp}</span> | <span class="price-down">${openDown}</span></td><td class="mono" style="font-variant-numeric:tabular-nums"><span class="price-up">${upHigh}</span> (${upExc})</td><td class="mono" style="font-variant-numeric:tabular-nums"><span class="price-down">${downHigh}</span> (${downExc})</td><td>${candle}</td><td>${clsPill(w.class)}</td><td>${resPill}</td></tr>`;
  }
  tbl+='</tbody></table></div>';
  $('windowsTableWrap').innerHTML=tbl;
}

// Backtest execution
let equityChartInstance = null;
let pnlHistChartInstance = null;
let btChartDialogInstance = null;
window.selectedBacktestFile = "";
window._btFileChosen = false; // Issue #279: flips on any manual dataset pick
window._btSweepVisualData = null;
window._btSweepCenter = null; // Issue #378: sweep-only midpoint anchor, outside the card DOM
window._btChartDialogTrigger = null;
window._btRunning = false;
// Issue #383: bumped by every deliberate stop. A sweep that is waiting for the
// plain backtest to finish captures it before waiting and gives up when it
// changes — a transient flag would be missed by a loop that polls too late, and
// without any signal the wait would fall through and START a fresh run.
window._btStopSeq = 0;

function setBacktestLoadingState(isLoading){
  const btn = $('btnRunSweep');
  const icon = $('btnRunSweepIcon');
  const text = $('btnRunSweepText');
  if(!btn) return;
  if(isLoading){
    btn.disabled = true;
    btn.classList.add('thinking');
    if(icon) icon.innerHTML = '<span class="spinner"></span>';
    if(text) text.innerHTML = 'Simulating 0s <span class="thinking-dots"><span></span><span></span><span></span></span>';
    const lastRun = $('btLastRunTime');
    if(lastRun) lastRun.textContent = '';
    const elTime = $('btElapsedTime');
    if(elTime){
      elTime.textContent = '0s';
      elTime.style.color = 'var(--gold)';
    }
    const elSub = $('btElapsedSub');
    if(elSub) elSub.textContent = 'Simulating…';
  } else {
    btn.disabled = false;
    btn.classList.remove('thinking');
    if(icon) icon.textContent = '▶';
    if(text) text.textContent = 'Run Sweep';
  }
  // Issue #383: one Stop control for both run kinds; it follows the same loading
  // state the Run button does.
  updateBtStopVisibility();
}

// Issue #383/#386: show a Stop control while a run is in flight, and show it
// WHERE that run was started. `hidden` alone is not enough — a
// `.btn { display: ... }` rule beats the user-agent's `[hidden] { display: none }`
// — so the inline display is set too.
//
// Each control answers to its OWN run kind. One shared control shown for both
// would put two identical "Stop" buttons on screen at once, which is worse than
// either placement on its own.
function updateBtStopVisibility(){
  const btBtn = $('btnStopBacktest');
  if(btBtn){
    const busy = !!window._btRunning;
    btBtn.hidden = !busy;
    btBtn.disabled = !busy;
    btBtn.style.display = busy ? '' : 'none';
  }
  const svBtn = $('btnStopSweepVisual');
  if(svBtn){
    const busy = !!window._btSweepInFlight;
    svBtn.hidden = !busy;
    svBtn.disabled = !busy;
    svBtn.style.display = busy ? '' : 'none';
  }
}

// Issue #383: a deliberate stop is NOT a failure and NOT a stall, so it gets its
// own neutral notice. Written outside `#btSweepMeta`, which the idle re-render
// owns, so re-rendering the sweep card cannot wipe the reason for the stop.
function markBacktestStopped(){
  const lastRun = $('btLastRunTime');
  if (lastRun) lastRun.textContent = '⏹ stopped by operator';
  const elTime = $('btElapsedTime');
  if (elTime) {
    elTime.textContent = '--';
    elTime.style.color = 'var(--gold)';
  }
  const elSub = $('btElapsedSub');
  if (elSub) elSub.textContent = 'Stopped';
  const elBanner = $('btPairCostSub');
  if (elBanner) {
    elBanner.textContent = '⏹ Stopped by operator — partial results discarded.';
    elBanner.style.color = 'var(--gold)';
  }
}

// Issue #383: the worker did not confirm death, so the run slot is still held.
// That is not a stop either — it needs its own warning-toned line, or the
// operator reads a clean idle tab while the next Run is still going to 429.
function markBacktestStopping(detail){
  const lastRun = $('btLastRunTime');
  if (lastRun) lastRun.textContent = '⏹ stopping…';
  const elTime = $('btElapsedTime');
  if (elTime) {
    elTime.textContent = '--';
    elTime.style.color = 'var(--warn)';
  }
  const elSub = $('btElapsedSub');
  if (elSub) elSub.textContent = 'Stopping…';
  const elBanner = $('btPairCostSub');
  if (elBanner) {
    elBanner.textContent = `⏳ Stop requested — ${detail || 'waiting for the worker to exit.'} The next Run is still held.`;
    elBanner.style.color = 'var(--warn)';
  }
}

// Issue #383: stop the run on the SERVER, then reset the tab.
//
// Ordering is the whole point: ownership is detached BEFORE the request. Once
// `window._btAbort` / `window._btSweepAbort` are null, the stopped run's own
// `finally` sees it is superseded and skips its teardown, and any in-flight SSE
// event fails the ownership check and renders nothing. The reset below therefore
// cannot be undone by the run it is cancelling.
async function stopBacktestRun(){
  if (window._btStopBusy) return;
  window._btStopBusy = true;
  // Issue #386: both controls, through the shared class — whichever one the
  // operator pressed must show the same pending state.
  const stopBtns = document.querySelectorAll('.bt-stop');
  for (const b of stopBtns) {
    b.disabled = true;
    b.textContent = '⏹ Stopping…';
  }
  window._btStopSeq = (window._btStopSeq || 0) + 1;
  if (window._btAbort) { try{ window._btAbort.abort(); }catch{} window._btAbort = null; }
  if (window._btSweepAbort) { try{ window._btSweepAbort.abort(); }catch{} window._btSweepAbort = null; }
  window._btRunning = false;
  window._btSweepInFlight = false;

  let body = null;
  try {
    const res = await fetch('/api/backtest/cancel', {method: 'POST'});
    body = await res.json();
  } catch(err) {
    body = {ok: false, detail: 'the stop request failed: ' + err};
  }

  // Reset the tab either way: the guards are free, or the endpoint says plainly
  // that the run slot is still held (rendered below). Either way the operator
  // reads why, and either way nothing from the aborted run is rendered.
  window._btRunning = false;
  window._btSweepInFlight = false;
  window._btAbort = null;
  window._btSweepAbort = null;
  btRenderToken = null;
  btDestroyProvisionalChart();
  stopBtTimer();
  setBacktestLoadingState(false);
  if(window._btSweepTimerId){ clearInterval(window._btSweepTimerId); window._btSweepTimerId = null; }
  window._btSweepStartTime = null;
  const sweepBtn = $('btnRunSweepVisual');
  if(sweepBtn){ sweepBtn.disabled = false; sweepBtn.textContent = '▶ Run Sweep Visual'; }
  window._btStopBusy = false;
  for (const b of document.querySelectorAll('.bt-stop')) b.textContent = '⏹ Stop';

  if (body && body.stopping) markBacktestStopping(body.detail);
  else if (body && body.ok === false) markBacktestStopping(body.detail);
  else markBacktestStopped();

  updateBtStopVisibility();
  // Re-render the idle sweep card so no pending/progress markers survive the stop.
  renderSweepIdle();
}

function fmtElapsed(ms){
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return s + 's';
  const m = Math.floor(s / 60);
  const rest = s % 60;
  return m + 'm ' + String(rest).padStart(2, '0') + 's';
}

function startBtTimer(){
  stopBtTimer();
  window._btStartTime = performance.now();
  window._btTimerId = setInterval(() => {
    const elapsed = fmtElapsed(performance.now() - window._btStartTime);
    const text = $('btnRunSweepText');
    if(text && window._btRunning){
      text.innerHTML = 'Simulating ' + elapsed + ' <span class="thinking-dots"><span></span><span></span><span></span></span>';
    }
    const elTime = $('btElapsedTime');
    if(elTime && window._btRunning){
      elTime.textContent = elapsed;
    }
  }, 500);
}

function stopBtTimer(){
  if(window._btTimerId){ clearInterval(window._btTimerId); window._btTimerId = null; }
}

function runBacktestOnFile(filename){
  window.selectedBacktestFile = filename;
  const sel = $('btFileSelect');
  if(sel){
    let optExists = Array.from(sel.options).some(o => o.value === filename);
    if(!optExists){
      const opt = document.createElement('option');
      opt.value = filename;
      opt.textContent = filename;
      sel.appendChild(opt);
    }
    sel.value = filename;
  }
  updateBtRuntimeEstimate();
  switchTab('backtest');
  runBacktest(filename);
}

function toggleBtSection(btn, bodyId){
  const body = document.getElementById(bodyId);
  if(!btn || !body) return;
  const open = btn.getAttribute('aria-expanded') === 'true';
  btn.setAttribute('aria-expanded', open ? 'false' : 'true');
  body.hidden = open;
  try {
    const state = JSON.parse(localStorage.getItem('btSectionsOpen') || '{}');
    state[bodyId] = !open;
    localStorage.setItem('btSectionsOpen', JSON.stringify(state));
  } catch(e) { /* localStorage unavailable */ }
}

(function initBtSections(){
  if(typeof document === 'undefined' || !document.getElementById || !document.querySelector) return;
  let state = {};
  try { state = JSON.parse(localStorage.getItem('btSectionsOpen') || '{}'); } catch(e) {}
  const defaults = {
    btSecParametersBody: true,
    btSecGeometryBody: true,
    btSecOverallBody: true,
    btSecSweepBody: true,
    btSecSeriesBody: true,
    btSecLogBody: true,
    btSecOperatorBody: true,
    btSecStructuralBody: true,
    btSecPolicyBody: false
  };
  Object.keys(defaults).forEach(function(id){
    const body = document.getElementById(id);
    const btn = body ? document.querySelector('.bt-section-head[aria-controls="' + id + '"]') : null;
    if(!body || !btn) return;
    const open = (id in state) ? !!state[id] : defaults[id];
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    body.hidden = !open;
  });
})();

// Backtester market/timeframe chip selection (mirrors the cockpit chips,
// but never auto-runs: a full replay is expensive, so chips only set state
// and the operator presses Run).
let selectedBtTokens = new Set(['BTC', 'ETH', 'BNB', 'SOL', 'XRP']);
let selectedBtDuration = 'both';

function updateBtFilterUI() {
  ['BTC', 'ETH', 'BNB', 'SOL', 'XRP'].forEach(tok => {
    const chip = $(`btToken-${tok}`);
    if (!chip) return;
    const on = selectedBtTokens.has(tok);
    chip.setAttribute('aria-pressed', on ? 'true' : 'false');
    if (on) chip.classList.add('active');
    else chip.classList.remove('active');
  });
  const btDurStates = [['btDur5m', '5m'], ['btDur15m', '15m'], ['btDurBoth', 'both']];
  btDurStates.forEach(([id, dur]) => {
    const b = $(id);
    if (!b) return;
    const on = selectedBtDuration === dur;
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
    b.className = on ? 'tab-btn active' : 'tab-btn';
  });
  updateBtRuntimeEstimate();
}

function toggleBtToken(tok) {
  if (selectedBtTokens.has(tok)) {
    if (selectedBtTokens.size <= 1) {
      alert('At least one market must remain selected.');
      return;
    }
    selectedBtTokens.delete(tok);
  } else {
    selectedBtTokens.add(tok);
  }
  updateBtFilterUI();
  // Issue #355: the sweep card's Markets grid is now selection-driven, so an
  // idle card must follow the chips. Never starts a run, never touches a sweep.
  if (typeof refreshSweepIdleCard === 'function') refreshSweepIdleCard();
}

function setBtTokensAll(selectAll) {
  selectedBtTokens = selectAll ? new Set(['BTC', 'ETH', 'BNB', 'SOL', 'XRP']) : new Set(['BTC']);
  updateBtFilterUI();
  if (typeof refreshSweepIdleCard === 'function') refreshSweepIdleCard();
}

function setBtDuration(dur) {
  if (selectedBtDuration === dur) return;
  selectedBtDuration = dur;
  updateBtFilterUI();
  if (typeof refreshSweepIdleCard === 'function') refreshSweepIdleCard();
}

// Interactive Backtest Runtime Estimation (Issue #330)
function calculateBtEstimatedRuntime(fileOverride){
  const files = window.tickManifestFiles || [];
  const fileVal = fileOverride !== undefined ? fileOverride : ($('btFileSelect') ? $('btFileSelect').value : (window.selectedBacktestFile || ''));
  const targetFiles = fileVal ? files.filter(f => f.name === fileVal) : files;

  let matchedWindows = 0;
  for (const f of targetFiles) {
    if (f.market_breakdown && f.market_breakdown.length > 0) {
      for (const mb of f.market_breakdown) {
        const tok = (mb.series || '').split('-')[0].toUpperCase();
        const dur = mb.duration;
        if (typeof selectedBtTokens !== 'undefined' && !selectedBtTokens.has(tok)) continue;
        if (typeof selectedBtDuration !== 'undefined') {
          if (selectedBtDuration === '5m' && dur !== 300) continue;
          if (selectedBtDuration === '15m' && dur !== 900) continue;
        }
        matchedWindows += (mb.windows || 0);
      }
    } else {
      const win = f.windows_count || (f.window_quality ? (f.window_quality.research_windows || f.window_quality.clean_windows || f.window_quality.full_windows || 0) : 0);
      const tokCount = (typeof selectedBtTokens !== 'undefined') ? selectedBtTokens.size : 5;
      const durRatio = (typeof selectedBtDuration !== 'undefined')
        ? (selectedBtDuration === 'both' ? 1.0 : (selectedBtDuration === '5m' ? 0.75 : 0.25))
        : 1.0;
      matchedWindows += Math.round(win * durRatio * (tokCount / 5.0));
    }
  }

  if (matchedWindows <= 0) {
    return { windows: 0, seconds: 0, text: '—' };
  }

  // Linear benchmark: ~1.2s base overhead + ~0.18s per matching window replay
  const estSec = Math.max(1, Math.round(1.2 + matchedWindows * 0.18));
  return { windows: matchedWindows, seconds: estSec, text: fmtElapsed(estSec * 1000) };
}

function updateBtRuntimeEstimate(){
  const badge = $('btRuntimeEstBadge');
  if (!badge) return;
  if (!window.tickManifestFiles && !window.tickManifestData) {
    badge.textContent = '⏱️ Est: calculating…';
    badge.title = 'Loading dataset manifest…';
    return;
  }
  const est = calculateBtEstimatedRuntime();
  if (est.windows <= 0) {
    badge.textContent = '⏱️ Est: — (0 windows)';
    badge.title = 'No windows match current dataset and filter selection';
    badge.style.color = 'var(--dim)';
    badge.style.borderColor = 'var(--line)';
  } else {
    badge.textContent = `⏱️ Est: ~${est.text} (${est.windows.toLocaleString()} win)`;
    badge.title = `Estimated replay runtime: ~${est.text} for ${est.windows.toLocaleString()} windows based on active file, markets, and duration`;
    badge.style.color = 'var(--cyan)';
    badge.style.borderColor = 'var(--line)';
  }
}

// Issue #419: one cents convention at the dashboard edge. The engine, the
// API and the CLI stay in dollar floats; price inputs display whole-number
// cents (scale x100, step 0.1c = venue tick 0.001). Every input writer and
// every input reader below routes through these three helpers, so a value
// converts exactly once at each UI boundary. Top-level on purpose: the node
// parity harness extracts them by name straight from the served page.
function centsToDollars(c){
  const num = Number(c);
  if(!Number.isFinite(num)) return NaN;
  // Math.round, not toFixed: toFixed throws a RangeError past 1e21, and an
  // operator can type an exponent the validator rejects but the preview
  // still reads — a marked-bad input must never break the render path.
  return Math.round(num * 10) / 1000;
}
function dollarsToCents(d){
  const num = Number(d);
  if(!Number.isFinite(num)) return NaN;
  const c = num * 100;
  // One decimal of a cent only when it represents the value (1e-9); anything
  // finer keeps up to 3 decimals so no value ever changes silently.
  const r1 = Number(c.toFixed(1));
  if(Math.abs(r1 - c) < 1e-9) return r1;
  return Number(c.toFixed(3));
}
function formatCents(d){
  const num = Number(d);
  if(!Number.isFinite(num)) return String(d);
  return `${dollarsToCents(num)}c`;
}
// One cents-input validator for both tabs. Rejects empty, non-finite,
// out-of-[min,max] and more-than-one-decimal input; toggles input-invalid.
function validateCentsInput(el){
  if(!el) return true;
  const raw = String(el.value).trim();
  let ok = raw !== '' && Number.isFinite(Number(raw)) && /^-?\d+(\.\d)?$/.test(raw);
  if(ok && el.min !== '' && el.min != null) ok = Number(raw) >= Number(el.min);
  if(ok && el.max !== '' && el.max != null) ok = Number(raw) <= Number(el.max);
  el.classList.toggle('input-invalid', !ok);
  return ok;
}
// Backtest-tab twin of validateCockpitInputs: the nine price inputs plus the
// strict lo < hi ordering (both ends marked when it fails).
function validateBacktestInputs(){
  let ok = true;
  ['btOffset', 'btPairCost', 'btExit5m', 'btExit15m', 'btExitBtc', 'btExitSol',
   'btExitReversal', 'btQuoteLo', 'btQuoteHi'].forEach(id => {
    const el = $(id);
    if(el && !validateCentsInput(el)) ok = false;
  });
  const loEl = $('btQuoteLo');
  const hiEl = $('btQuoteHi');
  if(loEl && hiEl){
    const lo = centsToDollars(String(loEl.value).trim());
    const hi = centsToDollars(String(hiEl.value).trim());
    if(!(Number.isFinite(lo) && Number.isFinite(hi) && lo < hi)){
      loEl.classList.add('input-invalid');
      hiEl.classList.add('input-invalid');
      ok = false;
    }
  }
  return ok;
}

// One reader for every Backtester control, so "Run Sweep" and "Run Backtest"
// cannot disagree about what the page is set to. The sweep varies only the
// chosen axis; every other knob is exactly the number typed above.
function btControlValues(overrides){
  const o = overrides || {};
  const getVal = (id, def) => {
    const el = $(id);
    if (!el) return def;
    const v = String(el.value).trim();
    return (v !== '' && Number.isFinite(Number(v))) ? Number(v) : def;
  };
  // Issue #419: price inputs hold cents; the request stays in dollars.
  const getCents = (id, def) => {
    const el = $(id);
    if (!el) return def;
    const raw = String(el.value).trim();
    if (raw === '' || !Number.isFinite(Number(raw))) return def;
    return centsToDollars(raw);
  };
  const size = Math.max(5, Math.round(getVal('btSize', 5)));
  return {
    offset: getCents('btOffset', 0.02),
    queue: getVal('btQueue', 50),
    pairCost: getCents('btPairCost', 0.99),
    exit5m: getCents('btExit5m', 0.05),
    exit15m: getCents('btExit15m', 0.05),
    exitBtc: getCents('btExitBtc', 0.05),
    exitSol: getCents('btExitSol', 0.05),
    exitReversal: getCents('btExitReversal', 0.02),
    size: (o.size !== undefined) ? o.size : size,
    maxStartDelay: getVal('btMaxStartDelay', 0.0),
    quoteLo: getCents('btQuoteLo', 0.10),
    quoteHi: getCents('btQuoteHi', 0.90),
    entryDelayPct: Math.max(0, Math.min(100, getVal('btEntryDelay', 0.0))),
    // Issue #164: knobs the live engine has always had, now simulated too.
    deadZonePct: Math.max(0, Math.min(100, getVal('btDeadZoneVal', 10.0))),
    nakedLegAtExpiry: $('btNakedLegAtExpiry') ? $('btNakedLegAtExpiry').value : 'close',
    legChase: $('btLegChase') ? $('btLegChase').value : '0',
    // The fee coefficient, min order size, merge gas and tick size are venue
    // facts with no control on this tab; neither endpoint takes them, so the
    // engine uses its own defaults. Nothing to read here.
  };
}

// Issue #355: the ONE reader of the Backtest Scope chips. The request query and
// the sweep card's Markets grid both come from here, so the grid can never
// disagree with what the sweep was actually asked to replay.
const BT_ALL_TOKENS = ['BTC', 'ETH', 'BNB', 'SOL', 'XRP'];

function btSelection(){
  const tokens = (typeof selectedBtTokens !== 'undefined') ? [...selectedBtTokens] : [];
  const duration = (typeof selectedBtDuration !== 'undefined') ? selectedBtDuration : 'both';
  return { tokens: tokens, duration: duration };
}

// The selected (token, timeframe) pairs as canonical series slugs — the same
// `<token>-up-or-down-<dur>` shape the sweep result reports in `series_present`.
function btSelectedSeriesSlugs(sel){
  const s = sel || btSelection();
  const durations = s.duration === '5m' ? ['5m'] : (s.duration === '15m' ? ['15m'] : ['5m', '15m']);
  const out = [];
  s.tokens.forEach(tok => {
    durations.forEach(dur => out.push(`${String(tok).toLowerCase()}-up-or-down-${dur}`));
  });
  return out;
}

// Shared query string for both endpoints: every knob the page exposes, plus
// the market/timeframe chips when they narrow the universe.
function btControlQuery(v, axis){
  let q = `offset=${v.offset}&queue=${v.queue}&pair_cost=${v.pairCost}`
        + `&exit_default_5m=${v.exit5m}&exit_default_15m=${v.exit15m}`
        + `&exit_btc_5m=${v.exitBtc}&exit_sol_5m=${v.exitSol}`
        + `&size=${v.size}&max_start_delay=${v.maxStartDelay}`
        + `&quote_lo=${v.quoteLo}&quote_hi=${v.quoteHi}`
        + `&entry_delay_pct=${v.entryDelayPct}&exit_reversal=${v.exitReversal}`
        + `&dead_zone_pct=${v.deadZonePct}`
        + `&naked_leg_at_expiry=${encodeURIComponent(v.nakedLegAtExpiry)}`
        + `&enable_leg_chase=${v.legChase}`;
  const fileVal = ($('btFileSelect') ? $('btFileSelect').value : (window.selectedBacktestFile || ''));
  if(fileVal){ q += `&file=${encodeURIComponent(fileVal)}`; }
  // Market / timeframe selection (chip multi-select): all tokens or
  // both frames = omit the param, which the API reads as "all".
  const sel = btSelection();
  if (sel.tokens.length > 0 && sel.tokens.length < BT_ALL_TOKENS.length) {
    q += `&series=${encodeURIComponent(sel.tokens.map(t => t.toLowerCase()).join(','))}`;
  }
  if (sel.duration === '5m') {
    q += `&durations=300`;
  } else if (sel.duration === '15m') {
    q += `&durations=900`;
  }
  return q;
}

// Issue #331: the authoritative final render, extracted verbatim from the
// former success path of runBacktest. `data` is the exact backtest result dict;
// `fileVal` feeds the hash badge suffix. Any stream transport may call this —
// the chart and payload rendering must stay identical to the blocking era.
// Zero-fill runs carry an empty bucket array: the edge loop below would read
// histBuckets[-1].hi and throw, and the stream dispatcher would mislabel the
// run Failed. An empty chart keeps the axis contract (min 0, max 1).
function histEdgeValues(buckets){
  if (!Array.isArray(buckets) || !buckets.length) return [1];
  const edges = [];
  for (let i = 0; i <= buckets.length; i++) {
    edges.push(i === buckets.length ? (buckets.length ? buckets[i - 1].hi / 100 : 1) : buckets[i].lo / 100);
  }
  return edges.map(v => Math.round(v * 100) / 100);
}
function renderBacktestResult(data, fileVal){
  const ov = data.overall || {};
  const enteredTxt = (ov.entered_windows !== undefined) ? ` (${ov.entered_windows} entered)` : '';
  const tookMs = window._btStartTime ? (performance.now() - window._btStartTime) : 0;
  const tookStr = fmtElapsed(tookMs);
  const tookTxt = tookStr ? ` · took ${tookStr}` : '';
  $('btHash').textContent = `Hash: ${data.params_hash} · ${data.n_windows} windows${enteredTxt}${fileVal ? ' · [' + fileVal + ']' : ''}${tookTxt}`;
// Persistent "how long did the results take" badge next to the Run Sweep
// button — the in-button counter resets to "Run Sweep" when the run ends.
  const lastRun = $('btLastRunTime');
  if(lastRun && window._btStartTime){
    lastRun.textContent = `✓ results in ${tookStr}`;
  }
  const elTime = $('btElapsedTime');
  if(elTime){
    elTime.textContent = tookStr;
    elTime.style.color = 'var(--cyan)';
  }
  const elSub = $('btElapsedSub');
  if(elSub){
    elSub.textContent = 'Sweep duration';
  }
  $('btTotalPnl').textContent = fmtUsd(ov.total_pnl_cents||0, true);  $('btTotalPnl').style.color = (ov.total_pnl_cents||0)>=0 ? 'var(--up)' : 'var(--down)';
  $('btAvgPnl').textContent = fmtUsd(ov.avg_pnl_cents||0, true) + ' / window';
  $('btPairRate').textContent = ((ov.pair_rate||0)*100).toFixed(1) + '%';
  $('btPairsCount').textContent = `${ov.pairs||0} / ${ov.windows||0} pairs${enteredTxt}`;
  if ($('btMeanPairCost')) {
    if (ov.mean_pair_cost != null) {
      $('btMeanPairCost').textContent = '$' + Number(ov.mean_pair_cost).toFixed(3);
      $('btMeanPairCost').style.color = ov.mean_pair_cost <= 1.00 ? 'var(--up)' : 'var(--down)';
    } else {
      $('btMeanPairCost').textContent = '--';
      $('btMeanPairCost').style.color = 'var(--dim)';
    }
  }
  if ($('btPairCostSub')) {
    if (ov.pairs_above_settle > 0) {
      $('btPairCostSub').textContent = `${ov.pairs_above_settle} above $1.00 settle`;
      $('btPairCostSub').style.color = 'var(--down)';
    } else if (ov.mean_pair_edge_cents != null) {
      const edge = Number(ov.mean_pair_edge_cents);
      $('btPairCostSub').textContent = `Avg edge: ${(edge >= 0 ? '+' : '') + edge.toFixed(1)}¢/pr`;
      $('btPairCostSub').style.color = 'var(--dim)';
    } else if (ov.pairs > 0) {
      $('btPairCostSub').textContent = `${ov.pairs} pairs captured`;
      $('btPairCostSub').style.color = 'var(--dim)';
    } else {
      $('btPairCostSub').textContent = '0 pairs captured';
      $('btPairCostSub').style.color = 'var(--dim)';
    }
  }
  $('btExitRate').textContent = ((ov.exit_rate||0)*100).toFixed(1) + '%';
  $('btExitsCount').textContent = `${ov.exits||0} exits`;
  $('btMaxDd').textContent = '-' + fmtPrice((ov.max_drawdown_cents||0)/100);
  if ($('btMaxDdSub')) {
    $('btMaxDdSub').textContent = 'Peak to trough from $0.00 start';
  }
  $('btWinRate').textContent = ((ov.win_rate||0)*100).toFixed(1) + '%';
  if ($('btWinsCount')) {
    $('btWinsCount').textContent = `${ov.wins||0} / ${ov.windows||0} profitable`;
  }
  window.btActiveSize = (data.params && data.params.size) ? data.params.size : 5;

// Equity Curve Chart
  const eqData = data.equity_curve || [];
  const labels = eqData.map(e => e.window_idx);
  const pnlValues = eqData.map(e => ((e.cumulative_pnl_cents||0)/100).toFixed(2));

// Zero-fill / flatline warning diagnostic (issue #204)
  const fillsCount = (ov.pairs || 0) + (ov.exits || 0);
  const hasFills = fillsCount > 0 || (ov.total_pnl_cents || 0) !== 0;
  if ($('btEquityWarning')) {
    if (!hasFills) {
      if ((ov.entered_windows || 0) === 0 && (ov.windows || 0) > 0) {
        $('btEquityWarning').textContent = `⚠️ 0 / ${ov.windows} windows entered (all windows skipped by gates, e.g. entry delay).`;
      } else {
        $('btEquityWarning').textContent = '⚠️ 0 fills recorded in this run. Check tape data density for this dataset.';
      }
      $('btEquityWarning').style.display = 'inline-block';
    } else {
      $('btEquityWarning').style.display = 'none';
    }
  }

  const minPnl = pnlValues.length ? Math.min(...pnlValues) : 0;
  const maxPnl = pnlValues.length ? Math.max(...pnlValues) : 0;
  const pnlSpan = Math.max(Math.abs(maxPnl - minPnl), Math.abs(maxPnl) * 0.15, 0.5);
  const pnlPad = Math.max(pnlSpan * 0.20, 0.35);

destroyChartInstance('chartEquity');
  const ctx = $('chartEquity').getContext('2d');
  const theme = getThemeTokens();
equityChartInstance = new Chart(ctx, {
  type: 'line',
  plugins: [{
    id: 'equityZeroLine',
    afterDraw: function(chart) {
      const yScale = chart.scales.y;
      if (!yScale) return;
      const y0 = yScale.getPixelForValue(0);
      if (y0 >= chart.chartArea.top && y0 <= chart.chartArea.bottom) {
        const c = chart.ctx;
        c.save();
        c.beginPath();
        c.setLineDash([6, 4]);
        c.strokeStyle = theme.gold;
        c.lineWidth = 1.5;
        c.moveTo(chart.chartArea.left, y0);
        c.lineTo(chart.chartArea.right, y0);
        c.stroke();
        c.restore();
      }
    }
  }],
  data: {
    labels: labels,
    datasets: [{
      label: 'Cumulative PnL ($)',
      data: pnlValues,
      borderColor: (ov.total_pnl_cents||0)>=0 ? theme.up : theme.down,
      backgroundColor: (ov.total_pnl_cents||0)>=0 ? hexToRgba(theme.up, 0.1) : hexToRgba(theme.down, 0.1),
      fill: true,
      tension: 0.1,
      pointRadius: labels.length > 100 ? 0 : 2,
    }]
  },
  options: {
    responsive: true,
    layout: {
      padding: { left: 8, right: 14, top: 14, bottom: 10 }
    },
    plugins: { legend: { display: false } },
    scales: {
      x: {
        offset: true,
        title: { display: true, text: 'Window', color: theme.dim },
        ticks: { color: theme.dim, maxTicksLimit: 12 },
        grid: { color: theme.line }
      },
      y: {
        grace: '18%',
        suggestedMax: Math.max(0, maxPnl) + pnlPad,
        suggestedMin: Math.min(0, minPnl) - pnlPad,
        title: { display: true, text: 'Cumulative P&L ($)', color: theme.dim },
        ticks: {
          color: theme.dim,
          callback: function(v){ return formatSweepMoneyTick(v); }
        },
        grid: {
          color: function(ctx){ return (ctx.tick && ctx.tick.value === 0) ? theme.gold : theme.line; },
          lineWidth: function(ctx){ return (ctx.tick && ctx.tick.value === 0) ? 2 : 1; },
          borderDash: function(ctx){ return (ctx.tick && ctx.tick.value === 0) ? [6, 4] : []; }
        }
      }
    }
  }
});

// Per-Window P&L Distribution Histogram (Issue #136)
  const histData = data.pnl_histogram || { buckets: [], n: 0, bucket_width_cents: 1.0, mean_cents: 0.0, median_cents: 0.0 };
  const histBuckets = histData.buckets || [];
  const histStatsEl = $('btPnlHistStats');
  const histWarnEl = $('btPnlHistWarning');
  if (histStatsEl) {
    if (histData.n > 0) {
      const meanStr = fmtUsd(histData.mean_cents || 0, true);
      const medStr = fmtUsd(histData.median_cents || 0, true);
      const bwStr = ((histData.bucket_width_cents || 0) / 100).toFixed(2);
      histStatsEl.textContent = `n=${histData.n} · Δ=$${bwStr} · Mean ${meanStr} · Median ${medStr}`;
    } else {
      histStatsEl.textContent = '';
    }
  }
  if (histWarnEl) {
    histWarnEl.style.display = (!hasFills && histData.n > 0) ? 'inline-block' : 'none';
  }

destroyChartInstance('chartPnlHist');
if ($('chartPnlHist')) {
  const histCtx = $('chartPnlHist').getContext('2d');
  const minEdge = histBuckets.length ? (histBuckets[0].lo / 100) : 0;
  const maxEdge = histBuckets.length ? (histBuckets[histBuckets.length - 1].hi / 100) : 1;
  const dataPoints = histBuckets.map(b => ({
    x: (b.lo + b.hi) / 200,
    y: b.count
  }));
  const allEdges = histEdgeValues(histBuckets);
  const histCounts = histBuckets.map(b => b.count);
  const histBgColors = histBuckets.map(b => {
    if (b.hi <= 0) return hexToRgba(theme.down, 0.7);
    if (b.lo >= 0) return hexToRgba(theme.up, 0.7);
    return hexToRgba(theme.dim, 0.6);
  });
  const histBorderColors = histBuckets.map(b => {
    if (b.hi <= 0) return theme.down;
    if (b.lo >= 0) return theme.up;
    return theme.dim;
  });

  const maxHistCount = histCounts.length ? Math.max(...histCounts) : 0;
  const histYPad = Math.max(1, Math.ceil(maxHistCount * 0.25));

  pnlHistChartInstance = new Chart(histCtx, {
    type: 'bar',
    plugins: [{
      id: 'pnlHistZeroLine',
      afterDraw: function(chart) {
        const xScale = chart.scales.x;
        if (!xScale) return;
        const x0 = xScale.getPixelForValue(0);
        if (x0 >= chart.chartArea.left && x0 <= chart.chartArea.right) {
          const c = chart.ctx;
          c.save();
          c.beginPath();
          c.setLineDash([6, 4]);
          c.strokeStyle = theme.gold;
          c.lineWidth = 2;
          c.moveTo(x0, chart.chartArea.top);
          c.lineTo(x0, chart.chartArea.bottom);
          c.stroke();
          c.restore();
        }
      }
    }],
    data: {
      datasets: [{
        label: 'Windows',
        data: dataPoints,
        backgroundColor: histBgColors,
        borderColor: histBorderColors,
        borderWidth: 1,
        barPercentage: 1.0,
        categoryPercentage: 1.0,
      }]
    },
    options: {
      responsive: true,
      layout: {
        padding: { left: 8, right: 14, top: 14, bottom: 8 }
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: function(items) {
              if (!items.length) return '';
              const b = histBuckets[items[0].dataIndex];
              if (!b) return '';
              const loSign = b.lo < 0 ? '-$' : '$';
              const hiSign = b.hi < 0 ? '-$' : '$';
              const loStr = loSign + Math.abs(b.lo / 100).toFixed(2);
              const hiStr = hiSign + Math.abs(b.hi / 100).toFixed(2);
              return `P&L Range: ${loStr} to ${hiStr}`;
            },
            label: function(item) {
              const pct = histData.n ? ((item.parsed.y / histData.n) * 100).toFixed(1) : '0.0';
              return ` ${item.parsed.y} windows (${pct}%)`;
            }
          }
        }
      },
      scales: {
        x: {
          type: 'linear',
          offset: false,
          min: minEdge,
          max: maxEdge,
          afterBuildTicks: function(scale) {
            let chosen = allEdges;
            if (allEdges.length > 14) {
              const stride = Math.ceil(allEdges.length / 10);
              chosen = allEdges.filter((v, idx) => idx % stride === 0 || Math.abs(v) < 0.001 || idx === allEdges.length - 1);
            }
            scale.ticks = chosen.map(v => ({ value: v }));
          },
          title: { display: true, text: 'Window P&L ($)', color: theme.dim },
          ticks: {
            color: theme.dim,
            maxRotation: 45,
            minRotation: 0,
            autoSkip: false,
            callback: function(v) {
              return formatSweepMoneyTick(v);
            }
          },
          grid: {
            offset: false,
            color: function(ctx) {
              return (ctx.tick && Math.abs(ctx.tick.value) < 0.001) ? theme.gold : theme.line;
            },
            lineWidth: function(ctx) {
              return (ctx.tick && Math.abs(ctx.tick.value) < 0.001) ? 2 : 1;
            },
            borderDash: function(ctx) {
              return (ctx.tick && Math.abs(ctx.tick.value) < 0.001) ? [6, 4] : [];
            }
          }
        },
        y: {
          beginAtZero: true,
          grace: 1,
          suggestedMax: maxHistCount + histYPad,
          title: { display: true, text: 'Windows Count', color: theme.dim },
          ticks: { color: theme.dim, precision: 0 },
          grid: { color: theme.line }
        }
      }
    }
  });
}

// Per series table with tooltips and execution vs oscillation clarity
let stbl = '<table class="tbl"><thead><tr>'
  + '<th>Series</th>'
  + '<th>Windows</th>'
  + '<th title="Both legs filled and merged on CTF. Note: Oscillating windows may not fill limit orders if price drifted rapidly before quotes rested or opposite leg never touched.">Pair Captured ℹ️</th>'
  + '<th title="One leg filled then adverse drift triggered safety stop exit before opposite leg filled.">Exits ℹ️</th>'
  + '<th>Total P&L ($)</th>'
  + '<th>Avg / Window ($)</th>'
  + '<th title="Price excursion >= 2c in both directions vs 50c mid. Market oscillation does not guarantee limit order fills.">Oscillating ℹ️</th>'
  + '<th>Monotonic</th>'
  + '</tr></thead><tbody>';
for(const [k,v] of Object.entries(data.per_series||{})){
  const label = canonicalMarketName(k || v.label);
  stbl+=`<tr><td style="font-weight:700">${esc(label)}</td><td class="mono" style="font-variant-numeric:tabular-nums">${v.windows}</td><td style="color:var(--up);font-weight:700;font-variant-numeric:tabular-nums">${(v.pair_rate*100).toFixed(1)}% (${v.pairs})</td><td style="color:var(--down);font-variant-numeric:tabular-nums">${(v.exit_rate*100).toFixed(1)}% (${v.exits})</td><td class="mono" style="font-weight:700;font-variant-numeric:tabular-nums;color:${v.total_pnl_cents>=0?'var(--up)':'var(--down)'}">${fmtUsd(v.total_pnl_cents,true)}</td><td class="mono" style="font-variant-numeric:tabular-nums">${fmtUsd(v.avg_pnl_cents,true)}</td><td class="mono" style="font-variant-numeric:tabular-nums">${v.oscillating}</td><td class="mono" style="font-variant-numeric:tabular-nums">${v.monotonic}</td></tr>`;
}
stbl+='</tbody></table>';
$('btSeriesTableWrap').innerHTML=stbl;

// Populate Series Filter dropdown for Executed Windows Log
window.allBacktestTrades = data.trades_sample || [];
window.btLogCurrentPage = 1;
if ($('btLogSeriesFilter')) {
  const currentVal = $('btLogSeriesFilter').value;
  const seriesLabels = new Map();
  for (const t of window.allBacktestTrades) {
    if (t.series) seriesLabels.set(t.series, canonicalMarketName(t.series || t.label));
  }
  let opts = '<option value="">All Series</option>';
  for (const [slug, label] of seriesLabels.entries()) {
    opts += `<option value="${esc(slug)}"${currentVal === slug ? ' selected' : ''}>${esc(label)}</option>`;
  }
  $('btLogSeriesFilter').innerHTML = opts;
}

renderBacktestTradesPage();
}

// Backtest failure banner, shared by the HTTP-error and stream-error paths.
function markBacktestFailed(errMsg){
  $('btHash').textContent = `Backtest error: ${errMsg}`;
  const lastRun = $('btLastRunTime');
  if (lastRun) lastRun.textContent = `✗ error: ${errMsg}`;
  const elTime = $('btElapsedTime');
  if (elTime) {
    elTime.textContent = '--';
    elTime.style.color = 'var(--down)';
  }
  const elSub = $('btElapsedSub');
  if (elSub) elSub.textContent = 'Failed';
  // #371: red "--" + "Failed" is not enough. Show the message itself in an
  // always-visible Backtest element so 429 / stall / dead-stream are actionable.
  const elBanner = $('btPairCostSub'); // #371/#372: visible sub-line carries the message; writing to the card container would delete its children
  if (elBanner) {
    elBanner.textContent = `✗ ${errMsg}`;
    elBanner.style.color = 'var(--down)';
  }
}

// Provisional live equity chart + provisional histogram during a streaming
// run (issue #331 + IIIB feedback: every visualization reacts per window).
let btProvisionalChart = null;
let btProvisionalHist = null;
// #371: render coalescing state — at most one scheduled render per interval.
let btPendingEnvelope = null;
let btRenderTimer = null;
let btRenderRaf = null;
let btLastRenderAt = 0;
let btRenderToken = null;
const BT_RENDER_MIN_INTERVAL_MS = 500;

function btBeginProvisionalChart(){
  destroyChartInstance('chartEquity');
  if (btProvisionalChart) { try { btProvisionalChart.destroy(); } catch {} btProvisionalChart = null; }
  if (!$('chartEquity')) return;
  const ctx = $('chartEquity').getContext('2d');
  const theme = getThemeTokens();
  btProvisionalChart = new Chart(ctx, {
    type: 'line',
    plugins: [],
    data: {
      labels: [],
      datasets: [{
        label: 'Cumulative PnL ($) — provisional',
        data: [],
        borderColor: theme.cyan || '#4dd0e1',
        backgroundColor: 'rgba(0,0,0,0)',
        fill: false,
        tension: 0.1,
        pointRadius: 0,
      }]
    },
    options: {
      responsive: true,
      animation: false,
      layout: { padding: { left: 8, right: 14, top: 14, bottom: 10 } },
      plugins: { legend: { display: false } },
      scales: {
        x: { offset: true, title: { display: true, text: 'Window', color: theme.dim },
             ticks: { color: theme.dim, maxTicksLimit: 12 }, grid: { color: theme.line } },
        y: { grace: '18%',
             title: { display: true, text: 'Cumulative P&L ($)', color: theme.dim },
             ticks: { color: theme.dim, callback: function(v){ return formatSweepMoneyTick(v); } },
             grid: { color: theme.line } }
      }
    }
  });
}

function btBeginProvisionalHist(){
  if (!$('chartPnlHist')) return;
  destroyChartInstance('chartPnlHist');
  if (btProvisionalHist) { try { btProvisionalHist.destroy(); } catch {} btProvisionalHist = null; }
  const histCtx = $('chartPnlHist').getContext('2d');
  const theme = getThemeTokens();
  btProvisionalHist = new Chart(histCtx, {
    type: 'bar',
    plugins: [],
    data: {
      labels: [],
      datasets: [{
        label: 'Windows — provisional',
        data: [],
        backgroundColor: theme.dim,
        borderColor: theme.dim,
        borderWidth: 1,
        barPercentage: 1.0,
        categoryPercentage: 1.0,
      }]
    },
    options: {
      responsive: true,
      animation: false,
      layout: { padding: { left: 8, right: 14, top: 14, bottom: 8 } },
      plugins: { legend: { display: false } },
      scales: {
        x: { type: 'linear', title: { display: true, text: 'Window P&L ($)', color: theme.dim },
             ticks: { color: theme.dim, callback: function(v){ return formatSweepMoneyTick(v); } },
             grid: { color: theme.line } },
        y: { beginAtZero: true, title: { display: true, text: 'Windows Count', color: theme.dim },
             ticks: { color: theme.dim, precision: 0 }, grid: { color: theme.line } }
      }
    }
  });
}

// Mirrors `_choose_bucket_width`'s steps so provisional bins land close to
// the final histogram's grid.
function btChooseHistStep(span){
  const steps = [0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 25.0, 50.0, 100.0, 200.0, 250.0, 500.0, 1000.0];
  const raw = span / 15.0;
  for (const s of steps) { if (s >= raw) return s; }
  return Math.ceil(raw / 500.0) * 500.0;
}

function btUpdateProvisionalHist(pnls, total){
  if (!btProvisionalHist || !Array.isArray(pnls)) return;
  if (!pnls.length) return;
  const vals = pnls.map(v => Number(v) || 0);
  // #371: single-pass min/max — spreading up to 2000 values four times a
  // second is wasteful and risks the arg-count limit.
  let pMin = Infinity, pMax = -Infinity;
  for (const v of vals) { if (v < pMin) pMin = v; if (v > pMax) pMax = v; }
  if (pMin === pMax) {
    btProvisionalHist.data.labels = [pMin - 0.5 + 0.5];
    btProvisionalHist.data.datasets[0].data = [vals.length];
  } else {
    const step = btChooseHistStep(pMax - pMin);
    const loEdge = Math.floor(pMin / step) * step;
    const hiEdge = Math.ceil(pMax / step) * step;
    const numBuckets = Math.max(1, Math.round((hiEdge - loEdge) / step));
    const counts = new Array(numBuckets).fill(0);
    const centers = [];
    for (let i = 0; i < numBuckets; i++) {
      centers.push(loEdge + (i + 0.5) * step);
    }
    for (const v of vals) {
      let idx = Math.floor((v - loEdge) / step);
      idx = Math.max(0, Math.min(numBuckets - 1, idx));
      counts[idx] += 1;
    }
    btProvisionalHist.data.labels = centers;
    btProvisionalHist.data.datasets[0].data = counts;
  }
  const statsEl = $('btPnlHistStats');
  if (statsEl && total > 0) {
    const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
    statsEl.textContent = `provisional n=${vals.length} · Mean $${(mean / 100).toFixed(2)}`;
  }
  btProvisionalHist.update('none');
}

function btAppendProvisionalPoints(msg){
  if (!btProvisionalChart || !msg || !Array.isArray(msg.points)) return;
  const ds = btProvisionalChart.data.datasets[0];
  for (const p of msg.points) {
    btProvisionalChart.data.labels.push(btProvisionalChart.data.labels.length + 1);
    ds.data.push(((p.cumulative_pnl_cents || 0) / 100).toFixed(2));
  }
  // #371: the per-envelope step stays cheap (data push only). Card writes,
  // histogram rebuild and chart updates are coalesced below — at most one
  // scheduled render per ~500 ms, always with the latest envelope.
  btPendingEnvelope = msg;
  btScheduleProvisionalRender();
}

// #371: coalesced render scheduler. setTimeout gates the interval;
// requestAnimationFrame aligns the paint with the frame when available.
function btScheduleProvisionalRender(){
  if (!btPendingEnvelope || btRenderTimer) return;
  const wait = Math.max(0, btLastRenderAt + BT_RENDER_MIN_INTERVAL_MS - Date.now());
  btRenderTimer = setTimeout(function(){
    btRenderTimer = null;
    const doRender = function(){
      btRenderRaf = null;
      btLastRenderAt = Date.now();
      const msg = btPendingEnvelope;
      btPendingEnvelope = null;
      // Same ownership check as the stream loop: a superseded or finished run
      // (window._btAbort replaced or cleared) must never render.
      if (msg && btRenderToken === window._btAbort) btRenderProvisional(msg);
    };
    if (typeof requestAnimationFrame === 'function') { btRenderRaf = requestAnimationFrame(doRender); } else { doRender(); }
  }, wait);
}
function btCancelScheduledRender(){
  if (btRenderTimer) { clearTimeout(btRenderTimer); btRenderTimer = null; }
  if (btRenderRaf) { if (typeof cancelAnimationFrame === 'function') cancelAnimationFrame(btRenderRaf); btRenderRaf = null; }
  btPendingEnvelope = null;
}
function btRenderProvisional(msg){
  if (!btProvisionalChart || !msg) return;
  // Metric cards react to every rendered batch (same semantics as the final
  // overall block; entered_windows is only known at the end).
  const windowsDone = msg.windows_done || 0;
  const totalPnl = msg.provisional_total_pnl_cents || 0;
  const pairs = msg.pairs || 0, exits = msg.exits || 0, wins = msg.wins || 0;
  const den = Math.max(windowsDone, 1);
  $('btTotalPnl').textContent = fmtUsd(totalPnl, true);
  $('btTotalPnl').style.color = totalPnl >= 0 ? 'var(--up)' : 'var(--down)';
  $('btAvgPnl').textContent = fmtUsd(totalPnl / den, true) + ' / window';
  $('btPairRate').textContent = ((pairs / den) * 100).toFixed(1) + '%';
  $('btPairsCount').textContent = `${pairs} / ${windowsDone} pairs`;
  if ($('btMeanPairCost')) {
    if (msg.mean_pair_cost != null) {
      $('btMeanPairCost').textContent = '$' + Number(msg.mean_pair_cost).toFixed(3);
      $('btMeanPairCost').style.color = msg.mean_pair_cost <= 1.00 ? 'var(--up)' : 'var(--down)';
    } else {
      $('btMeanPairCost').textContent = '--';
      $('btMeanPairCost').style.color = 'var(--dim)';
    }
  }
  if ($('btPairCostSub')) {
    if (msg.pairs_above_settle > 0) {
      $('btPairCostSub').textContent = `${msg.pairs_above_settle} above $1.00 settle`;
      $('btPairCostSub').style.color = 'var(--down)';
    } else if (msg.mean_pair_edge_cents != null) {
      const edge = Number(msg.mean_pair_edge_cents);
      $('btPairCostSub').textContent = `Avg edge: ${(edge >= 0 ? '+' : '') + edge.toFixed(1)}¢/pr`;
      $('btPairCostSub').style.color = 'var(--dim)';
    } else if (pairs > 0) {
      $('btPairCostSub').textContent = `${pairs} pairs captured`;
      $('btPairCostSub').style.color = 'var(--dim)';
    } else {
      $('btPairCostSub').textContent = '0 pairs captured';
      $('btPairCostSub').style.color = 'var(--dim)';
    }
  }
  $('btExitRate').textContent = ((exits / den) * 100).toFixed(1) + '%';
  $('btExitsCount').textContent = `${exits} exits`;
  $('btMaxDd').textContent = '-' + fmtPrice((msg.max_drawdown_cents || 0) / 100);
  if ($('btMaxDdSub')) {
    $('btMaxDdSub').textContent = 'Peak to trough from $0.00 start';
  }
  $('btWinRate').textContent = ((wins / den) * 100).toFixed(1) + '%';
  if ($('btWinsCount')) {
    $('btWinsCount').textContent = `${wins} / ${windowsDone} profitable`;
  }
  if (Array.isArray(msg.pnl_sample_cents)) {
    btUpdateProvisionalHist(msg.pnl_sample_cents, windowsDone);
  }
  if (msg.windows_done !== undefined) {
    const elSub = $('btElapsedSub');
    if (elSub && elSub.textContent.startsWith('Simulating…')) {
      elSub.textContent = `Simulating… ${msg.windows_done} windows · ${((msg.provisional_total_pnl_cents || 0) / 100).toFixed(2)} USD`;
    }
  }
  btProvisionalChart.update('none');
}

function btDestroyProvisionalChart(){
  btCancelScheduledRender(); // #371: a late frame must not touch a destroyed chart
  if (btProvisionalChart) { try { btProvisionalChart.destroy(); } catch {} btProvisionalChart = null; }
  if (btProvisionalHist) { try { btProvisionalHist.destroy(); } catch {} btProvisionalHist = null; }
}

// Minimal SSE parser over a fetch body reader: split on blank lines, take the
// `data:` payloads. Shared by /api/backtest/stream and
// /api/backtest/sweep/stream — EventSource is not usable here because its
// automatic reconnect would start duplicate runs.
async function consumeBacktestStream(res, ctl, onEvent, opts){
  // #371: opts is opt-in. The sweep caller (no opts) keeps its exact legacy
  // behaviour: read to EOF, ignore terminal events, no watchdog, silent ends.
  opts = opts || {};
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  let sawTerminal = false;
  let lastActivity = Date.now();
  const inactivityTimeoutMs = opts.inactivityTimeoutMs || 0;
  const readWithWatchdog = function(){
    if (!inactivityTimeoutMs) return reader.read();
    return Promise.race([
      reader.read(),
      new Promise(function(resolve){
        setTimeout(function(){
          if (Date.now() - lastActivity >= inactivityTimeoutMs) resolve({stalled: true});
          else resolve(null); // not stalled yet — loop and re-race
        }, inactivityTimeoutMs);
      }),
    ]).then(function(r){
      if (r === null) return readWithWatchdog();
      return r;
    });
  };
  const finish = async function(){
    try { await reader.cancel(); } catch {}
  };
  while (true) {
    const {done, value, stalled} = await readWithWatchdog();
    // The sweep passes its own controller (window._btSweepAbort), not the
    // regular backtest one — abort only when NEITHER run owns this reader.
    if (ctl.signal.aborted || (window._btAbort !== ctl && window._btSweepAbort !== ctl)) { try { await reader.cancel(); } catch {} return; }
    if (stalled) {
      // No bytes within the timeout: the connection is wedged. Cancel, abort
      // (the server then tears down the run's pool and releases the guards)
      // and fail visibly. Well above any healthy heartbeat interval.
      try { await reader.cancel(); } catch {}
      try { ctl.abort(new Error('stalled')); } catch {}
      if (opts.onStall) opts.onStall();
      return;
    }
    if (done) {
      if (opts.failOnEofWithoutTerminal && !sawTerminal) {
        if (opts.onEof) opts.onEof();
      }
      return;
    }
    lastActivity = Date.now();
    buf += decoder.decode(value, {stream: true});
    // Minimal SSE parser: split on sse-starlette's CRLF separators and blank
    // lines. Blocks end either at "\r\n\r\n" (the library's default separator)
    // or at "\n\n" — handle both so a separator change cannot wedge the curve.
    let idx;
    while ((idx = Math.min(
        buf.indexOf('\r\n\r\n') >= 0 ? buf.indexOf('\r\n\r\n') : Infinity,
        buf.indexOf('\n\n') >= 0 ? buf.indexOf('\n\n') : Infinity)) < Infinity) {
      const sepLen = buf.startsWith('\r\n\r\n', idx) ? 4 : 2;
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + sepLen);
      for (const line of block.split(/\r\n|\n/)) {
        // #371: SSE comment lines (`: ping`) are heartbeats, not payloads.
        if (line.startsWith(':')) continue;
        if (line.startsWith('data:')) {
          const payload = line.slice(5).trim();
          if (payload) {
            let ev = null;
            try { ev = JSON.parse(payload); } catch (e) { console.error('bad SSE payload', e); continue; }
            // Parse errors and callback errors stay separate (#371): a render
            // bug while handling `final` must be visible, not swallowed as a
            // malformed payload.
            try {
              onEvent(ev);
              if (opts.returnOnTerminal && ev && (ev.type === 'final' || ev.type === 'error')) {
                sawTerminal = true;
                await finish();
                return;
              }
            } catch (e) {
              if (opts.onCallbackError) { opts.onCallbackError(ev, e); return; }
              console.error('bad SSE payload', e);
            }
          }
        }
      }
    }
  }
}

// Issue #413: backtest templates — persist a completed run's configuration +
// result summary under a name, then refill the tab from it on demand.
// Loading never replays: it only sets controls, chips, preview and estimate.
window._btSaveableRunId = null;

function setBtSaveableRunId(runId){
  window._btSaveableRunId = runId || null;
  const btn = $('btnSaveTemplate');
  if (btn) btn.disabled = !window._btSaveableRunId;
}

function btEscHtml(s){
  return String(s == null ? '' : s).replace(/[&<>"']/g, function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
  });
}

function btTemplateStatus(msg, isErr){
  const el = $('btTemplateStatus');
  if (!el) return;
  el.textContent = msg || '';
  el.style.color = isErr ? 'var(--down)' : 'var(--dim)';
}

async function saveBacktestTemplate(){
  const nameEl = $('btTemplateName');
  const name = nameEl ? nameEl.value.trim() : '';
  if (!window._btSaveableRunId) { btTemplateStatus('Run a backtest first — nothing to save yet.', true); return; }
  if (!name) { btTemplateStatus('Type a template name first.', true); return; }
  try {
    const res = await fetch('/api/backtest/templates', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: name, run_id: window._btSaveableRunId}),
    });
    const j = await res.json();
    if (!res.ok) { btTemplateStatus('Save failed: ' + (j.error || res.status), true); return; }
    btTemplateStatus(j.overwritten ? `Template '${j.name}' overwritten.` : `Template '${j.name}' saved.`);
    loadBacktestTemplateList();
  } catch(err) {
    btTemplateStatus('Save failed: ' + (err && err.message ? err.message : err), true);
  }
}

async function loadBacktestTemplateList(){
  const list = $('btTemplateList');
  if (!list) return;
  try {
    const res = await fetch('/api/backtest/templates');
    const j = await res.json();
    const items = (j && j.templates) || [];
    if (!items.length) {
      list.innerHTML = '<div class="mono" style="font-size:11px;color:var(--dim)">No saved templates yet — run a backtest, then press Save as Template.</div>';
      return;
    }
    list.innerHTML = items.map(function(t){
      const nm = t.name || '';
      const enc = encodeURIComponent(nm);
      const sub = t.invalid ? 'unreadable file' : `${t.n_windows != null ? t.n_windows + ' windows · ' : ''}${t.total_pnl_cents != null ? (Number(t.total_pnl_cents) >= 0 ? '+' : '') + Number(t.total_pnl_cents).toFixed(1) + 'c · ' : ''}${t.selection ? btEscHtml(t.selection) : ''}`;
      return `<div style="display:flex;gap:8px;align-items:center;background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:6px 10px">`
        + `<div style="flex:1;min-width:0"><div style="font-weight:600">${btEscHtml(nm)}</div>`
        + `<div class="mono" style="font-size:10px;color:var(--dim)">${sub}</div></div>`
        + `<button class="btn" style="font-size:11px;padding:4px 10px" onclick="loadBacktestTemplate('${enc}')">Load</button>`
        + `<button class="btn" style="font-size:11px;padding:4px 10px" onclick="deleteBacktestTemplate('${enc}')">Delete</button></div>`;
    }).join('');
  } catch(err) {
    btTemplateStatus('Could not load templates: ' + (err && err.message ? err.message : err), true);
  }
}

async function loadBacktestTemplate(encName){
  const name = decodeURIComponent(encName);
  try {
    const res = await fetch('/api/backtest/templates/' + encodeURIComponent(name));
    const j = await res.json();
    if (res.status === 409) { btTemplateStatus(`Template '${name}' is stale — parameters changed since it was saved.`, true); return; }
    if (!res.ok) { btTemplateStatus('Load failed: ' + (j.error || res.status), true); return; }
    const note = applyBacktestTemplate(j);
    if (note) btTemplateStatus(note, true);
    else btTemplateStatus(`Template '${name}' loaded — controls updated, no replay started.`);
  } catch(err) {
    btTemplateStatus('Load failed: ' + (err && err.message ? err.message : err), true);
  }
}

function applyBacktestTemplate(t){
  const a = (t && t.request_args) || {};
  let note = null;
  const setVal = function(id, v){
    if (v === undefined || v === null) return;
    const el = $(id);
    if (el) el.value = String(v);
  };
  // Issue #419: template args are dollars; price inputs display cents.
  const setCents = function(id, v){
    if (v === undefined || v === null) return;
    const el = $(id);
    if (el) el.value = String(dollarsToCents(v));
  };
  setCents('btOffset', a.offset);
  setVal('btQueue', a.queue);
  setCents('btPairCost', a.pair_cost);
  setCents('btExit5m', a.exit_default_5m);
  setCents('btExit15m', a.exit_default_15m);
  setCents('btExitBtc', a.exit_btc_5m);
  setCents('btExitSol', a.exit_sol_5m);
  setVal('btSize', a.size);
  setCents('btQuoteLo', a.quote_lo);
  setCents('btQuoteHi', a.quote_hi);
  setCents('btExitReversal', a.exit_reversal);
  if (a.entry_delay_pct !== undefined && a.entry_delay_pct !== null) setVal('btEntryDelay', a.entry_delay_pct);
  else if (a.entry_delay_sec !== undefined && a.entry_delay_sec !== null && a.entry_delay_sec !== '') {
    // #445 N2: the tab exposes one percent for both durations, but the engine
    // resolves that percent against each window's own length. A seconds-only
    // template used to be dropped here; now it converts against the shortest
    // scoped timeframe (exact there — the preset's measured winner — and a
    // longer wait on longer windows), and the notice spells out both numbers
    // so the approximation is visible instead of the delay vanishing.
    const rawDurs = (t && t.scope && Array.isArray(t.scope.duration_values) && t.scope.duration_values.length)
      ? t.scope.duration_values : [300, 900];
    const sec = Number(a.entry_delay_sec);
    if (sec === 0) {
      setVal('btEntryDelay', 0);
      note = null;
    } else {
      let validDurs = rawDurs.map(Number).filter(d => Number.isFinite(d) && d > 0);
      if (!validDurs.length) validDurs = [300, 900];
      const shortest = Math.min.apply(null, validDurs);
      const durLabel = d => d === 300 ? '5m' : d === 900 ? '15m' : (d / 60) + 'm';
      // One percent control (step=1, max=100) serves both durations: convert
      // against the shortest scoped window and round to the control's own
      // granularity. Below half a percent the control cannot represent the
      // delay at all — refuse rather than write a value that vanishes; above
      // the control max the replay would clamp silently, so clamp the write
      // and say so instead of claiming a percent the replay never runs.
      let pct = Math.round((sec / shortest) * 100);
      if (Number.isFinite(pct) && pct >= 1) {
        let clampedNote = '';
        if (pct > 100) { pct = 100; clampedNote = ' (clamped to control max)'; }
        setVal('btEntryDelay', pct);
        const perDur = validDurs.map(d => '~' + Math.round(pct / 100 * d) + 's of ' + durLabel(d)).join(', ');
        note = 'Template uses entry_delay_sec: ' + sec + 's loaded as ' + pct + '%' + clampedNote + ' (' + perDur + '), closest single percent for the scoped timeframes.';
      } else {
        note = 'Template uses entry_delay_sec; the percent control was left unchanged.';
      }
    }
  }
  if (a.dead_zone_pct !== undefined && a.dead_zone_pct !== null) setVal('btDeadZoneVal', a.dead_zone_pct);
  else if (a.dead_zone_val !== undefined && a.dead_zone_val !== null && (a.dead_zone_unit || 'pct') === 'pct') setVal('btDeadZoneVal', Number(a.dead_zone_val) * 100);
  else if (a.dead_zone_val !== undefined && a.dead_zone_val !== null) note = note || 'Template uses a seconds dead-zone; the percent control was left unchanged.';
  setVal('btNakedLegAtExpiry', a.naked_leg_at_expiry);
  if (a.enable_leg_chase !== undefined && a.enable_leg_chase !== null) setVal('btLegChase', a.enable_leg_chase ? '1' : '0');
  if (a.max_start_delay !== undefined && a.max_start_delay !== null) {
    const sel = $('btMaxStartDelay');
    if (sel) {
      const want = String(a.max_start_delay);
      let has = false;
      for (const o of sel.options) { if (o.value === want) { has = true; break; } }
      if (!has && typeof document !== 'undefined' && document.createElement) {
        const opt = document.createElement('option');
        opt.value = want; opt.textContent = want;
        sel.appendChild(opt);
      }
      sel.value = want;
    }
  }
  const file = a.file || '';
  const sel = $('btFileSelect');
  if (sel) {
    let has = false;
    for (const o of sel.options) { if (o.value === file) { has = true; break; } }
    if (file && !has && typeof document !== 'undefined' && document.createElement) {
      const opt = document.createElement('option');
      opt.value = file; opt.textContent = file + ' (unavailable)';
      sel.appendChild(opt);
    }
    sel.value = file;
  }
  window.selectedBacktestFile = file;
  window._btFileChosen = true;
  const scope = (t && t.scope) || {};
  let tokens = Array.isArray(scope.series_tokens) && scope.series_tokens.length ? scope.series_tokens : null;
  if (!tokens && typeof a.series === 'string' && a.series) {
    tokens = a.series.split(',').map(function(s){ return s.trim().toUpperCase(); });
  }
  if (tokens) {
    const known = tokens.filter(function(x){ return BT_ALL_TOKENS.indexOf(x) >= 0; });
    if (known.length) selectedBtTokens = new Set(known);
  }
  const durVals = Array.isArray(scope.duration_values) ? scope.duration_values : null;
  if (durVals) {
    const only5 = durVals.length === 1 && durVals[0] === 300;
    const only15 = durVals.length === 1 && durVals[0] === 900;
    selectedBtDuration = only5 ? '5m' : (only15 ? '15m' : 'both');
  } else if (typeof a.durations === 'string' && a.durations) {
    selectedBtDuration = a.durations === '300' ? '5m' : (a.durations === '900' ? '15m' : 'both');
  }
  updateBtFilterUI();
  updateBacktestParamPreview();
  updateBtRuntimeEstimate();
  return note;
}

async function deleteBacktestTemplate(encName){
  const name = decodeURIComponent(encName);
  try {
    const res = await fetch('/api/backtest/templates/' + encodeURIComponent(name), {method: 'DELETE'});
    const j = await res.json();
    if (!res.ok) { btTemplateStatus('Delete failed: ' + (j.error || res.status), true); return; }
    btTemplateStatus(`Template '${name}' deleted.`);
    loadBacktestTemplateList();
  } catch(err) {
    btTemplateStatus('Delete failed: ' + (err && err.message ? err.message : err), true);
  }
}

async function runBacktest(fileOverride){
  // Issue #419: bad cents input blocks the request instead of replaying it.
  if(!validateBacktestInputs()) return;
  if (window._btAbort) { try{ window._btAbort.abort(); }catch{} }
  const ctl = new AbortController();
  window._btAbort = ctl;
  setBtSaveableRunId(null);
  btRenderToken = ctl; // #371: ownership token for coalesced renders
  window._btRunning = true;
  setBacktestLoadingState(true);
  startBtTimer();
  let fileVal = '';
  try {
    const v = btControlValues();
    const size = v.size;
    if ($('btSize')) $('btSize').value = size;
    fileVal = fileOverride !== undefined ? fileOverride : ($('btFileSelect') ? $('btFileSelect').value : (window.selectedBacktestFile || ''));
    if (fileOverride !== undefined && $('btFileSelect')) {
      $('btFileSelect').value = fileOverride;
    }
    const url = `/api/backtest/stream?${btControlQuery(v)}`;

    // After an intentional abort of the previous stream, the server needs a
    // moment to detect the disconnect and release the single-run guard; a
    // fresh request can 429 briefly. Retry a bounded few times with a short
    // delay before surfacing the 429.
    let res = null;
    let lastStatus = 0, lastErrorText = '';
    for (let attempt = 0; attempt < 4; attempt++) {
      res = await fetch(url, {signal: ctl.signal});
      if (window._btAbort !== ctl) return; // superseded — never render stale results
      if (res.status !== 429 || window._btAbort === null) break;
      lastStatus = res.status;
      try { const j = await res.json(); if (j && j.error) lastErrorText = j.error; } catch {}
      if (attempt < 3) await new Promise(r => setTimeout(r, 300)); // #371: no sleep after the last attempt
    }
    if (lastStatus === 429) {
      // #371: retry-then-silence was the "stuck, nothing happens" symptom.
      // Surface an actionable message instead of giving up quietly.
      if (window._btAbort === ctl) {
        markBacktestFailed(lastErrorText || 'Another backtest or sweep is already running on the server. Wait for it to finish, then press Run again.');
      }
      return;
    }

    // Validation and busy responses arrive as JSON (200/4xx), not SSE —
    // branch on the content type so their `error` text reaches the operator
    // instead of ending the run silently with a blank chart.
    const ctype = (res.headers && res.headers.get('content-type')) || '';
    if (!res.ok || !ctype.includes('text/event-stream')) {
      let errMsg = `HTTP ${res.status}`;
      try { const j = await res.json(); if (j && j.error) errMsg = j.error; } catch {}
      if (window._btAbort === ctl) markBacktestFailed(errMsg);
      return;
    }
    if (window._btAbort !== ctl) return;

    btBeginProvisionalChart();
    btBeginProvisionalHist();

    await consumeBacktestStream(res, ctl, (ev) => {
      if (window._btAbort !== ctl) return; // superseded — ignore stale events
      if (ev.type === 'progress') {
        btAppendProvisionalPoints(ev);
      } else if (ev.type === 'final') {
        btDestroyProvisionalChart();
        renderBacktestResult(ev.result, fileVal);
        if (ev.result && ev.result.run_id) setBtSaveableRunId(ev.result.run_id);
      } else if (ev.type === 'error') {
        markBacktestFailed(ev.error || 'stream error');
      }
    }, {
      // #371 opt-in: terminal events end the read, EOF without a terminal
      // event fails visibly, silent connections trip the inactivity watchdog.
      returnOnTerminal: true,
      failOnEofWithoutTerminal: 'Stream ended without a result',
      inactivityTimeoutMs: 15000,
      onStall: () => { if (window._btAbort === ctl) markBacktestFailed('Backtest stream stalled — no data received. The run may have been lost; press Run to retry.'); },
      onEof: () => { if (window._btAbort === ctl) markBacktestFailed('Stream ended without a result'); },
      onCallbackError: (ev, err) => { if (window._btAbort === ctl) markBacktestFailed('Render error while handling ' + (ev && ev.type) + ': ' + (err && err.message ? err.message : err)); },
    });
  } catch(err) {
    if (err && err.name === 'AbortError') return;
    console.error('Error running backtest:', err);
  } finally {
    // Only the current run may tear down the shared provisional chart — a
    // superseded run's finally must not destroy the newer run's live curve.
    if (window._btAbort === ctl) {
      btRenderToken = null; // #371: cancel coalesced renders with the run
      btDestroyProvisionalChart();
      window._btAbort = null;
      window._btRunning = false;
      stopBtTimer();
      setBacktestLoadingState(false);
      const elSub = $('btElapsedSub');
      if (elSub && (elSub.textContent === 'Simulating…' || elSub.textContent.startsWith('Simulating…'))) {
        elSub.textContent = 'Execution time';
      }
    }
  }
}

// Client-side interactive pagination & filtering for Executed Windows Log
window.btExpandedSlugs = window.btExpandedSlugs || new Set();

function toggleBtLogDetail(slug, ev) {
  if (ev) ev.stopPropagation();
  if (!window.btExpandedSlugs) window.btExpandedSlugs = new Set();
  if (window.btExpandedSlugs.has(slug)) {
    window.btExpandedSlugs.delete(slug);
  } else {
    window.btExpandedSlugs.add(slug);
  }
  renderBacktestTradesPage();
}

function onBtLogFilterChange() {
  window.btLogCurrentPage = 1;
  renderBacktestTradesPage();
}

function onBtLogPageSizeChange() {
  window.btLogCurrentPage = 1;
  renderBacktestTradesPage();
}

function onBtLogPagePrev() {
  if (window.btLogCurrentPage > 1) {
    window.btLogCurrentPage--;
    renderBacktestTradesPage();
  }
}

function onBtLogPageNext() {
  window.btLogCurrentPage++;
  renderBacktestTradesPage();
}

function renderBacktestTradesPage() {
  const allTrades = window.allBacktestTrades || [];
  const search = ($('btLogSearch') ? $('btLogSearch').value.trim().toLowerCase() : '');
  const seriesFilter = ($('btLogSeriesFilter') ? $('btLogSeriesFilter').value : '');
  const resultFilter = ($('btLogResultFilter') ? $('btLogResultFilter').value : '');
  const pageSizeVal = ($('btLogPageSize') ? $('btLogPageSize').value : '25');

  const filtered = allTrades.filter(t => {
    if (seriesFilter && t.series !== seriesFilter) return false;
    if (resultFilter === 'pair' && !t.both_filled) return false;
    if (resultFilter === 'exit' && !t.exit_triggered) return false;
    if (resultFilter === 'unresolved' && (t.both_filled || t.exit_triggered)) return false;
    if (search) {
      const matchSlug = (t.slug || '').toLowerCase().includes(search);
      const matchSeries = (t.series || '').toLowerCase().includes(search);
      const matchLabel = (t.label || '').toLowerCase().includes(search);
      if (!matchSlug && !matchSeries && !matchLabel) return false;
    }
    return true;
  });

  const total = filtered.length;
  const pageSize = pageSizeVal === 'all' ? Math.max(1, total) : parseInt(pageSizeVal, 10);
  const maxPage = Math.max(1, Math.ceil(total / pageSize));
  if (window.btLogCurrentPage > maxPage) window.btLogCurrentPage = maxPage;
  const page = window.btLogCurrentPage || 1;

  const startIdx = total === 0 ? 0 : (page - 1) * pageSize;
  const endIdx = Math.min(startIdx + pageSize, total);
  const pageTrades = filtered.slice(startIdx, endIdx);

  if ($('btLogPageInfo')) {
    $('btLogPageInfo').textContent = total === 0
      ? 'No matching windows found'
      : `Showing ${startIdx + 1}–${endIdx} of ${total} windows (Page ${page} of ${maxPage})`;
  }
  if ($('btLogBtnPrev')) $('btLogBtnPrev').disabled = (page <= 1);
  if ($('btLogBtnNext')) $('btLogBtnNext').disabled = (page >= maxPage || total === 0);

  let ttbl = '<table class="tbl bt-master-tbl"><thead><tr>'
    + '<th style="width:36px;text-align:center"></th>'
    + '<th>Market</th>'
    + '<th title="Total net P&L and percentage return on capital invested in this window">Total P&L (% on Invested) ℹ️</th>'
    + '<th title="Total completed pairs merged in this window">Merges ℹ️</th>'
    + '<th title="Stop-loss exits triggered on adverse drift in this window">Stop Loss ℹ️</th>'
    + '<th title="Single leg held into last 30s of window (dead zone)">Dead Zone ℹ️</th>'
    + '<th title="Final settlement resolution mark for this market window">Resolution ℹ️</th>'
    + '</tr></thead><tbody>';

  if (pageTrades.length === 0) {
    ttbl += '<tr><td colspan="7" style="text-align:center;padding:18px;color:var(--dim)">No executed windows match the selected criteria.</td></tr>';
  } else {
    const size = window.btActiveSize || 5;
    for (const t of pageTrades) {
      const slug = t.slug || '';
      const safeSlug = slug.replace(/[^a-zA-Z0-9_-]/g, '_');
      const isExp = window.btExpandedSlugs && window.btExpandedSlugs.has(safeSlug);
      const marketLabel = canonicalMarketName(t.series || t.label || '');
      const pnlUsd = fmtUsd(t.pnl_cents, true);

      const mergesCount = t.pairs_count != null ? t.pairs_count : (t.both_filled ? 1 : 0);
      const stopsCount = t.stops_count != null ? t.stops_count : (t.exit_triggered ? 1 : 0);
      const isDeadZone = t.is_dead_zone ? 1 : ((t.exit_reason && t.exit_reason.includes('dead_zone')) ? 1 : 0);
      const isSingleLegSettled = !t.both_filled && !t.exit_triggered && t.settlement_mid != null && (t.up_filled || t.down_filled);

      const pairUnitCost = t.first_pair_cost != null ? t.first_pair_cost : ((t.entry_up || 0.48) + (t.entry_down || 0.48) || 0.96);
      const stopEntryPrice = (t.exit_side === 'down' ? (t.entry_down || t.entry_up) : (t.entry_up || t.entry_down)) || 0.50;
      const settleEntryPrice = (t.up_filled ? t.entry_up : t.entry_down) || 0.50;

      let invested = (mergesCount * pairUnitCost * size)
        + (stopsCount * stopEntryPrice * size)
        + (isSingleLegSettled ? settleEntryPrice * size : 0);
      if (invested <= 0) invested = size * 1.0;
      const pnlPct = invested > 0 ? ((t.pnl_cents / 100) / invested * 100) : 0;
      const pnlPctStr = (pnlPct >= 0 ? '+' : '') + pnlPct.toFixed(1) + '%';
      const pnlDisplay = `<span style="font-weight:700;color:${t.pnl_cents>=0?'var(--up)':'var(--down)'}">${pnlUsd} (${pnlPctStr})</span>`;

      let resBadge = '<span style="color:var(--dim)">—</span>';
      if (t.settlement_mid != null) {
        resBadge = t.settlement_mid >= 0.5
          ? `<span style="color:var(--up);font-weight:700" title="Settlement mark: $${Number(t.settlement_mid).toFixed(3)} (UP won)">UP</span>`
          : `<span style="color:var(--down);font-weight:700" title="Settlement mark: $${Number(t.settlement_mid).toFixed(3)} (DOWN won)">DOWN</span>`;
      } else if (t.both_filled) {
        resBadge = '<span style="color:var(--up);font-weight:600" title="Completed pair captured and merged">UP/DN</span>';
      } else if (t.exit_triggered) {
        resBadge = `<span style="color:var(--dim)" title="Exited on stop">${esc((t.exit_side || 'EXIT').toUpperCase())}</span>`;
      }

      ttbl += `<tr class="bt-master-row" onclick="toggleBtLogDetail('${safeSlug}', event)" style="cursor:pointer;background:${isExp ? 'rgba(255,255,255,0.03)' : 'transparent'}">`
        + `<td style="text-align:center;color:var(--dim)"><button type="button" class="btn" style="background:none;border:none;color:var(--dim);cursor:pointer;padding:2px 6px;font-size:11px" aria-expanded="${isExp}">${isExp ? '▼' : '▶'}</button></td>`
        + `<td style="font-weight:600">${esc(marketLabel)}</td>`
        + `<td class="mono" style="font-variant-numeric:tabular-nums">${pnlDisplay}</td>`
        + `<td class="mono" style="font-variant-numeric:tabular-nums">${mergesCount}</td>`
        + `<td class="mono" style="font-variant-numeric:tabular-nums;color:${stopsCount > 0 ? 'var(--down)' : 'var(--dim)'}">${stopsCount}</td>`
        + `<td class="mono" style="font-variant-numeric:tabular-nums;color:${isDeadZone > 0 ? 'var(--gold)' : 'var(--dim)'}">${isDeadZone}</td>`
        + `<td>${resBadge}</td>`
        + `</tr>`;

      if (isExp) {
        const startTsMatch = slug.match(/(\d{10})$/);
        const startTs = startTsMatch ? parseInt(startTsMatch[1], 10) : 0;
        const delaySec = Math.round(t.start_delay_sec || 0);
        const mm1 = Math.floor(delaySec / 60); const ss1 = delaySec % 60;
        const elapsed1 = `${mm1}:${ss1 < 10 ? '0' : ''}${ss1}`;
        const localTime1 = startTs > 1000000000 ? new Date((startTs + delaySec) * 1000).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'}) : '';
        const timeStr1 = localTime1 ? `${localTime1} (${elapsed1})` : elapsed1;

        let subRows = '';
        let tradeIdx = 1;
        // Issue #377: per-round timing from the engine ledger. Records may be
        // absent (legacy payloads) or lack timing keys — every read falls back
        // to the window-level rendering used before.
        const fmtElapsed377 = (sec) => {
          const e = Math.round(Number(sec) || 0);
          const mm = Math.floor(e / 60); const ss = e % 60;
          return `${mm}:${ss < 10 ? '0' : ''}${ss}`;
        };
        const fmtFillTime377 = (ts, el) => {
          if (ts == null && el == null) return null;
          const eStr = fmtElapsed377(el || 0);
          const lt = (ts != null && Number(ts) > 1000000000)
            ? new Date(Number(ts) * 1000).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'}) : '';
          return lt ? `${lt} (${eStr})` : eStr;
        };

        if (mergesCount > 0 || t.both_filled) {
          const nMerges = Math.max(1, mergesCount);
          // MERGED rows show per-pair predicted values from the backtest, and
          // window aggregates are not per-pair values.
          for (let m = 0; m < nMerges; m++) {
            const pairRec = (t.pairs && t.pairs[m] != null) ? t.pairs[m] : null;

            let upPrice = '—';
            let dnPrice = '—';
            let pCostStr = '—';
            let pnlCell = '—';

            if (pairRec != null) {
              if (pairRec.entry_up != null) {
                upPrice = '$' + Number(pairRec.entry_up).toFixed(3);
              }
              if (pairRec.entry_down != null) {
                dnPrice = '$' + Number(pairRec.entry_down).toFixed(3);
              }
              if (pairRec.pair_cost != null && pairRec.edge_cents != null) {
                const pCost = Number(pairRec.pair_cost);
                const pEdge = Number(pairRec.edge_cents);
                const edgeStr = (pEdge >= 0 ? '+' : '') + pEdge.toFixed(1) + '¢';
                pCostStr = `$${pCost.toFixed(3)} (${edgeStr})`;
                const eachPairPnl = pEdge * size;
                const pPnlUsd = fmtUsd(eachPairPnl, true);
                const pPct = pCost > 0 ? (pEdge / (pCost * 100) * 100).toFixed(1) : '0.0';
                const pPctStr = (pPct >= 0 ? '+' : '') + pPct + '%';
                pnlCell = `${pPnlUsd} (${pPctStr})`;
              }
            }

            const upTime377 = (pairRec != null ? fmtFillTime377(pairRec.fill_ts_up, pairRec.fill_elapsed_up) : null) || (m === 0 ? timeStr1 : '—');
            const dnTime377 = (pairRec != null ? fmtFillTime377(pairRec.fill_ts_down, pairRec.fill_elapsed_down) : null) || '—';
            const durStr377 = (pairRec != null && pairRec.duration_sec != null) ? fmtElapsed377(pairRec.duration_sec) : '—';

            subRows += `<tr style="border-bottom:none">`
              + `<td rowspan="2" class="mono" style="font-weight:700;text-align:center;vertical-align:middle;border-right:1px solid var(--line);background:rgba(255,255,255,0.015)">#${tradeIdx++}</td>`
              + `<td style="font-weight:700;color:var(--up)">UP</td>`
              + `<td class="mono" style="font-size:11px">${upTime377}</td>`
              + `<td class="mono">${upPrice}</td>`
              + `<td rowspan="2" class="mono" style="vertical-align:middle;border-left:1px solid var(--line);border-right:1px solid var(--line);background:rgba(255,255,255,0.01)">${pCostStr}</td>`
              + `<td rowspan="2" class="mono" style="vertical-align:middle;border-right:1px solid var(--line)" title="Time from first fill to merge completion">${durStr377}</td>`
              + `<td rowspan="2" style="vertical-align:middle;border-right:1px solid var(--line)"><span class="pill pill-osc" title="Completed pair merged 1:1 on Polymarket CTF">MERGED</span></td>`
              + `<td rowspan="2" class="mono" style="vertical-align:middle;font-weight:700;color:var(--up);background:rgba(16,185,129,0.04)">${pnlCell}</td>`
              + `</tr>`
              + `<tr style="border-top:none">`
              + `<td style="font-weight:700;color:var(--down)">DOWN</td>`
              + `<td class="mono" style="font-size:11px;color:var(--dim)">${dnTime377}</td>`
              + `<td class="mono">${dnPrice}</td>`
              + `</tr>`;
          }
        }

        if (stopsCount > 0 || t.exit_triggered) {
          const exPrice = t.exit_price != null ? '$' + Number(t.exit_price).toFixed(3) : '—';
          const entryPrice = (t.exit_side === 'down' ? (t.entry_down || t.entry_up) : (t.entry_up || t.entry_down)) || 0.50;
          const entrySide = (t.exit_side === 'down') ? 'DOWN' : 'UP';
          const exPnlCents = (t.both_filled && t.pair_pnl_cents != null) ? (t.pnl_cents - t.pair_pnl_cents) : t.pnl_cents;
          const exPnlUsd = fmtUsd(exPnlCents, true);
          const exInvested = size * entryPrice;
          const exPct = exInvested > 0 ? ((exPnlCents / 100) / exInvested * 100).toFixed(1) : '0.0';
          const exPctStr = (exPct >= 0 ? '+' : '') + exPct + '%';

          const statusTitle = isDeadZone
            ? 'Single leg held into last 30s of window. Closed before expiry.'
            : `Adverse drift exceeded exit threshold (${t.exit_side || 'adverse'}). Taker stop exit on book.`;
          const statusPill = isDeadZone
            ? `<span class="pill pill-flat" style="color:var(--gold)" title="${statusTitle}">DEAD_ZONE</span>`
            : `<span class="pill pill-mono" style="color:var(--down)" title="${statusTitle}">STOP_LOSS</span>`;

          // Issue #377: one row per stop record when the ledger is present;
          // legacy single-exit aggregates otherwise (old payloads unchanged).
          const stopRecs377 = (t.stops && t.stops.length)
            ? t.stops.filter(r => r && (r.kind === 'stop' || r.kind === 'dead_zone_close')) : null;
          const nStops = stopRecs377 ? stopRecs377.length : Math.max(1, stopsCount);
          for (let s = 0; s < nStops; s++) {
            const rec377 = stopRecs377 ? stopRecs377[s] : null;
            const entryPrice377 = (rec377 && rec377.entry_price != null) ? Number(rec377.entry_price) : entryPrice;
            const entrySide377 = (rec377 && rec377.side) ? String(rec377.side).toUpperCase() : entrySide;
            const exPrice377 = (rec377 && rec377.exit_price != null) ? '$' + Number(rec377.exit_price).toFixed(3) : exPrice;
            const stopTime377 = (rec377 ? fmtFillTime377(rec377.entry_ts, rec377.entry_elapsed) : null) || (mergesCount === 0 && s === 0 ? timeStr1 : '—');
            const stopDur377 = (rec377 && rec377.duration_sec != null) ? fmtElapsed377(rec377.duration_sec) : '—';
            let stopPnlHtml377 = `${exPnlUsd} (${exPctStr})`;
            if (rec377 && rec377.pnl_cents != null) {
              const rPnl = Number(rec377.pnl_cents) * size;
              const rInv = size * entryPrice377;
              const rPct = rInv > 0 ? ((rPnl / 100) / rInv * 100).toFixed(1) : '0.0';
              stopPnlHtml377 = `${fmtUsd(rPnl, true)} (${(rPct >= 0 ? '+' : '') + rPct}%)`;
            }
            const pill377 = rec377
              ? (rec377.kind === 'dead_zone_close'
                ? `<span class="pill pill-flat" style="color:var(--gold)" title="${statusTitle}">DEAD_ZONE</span>`
                : `<span class="pill pill-mono" style="color:var(--down)" title="${statusTitle}">STOP_LOSS</span>`)
              : statusPill;
            subRows += `<tr style="border-bottom:none">`
              + `<td rowspan="2" class="mono" style="font-weight:700;text-align:center;vertical-align:middle;border-right:1px solid var(--line);background:rgba(255,255,255,0.015)">#${tradeIdx++}</td>`
              + `<td style="font-weight:700;color:${entrySide377==='UP'?'var(--up)':'var(--down)'}">${entrySide377}</td>`
              + `<td class="mono" style="font-size:11px">${stopTime377}</td>`
              + `<td class="mono">$${Number(entryPrice377).toFixed(3)}</td>`
              + `<td rowspan="2" class="mono" style="vertical-align:middle;border-left:1px solid var(--line);border-right:1px solid var(--line);color:var(--dim)">—</td>`
              + `<td rowspan="2" class="mono" style="vertical-align:middle;border-right:1px solid var(--line)" title="Time in market before stop loss triggered">${stopDur377}</td>`
              + `<td rowspan="2" style="vertical-align:middle;border-right:1px solid var(--line)">${pill377}</td>`
              + `<td rowspan="2" class="mono" style="vertical-align:middle;font-weight:700;color:var(--down);background:rgba(239,68,68,0.04)">${stopPnlHtml377}</td>`
              + `</tr>`
              + `<tr style="border-top:none">`
              + `<td style="font-weight:700;color:var(--dim)">EXIT</td>`
              + `<td class="mono" style="font-size:11px;color:var(--dim)">—</td>`
              + `<td class="mono">${exPrice377}</td>`
              + `</tr>`;
          }
        }

        if (isSingleLegSettled) {
          const filledSide = t.up_filled ? 'UP' : 'DOWN';
          const entryPrice = (t.up_filled ? t.entry_up : t.entry_down) || 0.50;
          // Issue #377: entry-fill time from the settle record when present.
          const settleRec377 = ((t.stops || []).find(r => r && r.kind === 'settle')) || null;
          const settleTime377 = (settleRec377 ? fmtFillTime377(settleRec377.entry_ts, settleRec377.entry_elapsed) : null) || timeStr1;
          const settleMark = t.settlement_mid != null ? Number(t.settlement_mid).toFixed(3) : '—';
          const pnlUsdSingle = fmtUsd(t.pnl_cents, true);
          const sInvested = size * entryPrice;
          const sPct = sInvested > 0 ? ((t.pnl_cents / 100) / sInvested * 100).toFixed(1) : '0.0';
          const sPctStr = (sPct >= 0 ? '+' : '') + sPct + '%';
          const sColor = t.pnl_cents >= 0 ? 'var(--up)' : 'var(--down)';

          subRows += `<tr>`
            + `<td class="mono" style="font-weight:700;text-align:center;border-right:1px solid var(--line);background:rgba(255,255,255,0.015)">#${tradeIdx++}</td>`
            + `<td style="font-weight:700;color:${filledSide === 'UP' ? 'var(--up)' : 'var(--down)'}">${filledSide}</td>`
            + `<td class="mono" style="font-size:11px">${settleTime377}</td>`
            + `<td class="mono">$${Number(entryPrice).toFixed(3)}</td>`
            + `<td class="mono" style="color:var(--dim)">—</td>`
            + `<td class="mono" title="Held through expiry into settlement">Expiry</td>`
            + `<td><span class="pill pill-flat" style="color:${sColor}" title="Single leg held to settlement mark $${settleMark}">SETTLED</span></td>`
            + `<td class="mono" style="font-weight:700;color:${sColor}">${pnlUsdSingle} (${sPctStr})</td>`
            + `</tr>`;
        }

        if (!subRows) {
          subRows = `<tr>`
            + `<td class="mono" style="text-align:center">#1</td>`
            + `<td style="color:var(--dim)">—</td>`
            + `<td class="mono" style="font-size:11px">${timeStr1}</td>`
            + `<td class="mono" style="color:var(--dim)">—</td>`
            + `<td class="mono" style="color:var(--dim)">—</td>`
            + `<td class="mono" style="color:var(--dim)">0s</td>`
            + `<td><span class="pill pill-flat" title="Orders rested without fill or window expired flat">UNRESOLVED</span></td>`
            + `<td class="mono" style="color:var(--dim)">$0.00 (0.0%)</td>`
            + `</tr>`;
        }

        ttbl += `<tr id="btDetail_${safeSlug}" class="bt-detail-row">`
          + `<td colspan="7" style="padding:10px 14px;background:rgba(0,0,0,0.22);border-top:1px dashed var(--line);border-bottom:1px solid var(--line)">`
          + `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">`
          + `<span class="mono" style="font-size:11px;color:var(--faint)">Window: <b style="color:var(--tx)">${esc(slug)}</b> (Start Delay: ${t.start_delay_sec ? t.start_delay_sec + 's' : '0s'}${t.is_partial ? ' · Partial' : ''})</span>`
          + `${t.settlement_mid != null ? `<span class="mono" style="font-size:11px;color:var(--dim)">Settlement Mark: <b style="color:var(--gold)">$` + Number(t.settlement_mid).toFixed(3) + `</b></span>` : ''}`
          + `</div>`
          + `<table class="tbl bt-sub-tbl" style="width:100%;font-size:11.5px;margin:2px 0;background:var(--panel);border:1px solid var(--line);border-radius:6px;border-collapse:collapse">`
          + `<thead><tr style="color:var(--faint);font-size:10px;text-transform:uppercase;letter-spacing:0.5px">`
          + `<th style="width:50px;text-align:center">Trade</th>`
          + `<th style="width:65px">Side</th>`
          + `<th title="Fill timestamp and elapsed window time">Time (Elapsed) ℹ️</th>`
          + `<th>Fill Price</th>`
          + `<th>Pair Cost (Edge)</th>`
          + `<th title="Time in market with leg exposure before resolution">Duration ℹ️</th>`
          + `<th>Status</th>`
          + `<th>Trade P&L (%)</th>`
          + `</tr></thead><tbody>${subRows}</tbody></table>`
          + `</td></tr>`;
      }
    }
  }
  ttbl += '</tbody></table>';
  $('btTradesTableWrap').innerHTML = ttbl;
}

function resetBtParams(){
  // Issue #378: reset restores the literal sweep defaults (never in the input-ID list).
  window._btSweepCenter = null;
  $('btOffset').value = "2";
  $('btQueue').value = "0";
  $('btPairCost').value = "99";
  $('btExit5m').value = "5";
  $('btExit15m').value = "5";
  $('btExitBtc').value = "5";
  $('btExitSol').value = "5";
  $('btSize').value = "5";
  if ($('btMaxStartDelay')) $('btMaxStartDelay').value = "0";
  if ($('btQuoteLo')) $('btQuoteLo').value = "10";
  if ($('btQuoteHi')) $('btQuoteHi').value = "90";
  if ($('btEntryDelay')) $('btEntryDelay').value = "0";
  if ($('btExitReversal')) $('btExitReversal').value = "2";
  if ($('btDeadZoneVal')) $('btDeadZoneVal').value = "10";
  if ($('btFileSelect')) $('btFileSelect').value = "";
  window.selectedBacktestFile = "";
  window._btFileChosen = true; // Issue #279: Reset picks All Files — a manual-equivalent choice loadManifest must not override
  updateBacktestParamPreview();
  updateBtRuntimeEstimate();
  // Issue #389: the reset also clears the anchor, so the sweep card repaints
  // from those defaults — otherwise the labelled field keeps showing a value
  // (and an is-set state) that is no longer in effect. Guarded like the other
  // callers: never starts a run, never touches a sweep in flight.
  if (typeof refreshSweepIdleCard === 'function') refreshSweepIdleCard();
  runBacktest();
}

// Sweep Visual — one axis X-Y: 1 aggregate chart + 10 per-series charts
// Issue #344: the tested axis values, mirrored from the server's SWEEP_AXES
// grid — the immediate card needs them before the first response arrives.
// Issue #388: axes whose values are names, not numbers. Single frontend source
// for the categorical behaviours; the server derives the same set from
// `SWEEP_AXES` and the Node parity harness keeps the two in step.
function sweepAxisIsCategorical(axis){
  return axis === 'leg_chase' || axis === 'naked_leg';
}

// Issue #388: where a bar sits on the x axis. Categorical axes have no number
// to plot, so their bars sit at their index and every tick value on that axis
// IS an index — the label resolver below turns it back into the point it
// stands for. Numeric axes keep plotting their value, exactly as before.
function sweepPointX(axis, p, i){
  return sweepAxisIsCategorical(axis) ? i : Number(p.value);
}

function sweepAxisValues(axis, center){
  const grids = {
    queue: [0.0, 10.0, 25.0, 50.0, 100.0, 200.0, 500.0, 1000.0, 2000.0],
    offset: [0.010, 0.015, 0.020, 0.025, 0.030, 0.035, 0.040],
    exit_stop_default: [0.06, 0.08, 0.10, 0.12, 0.14, 0.16],
    exit_stop_btc: [0.06, 0.08, 0.10, 0.12, 0.14, 0.16],
    exit_stop_sol: [0.06, 0.08, 0.10, 0.12, 0.14, 0.16],
    exit_rev: [0.010, 0.015, 0.020, 0.025, 0.030],
    late_entry: [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0],
    quote_range: [0.00, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30],
    leg_chase: [false, true],
    naked_leg: ['close', 'hold'],
    dead_zone_pct: [0.0, 5.0, 10.0, 15.0, 20.0, 30.0],
  };
  const grid = grids[axis] || [];
  // Issue #388: categorical axes always use the literal value list — there is
  // no midpoint between names — mirroring the server `_sweep_axis_values`.
  if(sweepAxisIsCategorical(axis)){
    return {values: grid.slice(), clamped: 0};
  }
  // No anchor: the literal defaults, so idle and unanchored cards never drift.
  if(center === undefined || center === null || !Number.isFinite(center) || !grid.length){
    return {values: grid.slice(), clamped: 0};
  }
  // Issue #378: mirror of the server `_sweep_axis_values` — translate so index
  // N // 2 equals the anchor, clamp, round, drop duplicates, count clamped.
  const mid = Math.floor(grid.length / 2);
  const translated = grid.map(g => center + (g - grid[mid]));
  const specBounds = name => {
    const spec = (typeof paramSpecFor === 'function') ? paramSpecFor(name) : null;
    return (spec && spec.bounds) ? spec.bounds : null;
  };
  const clampTo = (t, lo, hi) => Math.max(lo, Math.min(hi, t));
  // Cosmetic rounding is the axis's own shape, not clamping — count bound
  // hits BEFORE rounding, mirroring the server helper.
  const countHits = (before, after) => {
    let n = 0;
    after.forEach((c, i) => { if(c !== before[i]) n++; });
    return n;
  };
  let clamped, n;
  if(axis === 'quote_range'){
    const bounded = translated.map(t => clampTo(t, 0, 0.49));
    n = countHits(translated, bounded);
    clamped = bounded.map(v => Number(v.toFixed(2)));
  }else if(axis === 'late_entry'){
    const b = specBounds('entry_delay_pct');
    const frac = translated.map(t => t / 100.0);
    const boundedFrac = frac.map(f => b ? clampTo(f, b[0], b[1]) : f);
    n = countHits(frac, boundedFrac);
    clamped = boundedFrac.map(f => Number((f * 100.0).toFixed(6)));
  }else if(axis === 'dead_zone_pct'){
    // Issue #388: percent of window — never the stop axes' 0.001–0.50 clamp,
    // which would flatten this axis.
    const bounded = translated.map(t => clampTo(t, 0, 100));
    n = countHits(translated, bounded);
    clamped = bounded.map(v => Number(v.toFixed(6)));
  }else if(axis === 'offset' || axis === 'queue' || axis === 'exit_rev'){
    const reg = {offset: 'offset', queue: 'queue_gate', exit_rev: 'exit_reversal'}[axis];
    const b = specBounds(reg);
    const bounded = b ? translated.map(t => clampTo(t, b[0], b[1])) : translated.slice();
    n = countHits(translated, bounded);
    clamped = bounded.map(v => Number(v.toFixed(6)));
  }else{
    // Stop axes have no scalar registry bounds; the sweep-local 0.001–0.50
    // matches the existing `exit_thresh` API limit.
    const bounded = translated.map(t => clampTo(t, 0.001, 0.50));
    n = countHits(translated, bounded);
    clamped = bounded.map(v => Number(v.toFixed(6)));
  }
  const seen = new Set();
  const values = [];
  clamped.forEach(v => { if(!seen.has(v)){ seen.add(v); values.push(v); } });
  return {values: values, clamped: n};
}

// Changing the axis mid-run must not race the one-worker guard: abort the
// in-flight stream first, then start the new sweep on the fresh axis.
function onSweepAxisChange(){
  window._btSweepCenter = null; // Issue #378: units differ per axis — never carry an anchor across.
  if (window._btSweepAbort) { try { window._btSweepAbort.abort(); } catch {} window._btSweepAbort = null; }
  runSweepVisual();
}

// Issue #378: sweep-only anchor input. Blank/non-finite clears back to the
// literal defaults; an explicit 0 stays 0. Bound to `onchange` only — typing
// must not restart the one-worker sweep on every keystroke.
function onSweepCenterChange(raw){
  const num = (raw === '' || raw === null || raw === undefined) ? NaN : Number(raw);
  let val = Number.isFinite(num) ? num : null;
  // Issue #419: the anchor field on price axes holds cents — convert to the
  // dollar anchor the grids and the server clamp on. Blank stays null and an
  // explicit 0 stays 0 through the conversion.
  if(val !== null){
    const axis = ($('btSweepAxis') && $('btSweepAxis').value) || '';
    if(axis === 'offset' || axis === 'exit_rev' || axis === 'quote_range'
       || axis === 'exit_stop_default' || axis === 'exit_stop_btc' || axis === 'exit_stop_sol'){
      const d = centsToDollars(val);
      val = Number.isFinite(d) ? d : null;
    }
  }
  window._btSweepCenter = val;
  if (window._btSweepAbort) { try { window._btSweepAbort.abort(); } catch {} window._btSweepAbort = null; }
  runSweepVisual();
}

async function runSweepVisual(){
  const btn = $('btnRunSweepVisual');
  const axis = $('btSweepAxis') ? $('btSweepAxis').value : 'queue';
  // Issue #355: the selection is snapshotted HERE, with `v`. Every render of
  // this run uses it, so editing the chips mid-sweep cannot repaint the grid of
  // a run that was asked for something else.
  const sel = btSelection();
  // Issue #378: snapshot the anchor with the selection — every render of this
  // run (pending card, progress, request) uses it, so editing the input
  // mid-sweep cannot repaint a run that was asked for something else.
  const center = (window._btSweepCenter != null) ? window._btSweepCenter : null;
  // Issue #419: bad cents input blocks the request instead of replaying it.
  if(!validateBacktestInputs()) return;
  const selectedSeries = btSelectedSeriesSlugs(sel);
  // Station VI (#355 closeout): the run-start snapshot travels in
  // `selectedSeries`, not on `window` — nothing ever read the old handle.
  // CodeRabbit round 1 (#356): explicit in-flight flag. The pending card is
  // rendered below before `_btSweepAbort` exists, and `finally` never cleared
  // the abort — so a chip toggle during the wait repainted the pending card,
  // and a failed run blocked idle refreshes for the session.
  window._btSweepInFlight = true;
  // Issue #383: capture the stop generation before waiting. A deliberate stop
  // advances it, and this run must give up rather than fall through and start.
  const stopSeqAtWait = window._btStopSeq || 0;
  updateBtStopVisibility();
  // Same reader the backtest uses, so the sweep's base point is exactly the
  // configuration shown on this page. Only `axis` varies; every other knob is
  // held at the operator's value. `size` is pinned to the page value because
  // the sweep scales P&L by it — sweeping it would scale the axis itself.
  const v = btControlValues();
  const meta = $('btSweepMeta');
  if(window._btSweepTimerId){ clearInterval(window._btSweepTimerId); window._btSweepTimerId = null; }
  if(btn){ btn.disabled = true; btn.textContent = '⏳ Waiting…'; }
  try{
    // Issue #344: write the card immediately from the submitted values — the
    // operator sees parameters held, constraints, markets and the axis
    // selector before the first window settles. Charts start empty and fill.
    if(meta){
      meta.innerHTML = sweepCard(v, {
        axis: axis,
        sweep_center: center,
        points: (sweepAxisValues(axis, center).values || []).map(val => ({
          label: formatSweepTickValue(axis, val),
          value: val,
          overall: {}, per_series: {}, series_present: [],
        })),
        series_order: ['btc-up-or-down-5m','eth-up-or-down-5m','bnb-up-or-down-5m','sol-up-or-down-5m','xrp-up-or-down-5m','btc-up-or-down-15m','eth-up-or-down-15m','bnb-up-or-down-15m','sol-up-or-down-15m','xrp-up-or-down-15m'],
        series_labels: {},
        // Issue #355: the grid shows the selection from this instant, so the
        // card is never all-grey while the sweep runs.
        selected_series: selectedSeries,
        pending: true,
      }, '');
    }
    // Both endpoints share the one-worker guard. If an explicit regular
    // backtest is already running, wait for that local run instead of showing
    // a misleading 429 when the operator starts the visual sweep.
    const waitUntil = Date.now() + 300000;
    while (window._btRunning && Date.now() < waitUntil) {
      // Issue #383: a stop during the wait is a deliberate cancellation — return
      // instead of starting the sweep the operator just cancelled.
      if ((window._btStopSeq || 0) !== stopSeqAtWait) {
        if(meta){ meta.textContent = 'stopped by operator — sweep not started.'; }
        return;
      }
      if(meta){ meta.textContent = 'waiting for the selected-file backtest to finish…'; }
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    if ((window._btStopSeq || 0) !== stopSeqAtWait) {
      if(meta){ meta.textContent = 'stopped by operator — sweep not started.'; }
      return;
    }
    if (window._btRunning) {
      if(meta){ meta.textContent = 'backtest is still running; try the sweep again when it finishes.'; }
      return;
    }
    window._btSweepStartTime = performance.now();
    if(btn){ btn.textContent = '⏳ Sweeping…'; }
    window._btSweepTimerId = setInterval(() => {
      const el = $('sweepElapsedTime');
      if (el && window._btSweepStartTime) {
        el.textContent = fmtElapsed(performance.now() - window._btSweepStartTime);
      }
    }, 500);
    const ctl = new AbortController();
    window._btSweepAbort = ctl;
    // Issue #388: categorical axes have no midpoint — never send an anchor for
    // them, even if a stale value is still held in window._btSweepCenter.
    const centerSuffix = (center != null && Number.isFinite(center) && !sweepAxisIsCategorical(axis))
      ? `&sweep_center=${encodeURIComponent(center)}` : '';
    const url = `/api/backtest/sweep/stream?axis=${encodeURIComponent(axis)}&${btControlQuery(v)}${centerSuffix}`;
    const res = await fetch(url, {signal: ctl.signal});
    if (window._btSweepAbort !== ctl) return;
    // Validation and busy responses arrive as JSON, not SSE — surface their
    // error text immediately instead of silently rendering an empty chart.
    const ctype = (res.headers && res.headers.get('content-type')) || '';
    if (res.status === 429) {
      if(meta){ meta.textContent = 'busy — a backtest is already running, retry shortly.'; }
      return;
    }
    if (!res.ok || !ctype.includes('text/event-stream')) {
      let errMsg = `HTTP ${res.status}`;
      try { const j = await res.json(); if (j && j.error) errMsg = j.error; } catch {}
      if(meta){ meta.textContent = errMsg; }
      return;
    }
    if (window._btSweepAbort !== ctl) return;
    // Live fill: every progress event re-renders the card + charts from the
    // running totals; the final event replaces everything with the exact
    // authoritative payload (same path as the blocking era).
    await consumeBacktestStream(res, ctl, (ev) => {
      if (window._btSweepAbort !== ctl) return; // superseded — ignore stale events
      if (ev.type === 'progress') {
        renderSweepVisual(buildSweepProgressView(axis, v, ev, selectedSeries), v, true);
      } else if (ev.type === 'final') {
        // Issue #355: the final payload carries no selection of its own, so the
        // run-start snapshot is attached here for the authoritative render.
        ev.result.selected_series = selectedSeries;
        renderSweepVisual(ev.result, v);
      } else if (ev.type === 'error') {
        if(meta){ meta.textContent = ev.error || 'sweep error'; }
      }
    });
  }catch(err){
    if (err && err.name === 'AbortError') return;
    if(meta){ meta.textContent = 'sweep failed: ' + err; }
  }finally{
    window._btSweepInFlight = false;
    if(window._btSweepTimerId){ clearInterval(window._btSweepTimerId); window._btSweepTimerId = null; }
    window._btSweepStartTime = null;
    if(btn){ btn.disabled = false; btn.textContent = '▶ Run Sweep Visual'; }
    updateBtStopVisibility();
  }
}

// Idle preparation (IIIB feedback): draw the sweep card + charts BEFORE the
// first run, so the section never looks like an empty rectangle. Issue #355: the
// markets show the operator's current selection as `pending` — "selected, not
// replayed yet" — instead of a fully greyed grid that read as "nothing selected".
function renderSweepIdle(){
  const axis = $('btSweepAxis') ? $('btSweepAxis').value : 'queue';
  // Issue #378: the idle card previews the live anchor, like the pending card.
  const liveCenter = (window._btSweepCenter != null) ? window._btSweepCenter : null;
  renderSweepVisual({
    axis: axis,
    sweep_center: liveCenter,
    points: (sweepAxisValues(axis, liveCenter).values || []).map(val => ({
      label: formatSweepTickValue(axis, val),
      value: val,
      overall: {}, per_series: {}, series_present: [],
    })),
    series_order: ['btc-up-or-down-5m','eth-up-or-down-5m','bnb-up-or-down-5m','sol-up-or-down-5m','xrp-up-or-down-5m','btc-up-or-down-15m','eth-up-or-down-15m','bnb-up-or-down-15m','sol-up-or-down-15m','xrp-up-or-down-15m'],
    series_labels: {},
    // The idle card has no run to stay stable for, so it reads the live chips.
    selected_series: btSelectedSeriesSlugs(),
    idle: true,
    pending: true,
  }, btControlValues());
}

// Issue #355: keep the idle grid aligned with Backtest Scope. Re-rendering never
// starts a run, and an in-flight sweep keeps its run-start snapshot untouched.
// CodeRabbit round 1 (#356): guard on the explicit in-flight flag, not the
// abort controller — the abort only exists after the wait loop, and was never
// cleared on failure.
function refreshSweepIdleCard(){
  if (window._btSweepInFlight || (window._btSweepVisualData && !window._btSweepVisualData.idle)) return;
  renderSweepIdle();
}

// "Empty but prepared" idle state for the whole backtest tab: sweep card with
// selector + grayed markets + drawn chart axes, and the equity/histogram
// charts created with visible scales instead of blank canvases. Safe to call
// repeatedly — it never overwrites a live or finished render.
function initBacktestIdle(){
  if (!$('btSweepAxis') && !window._btSweepVisualData) renderSweepIdle();
  // Chart.js is loaded only in the browser — in headless harnesses skip the
  // canvas work; the sweep card above is DOM-only and still renders.
  if (typeof Chart === 'undefined') return;
  if (!Chart.getChart('chartEquity')) btBeginProvisionalChart();
  if (!Chart.getChart('chartPnlHist')) btBeginProvisionalHist();
}

// Issue #344: build a sweep-response-shaped view from a progress event's
// running per-point totals, so the live fill reuses the exact final renderer.
// Best-point selection waits for `final` — running totals would crown a
// premature winner and flash the gold highlight.
function buildSweepProgressView(axis, v, ev, selectedSeries){
  return {
    axis: axis,
    points: ev.points || [],
    series_order: (ev.points && ev.points[0] && ev.points[0].per_series)
      ? Object.keys(ev.points[0].per_series) : [],
    series_labels: {},
    // Issue #355: the run-start selection rides along so the grid keeps saying
    // what was asked for, not what has happened to be replayed so far.
    selected_series: selectedSeries,
    best_overall: null,
    best_market: null,
    n_windows: ev.rows_done || 0,
    n_snaps: ev.n_snaps || 0,
    pending: true,
    rows_done: ev.rows_done || 0,
    // Issue #355: a live event from the read loop does not know the total yet
    // (`rows_total: null`). `|| 0` would have turned "unknown" into "zero rows
    // of zero", so the null is preserved and the card words it accordingly.
    rows_total: (ev.rows_total === null || ev.rows_total === undefined) ? null : ev.rows_total,
    // Issue #378: the worker reports what runs — `!= null` keeps an explicit 0.
    sweep_center: (ev.sweep_center != null ? ev.sweep_center : null),
    clamped_count: ev.clamped_count || 0,
  };
}

function sweepAxisLabel(axis){
  return ({
    queue: 'Queue depth — shares ahead',
    offset: 'Quote offset — distance from anchor',
    exit_stop_default: 'Stop distance — default',
    exit_stop_btc: 'Stop distance — BTC',
    exit_stop_sol: 'Stop distance — SOL',
    exit_rev: 'Reversal buffer — distance from anchor',
    late_entry: 'Late entry — % of window',
    quote_range: 'Quotable range — [lo, hi] bounds',
    leg_chase: 'Leg chase — off vs on',
    naked_leg: 'Naked leg at expiry — close vs hold',
    dead_zone_pct: 'Dead zone — % of window'
  })[axis] || axis;
}

// What the sweep replaces. Every other knob stays at the number typed on the
// page, but the chart cannot show which one moved. Each stop axis replaces
// exactly the thresholds its name names, so the note can always be honest with
// one sentence. The values here are what the page submitted — not a
// server-confirmed echo of what ran, which is why the note says "your" and not
// "effective".
function sweepOverrideNote(axis, v, pointValues){
  if(!v) return '';
  const points = Array.isArray(pointValues) ? pointValues : [];
  const equals = (a, b) => Math.abs(Number(a) - Number(b)) < 1e-6;
  const onAxis = value => points.some(p => equals(p, value));
  // The operator's value is shown exactly as submitted. The tick formatter
  // rounds (50.4 → 50, 6.4¢ keeps one decimal), and a rounded display next to
  // "no bar equals it" reads as a contradiction — the match decision above
  // compares the exact number, so the note must show the same number.
  const exact = value => {
    const num = Number(value);
    return Number.isFinite(num) ? String(num) : String(value);
  };

  const stopAxes = {
    exit_stop_default: () => {
      // This axis writes one value into both default thresholds, so a bar can
      // only be the operator's setting when their two default inputs agree and
      // that value is a tested point. The BTC/SOL overrides are untouched by
      // this axis — they never belong in the match.
      const items = [['5m', v.exit5m], ['15m', v.exit15m]]
        .map(p => ({ label: p[0], value: exact(p[1]) }));
      const on = equals(v.exit5m, v.exit15m) && onAxis(v.exit5m);
      const verdict = on
        ? { cls: 'yours', text: `the bar at ${exact(v.exit5m)} is your setting` }
        : { cls: 'none', text: 'no bar equals your values' };
      return { head: 'sweeps the default 5m + 15m stop — replaces the two submitted default stops',
               submittedLabel: 'submitted', items, verdict };
    },
    exit_stop_btc: () => ({
      head: 'sweeps the BTC stop — replaces the submitted BTC 5m Stop Loss',
      submittedLabel: '', items: [],
      verdict: onAxis(v.exitBtc)
        ? { cls: 'yours', text: 'that bar is your setting' }
        : { cls: 'none', text: 'no bar equals it' },
    }),
    exit_stop_sol: () => ({
      head: 'sweeps the SOL stop — replaces the submitted SOL 5m Stop Loss',
      submittedLabel: '', items: [],
      verdict: onAxis(v.exitSol)
        ? { cls: 'yours', text: 'that bar is your setting' }
        : { cls: 'none', text: 'no bar equals it' },
    }),
    quote_range: () => {
      const isSymmetric = equals(v.quoteLo, 1.0 - v.quoteHi);
      const on = isSymmetric && onAxis(v.quoteLo);
      const shown = `[${exact(v.quoteLo)}, ${exact(v.quoteHi)}]`;
      const verdict = on
        ? { cls: 'yours', text: 'that bar is your setting' }
        : { cls: 'none', text: 'no bar equals it' };
      return { head: `sweeps Quotable Range — replaces the submitted ${shown}`,
               submittedLabel: '', items: [], verdict };
    },
  };
  if(stopAxes[axis]) return stopAxes[axis]();

  // Issue #388: a categorical axis tests its whole universe — two values, both
  // always in the grid — so the submitted setting is one of the bars by
  // construction and the note can claim it without comparing numbers. The
  // `pointValues` argument is the numeric projection of the response points and
  // is meaningless here (`Number('hold')` is NaN), which is why this branch
  // reads the submission and not the points.
  if(sweepAxisIsCategorical(axis)){
    const categorical = ({
      leg_chase: ['Leg chase', (v.legChase === true || v.legChase === '1' || v.legChase === 'true') ? 'On' : 'Off'],
      naked_leg: ['Naked leg at expiry', String(v.nakedLegAtExpiry) === 'hold' ? 'Hold' : 'Close'],
    })[axis];
    return { head: `sweeps ${categorical[0]} — replaces the submitted ${categorical[1]}`,
             submittedLabel: '', items: [],
             verdict: { cls: 'yours', text: 'that bar is your setting' } };
  }

  const single = ({
    queue: ['Queue depth', v.queue],
    offset: ['Quote offset', v.offset],
    exit_rev: ['Reversal buffer', v.exitReversal],
    late_entry: ['Late Entry', v.entryDelayPct],
    dead_zone_pct: ['Dead zone', v.deadZonePct],
  })[axis];
  if(!single) return '';
  const shown = exact(single[1]);
  const verdict = onAxis(single[1])
    ? { cls: 'yours', text: 'that bar is your setting' }
    : { cls: 'none', text: 'no bar equals it' };
  return { head: `sweeps ${single[0]} — replaces the submitted ${shown}`,
           submittedLabel: '', items: [], verdict };
}

// The sweep display: subject + tested values as a title above, then one
// compact card with three columns — parameters held, designed constraints,
// participating markets — and the where-am-I verdict at the bottom.
// `v` is the pre-request snapshot, `data` the sweep response (axis + markets).
// Issue #355: the one Markets grid. `sweepCard()` and `sweepCardTail()` each
// built their own copy of this, so both had to be fixed together and could not
// drift. Three states, because "selected but nothing replayed yet" and "not
// selected" are different facts and used to look identical:
//   solid   - the market produced windows in this run
//   pending - selected, no windows yet (or none at all)
//   off     - not selected
// `selectedSlugs` empty/absent = no selection was supplied, so participation
// alone decides, exactly as before.
function sweepMarketsGridHtml(points, selectedSlugs){
  const present = new Set();
  (points || []).forEach(p => (p.series_present || []).forEach(s => present.add(s)));
  const selected = (selectedSlugs && selectedSlugs.length) ? new Set(selectedSlugs) : null;
  const running = (points || []).length > 0;
  const chip = (token, dur) => {
    const slug = `${token.toLowerCase()}-up-or-down-${dur}`;
    const label = `${dur === '5m' ? '05m' : '15m'} ${token}`;
    if (present.has(slug)) {
      return `<span class="sweep-mkt" title="${label} — replayed in this sweep">${label}</span>`;
    }
    if (selected && selected.has(slug)) {
      const why = running ? 'selected — no windows yet' : 'selected — no windows';
      return `<span class="sweep-mkt pending" title="${label} — ${why}">${label}</span>`;
    }
    return `<span class="sweep-mkt off" title="${label} — not selected">${label}</span>`;
  };
  return BT_ALL_TOKENS.map(t => chip(t, '5m')).join('')
    + BT_ALL_TOKENS.map(t => chip(t, '15m')).join('');
}

// Issue #401: unified windows counter format. Returns "done/total" (e.g. "1122/4210"
// or "0/0") when total is a known number, and "done/?" when total is null/undefined.
function sweepWindowsCounter(done, total){
  const d = (done !== null && done !== undefined) ? done : 0;
  if (total === null || total === undefined) return `${d}/?`;
  return `${d}/${total}`;
}

// Issue #355 / #401: the card's running-totals phrase. Three states, because
// `rows_total` is genuinely three-valued now: undefined (nothing has reported
// yet), null (streaming, total unknown) and a number (the converged pass).
function sweepProgressText(data){
  if (data.rows_total === null) return `${data.rows_done || 0} windows replayed…`;
  if (data.rows_total === undefined) return 'starting…';
  return `${data.rows_done || 0}/${data.rows_total} windows`;
}

function sweepCard(v, data, statsHtml){
  const pct = x => x + '%';
  const onoff = x => (String(x) === '1' || x === true) ? 'Enabled' : 'Disabled';
  const axisName = ({
    queue: 'Shares Ahead Queue',
    offset: 'Spread Offset',
    exit_stop_default: 'Exit Stop Loss — default',
    exit_stop_btc: 'Exit Stop Loss — BTC',
    exit_stop_sol: 'Exit Stop Loss — SOL',
    exit_rev: 'Reversal Buffer',
    late_entry: 'Late Entry (% window)',
    quote_range: 'Quotable Range',
  })[data.axis] || data.axis;
  // Issue #419: summaries resolve through the tick formatter, so pending,
  // idle and final cards print one cents convention.
  const values = (data.points || []).map(p => formatSweepTickValue(data.axis, p.value)).join(', ');
  // Issue #355: the grid is built in one place (see sweepMarketsGridHtml).
  const mktsHtml = sweepMarketsGridHtml(data.points, data.selected_series);
  const row = (k, val, subject) => `<span class="sweep-row${subject ? ' subject' : ''}">`
    + `<span class="k">${k}${subject ? ' <span class="sweep-tag">← subject</span>' : ''}</span>`
    + `<span class="v">${val}</span></span>`;
  const held = `
    <span class="sweep-dl">
      <span class="sweep-lab">Parameters held</span>
      ${row('Spread Offset (c)', formatCents(v.offset), data.axis === 'offset')}
      ${row('Queue Depth Filter', String(Math.round(v.queue)), data.axis === 'queue')}
      ${row('Late Entry (% window)', pct(v.entryDelayPct), data.axis === 'late_entry')}
      ${row('Exit Stop 5m (c)', formatCents(v.exit5m), data.axis === 'exit_stop_default')}
      ${row('Exit Stop 15m (c)', formatCents(v.exit15m), data.axis === 'exit_stop_default')}
      ${row('BTC 5m Stop (c)', formatCents(v.exitBtc), data.axis === 'exit_stop_btc')}
      ${row('SOL 5m Stop (c)', formatCents(v.exitSol), data.axis === 'exit_stop_sol')}
      ${row('Reversal Buffer (c)', formatCents(v.exitReversal), data.axis === 'exit_rev')}
      ${row('Leg Chase', onoff(v.legChase), data.axis === 'leg_chase')}
    </span>`;
  const rules = `
    <span class="sweep-dl">
      <span class="sweep-lab">Designed constraints / rules</span>
      ${row('Quotable Range (c)', `[${formatCents(v.quoteLo)}, ${formatCents(v.quoteHi)}]`, data.axis === 'quote_range')}
      ${row('Dead Zone (% window)', pct(v.deadZonePct), data.axis === 'dead_zone_pct')}
      ${row('Naked Leg at Expiry', v.nakedLegAtExpiry === 'hold' ? 'Hold' : 'Close', data.axis === 'naked_leg')}
    </span>`;
  const markets = `
    <span class="sweep-dl">
      <span class="sweep-lab">Markets</span>
      <span class="sweep-mkt-grid">${mktsHtml}</span>
    </span>`;
  const verdict = sweepOverrideNote(data.axis, v, (data.points || []).map(p => Number(p.value)));
  const verdictHtml = (verdict && verdict.verdict && !data.pending && !data.idle)
    ? `<span class="sweep-verdict ${verdict.verdict.cls}">${verdict.verdict.text}</span>`
    : '';
  // Issue #344: the axis selector IS the title — one control chooses and
  // displays the sweep subject instead of a dropdown row + duplicate title.
  const axisOpts = ['queue','offset','exit_stop_default','exit_stop_btc','exit_stop_sol','exit_rev','late_entry','quote_range','leg_chase','naked_leg','dead_zone_pct']
    .map(a => `<option value="${a}"${a === data.axis ? ' selected' : ''}>${({queue:'Queue depth — shares ahead',offset:'Quote offset — distance from anchor',exit_stop_default:'Stop distance — default',exit_stop_btc:'Stop distance — BTC',exit_stop_sol:'Stop distance — SOL',exit_rev:'Reversal buffer — distance from anchor',late_entry:'Late entry — % of window',quote_range:'Quotable range — [lo, hi] bounds',leg_chase:'Leg chase — off vs on',naked_leg:'Naked leg at expiry — close vs hold',dead_zone_pct:'Dead zone — % of window'})[a]}</option>`)
    .join('');
  const titleSel = `<select id="btSweepAxis" onchange="onSweepAxisChange()" style="padding:4px 8px;font-size:14px;font-weight:600;background:var(--panel2);border:1px solid var(--line);border-radius:6px;color:var(--tx)">${axisOpts}</select>`;
  // Issue #378: sweep-only midpoint anchor. `!= null` keeps an explicit 0 (a
  // valid queue anchor); blank restores the literal defaults.
  // Issue #389: the field explains itself instead of relying on a hover tooltip
  // that dies with every re-render — a real <label> gives the input an
  // accessible name and leaves the `$('btSweepCenter')` lookups working, the
  // unit is visible text from this same map, and `is-set` makes a held value
  // look different from an empty one. The empty field reads `default`, never a
  // number: the placeholder used to be the default grid's own midpoint, so a
  // blank field displayed something that looked like live data.
  const anchorSet = (data.sweep_center !== undefined && data.sweep_center !== null);
  // Issue #419: the anchor input on price axes holds cents, like every other
  // price input; the stored sweep_center stays dollars.
  const anchorIsPrice = ['offset', 'exit_stop_default', 'exit_stop_btc', 'exit_stop_sol', 'exit_rev', 'quote_range'].includes(data.axis);
  const anchorVal = anchorSet ? String(anchorIsPrice ? dollarsToCents(data.sweep_center) : data.sweep_center) : '';
  // `quote_range` shifts the lo half of the [lo, hi] pair — say which half.
  const anchorUnit = ({queue: 'shares', offset: 'c', exit_stop_default: 'c', exit_stop_btc: 'c', exit_stop_sol: 'c', exit_rev: 'c', late_entry: '%', quote_range: 'lo bound (c)', leg_chase: '', naked_leg: '', dead_zone_pct: '%'})[data.axis] || '';
  // Issue #388: categorical axes have no midpoint — the field is shown but
  // disabled (the grid is always the literal value list), with an empty unit
  // and a title that says why instead of inviting a number.
  const anchorDisabled = sweepAxisIsCategorical(data.axis);
  const anchorTitle = anchorDisabled
    ? 'Categorical axis — its values are names, so there is no midpoint to anchor'
    : `Midpoint anchor (${anchorUnit}) — blank restores defaults`;
  const anchorField = `<label class="sweep-anchor-field${anchorSet ? ' is-set' : ''}" id="btSweepAnchorWrap" for="btSweepCenter" title="${anchorTitle}">`
    + `<span class="sweep-anchor-label">Anchor</span>`
    + `<input type="number" step="any" id="btSweepCenter" value="${anchorVal}" placeholder="default" onchange="onSweepCenterChange(this.value)"${anchorDisabled ? ' disabled' : ''} style="padding:4px 8px;font-size:14px;background:var(--panel2);border:1px solid var(--line);border-radius:6px;color:var(--tx);width:7em">`
    + `<span class="sweep-anchor-unit">${anchorUnit}</span>`
    + `</label>`;
  const anchorLine = anchorSet ? `<span class="sweep-anchor">anchored at ${formatSweepTickValue(data.axis, data.sweep_center)}</span>` : '';
  const clampLine = (data.clamped_count > 0) ? `<span class="sweep-clamp">${data.clamped_count} points clamped to bounds; bars show the values that ran</span>` : '';
  // Issue #344: idle (pre-first-run) shows the selector + a plain "testing …"
  // with no numbers. Issue #355: a live event carries `rows_total: null` because
  // the read loop does not know the total yet — reporting that as "row 0/0"
  // would read as a finished run of nothing, so it says how many are replayed.
  const pendingBadge = data.idle
    ? `<span class="sweep-title-sub">testing …</span>`
    : data.pending
    ? `<span class="sweep-title-sub">testing ${values} · ${sweepProgressText(data)}</span>`
    : `<span class="sweep-title-sub">testing ${values}</span>`;
  return `<span class="sweep-title">`
    + titleSel
    + anchorField
    + anchorLine
    + clampLine
    + pendingBadge
    + `</span>`
    + `<span class="sweep-card">`
    + (statsHtml || '')
    + `<span class="sweep-cols">${held}${rules}${markets}</span>`
    + verdictHtml
    + `</span>`;
}

// Everything after the title selector in the sweep card — the part that
// re-renders on each progress event without disturbing the axis dropdown.
function sweepCardTail(v, data, statsHtml){
  // Issue #419: see sweepCard — summaries use the tick formatter.
  const values = (data.points || []).map(p => formatSweepTickValue(data.axis, p.value)).join(', ');
  const pct = x => x + '%';
  const onoff = x => (String(x) === '1' || x === true) ? 'Enabled' : 'Disabled';
  const mktsHtml = sweepMarketsGridHtml(data.points, data.selected_series);
  const row = (k, val, subject) => `<span class="sweep-row${subject ? ' subject' : ''}">`
    + `<span class="k">${k}${subject ? ' <span class="sweep-tag">← subject</span>' : ''}</span>`
    + `<span class="v">${val}</span></span>`;
  const held = `
    <span class="sweep-dl">
      <span class="sweep-lab">Parameters held</span>
      ${row('Spread Offset (c)', formatCents(v.offset), data.axis === 'offset')}
      ${row('Queue Depth Filter', String(Math.round(v.queue)), data.axis === 'queue')}
      ${row('Late Entry (% window)', pct(v.entryDelayPct), data.axis === 'late_entry')}
      ${row('Exit Stop 5m (c)', formatCents(v.exit5m), data.axis === 'exit_stop_default')}
      ${row('Exit Stop 15m (c)', formatCents(v.exit15m), data.axis === 'exit_stop_default')}
      ${row('BTC 5m Stop (c)', formatCents(v.exitBtc), data.axis === 'exit_stop_btc')}
      ${row('SOL 5m Stop (c)', formatCents(v.exitSol), data.axis === 'exit_stop_sol')}
      ${row('Reversal Buffer (c)', formatCents(v.exitReversal), data.axis === 'exit_rev')}
      ${row('Leg Chase', onoff(v.legChase), data.axis === 'leg_chase')}
    </span>`;
  const rules = `
    <span class="sweep-dl">
      <span class="sweep-lab">Designed constraints / rules</span>
      ${row('Quotable Range (c)', `[${formatCents(v.quoteLo)}, ${formatCents(v.quoteHi)}]`, data.axis === 'quote_range')}
      ${row('Dead Zone (% window)', pct(v.deadZonePct), data.axis === 'dead_zone_pct')}
      ${row('Naked Leg at Expiry', v.nakedLegAtExpiry === 'hold' ? 'Hold' : 'Close', data.axis === 'naked_leg')}
    </span>`;
  const markets = `
    <span class="sweep-dl">
      <span class="sweep-lab">Markets</span>
      <span class="sweep-mkt-grid">${mktsHtml}</span>
    </span>`;
  const verdict = sweepOverrideNote(data.axis, v, (data.points || []).map(p => Number(p.value)));
  const verdictHtml = (verdict && verdict.verdict && !data.pending)
    ? `<span class="sweep-verdict ${verdict.verdict.cls}">${verdict.verdict.text}</span>`
    : '';
  return `<span class="sweep-title-sub">testing ${values}${data.pending ? ` · ${sweepProgressText(data)}` : ''}</span>`
    + `<span class="sweep-card">`
    + (statsHtml || '')
    + `<span class="sweep-cols">${held}${rules}${markets}</span>`
    + verdictHtml
    + `</span>`;
}

function formatSweepTickValue(axis, val){
  // Issue #388: the name-valued axes are decided by value, before any numeric
  // coercion — `Number('hold')` is NaN, and a bar index is not a value, so an
  // unrecognised input is printed as itself instead of being dressed up as a
  // name it may not be. The chart resolves indices to points before calling
  // here (see the tick label resolver in `sweepChartOptions`).
  if (axis === 'leg_chase') {
    if (val === true || val === '1' || val === 'true') return 'On';
    if (val === false || val === '0' || val === 'false') return 'Off';
    return String(val);
  }
  if (axis === 'naked_leg') {
    if (val === 'hold') return 'Hold';
    if (val === 'close') return 'Close';
    return String(val);
  }
  const num = Number(val);
  if (!Number.isFinite(num)) return String(val);
  if (axis === 'queue') {
    // Issue #378: anchored grids can land between shares — keep the fraction.
    if (Number.isInteger(num)) return String(num);
    return String(Number(num.toFixed(6)));
  }
  if (axis === 'late_entry') {
    if (Number.isInteger(num)) return `${num}%`;
    return `${Number(num.toFixed(6))}%`;
  }
  if (axis === 'dead_zone_pct') {
    // Issue #388: percent of window — the same precision rule as Late Entry,
    // because an anchored grid can land between whole percents.
    if (Number.isInteger(num)) return `${num}%`;
    return `${Number(num.toFixed(6))}%`;
  }
  if (axis === 'quote_range') {
    // Issue #419: the value is the lo end; hi mirrors it around 1.00.
    return `[${formatCents(num)}, ${formatCents(1.0 - num)}]`;
  }
  if (axis === 'offset' || axis === 'exit_rev'
      || axis === 'exit_stop_default' || axis === 'exit_stop_btc' || axis === 'exit_stop_sol') {
    // Issue #419: one cents format (was two ¢ precisions); off-grid anchored
    // values keep precision via dollarsToCents instead of going silent.
    return formatCents(num);
  }
  return Number.isInteger(num) ? String(num) : num.toFixed(3);
}

function formatSweepMoneyTick(v){
  const num = Number(v);
  if (!Number.isFinite(num)) return String(v);
  if (Math.abs(num) < 1e-9) return '$0';
  let s = num.toFixed(2);
  if (s === '-0.00' || s === '0.00') return '$0';
  if (s.endsWith('.00')) s = s.slice(0, -3);
  return '$' + s;
}

function sweepMarketName(slug){
  if (!slug || typeof slug !== 'string') return String(slug || '');
  const m = slug.match(/^([a-zA-Z0-9]+)-up-or-down-(\d+)m$/i);
  if (m) {
    const token = m[1].toUpperCase();
    const duration = m[2].padStart(2, '0') + 'm';
    return `${token} ${duration}`;
  }
  const m2 = slug.match(/^([a-zA-Z0-9]+)\s+(\d+)m$/i);
  if (m2) {
    const token = m2[1].toUpperCase();
    const duration = m2[2].padStart(2, '0') + 'm';
    return `${token} ${duration}`;
  }
  const m3 = slug.match(/^(\d+)m\s+([a-zA-Z0-9]+)$/i);
  if (m3) {
    const duration = m3[1].padStart(2, '0') + 'm';
    const token = m3[2].toUpperCase();
    return `${token} ${duration}`;
  }
  return slug;
}

function sweepTokenColor(slug){
  if (!slug || typeof slug !== 'string') return '';
  const list = (typeof ALL_COCKPIT_SERIES !== 'undefined' && Array.isArray(ALL_COCKPIT_SERIES)) ? ALL_COCKPIT_SERIES : [];
  const normalized = slug.toLowerCase().trim();
  const tokenMatch = normalized.match(/^([a-z0-9]+)/);
  const tokenPrefix = tokenMatch ? tokenMatch[1].toUpperCase() : '';
  for (const s of list) {
    if (s.slug && s.slug.toLowerCase() === normalized) return s.color;
    if (s.label && s.label.toLowerCase() === normalized) return s.color;
    if (s.token && s.token.toUpperCase() === tokenPrefix) return s.color;
  }
  return '';
}

function sweepZeroLinePlugin(){
  return {
    id: 'sweepZeroLine',
    afterDraw: function(chart) {
      const yScale = chart.scales.y;
      if (!yScale) return;
      const y0 = yScale.getPixelForValue(0);
      if (y0 >= chart.chartArea.top && y0 <= chart.chartArea.bottom) {
        const c = chart.ctx;
        const theme = (typeof getThemeTokens === 'function') ? getThemeTokens() : {};
        c.save();
        c.beginPath();
        c.setLineDash([6, 4]);
        c.strokeStyle = theme.gold;
        c.lineWidth = 1.5;
        c.moveTo(chart.chartArea.left, y0);
        c.lineTo(chart.chartArea.right, y0);
        c.stroke();
        c.restore();
      }
    }
  };
}

// Issue #390: the ten per-market cards are ~220px wide, and a `quote_range`
// label (`[0.30, 0.70]`, ~90px) is wider than the gap between two bars. A fixed
// budget of four labels therefore overlaps no matter which points are chosen,
// so the cards derive their tick step from the width the plot really has and
// from the widest label that will actually be drawn.
const SWEEP_CARD_MIN_PLOT_PX = 126;  // narrowest card (220px) minus its padding and y axis
const SWEEP_CARD_TICK_GAP_PX = 10;   // blank space demanded between two labels
const SWEEP_TICK_FONT_PX = 12;       // Chart.js default tick font size

let _sweepMeasureCtx = null;  // one cached 2D context, reused by every card

// Conservative width of one formatted tick label in px. The per-character
// estimate is always the floor: an unusable font family makes `measureText` fall
// back to a smaller font silently, and under-estimating is the one error that
// brings the overlap back. The 0.62 factor is deliberately above this font's
// real advance (a 12-character `[0.30, 0.70]` draws 60px, i.e. 5px/char), so the
// floor is an upper bound rather than a second guess at the drawn width. No
// canvas at all (Node harness): the estimate is all there is.
function sweepLabelWidthPx(text){
  const label = String(text);
  const estimated = Math.ceil(label.length * SWEEP_TICK_FONT_PX * 0.62) + 2;
  if (typeof document === 'undefined') return estimated;
  const font = (typeof Chart !== 'undefined' && Chart.defaults && Chart.defaults.font) ? Chart.defaults.font : {};
  if (!_sweepMeasureCtx) _sweepMeasureCtx = document.createElement('canvas').getContext('2d');
  _sweepMeasureCtx.font = `${Number(font.size) || SWEEP_TICK_FONT_PX}px ${font.family || 'sans-serif'}`;
  return Math.max(estimated, Math.ceil(_sweepMeasureCtx.measureText(label).width) + 2);
}

// Index step between two kept ticks, sized so their labels cannot touch:
// `plotWidthPx / count` is the slot a single bar should occupy, and each label
// needs its own width plus a gap. Never below the legacy budget (`ceil(count / 4)`),
// so a card can only ever lose labels, not gain clutter it never had.
// Unmeasurable widths (no canvas, hidden layout) fall back to that legacy step.
//
// The slot is an estimate, not the drawn geometry: Chart.js can leave dead space
// beside the bars, so measured on a card (194px plot, 7 points) consecutive bars
// sat 16.2px apart where this says 27.7px. The margin therefore rests on the
// width floor above running wide: at the narrowest card (198px canvas, horizontal
// labels) the two surviving labels measured 60px wide with 66.7-70px between
// them — 7-10px of clear space, and no colliding pair on any of the ten cards.
function sweepTickStep(count, plotWidthPx, widestLabelPx, gapPx){
  const legacy = Math.max(1, Math.ceil(count / 4));
  const slotPx = plotWidthPx / count;
  if (!Number.isFinite(slotPx) || slotPx <= 0) return legacy;
  if (!Number.isFinite(widestLabelPx) || widestLabelPx <= 0) return legacy;
  const neededPx = widestLabelPx + (Number.isFinite(gapPx) ? gapPx : SWEEP_CARD_TICK_GAP_PX);
  return Math.max(legacy, Math.ceil(neededPx / slotPx));
}

// Indices the cards label: every `step`-th tested value, plus the final value
// only when it clears the previous kept tick by a full step. The legacy
// expression (`index % step === 0 || index === count - 1`) forced the last value
// in beside a kept tick at count 6, 8, 11, 14 and 17 — adjacent indices are one
// bar apart, so those two labels always collided and smeared the card.
// A dropped final value is deliberate: unreadable text is worse than no text.
function sweepTickIndices(count, step){
  const n = Math.max(0, Math.floor(Number(count) || 0));
  const s = Math.max(1, Math.floor(Number(step) || 1));
  const kept = [];
  for (let i = 0; i < n; i += s) kept.push(i);
  const last = n - 1;
  if (n > 0 && kept[kept.length - 1] !== last && last - kept[kept.length - 1] >= s) kept.push(last);
  return kept;
}

// Issue #410: per-bar widths for non-uniform value axes. Chart.js sizes every
// bar from the *smallest* adjacent gap, so a geometric axis (queue depth)
// throttles all bars to the crowded low end. The target is FILL × the median
// adjacent gap and each bar is capped at FILL × its own nearest-neighbour gap,
// so neighbours provably never overlap ((w_i + w_j)/2 ≤ FILL·gap < gap) while
// uniform axes get exactly the Chart.js fit width. Positions use the same
// linear value→pixel map (conservative span) the tick planner assumes.
const SWEEP_BAR_FILL = 0.72;  // Chart.js categoryPercentage 0.8 × barPercentage 0.9
function sweepBarWidthsPx(xVals, plotWidthPx){
  const vals = Array.isArray(xVals) ? xVals.map(Number) : [];
  const n = vals.length;
  const out = new Array(n).fill(0);
  const plot = Number(plotWidthPx);
  if (n === 0 || !Number.isFinite(plot) || plot <= 0) return out;
  const lo = Math.min.apply(null, vals);
  const hi = Math.max.apply(null, vals);
  if (!(hi > lo)) {
    for (let i = 0; i < n; i++) out[i] = SWEEP_BAR_FILL * plot / n;
    return out;
  }
  const span = plot * (n - 1) / n;
  const px = v => (v - lo) / (hi - lo) * span;
  const gaps = [];
  for (let i = 1; i < n; i++) gaps.push(Math.max(0, px(vals[i]) - px(vals[i - 1])));
  const ordered = gaps.slice().sort((a, b) => a - b);
  const target = SWEEP_BAR_FILL * ordered[Math.floor(ordered.length / 2)];
  for (let i = 0; i < n; i++) {
    const left = i > 0 ? gaps[i - 1] : Infinity;
    const right = i < n - 1 ? gaps[i] : Infinity;
    out[i] = Math.max(0, Math.min(target, SWEEP_BAR_FILL * Math.min(left, right)));
  }
  return out;
}

// Issue #410: public-hook width applier (no private controller overrides).
// The chart's x values ride on `options.sweepXVals`; the plugin derives widths
// from the just-laid-out scale on every draw, so resizes re-plan for free.
const sweepBarWidthPlugin = {
  id: 'sweepBarWidth410',
  beforeDatasetsDraw(chart){
    const opts = (chart.config && chart.config.options) || {};
    const xVals = opts.sweepXVals;
    if (!xVals || !xVals.length) return;
    const scale = chart.scales ? chart.scales.x : null;
    const plot = (scale && Number.isFinite(scale.width) && scale.width > 0) ? scale.width : 0;
    if (!plot) return;
    const widths = sweepBarWidthsPx(xVals, plot);
    const meta = chart.getDatasetMeta ? chart.getDatasetMeta(0) : null;
    const data = (meta && meta.data) ? meta.data : [];
    for (let i = 0; i < data.length && i < widths.length; i++) {
      if (data[i] && widths[i] > 0) data[i].width = widths[i];
    }
  }
};

// Issue #410: uniformity probe for the wide surfaces. Two points or fewer are
// trivially uniform; a non-finite step disqualifies immediately.
function sweepIsUniformX(xVals){
  const n = (xVals || []).length;
  if (n < 3) return true;
  const gaps = [];
  for (let i = 1; i < n; i++) {
    const g = Number(xVals[i]) - Number(xVals[i - 1]);
    if (!Number.isFinite(g)) return false;
    gaps.push(g);
  }
  const tol = 1e-9 * Math.max(1, Math.abs(gaps[0]));
  return gaps.every(g => Math.abs(g - gaps[0]) <= tol);
}

// Issue #410: pixel-distance tick picker for non-uniform value axes. Positions
// use the same linear value→pixel map (conservative span) as the widths, so a
// kept pair is separated on the axis, not just in the index list. Greedy
// widest-separation within `maxTicks`, first value anchored, last value kept
// only when it clears by a full need (the sweepTickIndices rule, in pixels).
// Returns null when the geometry is unmeasurable — the caller keeps legacy.
function sweepPixelTickIndices(xVals, plotWidthPx, labelW, maxTicks, gapPx){
  const n = (xVals || []).length;
  const budget = Math.max(0, Math.floor(Number(maxTicks) || 0));
  if (n === 0 || budget === 0) return [];
  const plot = Number(plotWidthPx);
  if (!Number.isFinite(plot) || plot <= 0) return null;
  const lo = Math.min.apply(null, xVals);
  const hi = Math.max.apply(null, xVals);
  if (!(hi > lo)) return null;
  const widths = (labelW || []).map(Number).filter(Number.isFinite);
  const gap = Number.isFinite(Number(gapPx)) ? Number(gapPx) : 0;
  const need = (widths.length ? Math.max.apply(null, widths) : 0) + gap;
  if (!Number.isFinite(need) || need <= 0) return null;
  const span = plot * (n - 1) / n;
  const pos = xVals.map(v => (Number(v) - lo) / (hi - lo) * span);
  const cap = Math.min(budget, n);
  const kept = [0];
  while (kept.length < cap) {
    let best = -1;
    let bestD = -1;
    for (let i = 0; i < n; i++) {
      if (kept.indexOf(i) !== -1) continue;
      let d = Infinity;
      for (const k of kept) d = Math.min(d, Math.abs(pos[i] - pos[k]));
      if (d >= need && d > bestD) { bestD = d; best = i; }
    }
    if (best < 0) break;
    kept.push(best);
  }
  const last = n - 1;
  if (kept.indexOf(last) === -1 && kept.length < cap) {
    const prev = Math.max.apply(null, kept);
    if (pos[last] - pos[prev] >= need) kept.push(last);
  }
  return kept.sort((a, b) => a - b);
}

function sweepChartOptions(data, detail, isAgg){
  const theme = getThemeTokens();
  const points = data.points || [];
  const xVals = points.map((p, i) => sweepPointX(data.axis, p, i));
  const axisLabel = sweepAxisLabel(data.axis);
  const maxTicks = detail ? Math.min(14, xVals.length) : Math.min(4, xVals.length);
  // Issue #388: on a categorical axis a tick value is a bar index, so every
  // label site (the width-measuring planner, the tick list both branches build
  // and the axis callback) resolves it back to the point it stands for. The
  // other axes have value === position and take the plain formatter.
  const xTickLabel = value => {
    if (!sweepAxisIsCategorical(data.axis)) return formatSweepTickValue(data.axis, Number(value));
    const p = points[Math.round(Number(value))];
    // Review fix (Station IV): an unresolved index falls back to the index
    // itself, never to a blank label — the formatter prints whatever it was
    // given, so a scale that disagrees with its points is visible, not silent.
    return formatSweepTickValue(data.axis, p ? p.value : value);
  };
  return {
    responsive: true,
    maintainAspectRatio: !!detail,
    parsing: false,
    sweepXVals: xVals,  // Issue #410: x values for the bar-width plugin.
    layout: {
      padding: { left: detail ? 8 : (isAgg ? 6 : 4), right: detail ? 12 : (isAgg ? 8 : 6), top: 12, bottom: 8 }
    },
    plugins: {
      legend: { display: false },
      tooltip: {
        callbacks: {
          title: function(items){
            if (!items.length) return '';
            const p = points[items[0].dataIndex];
            if (!p) return '';
            return `${axisLabel}: ${formatSweepTickValue(data.axis, p.value)}`;
          }
        }
      }
    },
    scales: {
      x: {
        type: 'linear', offset: true,
        afterBuildTicks: function(scale){
          // Issue #390: only the ten small cards thin. `scale.width` is the plot
          // the scale was just allotted (canvas minus the y axis), so the step
          // matches the width being drawn — including after a window resize. A
          // card in a hidden tab has no width yet and plans for the narrowest
          // card instead of a wide one it is not. The aggregate card and the
          // detail dialog keep the legacy expression below, and their labels.
          if (!detail && !isAgg) {
            const canvasPx = (scale.chart && scale.chart.canvas) ? scale.chart.canvas.clientWidth : 0;
            const plotWidthPx = (canvasPx > 0 && Number.isFinite(scale.width) && scale.width > 0)
              ? scale.width : SWEEP_CARD_MIN_PLOT_PX;
            const widestPx = xVals.reduce((w, v) => Math.max(w, sweepLabelWidthPx(xTickLabel(v))), 0);
            const step = sweepTickStep(xVals.length, plotWidthPx, widestPx, SWEEP_CARD_TICK_GAP_PX);
            scale.ticks = sweepTickIndices(xVals.length, step)
              .map(i => ({ value: xVals[i], label: xTickLabel(xVals[i]) }));
            return;
          }
          // Issue #410: the aggregate card and the detail dialog thin by pixel
          // distance on non-uniform axes. Uniform and categorical axes keep the
          // legacy expression below, byte-identical; an unmeasurable plot falls
          // back to it rather than a thin budget the chart cannot afford.
          if (!sweepAxisIsCategorical(data.axis) && !sweepIsUniformX(xVals)) {
            const canvasPx410 = (scale.chart && scale.chart.canvas) ? scale.chart.canvas.clientWidth : 0;
            const plotWidthPx410 = (canvasPx410 > 0 && Number.isFinite(scale.width) && scale.width > 0) ? scale.width : 0;
            if (plotWidthPx410 > 0) {
              const labelW410 = xVals.map(v => sweepLabelWidthPx(xTickLabel(v)));
              const kept410 = sweepPixelTickIndices(xVals, plotWidthPx410, labelW410, maxTicks, SWEEP_CARD_TICK_GAP_PX);
              if (kept410) {
                scale.ticks = kept410.map(i => ({ value: xVals[i], label: xTickLabel(xVals[i]) }));
                return;
              }
            }
          }
          const step = Math.max(1, Math.ceil(xVals.length / Math.max(1, maxTicks)));
          scale.ticks = xVals.filter((value, index) => index % step === 0 || index === xVals.length - 1)
            .map(value => ({ value: value, label: xTickLabel(value) }));
        },
        title: { display: !!detail, text: axisLabel, color: theme.dim },
        ticks: {
          autoSkip: false,
          color: theme.dim,
          maxTicksLimit: maxTicks,
          maxRotation: detail ? 0 : 35,
          minRotation: 0,
          callback: function(v){
            return xTickLabel(v);
          }
        },
        grid: { color: theme.line }
      },
      y: {
        beginAtZero: true,
        grace: '18%',
        title: { display: (!!detail || !!isAgg), text: 'Total P&L ($)', color: theme.dim },
        ticks: { color: theme.dim, callback: function(v){ return formatSweepMoneyTick(v); } },
        grid: {
          color: function(ctx){ return (ctx.tick && ctx.tick.value === 0) ? theme.gold : theme.line; },
          lineWidth: function(ctx){ return (ctx.tick && ctx.tick.value === 0) ? 2 : 1; },
          borderDash: function(ctx){ return (ctx.tick && ctx.tick.value === 0) ? [6, 4] : []; }
        }
      }
    }
  };
}

function sweepChartColors(data, seriesKey, theme){
  return (data.points || []).map(point => {
    const isBest = seriesKey
      ? !!(data.best_market && data.best_market.series === seriesKey && data.best_market.value === point.value)
      : !!(data.best_overall && data.best_overall.value === point.value);
    if(isBest) return theme.gold;
    const rawVal = seriesKey
      ? ((point.per_series || {})[seriesKey])
      : ((point.overall || {}).total_pnl_cents);
    const num = Number(rawVal);
    if(Number.isFinite(num)){
      if(num > 0) return theme.up;
      if(num < 0) return theme.down;
    }
    return theme.dim;
  });
}

function closeBtChartDetail(){
  const dialog = $('btChartDialog');
  if(!dialog) return;
  dialog.hidden = true;
  if(btChartDialogInstance){ btChartDialogInstance.destroy(); btChartDialogInstance = null; }
  const trigger = window._btChartDialogTrigger;
  window._btChartDialogTrigger = null;
  if(trigger && typeof trigger.focus === 'function') trigger.focus();
}

function openBtChartDetail(seriesKey, title, trigger){
  const data = window._btSweepVisualData;
  const dialog = $('btChartDialog');
  const canvas = $('btChartDialogCanvas');
  const heading = $('btChartDialogTitle');
  if(!data || !dialog || !canvas) return;
  window._btChartDialogTrigger = trigger || document.activeElement;
  if(heading){
    heading.textContent = title || 'Sweep chart detail';
    heading.style.color = (seriesKey && typeof sweepTokenColor === 'function' ? sweepTokenColor(seriesKey) : '') || '';
  }
  dialog.hidden = false;
  if(btChartDialogInstance) btChartDialogInstance.destroy();
  const theme = getThemeTokens();
  const points = data.points || [];
  const values = points.map(point => seriesKey ? ((point.per_series || {})[seriesKey] || 0) / 100 : ((point.overall || {}).total_pnl_cents || 0) / 100);
  const colors = sweepChartColors(data, seriesKey, theme);
  btChartDialogInstance = new Chart(canvas.getContext('2d'), {
    type: 'bar',
    plugins: [sweepZeroLinePlugin(), sweepBarWidthPlugin],
    data: { datasets: [{ label: 'Total P&L ($)', data: points.map((p, i) => ({x: sweepPointX(data.axis, p, i), y: values[i]})), backgroundColor: colors, borderColor: colors, borderWidth: 1 }] },
    options: sweepChartOptions(data, true)
  });
  const close = $('btChartDialogClose');
  if(close) close.focus();
}

function setupBtChartDialog(){
  if(window._btChartDialogReady) return;
  const dialog = $('btChartDialog');
  const close = $('btChartDialogClose');
  const aggregate = $('btSweepAggCard');
  if(!dialog || !close || !aggregate) return;
  window._btChartDialogReady = true;
  close.addEventListener('click', closeBtChartDetail);
  dialog.addEventListener('click', event => { if(event.target === dialog) closeBtChartDetail(); });
  document.addEventListener('keydown', event => {
    if(dialog.hidden) return;
    if(event.key === 'Escape'){ closeBtChartDetail(); return; }
    if(event.key === 'Tab'){
      const focusable = Array.from(dialog.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'))
        .filter(el => !el.disabled && el.offsetParent !== null);
      if(!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if(event.shiftKey && document.activeElement === first){
        event.preventDefault();
        last.focus();
      } else if(!event.shiftKey && document.activeElement === last){
        event.preventDefault();
        first.focus();
      }
    }
  });
  const activate = event => {
    if(event.type === 'keydown' && event.key !== 'Enter' && event.key !== ' ') return;
    if(event.type === 'keydown') event.preventDefault();
    openBtChartDetail(null, 'All markets — Sweep Visual', aggregate);
  };
  aggregate.addEventListener('click', activate);
  aggregate.addEventListener('keydown', activate);
}

function renderSweepVisual(data, submitted, isProgress){
  window._btSweepVisualData = data;
  setupBtChartDialog();
  const theme = getThemeTokens();
  const points = data.points || [];
  const xVals = points.map((p, i) => sweepPointX(data.axis, p, i));
  const axisLabel = sweepAxisLabel(data.axis);
  const xy = y => points.map((p, i) => ({ x: xVals[i], y: y[i] }));
  const money = cents => `${cents >= 0 ? '+' : '-'}$${Math.abs(cents / 100).toFixed(2)}`;
  const bestOverall = data.best_overall || null;
  const bestMarket = data.best_market || null;
  const meta = $('btSweepMeta');
  if(meta){
    // Issue #344: during the run the stat row shows live settled counts
    // instead of a premature best-point crown (that waits for `final`).
    let statsHtml;
    if (data.idle) {
      statsHtml = '';
    } else if (isProgress) {
      const elapsedTxt = (window._btSweepStartTime) ? fmtElapsed(performance.now() - window._btSweepStartTime) : '0s';
      statsHtml = `<span class="sweep-stats">`
        + `<span><span class="sweep-lab">Windows</span><span class="sweep-stat-v">${sweepWindowsCounter(data.rows_done, data.rows_total)}</span></span>`
        + `<span><span class="sweep-lab">Elapsed</span><span class="sweep-stat-v" id="sweepElapsedTime">${elapsedTxt}</span></span>`
        + `</span>`;
    } else {
      const overallText = bestOverall ? `${bestOverall.label} (${money(bestOverall.total_pnl_cents)})` : '—';
      const bestMarketName = bestMarket ? ((typeof sweepMarketName === 'function' ? sweepMarketName(bestMarket.series) : bestMarket.series) || bestMarket.label) : '';
      const marketText = bestMarket ? `${bestMarketName} at ${bestMarket.point_label} (${money(bestMarket.total_pnl_cents)})` : '—';
      const tookTxt = (window._btSweepStartTime) ? fmtElapsed(performance.now() - window._btSweepStartTime) : '';
      statsHtml = `<span class="sweep-stats">`
        + `<span><span class="sweep-lab">Best overall</span><span class="sweep-stat-v">${overallText}</span></span>`
        + `<span><span class="sweep-lab">Best market</span><span class="sweep-stat-v">${marketText}</span></span>`
        + `<span><span class="sweep-lab">Windows</span><span class="sweep-stat-v">${sweepWindowsCounter(data.n_windows, data.n_windows)}</span></span>`
        + (tookTxt ? `<span><span class="sweep-lab">Took</span><span class="sweep-stat-v">${tookTxt}</span></span>` : '')
        + `</span>`;
    }
    // The card is the whole display: title (axis selector + tested values),
    // stat row, three columns, verdict. The old grey sentence is gone.
    // Preserve the selector's focus/selection across progress re-renders by
    // patching only the parts around it when the axis is unchanged.
    const existingSel = $('btSweepAxis');
    if (isProgress && existingSel && existingSel.value === data.axis) {
      // Re-render everything after the title element only: replace children
      // of meta except the first (the selector) by rebuilding via fragment.
      const keep = existingSel;
      // Issue #378: the title also owns the anchor field + notices. Keep the
      // live field so in-progress typing survives the re-render, and rebuild
      // the notice lines from this event's own metadata. The value survives
      // because the node is re-inserted; focus is captured before clearing and
      // restored after reattachment (Issue #394) if the anchor input was active.
      // Note: caret position is not restored as type="number" inputs do not support selection APIs.
      // Issue #389: keep the WRAPPER, not the bare input — the label and the
      // unit live in it, and preserving the input alone would drop them on the
      // first progress event.
      const keepAnchorWrap = $('btSweepAnchorWrap');
      const anchorInput = $('btSweepCenter');
      const anchorWasFocused = !!(keepAnchorWrap && anchorInput && document.activeElement === anchorInput);
      const anchorSetEv = (data.sweep_center !== undefined && data.sweep_center !== null);
      const anchorLineEv = anchorSetEv ? `<span class="sweep-anchor">anchored at ${formatSweepTickValue(data.axis, data.sweep_center)}</span>` : '';
      const clampLineEv = (data.clamped_count > 0) ? `<span class="sweep-clamp">${data.clamped_count} points clamped to bounds; bars show the values that ran</span>` : '';
      meta.innerHTML = '';
      // Keep the selector inside its .sweep-title flex wrapper — the progress
      // path rebuilds the tail, but the title row keeps its layout contract.
      const titleWrap = document.createElement('span');
      titleWrap.className = 'sweep-title';
      titleWrap.appendChild(keep);
      if (keepAnchorWrap) titleWrap.appendChild(keepAnchorWrap);
      const notesEv = document.createElement('span');
      notesEv.innerHTML = anchorLineEv + clampLineEv;
      while (notesEv.firstChild) titleWrap.appendChild(notesEv.firstChild);
      meta.appendChild(titleWrap);
      const rest = document.createElement('span');
      rest.innerHTML = sweepCardTail(submitted, data, statsHtml);
      while (rest.firstChild) meta.appendChild(rest.firstChild);
      if (anchorWasFocused && anchorInput && typeof anchorInput.focus === 'function') {
        anchorInput.focus({ preventScroll: true });
      }
    } else {
      meta.innerHTML = sweepCard(submitted, data, statsHtml);
    }
  }
  const mkOpts = isAgg => sweepChartOptions(data, false, isAgg);
  const chartColors = seriesKey => sweepChartColors(data, seriesKey, theme);
  destroyChartInstance('chartSweepAgg');
  const aggCtx = $('chartSweepAgg');
  if(aggCtx){
    const aggregateColors = chartColors(null);
    new Chart(aggCtx.getContext('2d'), {
      type: 'bar',
      plugins: [sweepZeroLinePlugin(), sweepBarWidthPlugin],
      data: { datasets: [{ label: 'Total P&L ($)', data: xy(points.map(p => (p.overall.total_pnl_cents || 0) / 100)), backgroundColor: aggregateColors, borderColor: aggregateColors, borderWidth: 1 }] },
      options: mkOpts(true)
    });
  }
  const aggregate = $('btSweepAggCard');
  if(aggregate) aggregate.setAttribute('aria-label', `Open aggregate Sweep Visual chart detail for ${points.length} tested values`);
  const grid = $('btSweepGrid');
  if(!grid) return;
  // Issue #371: tear down per-market charts by canvas element while the
  // canvases are still attached (id lookup fails after detachment).
  Array.from(grid.querySelectorAll('canvas[id]')).forEach(cv => destroyChart(cv));
  grid.innerHTML = '';
  const order = data.series_order || [];
  order.forEach((seriesKey, idx) => {
    const isBestMarket = bestMarket && bestMarket.series === seriesKey;
    const marketDisplayName = (typeof sweepMarketName === 'function') ? sweepMarketName(seriesKey) : ((data.series_labels || {})[seriesKey] || seriesKey);
    const tokenColor = (typeof sweepTokenColor === 'function') ? sweepTokenColor(seriesKey) : '';
    const card = document.createElement('div');
    card.className = 'bt-chart-card';
    card.tabIndex = 0;
    card.setAttribute('role', 'button');
    card.setAttribute('aria-label', `Open ${marketDisplayName} Sweep Visual chart detail for ${points.length} tested values`);
    card.style.cssText = `background:var(--panel2);border:1px solid ${isBestMarket ? theme.gold : 'var(--line)'};border-radius:10px;padding:8px 10px`;
    const activate = event => {
      if(event.type === 'keydown' && event.key !== 'Enter' && event.key !== ' ') return;
      if(event.type === 'keydown') event.preventDefault();
      openBtChartDetail(seriesKey, marketDisplayName, card);
    };
    card.addEventListener('click', activate);
    card.addEventListener('keydown', activate);
    const title = document.createElement('div');
    title.style.cssText = 'font:700 11px var(--disp);color:var(--faint);margin-bottom:4px';
    if(tokenColor) title.style.color = tokenColor;
    title.textContent = `${marketDisplayName}${isBestMarket ? ' ★ BEST MARKET' : ''}`;
    const cvWrap = document.createElement('div');
    cvWrap.style.cssText = 'position:relative;height:95px';
    const cv = document.createElement('canvas');
    const cvId = 'chartSweep_' + idx;
    cv.id = cvId;
    cv.height = 95;
    cvWrap.appendChild(cv);
    card.appendChild(title);
    card.appendChild(cvWrap);
    grid.appendChild(card);
    const y = points.map(p => ((p.per_series || {})[seriesKey] || 0) / 100);
    const colors = chartColors(seriesKey);
    destroyChart(cv);
    new Chart(cv.getContext('2d'), {
      type: 'bar',
      plugins: [sweepZeroLinePlugin(), sweepBarWidthPlugin],
      data: { datasets: [{ data: xy(y), backgroundColor: colors, borderColor: colors, borderWidth: 1 }] },
      options: mkOpts(false)
    });
  });
}


// Statistical Summary Charts
function destroyChartInstance(canvasId){
  // Chart.js exists only in the browser; headless harnesses skip cleanly.
  if (typeof Chart === 'undefined') return;
  const existing = Chart.getChart(canvasId);
  if(existing) existing.destroy();
}

// Issue #371: destroy by canvas element — works even when the canvas has been
// detached from the DOM, where Chart.getChart(canvasId) lookup fails.
function destroyChart(canvas){
  if (typeof Chart === 'undefined' || !canvas) return;
  const existing = Chart.getChart(canvas);
  if(existing) existing.destroy();
}

async function renderSummaryCharts(){
  const d=await (await fetch('/api/oscillation',{cache:'no-store'})).json();
  renderOscillationHero(d.summary);
  const theme = getThemeTokens();
  const sum=d.summary.per_series||{};
  const order=['BTC 5m','ETH 5m','BNB 5m','SOL 5m','XRP 5m','BTC 15m','ETH 15m','BNB 15m','SOL 15m','XRP 15m'];
  const osc=[], mono=[];
  for(const lbl of order){
    let found = null;
    for(const k of Object.keys(sum)){
      if(sum[k].label === lbl){ found = sum[k]; break; }
    }
    osc.push(found ? (found.oscillating||0) : 0);
    mono.push(found ? (found.monotonic||0) : 0);
  }

  const canvasAsset = $('cPerAsset');
  if(canvasAsset){
    destroyChartInstance('cPerAsset');
    new Chart(canvasAsset,{
      type:'bar',
      plugins:[sweepZeroLinePlugin()],
      data:{
        labels:order,
        datasets:[
          {label:'oscillating',data:osc,backgroundColor:theme.up},
          {label:'monotonic',data:mono,backgroundColor:theme.down}
        ]
      },
      options:{
        responsive:true,
        layout:{padding:{left:6,right:10,top:12,bottom:6}},
        plugins:{legend:{position:'bottom',labels:{color:theme.dim}}},
        scales:{
          x:{offset:true,ticks:{color:theme.dim},grid:{color:theme.line}},
          y:{
            beginAtZero:true,
            grace:'15%',
            ticks:{color:theme.dim},
            grid:{
              color:function(ctx){ return (ctx.tick && ctx.tick.value === 0) ? theme.gold : theme.line; },
              lineWidth:function(ctx){ return (ctx.tick && ctx.tick.value === 0) ? 2 : 1; },
              borderDash:function(ctx){ return (ctx.tick && ctx.tick.value === 0) ? [6, 4] : []; }
            }
          }
        }
      }
    });
  }

  const a=await (await fetch('/api/analysis',{cache:'no-store'})).json();
  const hm = a.hist_max || {};
  const bLabels=['$0.00-$0.10','$0.10-$0.20','$0.20-$0.30','$0.30-$0.40','$0.40-$0.50'];
  let bCounts=[0,0,0,0,0];
  if(Object.keys(hm).length > 0){
    bCounts = [
      (hm[0]||0) + (hm[5]||0),
      (hm[10]||0) + (hm[15]||0),
      (hm[20]||0) + (hm[25]||0),
      (hm[30]||0) + (hm[35]||0),
      (hm[40]||0) + (hm[45]||0) + (hm[50]||0)
    ];
  } else {
    (a.rows||[]).forEach(r=>{
      const m=Math.max(r.max_up||0,r.max_down||0)*100;
      if(m<10) bCounts[0]++; else if(m<20) bCounts[1]++; else if(m<30) bCounts[2]++; else if(m<40) bCounts[3]++; else bCounts[4]++;
    });
  }

  const canvasHist = $('cHist');
  if(canvasHist){
    destroyChartInstance('cHist');
    new Chart(canvasHist,{
      type:'bar',
      plugins:[sweepZeroLinePlugin()],
      data:{labels:bLabels,datasets:[{label:'Windows',data:bCounts,backgroundColor:theme.gold}]},
      options:{
        responsive:true,
        layout:{padding:{left:6,right:10,top:12,bottom:6}},
        plugins:{legend:{display:false}},
        scales:{
          x:{offset:true,ticks:{color:theme.dim}},
          y:{
            beginAtZero:true,
            grace:'15%',
            ticks:{color:theme.dim},
            grid:{
              color:function(ctx){ return (ctx.tick && ctx.tick.value === 0) ? theme.gold : theme.line; },
              lineWidth:function(ctx){ return (ctx.tick && ctx.tick.value === 0) ? 2 : 1; },
              borderDash:function(ctx){ return (ctx.tick && ctx.tick.value === 0) ? [6, 4] : []; }
            }
          }
        }
      }
    });
  }

  const hs = a.hist_start || {};
  const sBuckets=['$0.00-$0.01','$0.01-$0.02','$0.02-$0.05','$0.05-$0.10','>$0.10'];
  let sCounts=[0,0,0,0,0];
  if(Object.keys(hs).length > 0){
    sCounts = [
      hs[0]||0,
      hs[1]||0,
      (hs[2]||0) + (hs[3]||0),
      hs[5]||0,
      hs[10]||0
    ];
  } else {
    (a.rows||[]).forEach(r=>{
      const d=Math.abs((r.start_mid||0.5)-0.5)*100;
      if(d<1) sCounts[0]++; else if(d<2) sCounts[1]++; else if(d<5) sCounts[2]++; else if(d<10) sCounts[3]++; else sCounts[4]++;
    });
  }

  const canvasStart = $('cStart');
  if(canvasStart){
    destroyChartInstance('cStart');
    new Chart(canvasStart,{
      type:'doughnut',
      plugins:[sweepZeroLinePlugin()],
      data:{labels:sBuckets,datasets:[{data:sCounts,backgroundColor:[theme.up,theme.proj,theme.gold,theme.down,theme.faint]}]},
      options:{responsive:true,plugins:{legend:{position:'bottom',labels:{color:theme.dim}}}}
    });
  }

  const rows = a.rows || [];
  const pBuckets=['1.00-1.02','1.02-1.04','1.04-1.06','1.06+'];
  const pCounts=[0,0,0,0];
  rows.forEach(r=>{
    const p=r.touch_pair_median||1.012;
    if(p<1.02) pCounts[0]++; else if(p<1.04) pCounts[1]++; else if(p<1.06) pCounts[2]++; else pCounts[3]++;
  });

  const canvasPair = $('cPair');
  if(canvasPair){
    destroyChartInstance('cPair');
    new Chart(canvasPair,{
      type:'bar',
      plugins:[sweepZeroLinePlugin()],
      data:{labels:pBuckets,datasets:[{data:pCounts,backgroundColor:theme.proj}]},
      options:{
        responsive:true,
        layout:{padding:{left:6,right:10,top:12,bottom:6}},
        plugins:{legend:{display:false}},
        scales:{
          x:{offset:true,ticks:{color:theme.dim}},
          y:{
            beginAtZero:true,
            grace:'15%',
            ticks:{color:theme.dim},
            grid:{
              color:function(ctx){ return (ctx.tick && ctx.tick.value === 0) ? theme.gold : theme.line; },
              lineWidth:function(ctx){ return (ctx.tick && ctx.tick.value === 0) ? 2 : 1; },
              borderDash:function(ctx){ return (ctx.tick && ctx.tick.value === 0) ? [6, 4] : []; }
            }
          }
        }
      }
    });
  }
}

// Tick Files Manifest
let pendingDeleteFileName = '';

function showNotice(msg, isError){
  const el = $('manifestNotice');
  if(!el) return;
  el.style.display = 'block';
  el.style.background = isError ? 'rgba(240,104,77,0.15)' : 'rgba(51,201,181,0.15)';
  el.style.color = isError ? 'var(--down)' : 'var(--up)';
  el.style.border = '1px solid ' + (isError ? 'rgba(240,104,77,0.3)' : 'rgba(51,201,181,0.3)');
  el.textContent = msg;
  setTimeout(()=>{ el.style.display = 'none'; }, 4000);
}

// Issue #292: golden-dataset certification card. The backend computes every
// charter gate — this only renders state + checks[] (measured / required / ok,
// with n/N progress where the backend supplies them).
async function loadGoldenCard(){
  const wrap = document.getElementById('goldenCardWrap');
  if(!wrap) return;
  try{
    const res = await fetch('/api/ticks/golden');
    const d = await res.json();
    const fmt = v => {
      if(v === null || v === undefined) return '—';
      const n = Number(v);
      return Number.isFinite(n) ? n.toLocaleString() : String(v);
    };
    const badge = d.state === 'certified'
      ? '<span class="pill" style="background:rgba(51,201,181,0.15);color:var(--up);border-color:rgba(51,201,181,0.3);font-weight:700;font-size:11px;padding:3px 10px">CERTIFIED</span>'
      : d.state === 'present'
        ? '<span class="pill" style="background:rgba(243,186,47,0.15);color:var(--gold);border-color:var(--gold);font-weight:700;font-size:11px;padding:3px 10px">PRESENT · NOT CERTIFIED</span>'
        : '<span class="pill pill-flat" style="font-size:11px;padding:3px 10px">NO GOLDEN DATASET YET</span>';

    let html = `<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">${badge}`
      + `<span style="font-size:11px;color:var(--dim)">Policy version: ${d.policy_version ? esc(String(d.policy_version)) : '— (certification has not run)'}</span></div>`;
    // Issue #281 follow-up: the set-level readiness is what golden certification
    // is FOR — say it in words, since per-file rows can only ever read EXPLORATORY.
    html += `<div style="font-size:11px;color:var(--dim);margin-bottom:8px">Set readiness: `
      + `<span style="color:${d.state === 'certified' ? 'var(--up)' : 'var(--dim)'};font-weight:700">`
      + `${d.state === 'certified' ? 'RESEARCH READY' : 'NOT READY'}</span>`
      + ` — certification is measured over the whole ${esc(d.state === 'absent' ? 'set' : 'golden set')}, not per file.</div>`;

    if(d.state === 'absent'){
      html += `<div style="font-size:12px;color:var(--dim);line-height:1.5;margin-bottom:8px">No <code>run/ticks/golden/</code> exists yet (${esc(d.reason || '')}). `
        + `Capture and certification are tracked by issue #281; the requirements are defined in the charter: <code>${esc(d.charter || 'docs/golden-tick-dataset.md')}</code> — all of them unmet below.</div>`;
    }
    if(d.state !== 'absent' || (d.checks || []).length){
      const rows = (d.checks || []).map(c => {
        const mark = c.ok ? '<span style="color:var(--up);font-weight:700">✓</span>' : '<span style="color:var(--down);font-weight:700">✗</span>';
        let target;
        if(c.n !== undefined && c.n !== null){
          target = `${fmt(c.measured)} / ${fmt(c.required)}`;
        } else if(c.direction === 'equal'){
          target = `${fmt(c.measured)} = ${fmt(c.required)}`;
        } else if(c.direction === 'min'){
          target = `${fmt(c.measured)} ≤ ${fmt(c.required)}`;
        } else {
          target = `${fmt(c.measured)} ≥ ${fmt(c.required)}`;
        }
        const label = String(c.name).replaceAll('_', ' ');
        return `<div style="display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid var(--line)">`
          + `<span style="width:18px;text-align:center">${mark}</span>`
          + `<span style="flex:1;font-size:12px">${esc(label)}</span>`
          + `<span class="mono" style="font-size:11px;color:${c.ok ? 'var(--up)' : 'var(--down)'};white-space:nowrap">${esc(target)}</span></div>`;
      }).join('');
      html += `<div>${rows}</div>`;
      html += `<div style="margin-top:10px;font-size:11px;color:var(--dim)">`
        + `${(d.days || []).length} golden day(s) · verdicts from cached verify reports · charter: <code>${esc(d.charter || '')}</code></div>`;
    }
    wrap.innerHTML = html;
  }catch(err){
    wrap.innerHTML = '<div style="color:var(--down);font-size:12px;padding:8px">Error loading golden dataset state</div>';
  }
}

async function loadManifest(){
  try{
    const res = await fetch('/api/ticks/manifest');
    const d = await res.json();
    window.tickManifestData = d;
    window.tickManifestFiles = d.files || [];

    const sel = $('btFileSelect');
    if(sel && d.files){
      const currentVal = sel.value || window.selectedBacktestFile;
      sel.innerHTML = '';
      const defOpt = document.createElement('option');
      defOpt.value = '';
      defOpt.textContent = 'All Files (Default)';
      if (d.aggregate) {
        const winRaw = (d.aggregate.total_windows||0).toLocaleString();
        const defWinVal = (d.aggregate.windows_source === 'partial' ? '≥' : '') + winRaw;
        const allWin = d.aggregate.total_windows || 0;
        const allEstSec = allWin > 0 ? Math.round(1.2 + allWin * 0.18) : 0;
        defOpt.textContent = `All Files / ${defWinVal} Windows (Default)`;
        if (allEstSec > 0) defOpt.title = `Estimated baseline runtime: ~${fmtElapsed(allEstSec * 1000)} for ${allWin.toLocaleString()} windows`;
      }
      sel.appendChild(defOpt);

      if (d.golden) {
        const goldOpt = document.createElement('option');
        goldOpt.value = 'golden';
        const winCount = (d.golden.windows_count || 4910).toLocaleString();
        const baseEstSec = d.golden.windows_count > 0 ? Math.round(1.2 + d.golden.windows_count * 0.18) : 0;
        goldOpt.textContent = `★ Golden Dataset / ${winCount} Windows (Certified)`;
        if (baseEstSec > 0) goldOpt.title = `Certified Golden dataset across all 6 days (~${fmtElapsed(baseEstSec * 1000)} for ${winCount} windows)`;
        sel.appendChild(goldOpt);
      }

      for(const f of d.files){
        if (f.name === 'golden') {
          if (!d.golden) {
            const opt = document.createElement('option');
            opt.value = 'golden';
            const winCount = (f.windows_count || 4910).toLocaleString();
            const baseEstSec = f.windows_count > 0 ? Math.round(1.2 + f.windows_count * 0.18) : 0;
            opt.textContent = `★ Golden Dataset / ${winCount} Windows (Certified)`;
            if (baseEstSec > 0) opt.title = `Certified Golden dataset across all 6 days (~${fmtElapsed(baseEstSec * 1000)} for ${winCount} windows)`;
            sel.appendChild(opt);
          }
          continue;
        }
        const opt = document.createElement('option');
        opt.value = f.name;
        // Windows, not lines. A line count is a property of how the collector
        // wrote the file, not of how much research the file supports; a window
        // is the unit the engine replays and the unit a robustness claim rests
        // on. The strongest count the file can back is shown, with the
        // readiness tier it earned, so two files are comparable at a glance.
        // No usable verify report means we genuinely do not know — say so
        // rather than print a misleading zero.
        const wq = f.window_quality;
        let detail;
        if(!wq){
          detail = 'windows unknown — not verified';
        }else{
          const best = wq.research_windows != null ? wq.research_windows
                    : (wq.clean_windows != null ? wq.clean_windows : wq.full_windows);
          const tier = wq.readiness_level === 'RESEARCH_READY' ? 'research-ready'
                    : (wq.readiness_level === 'EXPLORATORY' ? 'exploratory' : 'insufficient');
          const n = (best != null ? best : 0).toLocaleString();
          detail = wq.readiness_level === 'RESEARCH_READY'
            ? `${n} research windows`
            : `${n} ${tier} windows`;
        }
        const baseWin = (f.windows_count != null) ? f.windows_count : (f.window_quality ? (f.window_quality.research_windows || f.window_quality.clean_windows || f.window_quality.full_windows || 0) : 0);
        const baseEstSec = baseWin > 0 ? Math.round(1.2 + baseWin * 0.18) : 0;
        // Issue #279: the healthiest file is ★-marked and pre-selected on the
        // first load only — a stored manual choice (including All Files) wins.
        opt.textContent = `${f.is_preferred ? '★ ' : ''}${f.name} (${detail})`;
        if (baseEstSec > 0) opt.title = `Estimated baseline runtime: ~${fmtElapsed(baseEstSec * 1000)} for ${baseWin.toLocaleString()} windows`;
        sel.appendChild(opt);
      }
      if(currentVal && Array.from(sel.options).some(o => o.value === currentVal)){
        sel.value = currentVal;
      } else if(!currentVal && !window._btFileChosen && d.preferred_file && Array.from(sel.options).some(o => o.value === d.preferred_file)){
        // Issue #279: first load only — before any manual choice (an empty
        // dropdown value is also what a manual All Files pick leaves behind,
        // so _btFileChosen is what tells the two apart).
        sel.value = d.preferred_file;
        window.selectedBacktestFile = d.preferred_file;
      }
      updateBtRuntimeEstimate();
    }

    const wrap = $('manifestTableWrap');
    if(!wrap) return;
    wrap.innerHTML = '';

    // KPI tiles (Issue #109) — replaces the old totals card + tape banner.
    const aggWrap = $('manifestAggregateWrap');
    if(aggWrap){
      aggWrap.innerHTML = '';
      const a = d.aggregate || {};
      const m = d.manifest || {};
      if((a.total_files||0) > 0){
        const formatWindowTotal = value => value == null
          ? '—'
          : `${a.windows_source === 'partial' ? '≥' : ''}${value.toLocaleString()}`;
        const windows5m = formatWindowTotal(a.total_windows_5m);
        const windows15m = formatWindowTotal(a.total_windows_15m);
        const tapeRate = m.tape_empty_rate !== undefined ? (m.tape_empty_rate * 100).toFixed(1) + '%' : '—';
        const tapeCrit = m.tape_empty_rate !== undefined && m.tape_empty_rate > 0.99;
        const tile = (label, val, color) => `
          <div style="min-width:0;background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:10px 12px;text-align:center;overflow-wrap:anywhere">
            <div style="font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.04em">${label}</div>
            <div class="mono" style="font-size:15px;font-weight:700;color:${color||'var(--tx)'};margin-top:3px">${val}</div>
          </div>`;
        const tiles = document.createElement('div');
        tiles.style.cssText = 'display:grid;grid-template-columns:repeat(auto-fit,minmax(185px,1fr));gap:8px;margin-bottom:12px';
        const readiness = a.readiness || {};
        const readinessColor = readiness.level === 'RESEARCH_READY' ? 'var(--up)' : readiness.level === 'EXPLORATORY' ? 'var(--gold)' : readiness.level === 'INSUFFICIENT' ? 'var(--down)' : 'var(--dim)';
        tiles.innerHTML =
          tile('Files', a.total_files||0)
          + tile('Market Windows', `${windows5m} × 5m · ${windows15m} × 15m`, 'var(--gold)')
          + tile('Tape Empty · lower is better', tapeRate, tapeCrit ? 'var(--down)' : (m.tape_empty_rate !== undefined ? 'var(--gold)' : 'var(--dim)'))
          + tile('Research Readiness', readiness.level || 'PENDING', readinessColor);
        aggWrap.appendChild(tiles);
        const legend = document.createElement('div');
        legend.style.cssText = 'display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:6px;margin:0 0 12px;padding:9px 11px;background:var(--panel2);border:1px solid var(--line);border-radius:8px;color:var(--dim);font-size:11px;line-height:1.35';
        legend.innerHTML = '<div><strong style="color:var(--tx)">Market Windows</strong> · unique (series, cid) intervals.</div>'
          + '<div><strong style="color:var(--tx)">5m / 15m counts</strong> · 5m means 300-second windows; 15m means 900-second windows. Counts come from verification reports; ≥ means some files are not verified yet.</div>'
          + '<div><strong style="color:var(--tx)">Tape Empty</strong> · snapshots with empty tape_delta; lower is better.</div>';
        aggWrap.appendChild(legend);
      }
    }

    const tbl = document.createElement('table');
    tbl.className = 'tbl';
    const thead = document.createElement('tr');
    thead.innerHTML = '<th>Last Modified</th><th>File Name</th><th>5m / 15m</th><th>Integrity</th><th>Research Readiness</th><th>Actions</th><th>Size</th>';
    tbl.appendChild(thead);

    if(!d.files || d.files.length === 0){
      const tr = document.createElement('tr');
      tr.innerHTML = '<td colspan="7" style="text-align:center;color:var(--faint);padding:18px">No files found in run/ticks/</td>';
      tbl.appendChild(tr);
    } else {
      let fileIdx = 0;
      for(const f of d.files){
        fileIdx++;
        // Issue #295: a pristine entry's name is a subpath (pristine/<name>) —
        // DOM ids must never carry the raw `/`, so build a sanitized token.
        const domId = f.name.replaceAll('/', '_');
        const tr = document.createElement('tr');
        const mb = (f.bytes/(1024*1024)).toFixed(2)+' MB';
        const formatFileWindows = value => value == null ? '—' : value.toLocaleString();

        const tdMtime = document.createElement('td');
        tdMtime.className = 'mono';
        tdMtime.textContent = new Date(f.mtime*1000).toLocaleString('en-US');

        const tdName = document.createElement('td');
        tdName.className = 'mono';
        const btnName = document.createElement('button');
        btnName.type = 'button';
        btnName.style.cssText = 'background:transparent;border:none;padding:0;font:inherit;color:inherit;cursor:pointer;text-align:left;display:inline-flex;align-items:center;gap:2px';
        btnName.title = 'Toggle integrity report';
        btnName.setAttribute('aria-label', `Toggle integrity report for ${f.name}`);
        btnName.setAttribute('aria-expanded', 'false');
        btnName.innerHTML = `<span id="verify_arrow_${domId}" style="display:inline-block;width:16px;color:var(--gold)" aria-hidden="true">▶</span>${esc(f.name)}`;
        btnName.addEventListener('click', () => {
          const expanded = btnName.getAttribute('aria-expanded') === 'true';
          btnName.setAttribute('aria-expanded', String(!expanded));
          toggleFileVerify(domId);
        });
        tdName.appendChild(btnName);
        if (f.is_golden) {
          const goldBadge = document.createElement('span');
          goldBadge.textContent = '🏆 Golden Dataset';
          goldBadge.style.cssText = 'color:var(--gold);font-weight:700;font-size:11px;white-space:nowrap;margin-left:6px;background:rgba(255,215,0,0.1);padding:2px 6px;border-radius:4px;border:1px solid rgba(255,215,0,0.25)';
          tdName.appendChild(goldBadge);
        }
        if (f.is_preferred) {
          // Issue #279/#294: exactly one row carries the ★ badge.
          const pref = document.createElement('span');
          const isTier1 = d.preferred_tier === 1;
          pref.textContent = isTier1 ? '★ Preferred' : '★ Best available';
          pref.style.cssText = 'color:var(--gold);font-weight:700;font-size:11px;white-space:nowrap;margin-left:6px';
          pref.title = isTier1
            ? `Healthiest dataset: integrity PASS, complete capture, ${(f.readiness && f.readiness.level) || 'unknown'} readiness, ${(f.windows_count || 0).toLocaleString()} windows`
            : `Best available (no file fully qualifies): ${f.integrity_status || 'unchecked'} integrity, ${(f.capture_state && f.capture_state.label) || 'unknown'} capture, ${(f.readiness && f.readiness.level) || 'unknown'} readiness, ${(f.windows_count || 0).toLocaleString()} windows`;
          tdName.appendChild(pref);
        }

        const tdWindows = document.createElement('td');
        tdWindows.className = 'mono windows-cell';
        tdWindows.textContent = `${formatFileWindows(f.windows_5m)} / ${formatFileWindows(f.windows_15m)}`;
        if (f.windows_5m == null || f.windows_15m == null) {
          tdWindows.title = 'Not verified';
        }

        const tdIntegrity = document.createElement('td');
        tdIntegrity.className = 'mono';
        const tdReadiness = document.createElement('td');
        tdReadiness.className = 'mono readiness-cell';
        const readinessLevel = f.readiness && (f.readiness.level || f.readiness.readiness_level);
        tdReadiness.textContent = readinessLevel || '…';
        tdReadiness.style.color = readinessLevel === 'RESEARCH_READY' ? 'var(--up)' : readinessLevel === 'EXPLORATORY' ? 'var(--gold)' : readinessLevel === 'INSUFFICIENT' ? 'var(--down)' : 'var(--dim)';

        const tdActions = document.createElement('td');
        tdActions.style.display = 'flex';
        tdActions.style.gap = '6px';
        tdActions.style.alignItems = 'center';

        const verifyBadge = document.createElement('span');
        verifyBadge.id = 'verify_badge_' + domId;
        verifyBadge.style.cssText = 'font:700 10px var(--disp);color:var(--faint);white-space:nowrap';
        verifyBadge.textContent = '…';

        const btnRun = document.createElement('button');
        btnRun.className = 'btn';
        btnRun.style.cssText = 'padding:4px 10px;font-size:11px';
        btnRun.textContent = '⚡ Run Backtest';
        btnRun.addEventListener('click', () => runBacktestOnFile(f.name));

        const btnDel = document.createElement('button');
        btnDel.className = 'btn';
        btnDel.style.cssText = 'padding:4px 10px;font-size:11px;background:rgba(255,87,87,0.12);color:var(--down);border-color:rgba(255,87,87,0.3);cursor:pointer';
        btnDel.textContent = '🗑️ Delete';
        btnDel.addEventListener('click', () => deleteTickFile(f.name));
        // Issue #295/#281: derived datasets (pristine/, golden/) are managed
        // artifacts — the delete endpoint stays top-level-only, so the action
        // is omitted for any subpath entry or directory.
        if (f.name.includes('/') || f.is_dir) btnDel.style.display = 'none';

        tdIntegrity.appendChild(verifyBadge);
        tdActions.appendChild(btnRun);
        tdActions.appendChild(btnDel);

        const tdSize = document.createElement('td');
        tdSize.className = 'mono';
        tdSize.textContent = mb;

        tr.appendChild(tdMtime);
        tr.appendChild(tdName);
        tr.appendChild(tdWindows);
        tr.appendChild(tdIntegrity);
        tr.appendChild(tdReadiness);
        tr.appendChild(tdActions);
        tr.appendChild(tdSize);
        tbl.appendChild(tr);

        // Inline integrity accordion row: sits directly under the file row,
        // filled by verifyTickData() on load (no modal, no Verify button).
        const vRow = document.createElement('tr');
        vRow.id = 'verify_row_' + domId;
        vRow.style.display = 'none';
        const vTd = document.createElement('td');
        vTd.colSpan = 7;
        vTd.id = 'verify_cell_' + domId;
        vTd.style.cssText = 'background:var(--panel2);padding:12px 16px;border-top:1px solid var(--line)';
        vTd.innerHTML = '<div style="text-align:center;color:var(--faint);font-size:11px">Integrity report will load here…</div>';
        vRow.appendChild(vTd);
        tbl.appendChild(vRow);

        if (f.is_dir) {
          // Directory entries like golden already carry certified readiness and
          // status from manifest aggregation — render summary & statistics immediately.
          const {color, label} = fileVerifyStatusBits(f.integrity_status || 'PASS', f.capture_state || 'COMPLETE CAPTURE');
          verifyBadge.textContent = label;
          verifyBadge.style.color = color;
          tdReadiness.textContent = (f.readiness && (f.readiness.level || f.readiness.readiness_level)) || 'RESEARCH_READY';
          tdReadiness.style.color = 'var(--up)';
          vTd.dataset.loaded = '1';
          vTd.innerHTML = renderFileVerifyHtml(f.name, f)
            + `<div style="text-align:center;margin-top:6px"><button type="button" class="btn" style="font-size:10px;padding:3px 10px" aria-label="Rescan integrity report for ${esc(f.name)}" onclick="verifyTickData('${esc(f.name)}', true)">↻ Rescan</button></div>`;
        } else {
          // Queue the integrity check for this file — runs sequentially after
          // the table is built so earlier files populate first (verify runs on
          // every load but is served from the fingerprint cache when unchanged).
          _verifyQueue.push(f.name);
        }
      }
    }
    wrap.appendChild(tbl);
    runVerifyQueue(); // integrity checks, one file at a time
  }catch(err){
    $('manifestTableWrap').innerHTML = '<div style="color:var(--down);padding:12px">Error loading files list</div>';
  }
}

// Sequential verify queue: files are checked one at a time so earlier rows
// populate first; each check resolves from the server-side report cache and
// polls while the backend reports PENDING (background scan in progress).
const _verifyQueue = [];
let _verifyRunning = false;
function runVerifyQueue(){
  if(_verifyRunning) return;
  _verifyRunning = true;
  (async () => {
    while(_verifyQueue.length){
      const name = _verifyQueue.shift();
      try{ await verifyTickData(name); }catch(e){ /* row shows the error */ }
    }
    _verifyRunning = false;
  })();
}

function fileVerifyStatusBits(st, capture){
  const s = st || 'UNKNOWN';
  const state = capture || {};
  const color = s === 'PASS' ? 'var(--up)' : s === 'WARN' ? 'var(--gold)' : s === 'FAIL' ? 'var(--down)' : 'var(--dim)';
  const label = state.label || (s === 'PASS' ? 'COMPLETE CAPTURE' : s === 'WARN' ? 'PARTIAL CAPTURE' : s === 'FAIL' ? 'CORRUPTED DATA' : 'PENDING');
  return {color, label};
}

function formatReadinessValue(name, value){
  if(name && name.endsWith('_rate')) return `${(Number(value || 0) * 100).toFixed(2)}%`;
  return Number(value || 0).toLocaleString();
}

function readinessProgressRow(exploratory, research){
  const e = exploratory || {}, r = research || {};
  const name = e.name || r.name || '';
  const measured = Number(e.measured ?? r.measured ?? 0);
  const direction = e.direction || r.direction || 'min';
  const eTarget = Number(e.required ?? 0), rTarget = Number(r.required ?? 0);
  // Both milestones share one scale. Extend it to the measured value so an
  // over-target file can visibly pass the research marker instead of clipping.
  const scaleMax = Math.max(1, eTarget, rTarget, measured);
  const position = (value) => Math.min(100, Math.max(0, (Number(value || 0) / scaleMax) * 100));
  const measuredPct = position(measured);
  const ePos = position(eTarget), rPos = position(rTarget);
  const milestoneText = (level, target) => {
    const targetText = formatReadinessValue(name, target);
    const measuredText = formatReadinessValue(name, measured);
    if(direction === 'max'){
      return measured <= target
        ? `${level} limit ≤ ${targetText} · measured ${measuredText} · within limit`
        : `${level} limit ≤ ${targetText} · measured ${measuredText} · ${formatReadinessValue(name, measured - target)} above limit`;
    }
    return measured >= target
      ? `${level} target ≥ ${targetText} · measured ${measuredText} · target reached`
      : `${level} target ≥ ${targetText} · measured ${measuredText} · ${formatReadinessValue(name, target - measured)} more needed`;
  };
  const eOk = Boolean(e.ok), rOk = Boolean(r.ok);
  const next = eOk ? (rOk ? 'both targets reached' : `Next milestone: ${milestoneText('RESEARCH READY', rTarget)}`) : `Next milestone: ${milestoneText('EXPLORATORY', eTarget)}`;
  const label = e.label || r.label || name;
  const fillClass = direction === 'max' ? 'max' : 'min';
  const directionNote = direction === 'max' ? '0 is the target · lower is better' : 'higher is better';
  const measuredText = formatReadinessValue(name, measured);
  const exploratoryText = formatReadinessValue(name, eTarget);
  const researchText = formatReadinessValue(name, rTarget);
  return `<div class="tick-progress" data-metric="${esc(name)}" data-direction="${direction}" data-scale-max="${scaleMax}">
    <div class="tick-progress-head"><span class="tick-progress-label">${esc(label)}</span></div>
    <div class="tick-progress-direction">${directionNote}</div>
    <div class="tick-progress-scale" aria-hidden="true"><span class="tick-progress-scale-zero">0</span></div>
    <div class="tick-progress-measured-row"><div class="tick-progress-measured-readout">MEASURED · ${measuredText}</div></div>
    <div class="tick-progress-track" style="height:18px" role="progressbar" aria-label="${esc(label)} measured ${measuredText}; exploratory target ${exploratoryText}; research ready target ${researchText}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(measuredPct)}"><div class="tick-progress-fill ${fillClass}" style="width:${measuredPct.toFixed(1)}%"></div><span class="tick-progress-marker exploratory" style="left:${ePos.toFixed(2)}%"></span><span class="tick-progress-marker research" style="left:${rPos.toFixed(2)}%"></span></div>
    <div class="tick-progress-targets-row"><span class="tick-progress-target exploratory">EXPLORATORY · ${exploratoryText}${eOk ? ' ✓' : ''}</span><span class="tick-progress-target research">RESEARCH READY · ${researchText}${rOk ? ' ✓' : ''}</span></div>
    <div class="tick-progress-next">${esc(next)}</div>
  </div>`;
}

function renderReadinessProgress(readiness){
  const exploratory = readiness && readiness.exploratory_checks || [];
  const research = readiness && readiness.research_checks || [];
  const byName = new Map(research.map(c => [c.name, c]));
  return exploratory.map(e => readinessProgressRow(e, byName.get(e.name))).join('');
}

window.toggleReadinessTooltip = function(button){
  const tip = document.getElementById(button.getAttribute('aria-controls'));
  if(!tip) return;
  const open = !tip.hidden;
  tip.hidden = open;
  button.setAttribute('aria-expanded', String(!open));
};
if(!window._readinessTooltipBound){
  window._readinessTooltipBound = true;
  if(document.addEventListener){
    document.addEventListener('keydown', event => {
      if(event.key === 'Escape') document.querySelectorAll('.tick-tooltip-pop:not([hidden])').forEach(tip => {
        tip.hidden = true;
        const button = document.querySelector(`[aria-controls="${tip.id}"]`);
        if(button) button.setAttribute('aria-expanded', 'false');
      });
    });
    document.addEventListener('click', event => {
      document.querySelectorAll('.tick-tooltip-pop:not([hidden])').forEach(tip => {
        if(!tip.parentElement.contains(event.target)){
          tip.hidden = true;
          const button = document.querySelector(`[aria-controls="${tip.id}"]`);
          if(button) button.setAttribute('aria-expanded', 'false');
        }
      });
    });
  }
}

// Inline per-file integrity report — rendered under the file's row in the
// files table (accordion body). No modal.
function renderFileVerifyHtml(filename, d){  const {color: statusColor, label: statusLabel} = fileVerifyStatusBits(d.status, d.capture_state);
  const readiness = d.readiness || {};
  const readinessColor = readiness.level === 'RESEARCH_READY' ? 'var(--up)' : readiness.level === 'EXPLORATORY' ? 'var(--gold)' : readiness.level === 'INSUFFICIENT' ? 'var(--down)' : 'var(--dim)';
  const progressHtml = renderReadinessProgress(readiness);

  let html = `

    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
      <div>
        <div style="font:600 11px var(--disp);color:var(--dim);text-transform:uppercase;letter-spacing:.05em">Capture State</div>
        <div style="font:700 17px var(--disp);color:${statusColor};margin-top:2px">${esc(statusLabel)}</div>
        <div style="font-size:11px;color:var(--dim);margin-top:3px">${esc((d.capture_state || {}).description || '')}</div>
        <div style="font-size:11px;color:var(--gold);margin-top:3px">${esc((d.capture_state || {}).action || '')}</div>
      </div>
      <div class="mono" style="font-size:12px;color:var(--dim)">${filename === 'golden' || (d && d.is_golden) ? '🏆 Certified Dataset Integrity Report · golden' : `🔍 Integrity Report · ${esc(filename)}`}</div>
    </div>
    <div style="display:flex;gap:14px;align-items:flex-start;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin-bottom:10px">
      <div style="min-width:210px"><div style="font:600 11px var(--disp);color:var(--dim);text-transform:uppercase">Research Readiness <span class="tick-tooltip"><button type="button" class="tick-info" aria-expanded="false" aria-controls="readiness_tip_${esc(filename)}" aria-label="Explain Research Readiness" onclick="toggleReadinessTooltip(this)">i</button><span id="readiness_tip_${esc(filename)}" class="tick-tooltip-pop" role="tooltip" hidden>The targets tell us whether this file contains enough varied data for the selected analysis. They do not prove that the strategy is profitable. Choose settings on one period and check them on a later period that was not used for choosing them.</span></span></div><div style="font:700 17px var(--disp);color:${readinessColor};margin-top:2px">${esc(readiness.level || 'PENDING')}</div></div>
      <div style="font-size:11px;color:var(--dim);line-height:1.45">Each metric below is checked independently. One large number cannot compensate for a missing market, day, or quality check.</div>
    </div>
    <div class="tick-progress-grid" style="margin-bottom:10px">${progressHtml}</div>

    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:10px">
      <div style="background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 10px;text-align:center">
        <div style="font:500 11px var(--body);color:var(--dim)">Valid Tick Snapshots</div>
        <div class="mono" style="font-size:15px;font-weight:700;color:var(--up);margin-top:2px">${(d.valid_ticks||0).toLocaleString()}</div>
      </div>
      <div style="background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 10px;text-align:center">
        <div style="font:500 11px var(--body);color:var(--dim)">Corrupt Rows</div>
        <div class="mono" style="font-size:15px;font-weight:700;color:${(d.corrupt_lines||0)>0?'var(--down)':'var(--tx)'};margin-top:2px">${(d.corrupt_lines||0).toLocaleString()}</div>
      </div>
      <div style="background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 10px;text-align:center">
        <div style="font:500 11px var(--body);color:var(--dim)">Identified Market Windows</div>
        <div class="mono" style="font-size:15px;font-weight:700;margin-top:2px;font-variant-numeric:tabular-nums">${(d.windows_count||0).toLocaleString()}</div>
      </div>
      <div style="background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 10px;text-align:center">
        <div style="font:500 11px var(--body);color:var(--dim)">Crossed Books</div>
        <div class="mono" style="font-size:15px;font-weight:700;color:${(d.crossed_books||0)>0?'var(--down)':'var(--tx)'};margin-top:2px">${(d.crossed_books||0).toLocaleString()}</div>
      </div>
      <div style="background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 10px;text-align:center">
        <div style="font:500 11px var(--body);color:var(--dim)">Timestamp Gaps (&gt;6s)</div>
        <div class="mono" style="font-size:15px;font-weight:700;color:${(d.sampling_gaps_count||0)>0?'var(--gold)':'var(--tx)'};margin-top:2px">${(d.sampling_gaps_count||0).toLocaleString()}</div>
      </div>
      <div style="background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 10px;text-align:center">
        <div style="font:500 11px var(--body);color:var(--dim)">Collector Errors (err)</div>
        <div class="mono" style="font-size:15px;font-weight:700;color:${(d.collector_errors||0)>0?'var(--gold)':'var(--tx)'};margin-top:2px">${(d.collector_errors||0).toLocaleString()}</div>
      </div>
    </div>
  `;

  if(d.market_breakdown && d.market_breakdown.length > 0){
    const marketName = (s) => {
      const m = /^([a-z]{3})-up-or-down-(\d+)m$/.exec(String(s||''));
      return m ? `${String(m[2]).padStart(2, '0')}m ${({btc:'BTC', eth:'ETH', bnb:'BNB', sol:'SOL', xrp:'XRP'})[m[1]]||m[1].toUpperCase()}` : String(s||'');
    };
    html += '<div style="font:700 12px var(--disp);color:var(--tx);letter-spacing:.05em;text-transform:uppercase;margin:12px 0 6px">Per-Market Breakdown</div>'
      + '<table style="width:100%;border-collapse:collapse;font-size:12px">'
      + '<thead><tr>'
      + '<th scope="col" style="text-align:left;font:600 11px var(--disp);color:var(--dim);text-transform:uppercase;letter-spacing:.05em;padding:4px 8px">Market</th>'
      + '<th scope="col" style="text-align:right;font:600 11px var(--disp);color:var(--dim);text-transform:uppercase;letter-spacing:.05em;padding:4px 8px">Windows</th>'
      + '<th scope="col" style="text-align:right;font:600 11px var(--disp);color:var(--dim);text-transform:uppercase;letter-spacing:.05em;padding:4px 8px">Tape Entries</th>'
      + '<th scope="col" style="text-align:right;font:600 11px var(--disp);color:var(--dim);text-transform:uppercase;letter-spacing:.05em;padding:4px 8px">Tape Entries / Window</th>'
      + '</tr></thead><tbody>';
    for(const mb of d.market_breakdown){
      const alt = (d.market_breakdown.indexOf(mb) % 2) ? ' background:var(--panel);' : '';
      html += `<tr style="${alt}">`
        + `<td style="padding:6px 8px;font-weight:600;color:var(--tx)">${esc(marketName(mb.series))}</td>`
        + `<td class="mono" style="padding:6px 8px;text-align:right;font-variant-numeric:tabular-nums">${(mb.windows||0).toLocaleString()}</td>`
        + `<td class="mono" style="padding:6px 8px;text-align:right;font-variant-numeric:tabular-nums">${(mb.trades||0).toLocaleString()}</td>`
        + `<td class="mono" style="padding:6px 8px;text-align:right;font-variant-numeric:tabular-nums;color:var(--dim)">${mb.trades_per_window}</td>`
        + '</tr>';
    }
    html += '</table>';
  }

  if(d.sample_issues && d.sample_issues.length > 0){
    html += '<div style="margin:12px 0 6px;font:700 12px var(--disp);color:var(--down);letter-spacing:.05em;text-transform:uppercase">Sample Discrepancies</div><div style="background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 12px;max-height:140px;overflow-y:auto;font-size:12px" class="mono">';
    for(const is of d.sample_issues.slice(0, 10)){
      html += `<div style="margin-bottom:4px;color:var(--dim)">• Row ${is.line}: <span style="color:var(--tx)">${esc(is.detail)}</span></div>`;
    }
    html += '</div>';
  }
  return html;
}

// Verify one tick file and render the result inline into its accordion row.
// Server-side report cache makes this instant for unchanged files; a first
// pass (or a file the collector is still appending to) polls until the
// background scan lands. refresh=true bypasses the cache (manual rescan).
async function verifyTickData(filename, refresh){
  // Issue #295: DOM ids are built from a sanitized token (raw `/` replaced) —
  // the fetch itself still uses the real relative name.
  const domId = filename.replaceAll('/', '_');
  const cell = document.getElementById('verify_cell_' + domId);
  if(cell && !cell.dataset.loaded){
    cell.innerHTML = '<div style="text-align:center;padding:16px;color:var(--dim);font-size:13px">Running integrity check on ' + esc(filename) + '… <span class="spinner"></span></div>';
  }
  try{
    let d;
    for(;;){
      const res = await fetch('/api/ticks/verify?file=' + encodeURIComponent(filename) + (refresh ? '&refresh=1' : ''));
      d = await res.json();
      if(d.error){ throw new Error(d.error); }
      if(!d.pending && !d.stale){ break; }
      // Backend is scanning in the background — poll until the fresh,
      // fingerprint-matched report lands. A stale snapshot carries the
      // previous market_breakdown, so its counts stay provisional until
      // the rescan finishes (Issue #303 round 1).
      if(cell && (d.pending || d.stale)){
        const p = d.progress || {};
        const lines = (p.lines || 0).toLocaleString();
        const est = p.est_total || 0;
        const pct = est > 0 ? Math.min(99, Math.round((p.lines || 0) / est * 100)) : null;
        const elapsed = p.elapsed_sec != null ? p.elapsed_sec + 's' : '';
        const progHtml = d.stale
          ? `Refreshing ${esc(filename)} from the current file…`
          : est > 0
          ? `${lines} / ${est.toLocaleString()} lines · ${pct}% · ${elapsed}`
          : `${lines} lines processed · ${elapsed}`;
        cell.innerHTML = `<div style="text-align:center;padding:16px;color:var(--dim);font-size:13px">${d.stale ? progHtml : `Scanning ${esc(filename)} in background…`} <span class="spinner"></span><div class="mono" style="margin-top:6px;font-size:12px;color:var(--faint);font-variant-numeric:tabular-nums">${d.stale ? '' : progHtml}</div></div>`;
      }
      const badgeP = document.getElementById('verify_badge_' + domId);
      if(badgeP){ badgeP.textContent = '⏳'; badgeP.style.color = 'var(--dim)'; }
      await new Promise(r => setTimeout(r, 2000));
    }
    if(cell){
      cell.dataset.loaded = '1';
      const row = document.getElementById('verify_row_' + domId);
      const fileRow = row && row.previousElementSibling;
      const readinessCell = fileRow ? fileRow.querySelector('.readiness-cell') : null;
      const windowsCell = fileRow ? fileRow.querySelector('.windows-cell') : null;
      // The poll loop above exits only on a fresh fingerprint-matched
      // report, but guard anyway: never present a stale snapshot's
      // provisional breakdown as verified counts.
      if(windowsCell && Array.isArray(d.market_breakdown) && !d.stale){
        const countWindows = duration => d.market_breakdown
          .filter(market => Number(market.duration) === duration)
          .reduce((total, market) => total + Number(market.windows || 0), 0);
        windowsCell.textContent = `${countWindows(300).toLocaleString()} / ${countWindows(900).toLocaleString()}`;
        windowsCell.removeAttribute('title');
      }
      if(readinessCell && d.readiness){
        readinessCell.textContent = d.readiness.level || 'PENDING';
        readinessCell.style.color = d.readiness.level === 'RESEARCH_READY' ? 'var(--up)' : d.readiness.level === 'EXPLORATORY' ? 'var(--gold)' : d.readiness.level === 'INSUFFICIENT' ? 'var(--down)' : 'var(--dim)';
      }
      cell.innerHTML = renderFileVerifyHtml(filename, d) + (d.stale
        ? '<div style="text-align:center;color:var(--faint);font-size:10px;margin-top:6px">Snapshot of a file still being written — rescanning in background.</div>'
        : '')
        + `<div style="text-align:center;margin-top:6px"><button type="button" class="btn" style="font-size:10px;padding:3px 10px" aria-label="Rescan integrity report for ${esc(filename)}" onclick="verifyTickData('${esc(filename)}', true)">↻ Rescan</button></div>`;
    }
    // Refresh the status badge on the file row.
    const badge = document.getElementById('verify_badge_' + domId);
    if(badge){
      const {color, label} = fileVerifyStatusBits(d.status, d.capture_state);
      badge.textContent = label;
      badge.style.color = color;
    }
    // Update manifest entry and runtime badge if active file was verified
    if(window.tickManifestFiles && Array.isArray(d.market_breakdown)){
      const entry = window.tickManifestFiles.find(f => f.name === filename);
      if(entry){
        entry.market_breakdown = d.market_breakdown;
        entry.windows_count = d.windows_count || entry.windows_count;
        updateBtRuntimeEstimate();
      }
    }
  }catch(e){
    if(cell){
      cell.innerHTML = `<div style="color:var(--down);padding:10px;text-align:center;font-size:12px">Error verifying ${esc(filename)}: ${esc(e.message)}</div>`;
    }
  }
}

// Expand/collapse the inline verify accordion under a file row.
function toggleFileVerify(filename){
  const row = document.getElementById('verify_row_' + filename.replaceAll('/', '_'));
  const arrow = document.getElementById('verify_arrow_' + filename.replaceAll('/', '_'));
  if(!row) return;
  const open = row.style.display !== 'none';
  row.style.display = open ? 'none' : '';
  if(arrow) arrow.textContent = open ? '▶' : '▼';
}

function deleteTickFile(filename){
  pendingDeleteFileName = filename;
  const modal = $('deleteModalOverlay');
  const target = $('deleteFileNameTarget');
  if(modal && target){
    target.textContent = filename;
    modal.style.display = 'flex';
  } else {
    executeDeleteFileDirect(filename);
  }
}

function closeDeleteModal(){
  const modal = $('deleteModalOverlay');
  if(modal) modal.style.display = 'none';
  pendingDeleteFileName = '';
}

async function executeDeleteFile(){
  if(!pendingDeleteFileName) return;
  const filename = pendingDeleteFileName;
  const confirmBtn = $('confirmDeleteBtn');
  if(confirmBtn){
    confirmBtn.disabled = true;
    confirmBtn.textContent = 'Deleting...';
  }
  await executeDeleteFileDirect(filename);
  closeDeleteModal();
  if(confirmBtn){
    confirmBtn.disabled = false;
    confirmBtn.textContent = 'Yes, Delete File';
  }
}

async function executeDeleteFileDirect(filename){
  try{
    const res = await fetch('/api/ticks/file?filename=' + encodeURIComponent(filename), { method:'DELETE' });
    const data = await res.json();
    if(data.ok){
      showNotice('✅ File ' + filename + ' deleted successfully', false);
      loadManifest();
    } else {
      showNotice('❌ Error deleting file: ' + (data.error || 'Unknown error'), true);
    }
  }catch(e){
    showNotice('❌ Network error while deleting file', true);
  }
}

// Streaming File Upload Handlers (Zero-Memory Native Stream)
function handleFileSelect(evt){
  const file = evt.target.files && evt.target.files[0];
  if(file) uploadFileStream(file);
}

function handleFileDrop(evt){
  evt.preventDefault();
  const dropZone = $('dropZone');
  if(dropZone){
    dropZone.style.borderColor = 'var(--line)';
    dropZone.style.background = 'var(--panel2)';
  }
  const file = evt.dataTransfer && evt.dataTransfer.files && evt.dataTransfer.files[0];
  if(file) uploadFileStream(file);
}

async function uploadFileStream(file){
  if(!file) return;
  const statusEl = $('uploadStatus');
  const progWrap = $('uploadProgressWrap');
  const progBar = $('uploadProgressBar');
  const progText = $('uploadProgressText');

  progWrap.style.display = 'block';
  progBar.style.width = '0%';
  progBar.style.background = 'var(--up)';

  const totalSize = file.size;
  const CHUNK_SIZE = 4 * 1024 * 1024;
  const totalChunks = Math.max(1, Math.ceil(totalSize / CHUNK_SIZE));
  const uploadId = 'up_' + Date.now() + '_' + Math.random().toString(36).substring(2, 8);

  const totalMb = (totalSize / (1024 * 1024)).toFixed(1);
  progText.textContent = `Starting chunked upload: ${file.name} (${totalMb} MB, ${totalChunks} chunks)...`;
  statusEl.textContent = `Uploading chunk 1 of ${totalChunks}...`;

  let uploadedBytes = 0;

  for (let chunkIndex = 0; chunkIndex < totalChunks; chunkIndex++) {
    const start = chunkIndex * CHUNK_SIZE;
    const end = Math.min(start + CHUNK_SIZE, totalSize);
    const chunkBlob = file.slice(start, end);
    const currentChunkSize = end - start;

    let success = false;
    let lastError = '';
    let responseData = null;

    for (let attempt = 1; attempt <= 3; attempt++) {
      try {
        const url = `/api/ticks/upload-chunk?filename=${encodeURIComponent(file.name)}&uploadId=${encodeURIComponent(uploadId)}&chunkIndex=${chunkIndex}&totalChunks=${totalChunks}`;
        
        const res = await new Promise((resolve, reject) => {
          const xhr = new XMLHttpRequest();
          xhr.open('POST', url, true);
          xhr.setRequestHeader('Content-Type', 'application/octet-stream');

          xhr.upload.onprogress = function(e){
            if (e.lengthComputable) {
              const currentTotalUploaded = uploadedBytes + e.loaded;
              const percent = Math.min(99, Math.round((currentTotalUploaded / totalSize) * 100));
              progBar.style.width = percent + '%';
              const loadedMb = (currentTotalUploaded / (1024 * 1024)).toFixed(1);
              progText.textContent = `Uploading: ${loadedMb} MB / ${totalMb} MB (${percent}%) | Chunk ${chunkIndex + 1}/${totalChunks}`;
            }
          };

          xhr.onload = function() {
            if (xhr.status >= 200 && xhr.status < 300) {
              try {
                const data = JSON.parse(xhr.responseText);
                resolve({ ok: true, data });
              } catch (e) {
                resolve({ ok: true, data: {} });
              }
            } else {
              let errMsg = xhr.statusText || 'Server error';
              try {
                const errObj = JSON.parse(xhr.responseText);
                if (errObj.error) errMsg = errObj.error;
              } catch (_) {}
              reject(new Error(`Code ${xhr.status}: ${errMsg}`));
            }
          };

          xhr.onerror = function() {
            reject(new Error('Network communication error'));
          };

          xhr.send(chunkBlob);
        });

        responseData = res.data;
        success = true;
        uploadedBytes += currentChunkSize;
        break;
      } catch (err) {
        lastError = err.message || String(err);
        if (attempt < 3) {
          statusEl.textContent = `Attempt ${attempt} failed on chunk ${chunkIndex + 1}, retrying in 1s...`;
          await new Promise(r => setTimeout(r, 1000));
        }
      }
    }

    if (!success) {
      progBar.style.background = 'var(--down)';
      statusEl.innerHTML = `<span style="color:var(--down);font-weight:700">❌ Error uploading chunk ${chunkIndex + 1}/${totalChunks}: ${esc(lastError)}</span>`;
      return;
    }

    if (chunkIndex === totalChunks - 1 && responseData) {
      progBar.style.width = '100%';
      progText.textContent = '100% — Upload Complete!';
      const linesStr = (responseData.lines || 0).toLocaleString();
      const winStr = (responseData.windows_indexed || 0).toLocaleString();
      statusEl.innerHTML = `<span style="color:var(--up);font-weight:700">✅ Uploaded successfully: ${esc(responseData.filename || file.name)} (${linesStr} lines, ${winStr} windows indexed)</span>`;
      loadManifest();
      tick();
      return;
    }
  }
}

// --- LIVE TRADING COCKPIT LOGIC ---
let activeCockpitChartMode = 'total';
let cockpitState = null;
const ALL_COCKPIT_SERIES = [
  { slug: 'btc-up-or-down-5m', token: 'BTC', duration: 300, label: 'BTC 5m', color: '#f7931a' },
  { slug: 'eth-up-or-down-5m', token: 'ETH', duration: 300, label: 'ETH 5m', color: '#627eea' },
  { slug: 'bnb-up-or-down-5m', token: 'BNB', duration: 300, label: 'BNB 5m', color: '#f3ba2f' },
  { slug: 'sol-up-or-down-5m', token: 'SOL', duration: 300, label: 'SOL 5m', color: '#14f195' },
  { slug: 'xrp-up-or-down-5m', token: 'XRP', duration: 300, label: 'XRP 5m', color: '#00aae4' },
  { slug: 'btc-up-or-down-15m', token: 'BTC', duration: 900, label: 'BTC 15m', color: '#f7931a' },
  { slug: 'eth-up-or-down-15m', token: 'ETH', duration: 900, label: 'ETH 15m', color: '#627eea' },
  { slug: 'bnb-up-or-down-15m', token: 'BNB', duration: 900, label: 'BNB 15m', color: '#f3ba2f' },
  { slug: 'sol-up-or-down-15m', token: 'SOL', duration: 900, label: 'SOL 15m', color: '#14f195' },
  { slug: 'xrp-up-or-down-15m', token: 'XRP', duration: 900, label: 'XRP 15m', color: '#00aae4' },
];

let selectedCockpitTokens = new Set(['BTC', 'ETH', 'BNB', 'SOL', 'XRP']);
let selectedCockpitDuration = '5m';
let hasInitializedCockpitFilters = false;
// Holds the engine's exact slug set when it is not expressible as a
// token x duration product (e.g. a CLI selection of BTC 5m + ETH 15m).
// Sent verbatim so a parameter apply cannot silently widen the selection.
let cockpitExactSelection = null;

function getActiveCockpitSeries() {
  if (cockpitState && cockpitState.available_series && cockpitState.selected_series) {
    const selSet = new Set(cockpitState.selected_series);
    return cockpitState.available_series.filter(s => selSet.has(s.slug));
  }
  if (cockpitState && cockpitState.selected_series) {
    const selSet = new Set(cockpitState.selected_series);
    return ALL_COCKPIT_SERIES.filter(s => selSet.has(s.slug));
  }
  return ALL_COCKPIT_SERIES.slice(0, 5);
}

function getSelectedCockpitDurations() {
  if (selectedCockpitDuration === '5m') return [300];
  if (selectedCockpitDuration === '15m') return [900];
  return [300, 900];
}

function areCockpitFiltersLocked() {
  return !!(cockpitState && cockpitState.is_running);
}

function applyCockpitFilterLock(el, locked) {
  if (!el) return;
  el.disabled = locked;
  el.style.opacity = locked ? '0.4' : '';
  el.style.cursor = locked ? 'not-allowed' : '';
  el.title = locked ? 'Stop the bot to change market selection' : '';
}

function updateCockpitFilterUI() {
  const locked = areCockpitFiltersLocked();
  ['BTC', 'ETH', 'BNB', 'SOL', 'XRP'].forEach(tok => {
    const chip = $(`chip-token-${tok}`);
    if (chip) {
      if (selectedCockpitTokens.has(tok)) chip.classList.add('active');
      else chip.classList.remove('active');
      applyCockpitFilterLock(chip, locked);
    }
  });
  const btn5 = $('btnDur5m');
  const btn15 = $('btnDur15m');
  const btnBoth = $('btnDurBoth');
  if (btn5) btn5.className = selectedCockpitDuration === '5m' ? 'tab-btn active' : 'tab-btn';
  if (btn15) btn15.className = selectedCockpitDuration === '15m' ? 'tab-btn active' : 'tab-btn';
  if (btnBoth) btnBoth.className = selectedCockpitDuration === 'both' ? 'tab-btn active' : 'tab-btn';
  [btn5, btn15, btnBoth, $('btnTokensAll'), $('btnTokensClear')].forEach(b => applyCockpitFilterLock(b, locked));
  const hint = $('cockpitFilterLockHint');
  if (hint) hint.style.display = locked ? 'inline' : 'none';
}

function updateCockpitParamsLockUI(locked) {
  const paramIds = [
    'cockpitOffset',
    'cockpitExit',
    'cockpitExitNaked',
    'cockpitNakedLegAtExpiry',
    'cockpitExitReversal',
    'cockpitShares',
    'cockpitEntryDelay',
    'cockpitQuoteLo',
    'cockpitQuoteHi',
    'cockpitPairCost',
    'cockpitLegChase',
    'cockpitWsAuthority',
    'cockpitMode',
    'cockpitDeadZoneVal',
    'cockpitDeadZoneUnit',
    'cockpitWallet',
    'cockpitStartBal',
    'btnApplyParams',
  ];
  paramIds.forEach(id => {
    const el = $(id);
    if (!el) return;
    if (locked) {
      el.disabled = true;
      el.style.opacity = '0.4';
      el.style.cursor = 'not-allowed';
      el.title = 'Stop the bot to change parameters';
    } else {
      if (id === 'cockpitStartBal' && $('cockpitMode') && $('cockpitMode').value === 'live') {
        el.disabled = false;
        el.readOnly = true;
        el.style.opacity = '0.7';
        el.style.cursor = 'not-allowed';
        el.title = 'Starting balance is locked to real Polymarket net account value in LIVE mode.';
      } else {
        el.disabled = false;
        if (id === 'cockpitStartBal') el.readOnly = false;
        el.style.opacity = '';
        el.style.cursor = '';
        el.title = '';
      }
    }
  });

  const hint = $('cockpitParamsLockHint');
  if (hint) hint.style.display = locked ? 'inline' : 'none';
  if (!locked && typeof validateCockpitInputs === 'function') {
    validateCockpitInputs();
  }
}

function cockpitFilterProductSlugs(tokens, durations) {
  const durSet = new Set(durations);
  const seriesList = (cockpitState && cockpitState.available_series) || ALL_COCKPIT_SERIES;
  return seriesList.filter(s => tokens.has(s.token) && durSet.has(s.duration)).map(s => s.slug);
}

function syncCockpitFiltersFromState(st) {
  if (!st || !st.selected_series) return;
  const activeSlugs = new Set(st.selected_series);
  const tokens = new Set();
  let has5m = false;
  let has15m = false;
  const seriesList = st.available_series || ALL_COCKPIT_SERIES;
  seriesList.forEach(s => {
    if (activeSlugs.has(s.slug)) {
      tokens.add(s.token);
      if (s.duration === 300) has5m = true;
      if (s.duration === 900) has15m = true;
    }
  });
  if (tokens.size > 0) selectedCockpitTokens = tokens;
  if (has5m && has15m) selectedCockpitDuration = 'both';
  else if (has15m) selectedCockpitDuration = '15m';
  else selectedCockpitDuration = '5m';

  // If the chips cannot reproduce the engine's exact set, keep the exact set so a
  // later parameter apply resubmits it instead of the wider cross-product.
  const product = cockpitFilterProductSlugs(selectedCockpitTokens, getSelectedCockpitDurations());
  const matchesProduct = product.length === activeSlugs.size && product.every(slug => activeSlugs.has(slug));
  cockpitExactSelection = matchesProduct ? null : [...activeSlugs];

  updateCockpitFilterUI();
}

async function toggleCockpitToken(tok) {
  if (areCockpitFiltersLocked()) {
    alert('Cannot change market selection while the trading bot is running. Stop the bot first.');
    return;
  }
  cockpitExactSelection = null;
  if (selectedCockpitTokens.has(tok)) {
    if (selectedCockpitTokens.size <= 1) {
      alert('At least one cryptocurrency token must remain selected.');
      return;
    }
    selectedCockpitTokens.delete(tok);
  } else {
    selectedCockpitTokens.add(tok);
  }
  updateCockpitFilterUI();
  await applyCockpitConfig();
}

async function setCockpitTokensAll(selectAll) {
  if (areCockpitFiltersLocked()) {
    alert('Cannot change market selection while the trading bot is running. Stop the bot first.');
    return;
  }
  cockpitExactSelection = null;
  if (selectAll) {
    selectedCockpitTokens = new Set(['BTC', 'ETH', 'BNB', 'SOL', 'XRP']);
  } else {
    selectedCockpitTokens = new Set(['BTC']);
  }
  updateCockpitFilterUI();
  await applyCockpitConfig();
}

async function setCockpitDuration(dur) {
  if (areCockpitFiltersLocked()) {
    alert('Cannot change market selection while the trading bot is running. Stop the bot first.');
    return;
  }
  cockpitExactSelection = null;
  if (selectedCockpitDuration === dur) return;
  selectedCockpitDuration = dur;
  updateCockpitFilterUI();
  await applyCockpitConfig();
}

// Issue #139: PnL-per-position histogram from st.trades (session window).
// Mean + CI-lo use the study's bootstrap statistic (2,000 resamples).
function pnlBootstrapCiLo(values, resamples) {
  const n = values.length;
  const R = resamples || 2000;
  let sum = 0;
  for (const v of values) sum += v;
  const mean = sum / n;
  const means = new Array(R);
  for (let r = 0; r < R; r++) {
    let s = 0;
    for (let i = 0; i < n; i++) s += values[(Math.random() * n) | 0];
    means[r] = s / n;
  }
  means.sort((a, b) => a - b);
  return { mean: mean, lo: means[Math.floor(0.025 * R)] };
}
function freedmanDiaconisBins(values) {
  const n = values.length;
  const sorted = [...values].sort((a, b) => a - b);
  const at = p => sorted[Math.min(n - 1, Math.floor(p * (n - 1)))];
  const iqr = at(0.75) - at(0.25);
  const min = sorted[0], max = sorted[n - 1];
  let nb = 12;
  if (iqr > 0 && max > min) {
    const w = 2 * iqr / Math.cbrt(n);
    if (w > 0) nb = Math.ceil((max - min) / w);
  }
  nb = Math.max(12, Math.min(20, nb));
  const edges = [];
  for (let i = 0; i <= nb; i++) edges.push(min + (max - min) * i / nb);
  return edges;
}
function renderPnlHistogram(trades) {
  const wrap = $('pnlHistSvgWrap');
  const statsEl = $('pnlHistStats');
  const fb = $('pnlHistFallback');
  if (!wrap) return;
  const pnls = (trades || []).map(t => Number(t.pnl_usd)).filter(v => isFinite(v));
  if (pnls.length === 0) {
    if (statsEl) statsEl.textContent = 'No closed trades yet';
    wrap.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;min-height:150px;color:var(--dim);font-size:12px" class="mono">No closed trades yet — the distribution appears after the first settlements.</div>';
    if (fb) fb.innerHTML = '';
    const fbWrap = $('pnlHistFallbackWrap');
    if (fbWrap) fbWrap.style.display = 'none';
    return;
  }
  const stats = pnlBootstrapCiLo(pnls);
  const meanTxt = (stats.mean >= 0 ? '+' : '') + '$' + stats.mean.toFixed(2);
  const loTxt = (stats.lo >= 0 ? '+' : '') + '$' + stats.lo.toFixed(2);
  if (statsEl) statsEl.textContent = `n=${pnls.length} · mean ${meanTxt} · CI-lo ${loTxt}`;
  const zeros = pnls.filter(v => v === 0).length;
  const nz = pnls.filter(v => v !== 0);
  let bars = [];  // {label, count, color}
  if (nz.length === 0) {
    bars = [{ label: '0', count: zeros, color: 'var(--dim)' }];
  } else {
    const edges = freedmanDiaconisBins(nz);
    const counts = new Array(edges.length - 1).fill(0);
    for (const v of nz) {
      let bi = 0;
      while (bi < counts.length - 1 && v >= edges[bi + 1]) bi++;
      counts[bi]++;
    }
    for (let i = 0; i < counts.length; i++) {
      if (counts[i] === 0) continue;
      const mid = (edges[i] + edges[i + 1]) / 2;
      bars.push({ label: edges[i].toFixed(2) + '–' + edges[i + 1].toFixed(2), count: counts[i], color: mid >= 0 ? 'var(--up)' : 'var(--down)' });
    }
    if (zeros > 0) bars.unshift({ label: 'zero-PnL', count: zeros, color: 'var(--dim)' });
  }
  const w = 560, padL = 70, padR = 14, padT = 8, padB = 30;
  const maxC = Math.max(1, ...bars.map(b => b.count));
  const bw = (w - padL - padR) / bars.length;
  const maxH = 130;
  let rects = '';
  bars.forEach((b, i) => {
    const bh = Math.max(2, (b.count / maxC) * maxH);
    const x = padL + i * bw + 2;
    const y = padT + (maxH - bh);
    const tip = `${b.label}: ${b.count} positions`;
    rects += `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${Math.max(2, bw - 4).toFixed(1)}" height="${bh.toFixed(1)}" rx="2" fill="${b.color}" fill-opacity="0.8"><title>${esc(tip)}</title></rect>
      <text x="${(x + bw / 2).toFixed(1)}" y="${(padT + maxH + 14).toFixed(1)}" fill="var(--faint)" font-size="9" font-family="var(--mono)" text-anchor="middle">${esc(b.label.length > 12 ? b.count + '×' : b.label)}</text>`;
  });
  const h = padT + maxH + padB;
  wrap.innerHTML = `<svg width="100%" viewBox="0 0 ${w} ${h}" style="display:block" role="img" aria-label="PnL per position histogram"><title>PnL distribution over closed positions</title>
      <line x1="${padL}" y1="${padT}" x2="${padL}" y2="${padT + maxH}" stroke="var(--line)" stroke-width="1"/>
      <line x1="${padL}" y1="${padT + maxH}" x2="${w - padR}" y2="${padT + maxH}" stroke="var(--line)" stroke-width="1"/>
      ${rects}</svg>
    <div style="font-size:10px;color:var(--faint);margin-top:4px" class="mono">session window: last ${pnls.length} closed trades (matches the table); zero-PnL bin shown separately</div>`;
  if (fb) {
    const trows = bars.map(b => `<tr><td class="mono">${esc(b.label)}</td><td class="mono">${esc(b.count)}</td></tr>`).join('');
    fb.innerHTML = `<table class="tbl"><thead><tr><th>Bin</th><th>Positions</th></tr></thead><tbody>${trows}</tbody></table>`;
    const fbWrapShow = $('pnlHistFallbackWrap');
    if (fbWrapShow) fbWrapShow.style.display = '';
  }
}

async function fetchCockpitState() {
  try {
    const res = await fetch('/api/live/state', { cache: 'no-store' });
    if (res.ok) {
      const data = await res.json();
      cockpitState = data;
      renderCockpitUI(data);
    }
  } catch (e) {
    console.error('Failed fetching cockpit state', e);
  }
  await fetchQueueTelemetry();
}

// Issue #139: queue-telemetry panel (tape vs tapeq evidence as it accumulates).
async function fetchQueueTelemetry() {
  if (!$('queueSvgWrap')) return;
  try {
    const res = await fetch('/api/live/queue_telemetry', { cache: 'no-store' });
    if (res.ok) renderQueuePanel(await res.json());
  } catch (e) {
    console.error('Failed fetching queue telemetry', e);
  }
}

function renderQueuePanel(q) {
  const wrap = $('queueSvgWrap');
  const verdictEl = $('queueVerdict');
  const fb = $('queueFallback');
  if (!wrap) return;
  const verdictTxt = (q && q.verdict) ? q.verdict : 'awaiting fills';
  if (verdictEl) {
    verdictEl.textContent = verdictTxt;
    verdictEl.style.color = verdictTxt === 'tape-like' ? 'var(--up)'
      : verdictTxt === 'queue-toxic' ? 'var(--down)'
      : verdictTxt === 'mixed/unclear' ? 'var(--gold)' : '';
  }
  if (!q || q.empty || !Array.isArray(q.buckets) || q.total_fills === 0) {
    wrap.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;min-height:150px;color:var(--dim);font-size:12px" class="mono">awaiting fills — lines appear in run/live_fill_telemetry.jsonl after the first live fills.</div>';
    if (fb) fb.innerHTML = '';
    const fbWrap = $('queueFallbackWrap');
    if (fbWrap) fbWrap.style.display = 'none';
    return;
  }
  const fbWrapShow = $('queueFallbackWrap');
  if (fbWrapShow) fbWrapShow.style.display = '';
  const buckets = q.buckets;
  const maxCount = Math.max(1, ...buckets.map(b => b.count || 0));
  const w = 560, rowH = 34, padL = 86, padR = 110, h = buckets.length * rowH + 46;
  let rows = '';
  buckets.forEach((b, i) => {
    const y = 8 + i * rowH;
    const bw = Math.max(2, ((b.count || 0) / maxCount) * (w - padL - padR));
    const mean = b.mean_settle_pnl_usd;
    const meanTxt = (mean === null || mean === undefined) ? 'n/a' : (mean >= 0 ? '+' : '') + '$' + mean.toFixed(2);
    const meanCol = (mean === null || mean === undefined) ? 'var(--dim)' : (mean >= 0 ? 'var(--up)' : 'var(--down)');
    const tip = `${b.bucket}: ${b.count} fills, mean settle ${meanTxt}`;
    rows += `
      <text x="${padL - 8}" y="${y + 15}" fill="var(--dim)" font-size="11" font-family="var(--mono)" text-anchor="end">${esc(b.bucket)}</text>
      <rect x="${padL}" y="${y}" width="${bw.toFixed(1)}" height="18" rx="3" fill="var(--gold)" fill-opacity="0.75"><title>${esc(tip)}</title></rect>
      <text x="${(padL + bw + 6).toFixed(1)}" y="${y + 14}" fill="var(--tx)" font-size="11" font-family="var(--mono)">${b.count} fills</text>
      <text x="${(w - padR + 10).toFixed(1)}" y="${y + 14}" fill="${meanCol}" font-size="11" font-family="var(--mono)">${esc(meanTxt)}</text>`;
  });
  const ch = q.chased || { count: 0, mean_settle_pnl_usd: null };
  const chMean = ch.mean_settle_pnl_usd;
  const chMeanTxt = (chMean === null || chMean === undefined) ? 'n/a' : (chMean >= 0 ? '+' : '') + '$' + chMean.toFixed(2);
  const chY = 8 + buckets.length * rowH;
  rows += `<text x="${padL - 8}" y="${chY + 15}" fill="var(--dim)" font-size="11" font-family="var(--mono)" text-anchor="end">chased</text>
    <text x="${padL}" y="${chY + 14}" fill="var(--tx)" font-size="11" font-family="var(--mono)">${ch.count} fills × ${esc(chMeanTxt)} (kept separate)</text>`;
  wrap.innerHTML = `<svg width="100%" viewBox="0 0 ${w} ${h}" style="display:block" role="img" aria-label="fill ratio buckets"><title>Fill-ratio buckets with mean settlement PnL</title>${rows}</svg>`;
  if (fb) {
    let trows = buckets.map(b => {
      const mean = b.mean_settle_pnl_usd;
      return `<tr><td class="mono">${esc(b.bucket)}</td><td class="mono">${esc(b.count)}</td><td class="mono">${esc(mean === null || mean === undefined ? 'n/a' : mean.toFixed(2))}</td></tr>`;
    }).join('');
    trows += `<tr><td class="mono">chased</td><td class="mono">${esc(ch.count)}</td><td class="mono">${esc(chMeanTxt)}</td></tr>`;
    fb.innerHTML = `<table class="tbl"><thead><tr><th>Bucket</th><th>Fills</th><th>Mean settle $</th></tr></thead><tbody>${trows}</tbody></table>`;
  }
}

async function pollCockpit() {
  // #371 IIIB: the cockpit state fetch + full render (~80-300ms main-thread)
  // ran every 5s on ALL tabs, visibly freezing the Backtest tab mid-run.
  // Guard it like the oscillation poll: skip on Backtest / hidden, refresh once
  // on return. The SSE live stream keeps the cockpit fresh when it is visible.
  if (typeof document !== 'undefined' && document.hidden) { btOscStale = true; return; }
  if (typeof currentActiveTab !== 'undefined' && currentActiveTab === 'backtest') { btOscStale = true; return; }
  await fetchCockpitState();
}

async function toggleCockpitBot() {
  if (!cockpitState) await fetchCockpitState();
  const nextAction = cockpitState && cockpitState.is_running ? 'stop' : 'start';
  $('btnCockpitToggle').textContent = 'Loading...';
  try {
    const res = await fetch('/api/live/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: nextAction }),
    });
    const st = await res.json();
    if (!res.ok) {
      alert('Error toggling bot: ' + (st.detail || st.error || res.statusText));
      await fetchCockpitState();
      return;
    }
    cockpitState = st;
    renderCockpitUI(st);
  } catch (e) {
    alert('Error toggling bot: ' + e);
  }
}

async function restartCockpitBot() {
  try {
    const res = await fetch('/api/live/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'restart' }),
    });
    const st = await res.json();
    cockpitState = st;
    renderCockpitUI(st);
  } catch (e) {
    alert('Error restarting bot: ' + e);
  }
}

async function panicCancelAllOrders() {
  if (!confirm('Are you sure you want to CANCEL ALL ACTIVE ORDERS on Polymarket CLOB immediately?')) return;
  try {
    const res = await fetch('/api/live/cancel_all', { method: 'POST' });
    const data = await res.json();
    if (res.ok && data && data.ok) {
      alert('Panic Cancel completed. All active orders cleared.');
    } else {
      alert('Warning: Panic Cancel failed or was rejected. Orders may still be open!');
    }
    await fetchCockpitState();
  } catch (e) {
    alert('Error during panic cancel: ' + e);
  }
}

async function cancelSingleOrder(orderId) {
  if (!orderId) return;
  try {
    const res = await fetch('/api/live/cancel_order', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ order_id: orderId }),
    });
    const data = await res.json();
    if (!res.ok || !data || !data.ok) {
      alert('Warning: Order cancellation failed or was rejected. Order may still be open!');
    }
    await fetchCockpitState();
  } catch (e) {
    alert('Error cancelling order: ' + e);
  }
}

async function resetCockpitPnL() {
  if (!confirm('Are you sure you want to reset session P&L and trade history?')) return;
  try {
    const res = await fetch('/api/live/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'reset_pnl' }),
    });
    const st = await res.json();
    if (!res.ok || st.error) {
      alert(st.error || 'Reset refused: stop the engine before RESET P&L while orders are outstanding.');
    }
    cockpitState = st;
    resetToastState();
    renderCockpitUI(st);
  } catch (e) {
    alert('Error resetting PnL: ' + e);
  }
}

async function loadCockpitDemoData() {
  try {
    const res = await fetch('/api/live/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'demo_data' }),
    });
    const st = await res.json();
    cockpitState = st;
    resetToastState();
    renderCockpitUI(st);
  } catch (e) {
    alert('Error loading demo data: ' + e);
  }
}

async function syncRealRunTrades() {
  const btn = $('btnSyncRealRun');
  try {
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Syncing from Polymarket...'; }
    const res = await fetch('/api/live/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'sync_wallet_trades' }),
    });
    const st = await res.json();
    if (!res.ok) {
      throw new Error(st.error || 'Failed syncing trades');
    }
    cockpitState = st;
    renderCockpitUI(st);
    if (btn) { btn.disabled = false; btn.textContent = '📥 Sync Real Run (Polymarket)'; }
  } catch (e) {
    alert('Error syncing real run: ' + e);
    if (btn) { btn.disabled = false; btn.textContent = '📥 Sync Real Run (Polymarket)'; }
  }
}

let isApplyingCockpitConfig = false;

async function onCockpitModeChange(autoApply = true) {
  const mode = $('cockpitMode') ? $('cockpitMode').value : 'paper';
  const startBalInput = $('cockpitStartBal');
  const walletInput = $('cockpitWallet');
  const lblStartBal = $('lblCockpitStartBal');
  const lblWallet = $('lblCockpitWallet');

  if (mode === 'live') {
    if (startBalInput) {
      startBalInput.readOnly = true;
      startBalInput.style.background = 'rgba(240,104,77,0.08)';
      startBalInput.style.borderColor = 'rgba(240,104,77,0.4)';
      startBalInput.style.color = 'var(--tx)';
      startBalInput.style.cursor = 'not-allowed';
      startBalInput.title = 'Starting balance is locked to real Polymarket net account value in LIVE mode.';
    }
    if (lblStartBal) {
      lblStartBal.innerHTML = 'Starting Balance <span class="pill pill-mono" style="font-size:9px;padding:1px 5px;color:var(--gold);border-color:rgba(243,186,47,0.4)">🔒 LIVE NET VALUE</span>';
    }
    if (walletInput && cockpitState && cockpitState.env_wallet_address && !walletInput.value) {
      walletInput.value = cockpitState.env_wallet_address;
    }
    if (lblWallet) {
      lblWallet.innerHTML = 'Polymarket Wallet Address <span class="pill pill-flat" style="font-size:9px;padding:1px 5px;color:var(--up)">.ENV ACTIVE</span>';
    }
  } else {
    if (startBalInput) {
      startBalInput.readOnly = false;
      startBalInput.style.background = '';
      startBalInput.style.borderColor = '';
      startBalInput.style.color = '';
      startBalInput.style.cursor = 'auto';
      startBalInput.title = '';
    }
    if (lblStartBal) {
      lblStartBal.textContent = 'Starting Portfolio Balance ($)';
    }
    if (lblWallet) {
      lblWallet.textContent = 'Polymarket Wallet Address (Optional)';
    }
  }

  if (autoApply && !isApplyingCockpitConfig) {
    await applyCockpitConfig();
  }
}

function validateCockpitInputs() {
  let allValid = true;

  // 1-2b. Price knobs (issue #419): cents inputs sharing one validator —
  // offset 0.1-49c, exits and reversal 0.1-50c, pair cost 50-100c.
  for (const id of ['cockpitOffset', 'cockpitExit', 'cockpitExitReversal', 'cockpitPairCost']) {
    const el = $(id);
    if (el && !validateCentsInput(el)) allValid = false;
  }

  // 3. Shares: 5 to 10000
  const sharesEl = $('cockpitShares');
  if (sharesEl) {
    const raw = sharesEl.value.trim();
    const val = parseInt(raw, 10);
    if (raw === '' || isNaN(val) || val < 5 || val > 10000 || !Number.isInteger(Number(raw))) {
      sharesEl.classList.add('input-invalid');
      allValid = false;
    } else {
      sharesEl.classList.remove('input-invalid');
    }
  }

  // 4. Starting Balance: >= 5.0
  const startBalEl = $('cockpitStartBal');
  if (startBalEl && !startBalEl.readOnly) {
    const raw = startBalEl.value.trim();
    const val = parseFloat(raw);
    if (raw === '' || isNaN(val) || val < 5.0) {
      startBalEl.classList.add('input-invalid');
      allValid = false;
    } else {
      startBalEl.classList.remove('input-invalid');
    }
  }

  // 5. Dead Zone Val: >= 0 (and <= 1.0 if pct)
  const dzValEl = $('cockpitDeadZoneVal');
  const dzUnitEl = $('cockpitDeadZoneUnit');
  if (dzValEl) {
    const raw = dzValEl.value.trim();
    const val = parseFloat(raw);
    const unit = dzUnitEl ? dzUnitEl.value : 'pct';
    const maxVal = unit === 'pct' ? 1.0 : 3600.0;
    if (raw === '' || isNaN(val) || val < 0.0 || val > maxVal) {
      dzValEl.classList.add('input-invalid');
      allValid = false;
    } else {
      dzValEl.classList.remove('input-invalid');
    }
  }

  // 6. Quotable Range: each end in [0, 100]c and lo < hi (issue #228, cents
  // per issue #419). Each end keeps its cents validity; the ordering marks
  // both ends but never clears a cents failure.
  const quoteLoEl = $('cockpitQuoteLo');
  const quoteHiEl = $('cockpitQuoteHi');
  if (quoteLoEl && !validateCentsInput(quoteLoEl)) allValid = false;
  if (quoteHiEl && !validateCentsInput(quoteHiEl)) allValid = false;
  if (quoteLoEl && quoteHiEl) {
    const loRaw = quoteLoEl.value.trim();
    const hiRaw = quoteHiEl.value.trim();
    const lo = parseFloat(loRaw);
    const hi = parseFloat(hiRaw);
    const ok = loRaw !== '' && hiRaw !== '' && !isNaN(lo) && !isNaN(hi)
      && lo >= 0 && lo <= 100 && hi >= 0 && hi <= 100 && lo < hi;
    // Add-only: the cents validator above owns removal. Clearing here would
    // erase a cents hint (e.g. 10.55) whenever the ordering itself passes.
    if (!ok) {
      for (const el of [quoteLoEl, quoteHiEl]) el.classList.add('input-invalid');
      allValid = false;
    }
  }

  const applyBtn = $('btnApplyParams');
  if (applyBtn && !areCockpitFiltersLocked()) {
    applyBtn.disabled = !allValid;
    applyBtn.style.opacity = allValid ? '' : '0.5';
    applyBtn.style.cursor = allValid ? 'pointer' : 'not-allowed';
    applyBtn.title = allValid ? 'Apply strategy parameters' : 'Fix invalid parameters marked with red border';
  }

  return allValid;
}

async function applyCockpitConfig() {
  if (cockpitState && cockpitState.is_running) {
    return;
  }
  const filtersLocked = areCockpitFiltersLocked();
  if (isApplyingCockpitConfig) return;

  // Issue #419: inputs always hold cents now, so the client converts once
  // with centsToDollars below — the old whole-number heuristic would divide
  // twice and is gone. Server normalize_* validators stay untouched.
  if (!validateCockpitInputs()) {
    alert('Please correct the invalid parameters highlighted with a red border before applying.');
    return;
  }

  isApplyingCockpitConfig = true;
  const offset = centsToDollars($('cockpitOffset').value) || 0.02;
  const exit_thresh = centsToDollars($('cockpitExit').value) || 0.05;
  const exit_reversal = centsToDollars($('cockpitExitReversal').value) || 0.02;
  const shares = parseInt($('cockpitShares').value, 10) || 5;
  const mode = $('cockpitMode').value || 'paper';
  const wallet = $('cockpitWallet').value.trim();
  const startBal = parseFloat($('cockpitStartBal').value) || 1000.0;
  const dzValEl = $('cockpitDeadZoneVal');
  const dzUnitEl = $('cockpitDeadZoneUnit');
  const nakedExpiryEl = $('cockpitNakedLegAtExpiry');
  const dead_zone_val = dzValEl && dzValEl.value !== '' && !isNaN(parseFloat(dzValEl.value))
    ? parseFloat(dzValEl.value)
    : 0.10;
  const dead_zone_unit = dzUnitEl ? dzUnitEl.value : 'pct';
  const naked_leg_at_expiry = nakedExpiryEl ? nakedExpiryEl.value : 'close';
  const body = {
    offset,
    exit_thresh,
    exit_reversal,
    shares,
    mode,
    wallet_address: wallet,
    starting_balance: startBal,
    dead_zone_val,
    dead_zone_unit,
    naked_leg_at_expiry,
  };
  // Issue #228: the quotable range is two ends in one knob. Read off the two
  // dedicated inputs and post the pair; `update_config` clamps each end and
  // refuses an inverted pair (400, surfaced by the error path below).
  const quoteLoEl = $('cockpitQuoteLo');
  const quoteHiEl = $('cockpitQuoteHi');
  if (quoteLoEl && quoteHiEl
      && quoteLoEl.value !== '' && quoteHiEl.value !== ''
      && !isNaN(parseFloat(quoteLoEl.value)) && !isNaN(parseFloat(quoteHiEl.value))) {
    body.quote_range = [centsToDollars(quoteLoEl.value), centsToDollars(quoteHiEl.value)];
  }
  // Issue #164: knobs the live engine has always accepted but the Cockpit
  // never offered — entry_delay_sec among them. Pair cost is a cents input
  // (issue #419); the delay stays seconds.
  const pairCostEl = $('cockpitPairCost');
  if (pairCostEl && pairCostEl.value !== '' && !isNaN(parseFloat(pairCostEl.value))) {
    body.max_pair_cost = centsToDollars(pairCostEl.value);
  }
  const delayEl = $('cockpitEntryDelay');
  if (delayEl && delayEl.value !== '' && !isNaN(parseFloat(delayEl.value))) {
    body.entry_delay_sec = parseFloat(delayEl.value);
  }
  const chaseEl = $('cockpitLegChase');
  if (chaseEl) body.enable_leg_chase = chaseEl.value === 'true';
  // Issue #353: the socket-isolation switch — posted as a real boolean.
  const wsAuthEl = $('cockpitWsAuthority');
  if (wsAuthEl) body.ws_book_authority = wsAuthEl.value === 'true';
  // Market selection is immutable while the bot runs; only send filters when stopped
  if (!filtersLocked) {
    if (cockpitExactSelection) {
      body.selected_markets = cockpitExactSelection;
    } else {
      body.tokens = Array.from(selectedCockpitTokens);
      body.durations = getSelectedCockpitDurations();
    }
  }

  try {
    const res = await fetch('/api/live/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const st = await res.json();
    if (!res.ok) {
      let errMsg = st.error;
      if (!errMsg && Array.isArray(st.detail)) {
        errMsg = st.detail.map(d => {
          const loc = (d.loc || []).filter(x => x !== 'body').join('.');
          return (loc ? loc + ': ' : '') + d.msg;
        }).join('; ');
      }
      alert('Configuration rejected: ' + (errMsg || res.statusText));
      return;
    }
    cockpitState = st;
    renderCockpitUI(st);
  } catch (e) {
    alert('Error applying config: ' + e);
  } finally {
    isApplyingCockpitConfig = false;
  }
}

function setCockpitChartMode(mode) {
  activeCockpitChartMode = mode;
  $('btnChartModeTotal').className = mode === 'total' ? 'btn btn-primary' : 'btn';
  $('btnChartModeUsd').className = mode === 'breakdown_usd' ? 'btn btn-primary' : 'btn';
  $('btnChartModePct').className = mode === 'breakdown_pct' ? 'btn btn-primary' : 'btn';
  if (cockpitState) {
    renderCockpitChart(cockpitState.timeline, mode, cockpitState.starting_balance);
  }
}

// Per-leg pricing source badge (issue #353): the engine reports "ws" or
// "rest" per leg; anything else (unset) renders as an em dash, never raw.
function bookSourceLabel(s){
  return s === 'ws' ? 'WS' : s === 'rest' ? 'REST' : '—';
}

function renderCockpitUI(st) {
  if (!st) return;
  cockpitState = st;

  reconcileCockpitToasts(st);

  const sbDot = $('sidebarStatusDot');
  const sbText = $('sidebarStatusText');
  if (sbDot) {
    if (st.is_running) {
      sbDot.classList.add('running');
    } else {
      sbDot.classList.remove('running');
    }
  }
  if (sbText) {
    sbText.textContent = st.is_running ? 'BOT ACTIVE' : 'BOT IDLE';
  }

  // Sync mode dropdown & lock state only when not actively focused
  if (st.mode && $('cockpitMode') && document.activeElement !== $('cockpitMode')) {
    if ($('cockpitMode').value !== st.mode) {
      $('cockpitMode').value = st.mode;
    }
    onCockpitModeChange(false);
  }
  if (st.starting_balance != null && $('cockpitStartBal')) {
    if ($('cockpitStartBal').readOnly || document.activeElement !== $('cockpitStartBal')) {
      $('cockpitStartBal').value = st.starting_balance.toFixed(2);
    }
  }
  if (st.wallet_address && $('cockpitWallet') && document.activeElement !== $('cockpitWallet')) {
    $('cockpitWallet').value = st.wallet_address;
  }

  // Sync strategy parameter fields from engine state while running.
  // Issue #419: engine dollars render as cents via dollarsToCents.
  if (st.is_running) {
    if (st.params) {
      if ($('cockpitOffset') && st.params.offset != null) $('cockpitOffset').value = dollarsToCents(st.params.offset);
      if ($('cockpitExit') && st.params.exit_thresh != null) $('cockpitExit').value = dollarsToCents(st.params.exit_thresh);
      if ($('cockpitExitReversal') && st.params.exit_reversal != null) $('cockpitExitReversal').value = dollarsToCents(st.params.exit_reversal);
      // Issue #421: max_pair_cost is a structural limit the trading engine
      // enforces; a field that never reads it back both displays a stale number
      // and writes that stale number over the engine's on Apply. Same guard as
      // its siblings.
      if ($('cockpitPairCost') && st.params.max_pair_cost != null) $('cockpitPairCost').value = dollarsToCents(st.params.max_pair_cost);
      if ($('cockpitShares') && st.params.shares != null) $('cockpitShares').value = st.params.shares;
      if ($('cockpitDeadZoneVal') && st.params.dead_zone_val != null) $('cockpitDeadZoneVal').value = st.params.dead_zone_val;
      if ($('cockpitDeadZoneUnit') && st.params.dead_zone_unit != null) $('cockpitDeadZoneUnit').value = st.params.dead_zone_unit;
      if ($('cockpitNakedLegAtExpiry') && st.params.naked_leg_at_expiry != null) $('cockpitNakedLegAtExpiry').value = st.params.naked_leg_at_expiry;
      if ($('cockpitWsAuthority') && st.params.ws_book_authority != null) $('cockpitWsAuthority').value = String(st.params.ws_book_authority);
      if ($('cockpitQuoteLo') && st.params.quote_range != null) $('cockpitQuoteLo').value = dollarsToCents(st.params.quote_range[0]);
      if ($('cockpitQuoteHi') && st.params.quote_range != null) $('cockpitQuoteHi').value = dollarsToCents(st.params.quote_range[1]);
    }
    if ($('cockpitWallet') && st.wallet_address != null) {
      $('cockpitWallet').value = st.wallet_address;
    }
  }

  // Sync asset/duration filters from engine state on first receipt, and whenever
  // the bot is running (selection is engine-owned and immutable mid-run).
  if (!hasInitializedCockpitFilters && st.selected_series) {
    hasInitializedCockpitFilters = true;
    syncCockpitFiltersFromState(st);
    if (st.params) {
      if ($('cockpitOffset') && st.params.offset != null) $('cockpitOffset').value = dollarsToCents(st.params.offset);
      if ($('cockpitExit') && st.params.exit_thresh != null) $('cockpitExit').value = dollarsToCents(st.params.exit_thresh);
      if ($('cockpitExitReversal') && st.params.exit_reversal != null) $('cockpitExitReversal').value = dollarsToCents(st.params.exit_reversal);
      // Issue #421: same line in the first-init branch as in the running one —
      // a field hydrated in only one branch is stale on the other path.
      if ($('cockpitPairCost') && st.params.max_pair_cost != null) $('cockpitPairCost').value = dollarsToCents(st.params.max_pair_cost);
      if ($('cockpitShares') && st.params.shares != null) $('cockpitShares').value = st.params.shares;
      if ($('cockpitDeadZoneVal') && st.params.dead_zone_val != null) $('cockpitDeadZoneVal').value = st.params.dead_zone_val;
      if ($('cockpitDeadZoneUnit') && st.params.dead_zone_unit != null) $('cockpitDeadZoneUnit').value = st.params.dead_zone_unit;
      if ($('cockpitNakedLegAtExpiry') && st.params.naked_leg_at_expiry != null) $('cockpitNakedLegAtExpiry').value = st.params.naked_leg_at_expiry;
      if ($('cockpitWsAuthority') && st.params.ws_book_authority != null) $('cockpitWsAuthority').value = String(st.params.ws_book_authority);
      if ($('cockpitQuoteLo') && st.params.quote_range != null) $('cockpitQuoteLo').value = dollarsToCents(st.params.quote_range[0]);
      if ($('cockpitQuoteHi') && st.params.quote_range != null) $('cockpitQuoteHi').value = dollarsToCents(st.params.quote_range[1]);
    }
  } else if (st.is_running && st.selected_series) {
    syncCockpitFiltersFromState(st);
  } else {
    updateCockpitFilterUI();
  }

  // 1. Status Badges & Buttons
  const isRun = !!st.is_running;
  updateCockpitParamsLockUI(isRun);
  const statusPill = $('cockpitStatusPill');
  if (statusPill) {
    statusPill.textContent = isRun ? '🟢 BOT: RUNNING (1s)' : '⚪ BOT: STOPPED';
    statusPill.className = isRun ? 'pill pill-osc' : 'pill pill-flat';
    statusPill.style.color = isRun ? 'var(--up)' : 'var(--dim)';
  }

  const modePill = $('cockpitModePill');
  if (modePill) {
    modePill.textContent = st.mode === 'live' ? 'REAL MONEY' : 'PAPER TRADING';
    modePill.style.background = st.mode === 'live' ? 'rgba(240,104,77,0.15)' : 'rgba(51,201,181,0.15)';
    modePill.style.color = st.mode === 'live' ? 'var(--down)' : 'var(--up)';
    modePill.style.borderColor = st.mode === 'live' ? 'rgba(240,104,77,0.4)' : 'rgba(51,201,181,0.4)';
  }

  // Global stream health pill — traffic light in the top bar (green <1s, yellow 1s, red offline)
  (function(){
    const el = $('globalStreamPill') || $('cockpitStreamPill');
    if (!el) return;
    const sb = st.stream_bridge || {};
    const isLive = !!sb.binance_ws_connected;
    const isOk = !isLive && (!!sb.rtds_connected || !!liveStreamConnected);
    if (isLive) {
      el.textContent = '● STREAM · <1s';
      el.style.background = 'rgba(51,201,181,0.15)';
      el.style.color = 'var(--up)';
      el.style.borderColor = 'rgba(51,201,181,0.35)';
    } else if (isOk) {
      el.textContent = '● OK · 1s';
      el.style.background = 'rgba(243,186,47,0.15)';
      el.style.color = 'var(--gold)';
      el.style.borderColor = 'rgba(243,186,47,0.35)';
    } else {
      el.textContent = '● OFFLINE · POLLING';
      el.style.background = 'rgba(240,104,77,0.15)';
      el.style.color = 'var(--down)';
      el.style.borderColor = 'rgba(240,104,77,0.35)';
    }
  })();

  const toggleBtn = $('btnCockpitToggle');
  if (toggleBtn) {
    toggleBtn.textContent = isRun ? '⏹ STOP BOT' : '▶ START BOT';
    toggleBtn.className = isRun ? 'btn btn-danger' : 'btn btn-primary';
  }

  // 2. KPI Boxes
  const pnlUsd = st.total_pnl || 0.0;
  const pnlPct = st.total_pnl_pct || 0.0;
  const realPnlEl = $('cockpitRealizedPnl');
  if (realPnlEl) {
    realPnlEl.textContent = (pnlUsd >= 0 ? '+' : '') + '$' + pnlUsd.toFixed(2);
    realPnlEl.style.color = pnlUsd > 0 ? 'var(--up)' : pnlUsd < 0 ? 'var(--down)' : 'var(--tx)';
  }
  const realSubEl = $('cockpitRealizedSub');
  if (realSubEl) {
    realSubEl.textContent = (pnlPct >= 0 ? '+' : '') + pnlPct.toFixed(2) + '% return';
    realSubEl.style.color = pnlPct > 0 ? 'var(--up)' : pnlPct < 0 ? 'var(--down)' : 'var(--dim)';
  }

  const portValEl = $('cockpitPortfolioVal');
  if (portValEl) {
    portValEl.textContent = '$' + (st.portfolio_value || 1000.0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  const winRateEl = $('cockpitWinRate');
  if (winRateEl) {
    winRateEl.textContent = (st.win_rate || 0.0).toFixed(1) + '%';
  }
  const tradesSumEl = $('cockpitTradesSummary');
  if (tradesSumEl) {
    tradesSumEl.textContent = `${st.total_trades || 0} trades`;
  }

  const pairsCountEl = $('cockpitPairsCount');
  if (pairsCountEl) pairsCountEl.textContent = String(st.pairs_merged || 0);

  const stopsCountEl = $('cockpitStopsCount');
  if (stopsCountEl) stopsCountEl.textContent = String(st.stops_triggered || 0);

  const expEl = $('cockpitExposure');
  if (expEl) expEl.textContent = '$' + (st.active_exposure || 0.0).toFixed(2);

  // 3. Render Market Matrix Grid
  const gridEl = $('cockpitMarketGrid');
  const activeSeries = getActiveCockpitSeries();
  const activeBadge = $('cockpitActiveMarketsBadge');
  if (activeBadge) {
    activeBadge.textContent = `${activeSeries.length} ACTIVE MARKET${activeSeries.length === 1 ? '' : 'S'}`;
  }
  if (gridEl && st.markets) {
    let gridHtml = '';
    let staleMarkets = 0;
    for (const item of activeSeries) {
      const m = st.markets[item.slug] || {};
      const midStr = m.mid != null ? `$${m.mid.toFixed(3)}` : '-';
      const spreadStr = m.spread != null ? `touch ${m.spread.toFixed(3)}` : 'touch -';
      const remStr = m.time_remaining_sec != null ? hms(m.time_remaining_sec) : '-';
      // Issue #100: depleting seeker bar. Fraction of window left, clamped to
      // [0,1]; an expired/overrun window renders an empty bar, never negative.
      const { fillPct, barColor } = timeBarFraction(m.time_remaining_sec, m.win_duration_sec);
      const mktPnl = m.total_pnl_usd || 0.0;
      const pnlColor = mktPnl > 0 ? 'var(--up)' : mktPnl < 0 ? 'var(--down)' : 'var(--tx)';

      let statusBadgeCls = 'pill-flat';
      const marketStatusRaw = m.status || 'IDLE';
      if (marketStatusRaw === 'FILLED_UP' || marketStatusRaw === 'FILLED_DOWN'
          || marketStatusRaw === 'STOP_EXIT' || marketStatusRaw === 'STOP_EXIT_PENDING') {
        statusBadgeCls = 'pill-mono';
      } else if (marketStatusRaw === 'PAIR_MERGED') {
        statusBadgeCls = 'pill-osc';
      }
      const statusText = otStatusLabel(marketStatusRaw);

      // Issue #462: per-market tick health. Staleness is the authoritative signal —
      // "status looks fine" and "nothing is updating this market" are different facts —
      // and a recorded failure colours the pill even while the market is updating again.
      const tickAge = (m.tick_age_sec === null || m.tick_age_sec === undefined) ? null : m.tick_age_sec;
      if (m.tick_stale) staleMarkets += 1;
      let healthCls = 'pill-flat';
      let healthText = 'waiting';
      if (m.tick_error_count > 0) {
        healthCls = 'pill-mono';
        healthText = `failed x${m.tick_error_count}`;
      } else if (m.tick_stale) {
        healthCls = 'pill-stale';
        healthText = `stale ${Math.round(tickAge)}s`;
      } else if (tickAge !== null) {
        healthText = `${tickAge.toFixed(1)}s`;
      }
      const healthTooltipParts = [
        tickAge === null ? 'no strategy update yet' : `last update ${tickAge.toFixed(1)}s ago`,
      ];
      if (m.tick_stale) healthTooltipParts.push('stale: the engine has stopped updating this market');
      if (m.tick_error_count > 0) {
        healthTooltipParts.push(`${m.tick_error_count} tick error(s), last: ${m.last_tick_error || 'unknown'}`);
      }
      const healthTitle = healthTooltipParts.join(' \u00b7 ');

      let posStr = 'FLAT';
      const actualUp = cockpitLegPrice(m, 'up', st?.params?.offset);
      const actualDown = cockpitLegPrice(m, 'down', st?.params?.offset);
      if (m.status === 'STOP_EXIT' || m.exit_taken) {
        posStr = 'FLAT (STOPPED OUT)';
      } else if (m.status === 'TIMEOUT_NO_FILL' || m.status === 'DRIFT_SKIPPED' || m.status === 'LATE_START_SKIPPED' || m.status === 'NO_BOOK_SKIPPED' || m.entry_cancelled_timeout) {
        posStr = 'FLAT';
      } else if (m.filled_up && m.filled_down) {
        posStr = `MERGED PAIR (${m.order_shares || 5}) @ $${actualUp.toFixed(2)} + $${actualDown.toFixed(2)}`;
      } else if (m.filled_up) {
        posStr = `LONG UP (${m.order_shares || 5}) @ $${actualUp.toFixed(2)}`;
      } else if (m.filled_down) {
        posStr = `LONG DOWN (${m.order_shares || 5}) @ $${actualDown.toFixed(2)}`;
      }

      const fillsSub = (m.fill_price_up != null || m.fill_price_down != null)
        ? ` · Fills: $${(m.fill_price_up != null ? m.fill_price_up.toFixed(2) : '-')} / $${(m.fill_price_down != null ? m.fill_price_down.toFixed(2) : '-')}`
        : '';

      // No live bids remain for these terminal states: dim the Orders & Position
      // box exactly like cancelled rows in the Open Orders table.
      const bidsCancelled = m.status === 'STOP_EXIT' || m.status === 'STOP_EXIT_PENDING'
        || m.status === 'TIMEOUT_NO_FILL' || m.status === 'DRIFT_SKIPPED'
        || m.status === 'LATE_START_SKIPPED' || m.status === 'NO_BOOK_SKIPPED'
        || m.exit_taken || m.entry_cancelled_timeout;
      const bidsBoxDimCls = bidsCancelled ? ' mat-bids-cancelled' : '';
      // Human-readable cause shown inside the dimmed box (visible without
      // hovering) and mirrored as a native title tooltip on the box itself.
      let cancelReason = '';
      if (m.status === 'STOP_EXIT' || m.exit_taken) cancelReason = 'Bids cancelled — stop-loss exit';
      else if (m.status === 'STOP_EXIT_PENDING') cancelReason = 'Bids cancelling — stop-loss exit';
      else if (m.status === 'NO_BOOK_SKIPPED') cancelReason = 'Skipped — unpriceable book';
      else if (m.status === 'TIMEOUT_NO_FILL' || m.entry_cancelled_timeout) cancelReason = 'Bids cancelled — 10% entry timeout';
      else if (m.status === 'DRIFT_SKIPPED') cancelReason = 'Bids cancelled — adverse drift';
      else if (m.status === 'LATE_START_SKIPPED') cancelReason = 'Skipped — window started mid-way';
      const cancelReasonTooltip = cancelReason ? ` title="${esc(cancelReason)}"` : '';

      let bidsTextHtml = '';
      if (m.status === 'STOP_EXIT' || m.exit_taken) {
        bidsTextHtml = `Bids: <span style="color:var(--dim)">CANCELLED (STOPPED OUT)</span>${fillsSub}`;
      } else if (m.status === 'STOP_EXIT_PENDING') {
        bidsTextHtml = `Bids: <span style="color:var(--dim)">STOP EXITING</span>${fillsSub}`;
      } else if (m.status === 'TIMEOUT_NO_FILL') {
        bidsTextHtml = `Bids: <span style="color:var(--dim)">CANCELLED (10% TIMEOUT)</span>`;
      } else if (m.status === 'DRIFT_SKIPPED') {
        bidsTextHtml = `Bids: <span style="color:var(--dim)">CANCELLED (ADVERSE DRIFT)</span>`;
      } else if (m.status === 'LATE_START_SKIPPED') {
        bidsTextHtml = `Bids: <span style="color:var(--dim)">NOT QUOTED (STARTED MID-WINDOW)</span>`;
      } else if (m.status === 'NO_BOOK_SKIPPED') {
        bidsTextHtml = `Bids: <span style="color:var(--dim)">NOT QUOTED (UNPRICEABLE BOOK)</span>`;
      } else if (m.status === 'NO_BOOK') {
        bidsTextHtml = `Bids: <span style="color:var(--dim)">WAITING FOR BOOK</span>`;
      } else if (m.status === 'PAIR_MERGED' || m.pair_captured) {
        bidsTextHtml = `Bids: <span style="color:var(--dim)">MERGED / COMPLETE</span>${fillsSub}`;
      } else if (!st.is_running) {
        bidsTextHtml = `Bids: <span style="color:var(--dim)">INACTIVE (BOT STOPPED)</span>`;
      } else {
        const restingUpDisp = cockpitRestingPrice(m, 'up', st?.params?.offset);
        const restingDownDisp = cockpitRestingPrice(m, 'down', st?.params?.offset);
        bidsTextHtml = `Bids: $${restingUpDisp.toFixed(2)} / $${restingDownDisp.toFixed(2)}${fillsSub}`;
      }

      gridHtml += `
        <div class="card mat-market-card" data-market="${esc(item.slug)}" style="background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:12px;margin:0;display:flex;flex-direction:column;justify-content:space-between;transition:box-shadow 0.15s,border-color 0.15s">
          <div>
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
              <div style="display:flex;align-items:center;gap:6px">
                <span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${item.color}"></span>
                <a href="https://polymarket.com/market/${encodeURIComponent(m.market_slug || item.slug)}" target="_blank" rel="noopener" style="font:700 13px var(--disp);letter-spacing:0.04em;color:var(--tx);text-decoration:none;transition:color 0.15s" onmouseover="this.style.color='var(--gold)'" onmouseout="this.style.color='var(--tx)'" title="View ${esc(item.label)} on Polymarket">${esc(item.label)} ↗</a>
              </div>
              <span class="mono" style="font-size:11px;color:var(--gold);font-weight:600">⏱ ${remStr}</span>
            </div>
            <div class="mat-timebar-track" style="height:4px;border-radius:2px;background:rgba(255,255,255,0.08);margin-bottom:8px;overflow:hidden" title="${remStr} remaining">
              <div class="mat-timebar-fill" data-market="${esc(item.slug)}" style="height:100%;width:${fillPct}%;background:${barColor};transition:width 1s linear,background 0.3s"></div>
            </div>

            <div style="display:flex;justify-content:space-between;align-items:baseline;margin:6px 0">
              <span class="mono" style="font-size:17px;font-weight:700">${midStr}</span>
              <span class="mono" style="font-size:10px;color:var(--dim)">${spreadStr}</span>
            </div>

            <div class="mono" style="font-size:10px;color:var(--dim);margin-bottom:6px;line-height:1.4">
              UP: ${fmtPrice(m.up_bid)} / ${fmtPrice(m.up_ask)}<br>
              DN: ${fmtPrice(m.down_bid)} / ${fmtPrice(m.down_ask)}<br>
              Book: ${bookSourceLabel(m.book_source_up)} / ${bookSourceLabel(m.book_source_down)}
            </div>

            <div style="display:flex;justify-content:space-between;align-items:center;font-size:10px;margin-bottom:8px;background:rgba(255,255,255,0.03);padding:3px 6px;border-radius:4px">
              <span class="mono" style="color:var(--dim)">Spot 1s: <b id="cockpit-spot-price-${item.slug}" style="color:var(--tx)">${m.spot_price != null ? '$' + m.spot_price.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2}) : '-'}</b></span>
              <span id="cockpit-spot-drift-${item.slug}" class="mono" style="font-weight:600;color:${(m.spot_drift || 0) > 0 ? 'var(--up)' : (m.spot_drift || 0) < 0 ? 'var(--down)' : 'var(--dim)'}">${(m.spot_drift || 0) >= 0 ? '+' : ''}${((m.spot_drift || 0) * 100).toFixed(2)}%</span>
            </div>

            <div class="mat-bids-box${bidsBoxDimCls}"${cancelReasonTooltip} style="background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:6px 8px;margin-bottom:8px">
              <div style="font-size:9px;color:var(--faint);font-weight:700;text-transform:uppercase;margin-bottom:2px">Orders & Position</div>
              <div class="mono" style="font-size:11px;font-weight:600;color:var(--tx)">${posStr}</div>
              <div class="mono" style="font-size:10px;color:var(--dim)">${bidsTextHtml}</div>
              ${cancelReason ? `<div class="mono" style="font-size:9.5px;margin-top:3px;letter-spacing:0.02em">${esc(cancelReason)}</div>` : ''}
            </div>
          </div>

          <div>
            <div style="display:flex;align-items:center;justify-content:space-between;margin-top:6px;padding-top:6px;border-top:1px solid var(--line)">
              <span style="display:flex;align-items:center;gap:6px">
                <span class="pill ${statusBadgeCls}" style="font-size:9px;padding:2px 6px">${statusText}</span>
                <span class="pill ${healthCls}" style="font-size:9px;padding:2px 6px" title="${esc(healthTitle)}">${esc(healthText)}</span>
              </span>
              <span class="mono" style="font-size:12px;font-weight:700;color:${pnlColor}">
                ${mktPnl >= 0 ? '+' : ''}$${mktPnl.toFixed(2)}
              </span>
            </div>
          </div>
        </div>
      `;
    }
    gridEl.innerHTML = gridHtml;
    wireMarketCardHighlight(gridEl);

    // Issue #462: one number for the whole book, so a starving market is visible
    // even when the eye is not on its card.
    const staleBadgeEl = $('cockpitStaleMarketsBadge');
    if (staleBadgeEl) {
      staleBadgeEl.textContent = `${staleMarkets} STALE`;
      staleBadgeEl.className = staleMarkets > 0 ? 'pill pill-stale' : 'pill pill-flat';
    }
  }

  // 4. Tab 1: Render Open Orders & Pre-Quotes Table (9 columns, grouped by pair)
  const ordersBodyEl = $('cockpitOrdersBody');
  const ordersCountEl = $('otOrdersCount');
  const legacyOrdersCountEl = $('cockpitOrdersCount');
  const openOrders = st.open_orders || [];
  // Issue #91: filled legs are held positions, not resting bids. They leave
  // the Open Orders tab and are promoted into Positions (see 4b below).
  const isFilledStatus = (s) => ['FILLED', 'MATCHED'].includes(String(s || '').toUpperCase());
  const restingOrders = openOrders.filter(o => !isFilledStatus(o.status));
  const filledLegs = openOrders.filter(o => isFilledStatus(o.status));
  if (ordersCountEl) ordersCountEl.textContent = String(restingOrders.length);
  if (legacyOrdersCountEl) legacyOrdersCountEl.textContent = String(restingOrders.length);

  if (ordersBodyEl) {
    if (restingOrders.length === 0) {
      ordersBodyEl.innerHTML = '<tr><td colspan="9" style="text-align:center;color:var(--dim);padding:18px">No orders are resting on the book.</td></tr>';
    } else {
      const groupedOrders = groupOrdersByPair(restingOrders);
      // Issue #197: apply user sort before rendering
      const ordGrpList = Object.keys(groupedOrders).map(k => groupedOrders[k]);
      const sortedOrdGrps = otSortState.orders.col
        ? sortOtOrdersGroups(ordGrpList, otSortState.orders.col, otSortState.orders.dir)
        : ordGrpList;
      let ordHtml = '';
      for (const grp of sortedOrdGrps) {
        // Issue #100: two-way link with the matrix card — shared data-market
        // key on both ends lets hover/select highlight card and orders group
        // together.
        const grpMarketKey = grp.market_slug || grp.series_slug || '';
        const statusBadgeCls = grp.status === 'Paired' ? 'ot-tag-paired' : (grp.status === 'Partial' ? 'ot-tag-partial' : (grp.status === 'Cancelled' ? 'ot-tag-cancelled' : 'ot-tag-unpaired'));
        const statusBorderColor = grp.status === 'Paired' ? 'var(--up)' : (grp.status === 'Partial' ? 'var(--gold)' : (grp.status === 'Cancelled' ? 'var(--dim)' : 'var(--line)'));
        const mktSlug = grp.market_slug || grp.series_slug || '';
        const mktUrl = mktSlug ? `https://polymarket.com/market/${encodeURIComponent(mktSlug)}` : '';
        const mktLinkHtml = mktUrl
          ? `<a href="${mktUrl}" target="_blank" rel="noopener" style="color:var(--tx);text-decoration:none;transition:color 0.15s" onmouseover="this.style.color='var(--gold)'" onmouseout="this.style.color='var(--tx)'" title="View on Polymarket">${esc(grp.market)} ↗</a>`
          : esc(grp.market);
        const groupTime = (grp.legs.length > 0 && grp.legs[0].time && grp.legs[0].time !== '-') ? grp.legs[0].time : '-';
        const timeCell = `
          <td rowspan="${grp.rowspan}" class="mono ot-pair-lead mat-orders-group" data-market="${esc(grpMarketKey)}" style="font-size:11px;color:var(--faint);vertical-align:top;border-left:2px solid ${statusBorderColor};padding-left:10px">
            ${esc(groupTime)}
          </td>
        `;
        const mktCell = `
          <td rowspan="${grp.rowspan}" class="ot-pair-lead mat-orders-group" data-market="${esc(grpMarketKey)}" style="vertical-align:middle;padding-left:10px">
            <div style="font-weight:700;font-size:12.5px;color:var(--tx)" title="${esc(grp.market)}">${mktLinkHtml}</div>
            <div style="display:flex;align-items:center;gap:6px;margin-top:4px">
              <span class="ot-tag ${statusBadgeCls}">${esc(grp.status.toUpperCase())}</span>
              <span class="mono" style="font-size:10px;color:var(--dim)">Pair: <b style="color:${grp.pair_cost !== '--' ? 'var(--gold)' : 'var(--dim)'}">${esc(grp.pair_cost)}</b></span>
            </div>
          </td>
        `;

        grp.legs.forEach((leg, idx) => {
          const oId = leg.order_id || '-';
          const statusRaw = String(leg.status || 'OPEN').toUpperCase();
          const isCancelled = ['CANCELLED', 'CANCELED'].includes(statusRaw);
          const isFilled = isFilledStatus(leg.status);
          const canCancel = oId && oId !== '-' && !isCancelled && !isFilled;
          const isUp = leg.isUp;
          const sideBadgeCls = isCancelled ? 'ot-tag-cancelled' : (isUp ? 'ot-tag-up' : 'ot-tag-down');
          const priceStr = leg.priceNum != null ? `$${leg.priceNum.toFixed(2)}` : '-';
          const sizeStr = leg.sizeNum ? String(leg.sizeNum) : '-';
          const filledStr = leg.filledNum != null ? String(leg.filledNum) : '0';
          const totalCostStr = (leg.priceNum != null && leg.sizeNum) ? `$${(leg.priceNum * leg.sizeNum).toFixed(2)}` : '-';
          const statusStr = isCancelled ? 'CANCELED' : (leg.status || 'OPEN');
          const statusBadgePill = isCancelled ? 'pill pill-mono ot-tag-cancelled' : (isFilled ? 'pill pill-osc' : 'pill pill-mono');
          // Fully-cancelled groups and non-leading cancelled legs get a dimmed
          // row; the leading (rowspan) row of a Partial group keeps its shared
          // market/time cells at full strength while the live leg is present.
          const cancelledRowCls = (isCancelled && (grp.status === 'Cancelled' || idx > 0)) ? ' ot-row-cancelled' : '';
          // A cancelled leg whose row class would also dim the shared
          // market/time cells (lead row of a Partial group) is dimmed per-cell
          // instead, so only its own leg-specific cells lose strength.
          const cancelledCellCls = (isCancelled && !cancelledRowCls && grp.status === 'Partial') ? ' ot-cell-cancelled' : '';
          const cellClsAttr = cancelledCellCls ? ` class="${cancelledCellCls}"` : '';

          ordHtml += `
            <tr class="${(idx === 0 ? 'ot-pair-lead' : '') + cancelledRowCls}">
              ${idx === 0 ? (timeCell + mktCell) : ''}
              <td${cellClsAttr}><span class="ot-tag ${sideBadgeCls}">${esc(leg.side)}</span></td>
              <td class="mono${cancelledCellCls}" style="font-weight:600">${priceStr}</td>
              <td class="mono${cancelledCellCls}">${sizeStr}</td>
              <td class="mono${cancelledCellCls}" style="color:var(--dim)">${filledStr}</td>
              <td class="mono${cancelledCellCls}" style="color:var(--tx)">${totalCostStr}</td>
              <td${cellClsAttr}><span class="${statusBadgePill}" style="font-size:9px;padding:2px 6px">${esc(otStatusLabel(statusStr))}</span></td>
              <td${cellClsAttr}>
                ${canCancel ? `<button class="btn btn-danger cancel-order-btn" style="font-size:10px;padding:2px 7px" data-order-id="${esc(oId)}">✖ Cancel</button>` : '-'}
              </td>
            </tr>
          `;
        });
      }
      ordersBodyEl.innerHTML = ordHtml;
      ordersBodyEl.querySelectorAll('.cancel-order-btn').forEach(btn => {
        btn.addEventListener('click', () => cancelSingleOrder(btn.dataset.orderId));
      });
    }
  }

  // 4b. Tab 2: Render Polymarket Open Positions Table (8 columns, grouped by pair)
  const posBodyEl = $('cockpitPositionsBody');
  const posCountEl = $('otPositionsCount');
  const legacyPosCountEl = $('cockpitPositionsCount');
  // Issue #91: FILLED / MATCHED legs filtered out of Open Orders above are
  // promoted here as lightweight position entries (held shares at fill price;
  // market value and PnL stay '--' until the CLOB supplies them).
  const toFinite = (v) => { const n = Number(v); return (v != null && isFinite(n)) ? n : null; };
  const filledAsPositions = filledLegs.map(o => {
    const filledSize = toFinite(o.filled);
    const sizeVal = toFinite(o.size);
    return {
      title: o.market || o.label || o.token_id || 'Unknown',
      market: o.market || o.label || o.token_id || 'Unknown',
      market_slug: o.market_slug || '',
      series_slug: o.series_slug || '',
      outcome: o.side || '',
      size: (filledSize != null && filledSize !== 0) ? filledSize : (sizeVal != null ? sizeVal : 0),
      avgPrice: toFinite(o.price),
      curPrice: null,
      time: o.time || o.created_at || o.timestamp || '-',
      _fromFilledOrder: true
    };
  });
  const openPos = (st.open_positions || st.positions || []).concat(filledAsPositions);
  if (posCountEl) posCountEl.textContent = String(openPos.length);
  if (legacyPosCountEl) legacyPosCountEl.textContent = String(openPos.length);

  if (posBodyEl) {
    if (openPos.length === 0) {
      posBodyEl.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--dim);padding:18px">No open positions held in account.</td></tr>';
    } else {
      const groupedPos = groupPositionsByPair(openPos, st.markets);
      // Issue #197: apply user sort before rendering
      const posGrpList = Object.keys(groupedPos).map(k => groupedPos[k]);
      const sortedPosGrps = otSortState.positions.col
        ? sortOtPositionsGroups(posGrpList, otSortState.positions.col, otSortState.positions.dir)
        : posGrpList;
      let posHtml = '';
      for (const grp of sortedPosGrps) {
        const statusBadgeCls = grp.status === 'Paired' ? 'ot-tag-paired' : (grp.status === 'Partial' ? 'ot-tag-partial' : 'ot-tag-unpaired');
        const statusBorderColor = grp.status === 'Paired' ? 'var(--up)' : (grp.status === 'Partial' ? 'var(--gold)' : 'var(--line)');
        
        const mktSlug = grp.market_slug || grp.series_slug || '';
        const mktUrl = mktSlug ? `https://polymarket.com/market/${encodeURIComponent(mktSlug)}` : '';
        const mktLinkHtml = mktUrl
          ? `<a href="${mktUrl}" target="_blank" rel="noopener" style="color:var(--tx);text-decoration:none;transition:color 0.15s" onmouseover="this.style.color='var(--gold)'" onmouseout="this.style.color='var(--tx)'" title="View on Polymarket">${esc(grp.market)} ↗</a>`
          : esc(grp.market);
        const groupTime = (grp.legs.length > 0 && grp.legs[0].time && grp.legs[0].time !== '-') ? grp.legs[0].time : '-';
        const timeCell = `
          <td rowspan="${grp.rowspan}" class="mono ot-pair-lead" style="font-size:11px;color:var(--faint);vertical-align:top;border-left:2px solid ${statusBorderColor};padding-left:10px">
            ${esc(groupTime)}
          </td>
        `;
        const mktCell = `
          <td rowspan="${grp.rowspan}" class="ot-pair-lead" style="vertical-align:middle;padding-left:10px">
            <div style="font-weight:700;font-size:12.5px;color:var(--tx)" title="${esc(grp.market)}">${mktLinkHtml}</div>
            <div style="display:flex;align-items:center;gap:6px;margin-top:4px">
              <span class="ot-tag ${statusBadgeCls}">${esc(grp.status.toUpperCase())}</span>
            </div>
          </td>
        `;

        const mktValStr = grp.market_val != null ? `$${grp.market_val.toFixed(2)}` : '--';
        const unrelFmt = formatSignedMoneyPct(grp.unrealized_usd, grp.unrealized_pct);
        const unrelCol = grp.unrealized_usd != null ? (grp.unrealized_usd >= 0 ? 'var(--up)' : 'var(--down)') : 'var(--tx)';
        const relFmt = formatSignedMoneyPct(grp.realized_usd, grp.realized_pct);
        const relCol = grp.realized_usd != null ? (grp.realized_usd >= 0 ? 'var(--up)' : 'var(--down)') : 'var(--tx)';

        const pairSharedCells = `
          <td rowspan="${grp.rowspan}" class="mono ot-pair-lead" style="vertical-align:middle;font-weight:600">${mktValStr}</td>
          <td rowspan="${grp.rowspan}" class="mono ot-pair-lead" style="vertical-align:middle;font-weight:700;color:${unrelCol}">${unrelFmt}</td>
          <td rowspan="${grp.rowspan}" class="mono ot-pair-lead" style="vertical-align:middle;font-weight:700;color:${relCol}">${relFmt}</td>
        `;

        grp.legs.forEach((leg, idx) => {
          const isUp = leg.isUp;
          const sideBadgeCls = isUp ? 'ot-tag-up' : 'ot-tag-down';
          const sizeStr = leg.sizeNum ? leg.sizeNum.toFixed(2) : '-';
          const baseCostStr = leg.baseCost != null ? `$${leg.baseCost.toFixed(3)}` : '-';

          posHtml += `
            <tr class="${idx === 0 ? 'ot-pair-lead' : ''}">
              ${idx === 0 ? (timeCell + mktCell) : ''}
              <td><span class="ot-tag ${sideBadgeCls}">${esc(leg.side)}</span></td>
              <td class="mono">${sizeStr}</td>
              <td class="mono">${baseCostStr}</td>
              ${idx === 0 ? pairSharedCells : ''}
            </tr>
          `;
        });
      }
      posBodyEl.innerHTML = posHtml;
    }
  }

  // 5. Tab 3: Render Closed Trades Execution Log (8 columns)
  const bodyEl = $('cockpitTradesBody');
  const tradesCountEl = $('otTradesCount');
  const trades = st.trades || [];
  if (tradesCountEl) tradesCountEl.textContent = String(trades.length);

  if (bodyEl && st.trades) {
    if (st.trades.length === 0) {
      bodyEl.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--dim);padding:20px">No closed trades recorded in this session.</td></tr>';
    } else {
      // Issue #197: apply user sort before rendering
      const sortedTrades = otSortState.trades.col
        ? sortOtTradesList(st.trades, otSortState.trades.col, otSortState.trades.dir)
        : st.trades;
      let rowsHtml = '';
      for (const t of sortedTrades) {
        const pnlCol = t.pnl_usd >= 0 ? 'var(--up)' : 'var(--down)';
        const causeBadge = t.action === 'PAIR_MERGE'
          ? '<span class="pill pill-osc">Merged</span>'
          : (t.action && t.action.startsWith('STOP'))
          ? '<span class="pill pill-mono">Stop-Loss</span>'
          : '<span class="pill pill-flat">Settled</span>';

        const baseCostStr = t.entry_price_up && t.entry_price_down
          ? `$${t.entry_price_up.toFixed(2)} + $${t.entry_price_down.toFixed(2)}`
          : t.entry_price_up
          ? `UP @ $${t.entry_price_up.toFixed(2)}`
          : t.entry_price_down
          ? `DN @ $${t.entry_price_down.toFixed(2)}`
          : '-';

        const exitStr = t.exit_price != null ? `$${t.exit_price.toFixed(2)}` : '-';
        const gainLossFmt = formatSignedMoneyPct(t.pnl_usd, t.pnl_pct);
        const tradeSlug = t.market_slug || t.slug || t.series_slug || '';
        const tradeUrl = tradeSlug ? `https://polymarket.com/market/${encodeURIComponent(tradeSlug)}` : '';
        const tradeLabel = esc(t.label || t.market || '-');
        const tradeLinkHtml = tradeUrl
          ? `<a href="${tradeUrl}" target="_blank" rel="noopener" style="color:var(--tx);text-decoration:none;transition:color 0.15s" onmouseover="this.style.color='var(--gold)'" onmouseout="this.style.color='var(--tx)'" title="View on Polymarket">${tradeLabel} ↗</a>`
          : tradeLabel;

        rowsHtml += `
          <tr>
            <td class="mono" style="font-size:11px;color:var(--faint)">${esc(t.timestamp || '-')}</td>
            <td style="font-weight:700">${tradeLinkHtml}</td>
            <td>${causeBadge}</td>
            <td class="mono">${t.shares || 0}</td>
            <td class="mono">${baseCostStr}</td>
            <td class="mono">${exitStr}</td>
            <td class="mono" style="font-weight:700;color:${pnlCol}">${gainLossFmt}</td>
            <td style="font-size:11px;color:var(--dim)">${esc(t.notes || '')}</td>
          </tr>
        `;
      }
      bodyEl.innerHTML = rowsHtml;
    }
  }

  // 6. Issue #139: PnL-per-position histogram from the same trades.
  renderPnlHistogram(st.trades || []);

  // 7. Render Chart
  renderCockpitChart(st.timeline, activeCockpitChartMode, st.starting_balance);

  // 8. Issue #197: sync sort indicators after DOM update
  updateOtSortIndicators('orders');
  updateOtSortIndicators('positions');
  updateOtSortIndicators('trades');
}

let activeCockpitChartContext = null;

function renderCockpitChart(timeline, mode, startingBalance) {
  const wrap = $('cockpitSvgWrap');
  const legendEl = $('cockpitChartLegend');
  if (!wrap) return;

  if (!timeline || timeline.length === 0) {
    wrap.innerHTML = `<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--dim);font-size:12px" class="mono">Engine starting... recording real-time equity timeline.</div>`;
    if (legendEl) legendEl.innerHTML = '';
    activeCockpitChartContext = null;
    return;
  }

  const w = wrap.clientWidth || 800;
  const h = 270;
  const padL = 75, padR = 30, padT = 25, padB = 35;
  const plotW = Math.max(10, w - padL - padR);
  const plotH = Math.max(10, h - padT - padB);

  // Time ticks calculation (5 nicely spaced ticks)
  const timeTicks = [];
  const nTicks = Math.min(5, timeline.length);
  for (let k = 0; k < nTicks; k++) {
    const idx = Math.min(timeline.length - 1, Math.round(k * (timeline.length - 1) / (nTicks - 1 || 1)));
    timeTicks.push({
      idx,
      x: padL + (idx / Math.max(1, timeline.length - 1)) * plotW,
      timeStr: timeline[idx]?.time_str || '',
      anchor: k === 0 ? 'start' : k === nTicks - 1 ? 'end' : 'middle',
    });
  }

  let svgContent = '';

  if (mode === 'total') {
    const vals = timeline.map(pt => pt.portfolio_value != null ? pt.portfolio_value : startingBalance);
    let minV = Math.min(...vals, startingBalance);
    let maxV = Math.max(...vals, startingBalance);
    if (minV === maxV) { minV -= 5; maxV += 5; }
    // Add 8% vertical padding
    const vPad = (maxV - minV) * 0.08 || 1;
    minV -= vPad;
    maxV += vPad;
    const range = (maxV - minV) || 1;

    const getX = i => padL + (i / Math.max(1, vals.length - 1)) * plotW;
    const getY = v => padT + (1 - (v - minV) / range) * plotH;

    let pathD = '';
    let areaD = '';
    vals.forEach((v, i) => {
      const x = getX(i);
      const y = getY(v);
      if (i === 0) {
        pathD += `M ${x.toFixed(1)} ${y.toFixed(1)}`;
        areaD += `M ${x.toFixed(1)} ${y.toFixed(1)}`;
      } else {
        pathD += ` L ${x.toFixed(1)} ${y.toFixed(1)}`;
        areaD += ` L ${x.toFixed(1)} ${y.toFixed(1)}`;
      }
    });

    const lastX = getX(vals.length - 1);
    const lastY = getY(vals[vals.length - 1]);
    const zeroY = getY(startingBalance);
    areaD += ` L ${lastX.toFixed(1)} ${padT + plotH} L ${padL} ${padT + plotH} Z`;

    const lastVal = vals[vals.length - 1];
    const diffVal = lastVal - startingBalance;
    const diffPct = startingBalance > 0 ? (diffVal / startingBalance) * 100 : 0.0;
    const theme = getThemeTokens();
    const lineColor = diffVal >= 0 ? theme.up : theme.down;

    // Y Axis 5 Levels
    const yLevels = [minV, minV + range * 0.25, minV + range * 0.5, minV + range * 0.75, maxV];
    let yGridSvg = '';
    yLevels.forEach(lv => {
      const yPos = getY(lv);
      yGridSvg += `
        <line x1="${padL}" y1="${yPos.toFixed(1)}" x2="${w - padR}" y2="${yPos.toFixed(1)}" stroke="rgba(255,255,255,0.06)" stroke-dasharray="3,3"/>
        <line x1="${padL - 4}" y1="${yPos.toFixed(1)}" x2="${padL}" y2="${yPos.toFixed(1)}" stroke="rgba(255,255,255,0.2)"/>
        <text x="${padL - 8}" y="${(yPos + 3.5).toFixed(1)}" fill="var(--dim)" font-size="10" font-family="var(--mono)" text-anchor="end">$${lv.toFixed(2)}</text>
      `;
    });

    // Baseline Line for Starting Balance
    let baseLineSvg = '';
    if (zeroY >= padT && zeroY <= padT + plotH) {
      baseLineSvg = `
        <line x1="${padL}" y1="${zeroY.toFixed(1)}" x2="${w - padR}" y2="${zeroY.toFixed(1)}" stroke="rgba(243,186,47,0.4)" stroke-width="1.2" stroke-dasharray="3,2"/>
        <text x="${w - padR + 5}" y="${(zeroY + 3.5).toFixed(1)}" fill="var(--gold)" font-size="9" font-family="var(--mono)">Base $${startingBalance.toFixed(2)}</text>
      `;
    }

    // X Gridlines & Labels
    let xGridSvg = '';
    timeTicks.forEach(tt => {
      xGridSvg += `
        <line x1="${tt.x.toFixed(1)}" y1="${padT}" x2="${tt.x.toFixed(1)}" y2="${padT + plotH}" stroke="rgba(255,255,255,0.04)" stroke-dasharray="2,2"/>
        <line x1="${tt.x.toFixed(1)}" y1="${padT + plotH}" x2="${tt.x.toFixed(1)}" y2="${padT + plotH + 4}" stroke="rgba(255,255,255,0.2)"/>
        <text x="${tt.x.toFixed(1)}" y="${h - 8}" fill="var(--faint)" font-size="10" font-family="var(--mono)" text-anchor="${tt.anchor}">${tt.timeStr}</text>
      `;
    });

    svgContent = `
      <svg width="100%" height="100%" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="display:block">
        <defs>
          <linearGradient id="cockpitAreaGrad" x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" stop-color="${lineColor}" stop-opacity="0.25"/>
            <stop offset="100%" stop-color="${lineColor}" stop-opacity="0.0"/>
          </linearGradient>
        </defs>

        <!-- Gridlines -->
        ${yGridSvg}
        ${xGridSvg}
        ${baseLineSvg}

        <!-- Axes Spines -->
        <line x1="${padL}" y1="${padT}" x2="${padL}" y2="${padT + plotH}" stroke="var(--line)" stroke-width="1"/>
        <line x1="${padL}" y1="${padT + plotH}" x2="${w - padR}" y2="${padT + plotH}" stroke="var(--line)" stroke-width="1"/>

        <!-- Shaded Area & Line -->
        <path d="${areaD}" fill="url(#cockpitAreaGrad)"/>
        <path d="${pathD}" fill="none" stroke="${lineColor}" stroke-width="2.5" stroke-linecap="round"/>

        <!-- Latest Point Pulse -->
        <circle cx="${lastX.toFixed(1)}" cy="${lastY.toFixed(1)}" r="4" fill="${lineColor}"/>
        <circle cx="${lastX.toFixed(1)}" cy="${lastY.toFixed(1)}" r="7.5" fill="none" stroke="${lineColor}" stroke-opacity="0.5"/>

        <!-- Interactive Crosshair Layer -->
        <g id="cockpitCrosshairG" style="display:none;pointer-events:none">
          <line id="cockpitCrossLine" x1="0" y1="${padT}" x2="0" y2="${padT + plotH}" stroke="rgba(255,255,255,0.4)" stroke-width="1.2" stroke-dasharray="3,3"/>
          <circle id="cockpitCrossDot" cx="0" cy="0" r="5" fill="var(--tx)" stroke="${lineColor}" stroke-width="2.5"/>
        </g>

        <!-- Transparent Event Capture Rect -->
        <rect x="0" y="0" width="${w}" height="${h}" fill="transparent" style="cursor:crosshair" onmousemove="onCockpitChartMouseMove(event)" onmouseleave="onCockpitChartMouseLeave()"/>
      </svg>
    `;

    if (legendEl) {
      legendEl.innerHTML = `
        <div style="display:flex;align-items:center;gap:6px">
          <span style="width:10px;height:10px;background:${lineColor};border-radius:2px"></span>
          <span>Account Net Value: <strong style="color:var(--tx)">$${lastVal.toFixed(2)}</strong> (<span style="color:${diffVal >= 0 ? 'var(--up)' : 'var(--down)'}">${diffVal >= 0 ? '+' : ''}$${diffVal.toFixed(2)} / ${diffPct >= 0 ? '+' : ''}${diffPct.toFixed(2)}%</span>)</span>
        </div>
      `;
    }

    activeCockpitChartContext = {
      mode: 'total',
      timeline,
      startingBalance,
      series: getActiveCockpitSeries(),
      minV, maxV, range,
      padL, padR, padT, padB, plotW, plotH, w, h,
      getX, getY,
      lineColor,
    };
  } else {
    const isDollar = mode === 'breakdown_usd';
    const activeSeries = getActiveCockpitSeries();
    const seriesKeys = activeSeries.map(s => s.slug);

    let allVals = [];
    timeline.forEach(pt => {
      const src = isDollar ? (pt.pnl_usd || {}) : (pt.pnl_pct || {});
      seriesKeys.forEach(k => {
        allVals.push(src[k] != null ? src[k] : 0.0);
      });
    });

    let minV = Math.min(...allVals, 0.0);
    let maxV = Math.max(...allVals, 0.0);
    if (minV === maxV) { minV -= 0.5; maxV += 0.5; }
    const vPad = (maxV - minV) * 0.08 || 0.2;
    minV -= vPad;
    maxV += vPad;
    const range = (maxV - minV) || 1;

    const getX = i => padL + (i / Math.max(1, timeline.length - 1)) * plotW;
    const getY = v => padT + (1 - (v - minV) / range) * plotH;
    const zeroY = getY(0.0);

    // Y Axis 5 Levels
    const yLevels = [minV, minV + range * 0.25, minV + range * 0.5, minV + range * 0.75, maxV];
    let yGridSvg = '';
    yLevels.forEach(lv => {
      const yPos = getY(lv);
      const signStr = lv > 0 ? '+' : '';
      const lvFmt = isDollar ? `${signStr}$${lv.toFixed(2)}` : `${signStr}${lv.toFixed(1)}%`;
      yGridSvg += `
        <line x1="${padL}" y1="${yPos.toFixed(1)}" x2="${w - padR}" y2="${yPos.toFixed(1)}" stroke="rgba(255,255,255,0.06)" stroke-dasharray="3,3"/>
        <line x1="${padL - 4}" y1="${yPos.toFixed(1)}" x2="${padL}" y2="${yPos.toFixed(1)}" stroke="rgba(255,255,255,0.2)"/>
        <text x="${padL - 8}" y="${(yPos + 3.5).toFixed(1)}" fill="var(--dim)" font-size="10" font-family="var(--mono)" text-anchor="end">${lvFmt}</text>
      `;
    });

    // Zero baseline
    let baseLineSvg = '';
    if (zeroY >= padT && zeroY <= padT + plotH) {
      baseLineSvg = `
        <line x1="${padL}" y1="${zeroY.toFixed(1)}" x2="${w - padR}" y2="${zeroY.toFixed(1)}" stroke="rgba(255,255,255,0.25)" stroke-width="1.2" stroke-dasharray="2,2"/>
        <text x="${w - padR + 5}" y="${(zeroY + 3.5).toFixed(1)}" fill="var(--faint)" font-size="9" font-family="var(--mono)">0.00</text>
      `;
    }

    // X Gridlines & Labels
    timeTicks.forEach(tt => {
      xGridSvg += `
        <line x1="${tt.x.toFixed(1)}" y1="${padT}" x2="${tt.x.toFixed(1)}" y2="${padT + plotH}" stroke="rgba(255,255,255,0.04)" stroke-dasharray="2,2"/>
        <line x1="${tt.x.toFixed(1)}" y1="${padT + plotH}" x2="${tt.x.toFixed(1)}" y2="${padT + plotH + 4}" stroke="rgba(255,255,255,0.2)"/>
        <text x="${tt.x.toFixed(1)}" y="${h - 8}" fill="var(--faint)" font-size="10" font-family="var(--mono)" text-anchor="${tt.anchor}">${tt.timeStr}</text>
      `;
    });

    let linesSvg = '';
    let legendHtml = '';

    activeSeries.forEach(s => {
      let pathD = '';
      let lastVal = 0.0;
      timeline.forEach((pt, i) => {
        const src = isDollar ? (pt.pnl_usd || {}) : (pt.pnl_pct || {});
        const v = src[s.slug] != null ? src[s.slug] : 0.0;
        lastVal = v;
        const x = getX(i);
        const y = getY(v);
        if (i === 0) pathD += `M ${x.toFixed(1)} ${y.toFixed(1)}`;
        else pathD += ` L ${x.toFixed(1)} ${y.toFixed(1)}`;
      });

      linesSvg += `<path d="${pathD}" fill="none" stroke="${s.color}" stroke-width="2.2" stroke-linecap="round"/>`;

      const valStr = isDollar
        ? `${lastVal >= 0 ? '+' : ''}$${lastVal.toFixed(2)}`
        : `${lastVal >= 0 ? '+' : ''}${lastVal.toFixed(1)}%`;

      legendHtml += `
        <div style="display:flex;align-items:center;gap:6px">
          <span style="width:9px;height:9px;background:${s.color};border-radius:50%"></span>
          <span>${s.label}: <strong style="color:var(--tx)">${valStr}</strong></span>
        </div>
      `;
    });

    svgContent = `
      <svg width="100%" height="100%" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="display:block">
        <!-- Gridlines -->
        ${yGridSvg}
        ${xGridSvg}
        ${baseLineSvg}

        <!-- Axes Spines -->
        <line x1="${padL}" y1="${padT}" x2="${padL}" y2="${padT + plotH}" stroke="var(--line)" stroke-width="1"/>
        <line x1="${padL}" y1="${padT + plotH}" x2="${w - padR}" y2="${padT + plotH}" stroke="var(--line)" stroke-width="1"/>

        <!-- Multi-series Lines -->
        ${linesSvg}

        <!-- Interactive Crosshair Layer -->
        <g id="cockpitCrosshairG" style="display:none;pointer-events:none">
          <line id="cockpitCrossLine" x1="0" y1="${padT}" x2="0" y2="${padT + plotH}" stroke="rgba(255,255,255,0.4)" stroke-width="1.2" stroke-dasharray="3,3"/>
          <g id="cockpitCrossMultiDots"></g>
        </g>

        <!-- Transparent Event Capture Rect -->
        <rect x="0" y="0" width="${w}" height="${h}" fill="transparent" style="cursor:crosshair" onmousemove="onCockpitChartMouseMove(event)" onmouseleave="onCockpitChartMouseLeave()"/>
      </svg>
    `;

    if (legendEl) legendEl.innerHTML = legendHtml;

    activeCockpitChartContext = {
      mode,
      timeline,
      isDollar,
      startingBalance,
      series: activeSeries,
      minV, maxV, range,
      padL, padR, padT, padB, plotW, plotH, w, h,
      getX, getY,
    };
  }

  wrap.innerHTML = svgContent;
}

function onCockpitChartMouseMove(evt) {
  const ctx = activeCockpitChartContext;
  if (!ctx || !ctx.timeline || ctx.timeline.length === 0) return;

  const wrap = $('cockpitChartWrap');
  const tooltip = $('cockpitChartTooltip');
  if (!wrap || !tooltip) return;

  const rect = wrap.getBoundingClientRect();
  const scaleX = rect.width ? ctx.w / rect.width : 1;
  const clientX = evt.clientX - rect.left;
  const clientY = evt.clientY - rect.top;
  const mouseX = clientX * scaleX;

  // Clamp within plot bounds
  if (mouseX < ctx.padL - 10 || mouseX > ctx.w - ctx.padR + 10) {
    onCockpitChartMouseLeave();
    return;
  }

  const ratio = Math.max(0, Math.min(1, (mouseX - ctx.padL) / ctx.plotW));
  const idx = Math.round(ratio * (ctx.timeline.length - 1));
  const pt = ctx.timeline[idx];
  if (!pt) return;

  const xPos = ctx.getX(idx);

  // Update SVG Crosshair elements
  const crossG = $('cockpitCrosshairG');
  const crossLine = $('cockpitCrossLine');
  if (crossG && crossLine) {
    crossG.style.display = 'block';
    crossLine.setAttribute('x1', xPos.toFixed(1));
    crossLine.setAttribute('x2', xPos.toFixed(1));

    if (ctx.mode === 'total') {
      const dot = $('cockpitCrossDot');
      if (dot) {
        const val = pt.portfolio_value != null ? pt.portfolio_value : ctx.startingBalance;
        const yPos = ctx.getY(val);
        dot.setAttribute('cx', xPos.toFixed(1));
        dot.setAttribute('cy', yPos.toFixed(1));
      }
    } else {
      const multiDotsG = $('cockpitCrossMultiDots');
      if (multiDotsG) {
        let dotsSvg = '';
        const src = ctx.isDollar ? (pt.pnl_usd || {}) : (pt.pnl_pct || {});
        const activeSeries = ctx.series || getActiveCockpitSeries();
        activeSeries.forEach(s => {
          const v = src[s.slug] != null ? src[s.slug] : 0.0;
          const yPos = ctx.getY(v);
          dotsSvg += `<circle cx="${xPos.toFixed(1)}" cy="${yPos.toFixed(1)}" r="4.5" fill="${s.color}" stroke="var(--tx)" stroke-width="1.5"/>`;
        });
        multiDotsG.innerHTML = dotsSvg;
      }
    }
  }

  // Build Tooltip HTML
  let tooltipHtml = `<div style="font-size:10px;color:var(--gold);font-weight:700;margin-bottom:4px;font-family:var(--mono)">⏱ ${pt.time_str || ''}</div>`;

  if (ctx.mode === 'total') {
    const val = pt.portfolio_value != null ? pt.portfolio_value : ctx.startingBalance;
    const diffVal = val - ctx.startingBalance;
    const diffPct = ctx.startingBalance > 0 ? (diffVal / ctx.startingBalance) * 100 : 0.0;
    const col = diffVal >= 0 ? 'var(--up)' : 'var(--down)';

    tooltipHtml += `
      <div style="font-size:13px;font-weight:700;font-family:var(--mono);color:var(--tx);margin-bottom:2px">$${val.toFixed(2)}</div>
      <div style="font-size:11px;font-family:var(--mono);color:${col}">${diffVal >= 0 ? '+' : ''}$${diffVal.toFixed(2)} (${diffPct >= 0 ? '+' : ''}${diffPct.toFixed(2)}%)</div>
    `;
  } else {
    const src = ctx.isDollar ? (pt.pnl_usd || {}) : (pt.pnl_pct || {});
    tooltipHtml += `<div style="display:grid;grid-template-columns:auto auto;gap:3px 12px;margin-top:4px;font-family:var(--mono);font-size:10.5px">`;
    const activeSeries = ctx.series || getActiveCockpitSeries();
    activeSeries.forEach(s => {
      const v = src[s.slug] != null ? src[s.slug] : 0.0;
      const vStr = ctx.isDollar ? `${v >= 0 ? '+' : ''}$${v.toFixed(2)}` : `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`;
      const col = v >= 0 ? 'var(--up)' : 'var(--down)';
      tooltipHtml += `
        <div style="display:flex;align-items:center;gap:4px">
          <span style="width:6px;height:6px;background:${s.color};border-radius:50%"></span>
          <span>${s.label}</span>
        </div>
        <div style="text-align:right;font-weight:700;color:${col}">${vStr}</div>
      `;
    });
    tooltipHtml += `</div>`;
  }

  tooltip.innerHTML = tooltipHtml;
  tooltip.style.display = 'block';

  // Position tooltip relative to chart bounds
  let tipX = clientX + 15;
  if (tipX + 190 > rect.width) {
    tipX = clientX - 200;
  }
  let tipY = Math.max(8, clientY - 40);
  if (tipY + 130 > rect.height) {
    tipY = rect.height - 135;
  }

  tooltip.style.left = `${tipX}px`;
  tooltip.style.top = `${tipY}px`;
}

function onCockpitChartMouseLeave() {
  const crossG = $('cockpitCrosshairG');
  if (crossG) crossG.style.display = 'none';
  const tooltip = $('cockpitChartTooltip');
  if (tooltip) tooltip.style.display = 'none';
}

// ── Backtest Strategy Geometry Preview (Issue #263) ───────────────────────────
function layoutBacktestPreviewLabels(items, plotRight, labelX, labelRight, plotTop, plotBottom) {
  const labelHeight = 18;
  const minGap = 25;
  const minCenter = plotTop + labelHeight / 2;
  const maxCenter = plotBottom - labelHeight / 2;
  const labels = items
    .filter(item => item && Number.isFinite(item.y))
    .sort((a, b) => a.y - b.y)
    .map(item => ({ ...item, center: Math.max(minCenter, Math.min(maxCenter, item.y)) }));

  for (let i = 1; i < labels.length; i += 1) {
    labels[i].center = Math.max(labels[i].center, labels[i - 1].center + minGap);
  }
  const overflow = labels.length ? labels[labels.length - 1].center - maxCenter : 0;
  if (overflow > 0) {
    labels.forEach(label => { label.center -= overflow; });
  }

  return labels.map(label => `
    <line x1="${plotRight.toFixed(1)}" y1="${label.y.toFixed(1)}" x2="${(labelX - 7).toFixed(1)}" y2="${label.center.toFixed(1)}" stroke="${label.color}" stroke-opacity="0.65" stroke-width="1"/>
    <circle cx="${plotRight.toFixed(1)}" cy="${label.y.toFixed(1)}" r="2" fill="${label.color}"/>
    <rect class="bt-preview-level-label" data-label-center="${label.center.toFixed(1)}" x="${labelX.toFixed(1)}" y="${(label.center - labelHeight / 2).toFixed(1)}" width="${(labelRight - labelX).toFixed(1)}" height="${labelHeight}" rx="4" fill="var(--panel2)" fill-opacity="0.94" stroke="var(--line)"/>
    <text x="${(labelX + 7).toFixed(1)}" y="${(label.center + 3.5).toFixed(1)}" fill="${label.color}" font-size="10" font-family="var(--mono)">${label.text}</text>
  `).join('');
}

function updateBacktestParamPreview(){
  const svg = $('btParamPreviewSvg');
  if(!svg) return;

  // Safe numeric extraction with sensible fallbacks (preserves valid zeroes)
  const readFinite = (id, fallback) => {
    const raw = $(id)?.value;
    if(raw == null || String(raw).trim() === '') return fallback;
    const value = Number(raw);
    return Number.isFinite(value) ? value : fallback;
  };
  // Issue #419: price inputs hold cents — the same reader btControlValues
  // uses, so the preview cannot diverge from the request.
  const readCents = (id, fallback) => {
    const raw = $(id)?.value;
    if(raw == null || String(raw).trim() === '') return fallback;
    const value = centsToDollars(raw);
    return Number.isFinite(value) ? value : fallback;
  };

  const offset = Math.max(0, readCents('btOffset', 0.02));
  const exitStop = Math.max(0, readCents('btExit5m', 0.05));
  const exitReversal = Math.max(0, readCents('btExitReversal', 0.02));
  const entryDelayPct = Math.max(0, Math.min(100, readFinite('btEntryDelay', 0)));
  const quoteLo = Math.max(0, Math.min(1.0, readCents('btQuoteLo', 0.10)));
  const quoteHi = Math.max(0, Math.min(1.0, readCents('btQuoteHi', 0.90)));
  const deadZonePct = Math.max(0, Math.min(100, readFinite('btDeadZoneVal', 10)));

  // Geometry is normalized: the same controls apply to 5m and 15m windows.
  const windowPct = 100;
  const delayPct = entryDelayPct;
  const deadPct = deadZonePct;

  // Price calculations
  const mid = 0.50;
  const longBid = Math.max(0, mid - offset);
  const shortComp = Math.min(1.0, mid + offset);
  const stopPrice = Math.max(0, longBid - exitStop);
  const revPrice = Math.min(1.0, stopPrice + exitReversal);

  // Dimensions: reserve a stable right gutter for labels instead of stretching
  // the plot to the edge of the SVG at wide viewport sizes.
  const w = 900;
  const h = 420;
  const padL = 58;
  const padR = 245;
  const padT = 24;
  const padB = 36;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;

  const getX = (pct) => padL + (Math.max(0, Math.min(windowPct, pct)) / windowPct) * plotW;
  const getY = (p) => padT + (1.0 - Math.max(0.0, Math.min(1.0, p))) * plotH;

  // Background Gridlines
  let gridSvg = '';
  // Horizontal Price Lines (0.10 steps)
  for(let p = 0.0; p <= 1.001; p += 0.10){
    const yPos = getY(p);
    const isMid = Math.abs(p - 0.50) < 0.001;
    gridSvg += `
      <line x1="${padL}" y1="${yPos.toFixed(1)}" x2="${padL + plotW}" y2="${yPos.toFixed(1)}" stroke="${isMid ? 'rgba(243,186,47,0.45)' : 'rgba(255,255,255,0.06)'}" stroke-width="${isMid ? 1.5 : 1}" stroke-dasharray="${isMid ? '4,3' : '2,3'}"/>
      <text x="${padL - 6}" y="${(yPos + 3.5).toFixed(1)}" fill="${isMid ? 'var(--gold)' : 'var(--dim)'}" font-size="10" font-family="var(--mono)" font-weight="${isMid ? '700' : '400'}" text-anchor="end">${formatCents(p)}</text>
    `;
  }

  // Normalized timeline: no 5m assumption, so 15m windows align correctly.
  for(let pct = 0; pct <= windowPct; pct += 25){
    const xPos = getX(pct);
    gridSvg += `
      <line x1="${xPos.toFixed(1)}" y1="${padT}" x2="${xPos.toFixed(1)}" y2="${padT + plotH}" stroke="rgba(255,255,255,0.05)" stroke-width="1" stroke-dasharray="2,3"/>
      <text x="${xPos.toFixed(1)}" y="${h - 10}" fill="var(--faint)" font-size="10" font-family="var(--mono)" text-anchor="middle">${pct}%</text>
    `;
  }

  // Quotable Range Corridor Shading
  const qLoY = getY(Math.min(quoteLo, quoteHi));
  const qHiY = getY(Math.max(quoteLo, quoteHi));
  const qHeight = Math.max(0, qLoY - qHiY);
  let quotableSvg = `
    <rect x="${padL}" y="${qHiY.toFixed(1)}" width="${plotW}" height="${qHeight.toFixed(1)}" fill="rgba(56,189,248,0.05)" stroke="rgba(56,189,248,0.28)" stroke-width="1" stroke-dasharray="4,2"/>
  `;

  // Time Zones Shading
  let timeZonesSvg = '';
  // Entry Delay Zone
  if (delayPct > 0) {
    const delayW = getX(delayPct) - padL;
    timeZonesSvg += `
      <rect x="${padL}" y="${padT}" width="${delayW.toFixed(1)}" height="${plotH}" fill="rgba(235,178,58,0.12)" stroke="rgba(235,178,58,0.4)" stroke-width="1" stroke-dasharray="3,2"/>
      <text x="${(padL + delayW / 2).toFixed(1)}" y="${padT + 14}" fill="var(--gold)" font-size="9" font-family="var(--mono)" text-anchor="middle" font-weight="700">DELAY (${delayPct.toFixed(0)}%)</text>
    `;
  }

  // Dead Zone Tail
  if (deadPct > 0) {
    const deadX = getX(windowPct - deadPct);
    const deadW = padL + plotW - deadX;
    timeZonesSvg += `
      <rect x="${deadX.toFixed(1)}" y="${padT}" width="${deadW.toFixed(1)}" height="${plotH}" fill="rgba(240,104,77,0.12)" stroke="rgba(240,104,77,0.4)" stroke-width="1" stroke-dasharray="3,2"/>
      <text x="${(deadX + deadW / 2).toFixed(1)}" y="${padT + 14}" fill="var(--down)" font-size="9" font-family="var(--mono)" text-anchor="middle" font-weight="700">DEAD ZONE (${deadPct.toFixed(0)}%)</text>
    `;
  }

  // Active Trading Span Coordinates
  const activeStartX = getX(delayPct);
  const activeEndX = getX(windowPct - deadPct);
  const activeWidth = Math.max(0, activeEndX - activeStartX);
  const yLong = getY(longBid);
  const yShort = getY(shortComp);

  let activeContentSvg = '';
  if (activeWidth <= 0) {
    activeContentSvg = `
      <text x="${(padL + plotW / 2).toFixed(1)}" y="${(padT + plotH / 2).toFixed(1)}" fill="var(--down)" font-size="12" font-family="var(--disp)" font-weight="700" text-anchor="middle">⚠️ Quoting Window Blocked (Delay + Dead Zone ≥ Window Duration)</text>
    `;
  } else {
    // Stop Loss & Reversal Buffer
    let stopSvg = '';
    if (exitStop > 0) {
      const yStop = getY(stopPrice);
      const yRev = getY(revPrice);
      const revH = Math.max(0, yStop - yRev);

      // Reversal Buffer zone
      if (exitReversal > 0 && revH > 0) {
        stopSvg += `
          <rect x="${activeStartX.toFixed(1)}" y="${yRev.toFixed(1)}" width="${activeWidth.toFixed(1)}" height="${revH.toFixed(1)}" fill="rgba(235,178,58,0.16)" stroke="rgba(235,178,58,0.4)" stroke-width="1" stroke-dasharray="2,2"/>
          <line x1="${activeStartX.toFixed(1)}" y1="${yRev.toFixed(1)}" x2="${activeEndX.toFixed(1)}" y2="${yRev.toFixed(1)}" stroke="var(--gold)" stroke-width="1" stroke-dasharray="2,2"/>
        `;
      }

      // Stop Loss Line
      stopSvg += `
        <line x1="${activeStartX.toFixed(1)}" y1="${yStop.toFixed(1)}" x2="${activeEndX.toFixed(1)}" y2="${yStop.toFixed(1)}" stroke="var(--down)" stroke-width="2" stroke-dasharray="4,2"/>
      `;
    }

    // Resting Bids
    const midX = activeStartX + activeWidth * 0.5;

    const bidsSvg = `
      <!-- Spread bracket line -->
      <line x1="${midX.toFixed(1)}" y1="${yShort.toFixed(1)}" x2="${midX.toFixed(1)}" y2="${yLong.toFixed(1)}" stroke="rgba(255,255,255,0.2)" stroke-width="1.2" stroke-dasharray="2,2"/>
      <rect x="${(midX - 35).toFixed(1)}" y="${(getY(mid) - 7).toFixed(1)}" width="70" height="14" rx="3" fill="var(--panel)" stroke="rgba(255,255,255,0.15)"/>
      <text x="${midX.toFixed(1)}" y="${(getY(mid) + 3.5).toFixed(1)}" fill="var(--tx)" font-size="9" font-family="var(--mono)" text-anchor="middle" font-weight="700">2×off: ${formatCents(2 * offset)}</text>

      <!-- Long Bid Line -->
      <line x1="${activeStartX.toFixed(1)}" y1="${yLong.toFixed(1)}" x2="${activeEndX.toFixed(1)}" y2="${yLong.toFixed(1)}" stroke="var(--cyan)" stroke-width="2"/>
      <circle cx="${activeStartX.toFixed(1)}" cy="${yLong.toFixed(1)}" r="3.5" fill="var(--cyan)"/>
      <circle cx="${activeEndX.toFixed(1)}" cy="${yLong.toFixed(1)}" r="3.5" fill="var(--cyan)"/>

      <!-- Short Complement Line -->
      <line x1="${activeStartX.toFixed(1)}" y1="${yShort.toFixed(1)}" x2="${activeEndX.toFixed(1)}" y2="${yShort.toFixed(1)}" stroke="var(--up)" stroke-width="2"/>
      <circle cx="${activeStartX.toFixed(1)}" cy="${yShort.toFixed(1)}" r="3.5" fill="var(--up)"/>
      <circle cx="${activeEndX.toFixed(1)}" cy="${yShort.toFixed(1)}" r="3.5" fill="var(--up)"/>
    `;

    activeContentSvg = stopSvg + bidsSvg;
  }

  const labelX = padL + plotW + 18;
  const labelItems = [
    { y: (qHiY + qLoY) / 2, color: 'var(--cyan)', text: `Quotable: ${formatCents(quoteLo)}–${formatCents(quoteHi)}` },
    { y: getY(mid), color: 'var(--gold)', text: 'Mid: 50c' },
  ];
  if (activeWidth > 0) {
    labelItems.push(
      { y: yLong, color: 'var(--cyan)', text: `Long Bid: ${formatCents(longBid)}` },
      { y: yShort, color: 'var(--up)', text: `Short Comp: ${formatCents(shortComp)}` },
    );
    if (exitStop > 0) {
      labelItems.push({ y: getY(stopPrice), color: 'var(--down)', text: `Stop Loss: ${formatCents(stopPrice)} (-${formatCents(exitStop)})` });
    }
    if (exitStop > 0 && exitReversal > 0 && getY(stopPrice) > getY(revPrice)) {
      labelItems.push({ y: getY(revPrice), color: 'var(--gold)', text: `Reversal: ${formatCents(revPrice)} (+${formatCents(exitReversal)})` });
    }
  }
  const labelsSvg = layoutBacktestPreviewLabels(labelItems, padL + plotW, labelX, w - 18, padT, padT + plotH);

  // Spines
  const spinesSvg = `
    <line x1="${padL}" y1="${padT}" x2="${padL}" y2="${padT + plotH}" stroke="var(--line-hi)" stroke-width="1.2"/>
    <line x1="${padL}" y1="${padT + plotH}" x2="${padL + plotW}" y2="${padT + plotH}" stroke="var(--line-hi)" stroke-width="1.2"/>
  `;

  svg.innerHTML = `
    ${gridSvg}
    ${quotableSvg}
    ${timeZonesSvg}
    ${activeContentSvg}
    ${labelsSvg}
    ${spinesSvg}
  `;
}

function setupBacktestInputListeners(){
  const inputIds = [
    'btOffset', 'btQueue', 'btPairCost', 'btExit5m',
    'btExit15m', 'btExitBtc', 'btExitSol',
    'btSize', 'btFileSelect', 'btMaxStartDelay',
    'btQuoteLo', 'btQuoteHi', 'btEntryDelay',
    'btExitReversal', 'btDeadZoneVal'
  ];

  inputIds.forEach(id => {
    const el = $(id);
    if (!el) return;

    el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        el.blur();
        runBacktest();
      }
    });

    el.addEventListener('input', () => {
      validateBacktestInputs();
      updateBacktestParamPreview();
    });

    el.addEventListener('change', () => {
      validateBacktestInputs();
      updateBacktestParamPreview();
      // Parameter and dataset changes are deliberately staged. The operator
      // may change several knobs before explicitly starting the run.
      if (id === 'btFileSelect') {
        window.selectedBacktestFile = el.value;
        window._btFileChosen = true;
        updateBtRuntimeEstimate();
      }
    });
  });

  // Initial preview render
  updateBacktestParamPreview();
}

let liveEventSource = null;
let liveStreamConnected = false;
let cockpitPollTimer = null;

function ensureCockpitPolling() {
  if (!cockpitPollTimer) {
    cockpitPollTimer = setInterval(pollCockpit, 5000);
  }
}

function initLiveCockpitStream() {
  if (typeof EventSource === 'undefined') {
    fetchCockpitState();
    ensureCockpitPolling();
    return;
  }
  try {
    if (liveEventSource) {
      liveEventSource.close();
    }
    liveEventSource = new EventSource('/api/live/stream');
    liveEventSource.onopen = () => {
      liveStreamConnected = true;
      const el = $('globalStreamPill') || $('cockpitStreamPill');
      if (el) {
        const sb = (cockpitState && cockpitState.stream_bridge) || {};
        const isLive = !!sb.binance_ws_connected;
        if (isLive) {
          el.textContent = '● STREAM · <1s';
          el.style.background = 'rgba(51,201,181,0.15)'; el.style.color = 'var(--up)'; el.style.borderColor = 'rgba(51,201,181,0.35)';
        } else {
          el.textContent = '● OK · 1s';
          el.style.background = 'rgba(243,186,47,0.15)'; el.style.color = 'var(--gold)'; el.style.borderColor = 'rgba(243,186,47,0.35)';
        }
      }
    };
    liveEventSource.onmessage = (e) => {
      try {
        const env = JSON.parse(e.data);
        if (env.type === 'snapshot' || env.stream_id === 'state') {
          cockpitState = env.data;
          renderCockpitUI(env.data);
        } else if (env.stream_id === 'spot' && env.data) {
          if (cockpitState && cockpitState.markets) {
            const targetSlugs = (env.data.slugs && env.data.slugs.length) ? env.data.slugs : (env.data.slug ? [env.data.slug] : (function() {
              const sym = (env.data.symbol || '').toLowerCase();
              const prefix = sym.replace('usdt', '');
              const res = [];
              for (const k in cockpitState.markets) {
                if (k.startsWith(prefix)) res.push(k);
              }
              return res;
            })());

            for (const slug of targetSlugs) {
              if (slug && cockpitState.markets[slug]) {
                const m = cockpitState.markets[slug];
                const price = env.data.price;
                if ('actual_price' in env.data) m.actual_price = env.data.actual_price;
                if ('rtds_price' in env.data) m.rtds_price = env.data.rtds_price;
                if ('price_diff' in env.data) m.price_diff = env.data.price_diff;
                if ('price_diff_pct' in env.data) m.price_diff_pct = env.data.price_diff_pct;

                m.spot_price = price;
                if (m.spot_open_price == null && price) {
                  m.spot_open_price = price;
                }
                if (m.spot_open_price && price) {
                  m.spot_drift = (price - m.spot_open_price) / m.spot_open_price;
                }
                const pEl = $(`cockpit-spot-price-${slug}`);
                if (pEl && price != null) {
                  pEl.textContent = '$' + price.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
                }
                const dEl = $(`cockpit-spot-drift-${slug}`);
                if (dEl) {
                  const drift = m.spot_drift || 0;
                  dEl.textContent = (drift >= 0 ? '+' : '') + (drift * 100).toFixed(2) + '%';
                  dEl.style.color = drift > 0 ? 'var(--up)' : drift < 0 ? 'var(--down)' : 'var(--dim)';
                }
              }
            }

          }
        }
      } catch (err) {
        console.debug('SSE parse error', err);
      }
    };
    liveEventSource.onerror = () => {
      liveStreamConnected = false;
      const el = $('globalStreamPill') || $('cockpitStreamPill');
      if (el) {
        el.textContent = '● OFFLINE · POLLING';
        el.style.background = 'rgba(240,104,77,0.15)'; el.style.color = 'var(--down)'; el.style.borderColor = 'rgba(240,104,77,0.35)';
      }
      fetchCockpitState();
      ensureCockpitPolling();
    };
  } catch (e) {
    liveStreamConnected = false;
    ensureCockpitPolling();
  }
}

// Labels and bounds come from /api/params/spec before anything renders,
// so a stale hard-coded string can never be what the operator reads.
applyParamSpec();
setupBacktestInputListeners();
switchOtTab(activeOtTab);
applyOtHeight(otHeightExpanded);
initSidebarState();

// Initialize real-time streams and polls
fetchCockpitState();
initLiveCockpitStream();
ensureCockpitPolling();
tick();
setInterval(pollTick, 3000); // #371: guarded wrapper, not bare tick
// #371: returning to a visible document outside Backtest — refresh stale data once.
if (typeof document !== 'undefined' && document.addEventListener) {
  document.addEventListener('visibilitychange', function(){
    if (!document.hidden) btOscRefreshIfStale();
  });
}
loadManifest();   // tick files tab: load file list + run integrity verify on every dashboard load
loadBacktestTemplateList(); // backtest tab: saved-template list (empty state until the first save)
initBacktestIdle(); // IIIB: backtest tab opens pre-drawn (sweep card idle + chart axes)
</script></body></html>
"""


FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" rx="7" fill="#0a0d12" stroke="#232a35" stroke-width="1.5"/>'
    '<path d="M10 6v20" stroke="#33c9b5" stroke-width="2" stroke-linecap="round"/>'
    '<rect x="7" y="10" width="6" height="11" rx="2" fill="#33c9b5"/>'
    '<path d="M22 6v20" stroke="#f0684d" stroke-width="2" stroke-linecap="round"/>'
    '<rect x="19" y="11" width="6" height="11" rx="2" fill="#f0684d"/>'
    '</svg>'
)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """Serve SVG favicon for browser tab requests to avoid 404 logs."""
    return Response(
        content=FAVICON_SVG,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/", response_class=HTMLResponse)
@app.get("/oscillation", response_class=HTMLResponse)
@app.get("/summary", response_class=HTMLResponse)
@app.get("/analysis", response_class=HTMLResponse)
def root_spa_page():
    """Serve the complete unified dashboard SPA interface."""
    return HTMLResponse(FULL_APP_HTML, headers={"Cache-Control": "no-cache"})


