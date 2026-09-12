"""Oscillation & Backtest Lab dashboard for 5m/15m crypto spread capture.

Unified 4-tab SPA:
- Tab 1: Live Observation & Recent Closed Windows
- Tab 2: Backtest Simulator Sweeper with Equity Curve
- Tab 3: Statistical Analysis & Distributions
- Tab 4: Ticks File Repository & Ingestion Manager

Serves on :8802
"""
from __future__ import annotations

import asyncio
import collections
import gzip
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
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

ROOT = Path(__file__).resolve().parent.parent
RUN = ROOT / "run"
TICKS_DIR = RUN / "ticks"
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
    """Aggregate the fill sidecar into buckets + chased stats + verdict."""
    from scripts.bucket_fills import (
        _read_jsonl, _settlement_pnl_by_market, bucketize,
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
                "verdict": "awaiting fills"}
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
MAX_TEST_ORDER_SHARES = 10.0
# Manifest `ts` age below which a writer that is NOT our dashboard child
# counts as a live external standalone collector (Issue #151).
EXTERNAL_COLLECTOR_STALE_SEC = 5.0


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
        allowed_ports = {8802, 8888, 8000, 80, 443}
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
    goals = _agg_goals(_load_all_windows())
    now = time.time()
    return {
        "now": now,
        "summary": summary,
        "windows": wins,
        "live": live,
        "goals": goals,
        "default_goals": DEFAULT_GOALS,
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


def _aggregate_ticks(files: list[Path], manifest: dict[str, Any] | None) -> dict[str, Any]:
    """Sum cheap totals across tick files; per-series counts from cache sources only."""
    total_bytes = 0
    total_lines = 0
    any_estimated = False
    entries: list[tuple[Path, int]] = []
    for f in files:
        size = f.stat().st_size
        total_bytes += size
        is_est = size >= 20_000_000
        lines = int(size / 950) if is_est else _count_lines_fast(f)
        any_estimated = any_estimated or is_est
        total_lines += lines
        entries.append((f, lines))

    series_counts: dict[str, int] = {}
    total_windows = 0
    windows_known = True
    source = "none"
    cache_dir = files[0].parent / _VERIFY_CACHE_DIRNAME if files else None
    for f, _lines in entries:
        cached = (
            _read_verify_cache(
                cache_dir / f"{f.name}.json",
                expected_fingerprint=_file_fingerprint(f),
            )
            if cache_dir
            else None
        )
        if cached is None:
            windows_known = False
            continue
        source = "verify_cache"
        for s, c in cached.get("series_counts", {}).items():
            series_counts[s] = series_counts.get(s, 0) + int(c)
        total_windows += int(cached.get("windows_count", 0))

    if not series_counts and entries:
        # No fresh verify sidecars: fall back to a TTL-capped one-time scan.
        for f, _lines in entries:
            counts = _scan_series_counts(f)
            if counts:
                source = "scan_cache"
                for s, c in counts.items():
                    series_counts[s] = series_counts.get(s, 0) + int(c)

    return {
        "total_files": len(entries),
        "total_bytes": total_bytes,
        "total_lines": total_lines,
        "total_lines_estimated": any_estimated,
        "total_windows": total_windows,
        "windows_source": "cache" if (windows_known and entries) else ("partial" if entries else "none"),
        "tape_entries_total": int((manifest or {}).get("tape_entries_total", 0)),
        "series_counts": series_counts,
        "series_counts_source": source if series_counts else "none",
    }


@app.get("/api/ticks/manifest")
def api_ticks_manifest():
    """List available tick files + manifest stats for the slider UI."""
    out: dict[str, Any] = {"files": [], "manifest": None}
    if not TICKS_DIR.exists():
        return out
    mf = TICKS_DIR / "manifest.json"
    if mf.exists():
        try:
            out["manifest"] = json.loads(mf.read_text(encoding="utf-8"))
        except Exception:
            pass
    for f in sorted(TICKS_DIR.iterdir()):
        if (
            (f.suffix in (".jsonl", ".gz") or f.name.endswith(".jsonl.gz"))
            and f.is_file()
            and not f.name.endswith(".idx")
        ):
            size = f.stat().st_size
            is_est = size >= 20_000_000
            lines = (
                int(size / 950) if is_est else _count_lines_fast(f)
            )
            out["files"].append({
                "name": f.name,
                "bytes": size,
                "lines": lines,
                "lines_estimated": is_est,
                "mtime": f.stat().st_mtime,
            })
    try:
        out["aggregate"] = _aggregate_ticks(
            [TICKS_DIR / f["name"] for f in out["files"]], out["manifest"]
        )
        # Per-file market breakdown, when a cached verify report exists.
        for entry in out["files"]:
            cached = _read_verify_cache(
                TICKS_DIR / _VERIFY_CACHE_DIRNAME / f"{entry['name']}.json",
                expected_fingerprint=_file_fingerprint(TICKS_DIR / entry["name"]),
            )
            entry["market_breakdown"] = (cached or {}).get("market_breakdown", [])
            if cached:
                entry["windows_count"] = int(cached.get("windows_count", 0))
    except Exception:
        out["aggregate"] = {
            "total_files": 0,
            "total_bytes": 0,
            "total_lines": 0,
            "total_lines_estimated": False,
            "total_windows": 0,
            "windows_source": "none",
            "tape_entries_total": 0,
            "series_counts": {},
            "series_counts_source": "none",
        }
    return out


@app.get("/api/backtest")
def api_backtest(
    file: str = "",
    offset: float = 0.02,
    queue: float = 0.0,
    pair_cost: float = 1.05,
    exit_default_5m: float = 0.05,
    exit_default_15m: float = 0.05,
    exit_btc_5m: float = 0.05,
    exit_sol_5m: float = 0.05,
    exit_reversal: float = 0.02,
    size: int = 5,
    fill_model: str = "cross",
    gas: float = 0.0,
    max_start_delay: float = 0.0,
    filter_partial: bool = False,
    entry_timeout_pct: float = 0.10,
    reentry_drift_band: float = 0.015,
    min_requote_remaining_sec: float = 300.0,
    entry_delay_sec: float = 0.0,
    entry_band: float = 0.0,
    limit_windows: int = 0,
):
    """Run backtest simulation on selected tick file or all files in run/ticks/."""
    from backtest import BacktestParams, iter_ticks
    from backtest.engine import _simulate_window, group_by_cid
    from strategy.series import SERIES

    series_label_map = {s[0]: s[2] for s in SERIES}

    exit_thresh = {
        "default_5m": exit_default_5m,
        "default_15m": exit_default_15m,
        "btc-up-or-down-5m": exit_btc_5m,
        "sol-up-or-down-5m": exit_sol_5m,
        "btc-up-or-down-15m": exit_btc_5m,
        "sol-up-or-down-15m": exit_sol_5m,
    }

    size = max(5, int(size))

    if filter_partial and max_start_delay <= 0:
        max_start_delay = 5.0

    # Drift-skip re-entry (issue #95) query knobs, clamped like the live
    # engine's update_config: band 0 disables re-entry, 0 <= band <= 0.50.
    reentry_drift_band = max(0.0, min(0.50, reentry_drift_band))
    min_requote_remaining_sec = max(0.0, min_requote_remaining_sec)

    # Patient maker knobs (issue #145), clamped like LiveConfigPayload:
    # delay 0..3600 (a delay past the window simply never quotes),
    # band 0..0.50 (0 = off). Non-finite input (nan/inf) falls back to off —
    # min/max comparisons against NaN silently yield the boundary otherwise.
    if not math.isfinite(entry_delay_sec):
        entry_delay_sec = 0.0
    else:
        entry_delay_sec = max(0.0, min(3600.0, entry_delay_sec))
    if not math.isfinite(entry_band):
        entry_band = 0.0
    else:
        entry_band = max(0.0, min(0.50, entry_band))

    params = BacktestParams(
        offset=offset,
        queue_gate=queue,
        pair_cost_gate=pair_cost,
        exit_thresh_by_slug=exit_thresh,
        exit_reversal=exit_reversal,
        quote_shares=size,
        fill_model=fill_model,
        merge_gas_usd=gas,
        max_start_delay_sec=max_start_delay,
        entry_timeout_pct=entry_timeout_pct,
        reentry_drift_band=reentry_drift_band,
        min_requote_remaining_sec=min_requote_remaining_sec,
        entry_delay_sec=entry_delay_sec,
        entry_band=entry_band,
    )

    if not TICKS_DIR.exists():
        return {
            "error": "no ticks dir",
            "params_hash": params.params_hash(),
            "overall": {},
            "per_series": {},
            "equity_curve": [],
            "trades_sample": [],
            "n_snaps": 0,
            "n_windows": 0,
        }

    if file:
        if "/" in file or "\\" in file or ".." in file:
            return {
                "error": "invalid file param",
                "params_hash": params.params_hash(),
            }
        source = (TICKS_DIR / file).resolve()
        try:
            source.relative_to(TICKS_DIR.resolve())
        except ValueError:
            return {
                "error": "invalid file path",
                "params_hash": params.params_hash(),
            }
        if not source.exists() or not source.is_file():
            return {
                "error": f"file not found: {file}",
                "params_hash": params.params_hash(),
            }
        snaps = list(iter_ticks(source))
    else:
        snaps = list(iter_ticks(TICKS_DIR))

    grouped = group_by_cid(snaps)
    if not grouped:
        gp = params.grouped_params()
        return {
            "params_hash": params.params_hash(),
            "params": {
                "offset": params.offset,
                "queue": params.queue_gate,
                "pair_cost": params.pair_cost_gate,
                "exit_default_5m": exit_default_5m,
                "exit_default_15m": exit_default_15m,
                "exit_btc_5m": exit_btc_5m,
                "exit_sol_5m": exit_sol_5m,
                "exit_reversal": params.exit_reversal,
                "size": size,
                "fill_model": params.fill_model,
                "gas": params.merge_gas_usd,
                "max_start_delay": params.max_start_delay_sec,
                "reentry_drift_band": params.reentry_drift_band,
                "min_requote_remaining_sec": params.min_requote_remaining_sec,
                "entry_delay_sec": params.entry_delay_sec,
                "entry_band": params.entry_band,
            },
            "params_groups": gp,
            "overall": {
                "windows": 0,
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
            "equity_curve": [],
            "trades_sample": [],
            "n_snaps": 0,
            "n_windows": 0,
        }

    if params.max_start_delay_sec > 0:
        filtered_grouped = []
        for _cid, g in grouped:
            if not g:
                continue
            first_ts = float(g[0].get("ts", 0.0) or 0.0)
            start_ts = float(g[0].get("start_ts", 0.0) or 0.0)
            delay = max(0.0, first_ts - start_ts) if (first_ts and start_ts) else 0.0
            if delay <= params.max_start_delay_sec:
                filtered_grouped.append((_cid, g))
        grouped = filtered_grouped

    if limit_windows and limit_windows > 0:
        grouped = grouped[:limit_windows]

    per_window = [_simulate_window(g, params) for _cid, g in grouped]
    n_snaps = sum(len(g) for _cid, g in grouped)

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
    per_series_raw = defaultdict(
        lambda: {
            "windows": 0,
            "pairs": 0,
            "exits": 0,
            "oscillating": 0,
            "monotonic": 0,
            "flat": 0,
            "total_pnl_cents": 0.0,
            "reentry_count": 0,
            "reentry_pnl_cents": 0.0,
        }
    )

    trades_sample = []
    profitable_pairs = 0
    profitable_exits = 0
    unfilled_windows = 0

    for w in per_window:
        win_pnl = w.pnl_cents * size
        a = per_series_raw[w.series]
        a["windows"] += 1
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
        if w.reentry_count > 0:
            a["reentry_count"] += 1
            a["reentry_pnl_cents"] += win_pnl

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
            "pnl_cents": round(win_pnl, 2),
            "exit_reason": exit_info,
            "start_delay_sec": w.start_delay_sec,
            "is_partial": w.is_partial,
        })

    total_windows = len(per_window)
    total_pairs = sum(a["pairs"] for a in per_series_raw.values())
    total_exits = sum(a["exits"] for a in per_series_raw.values())
    total_pnl = sum(a["total_pnl_cents"] for a in per_series_raw.values())
    total_reentry_count = sum(a["reentry_count"] for a in per_series_raw.values())
    total_reentry_pnl = sum(a["reentry_pnl_cents"] for a in per_series_raw.values())

    overall = {
        "windows": total_windows,
        "pairs": total_pairs,
        "pair_rate": round(total_pairs / total_windows, 4)
        if total_windows
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
        "reentry_count": total_reentry_count,
        "reentry_pnl_cents": round(total_reentry_pnl, 2),
    }

    per_series_out = {}
    for s_slug, duration, s_label in SERIES:
        a = per_series_raw.get(
            s_slug,
            {
                "windows": 0,
                "pairs": 0,
                "exits": 0,
                "oscillating": 0,
                "monotonic": 0,
                "flat": 0,
                "total_pnl_cents": 0.0,
                "reentry_count": 0,
                "reentry_pnl_cents": 0.0,
            },
        )
        n = a["windows"]
        per_series_out[s_slug] = {
            "label": s_label,
            "windows": n,
            "pairs": a["pairs"],
            "pair_rate": round(a["pairs"] / n, 4) if n else 0.0,
            "exits": a["exits"],
            "exit_rate": round(a["exits"] / n, 4) if n else 0.0,
            "total_pnl_cents": round(a["total_pnl_cents"], 2),
            "avg_pnl_cents": round(a["total_pnl_cents"] / n, 2) if n else 0.0,
            "oscillating": a["oscillating"],
            "monotonic": a["monotonic"],
            "reentry_count": a["reentry_count"],
            "reentry_pnl_cents": round(a["reentry_pnl_cents"], 2),
        }

    gp = params.grouped_params()
    return {
        "params_hash": params.params_hash(),
        "params": {
            "offset": offset,
            "queue": queue,
            "pair_cost": pair_cost,
            "exit_default_5m": exit_default_5m,
            "exit_default_15m": exit_default_15m,
            "exit_btc_5m": exit_btc_5m,
            "exit_sol_5m": exit_sol_5m,
            "fill_model": fill_model,
            "size": size,
            "gas": gas,
            "max_start_delay_sec": max_start_delay,
            "reentry_drift_band": round(reentry_drift_band, 4),
            "min_requote_remaining_sec": round(min_requote_remaining_sec, 2),
            "entry_delay_sec": entry_delay_sec,
            "entry_band": entry_band,
        },
        "params_groups": gp,
        "n_snaps": n_snaps,
        "n_windows": total_windows,
        "overall": overall,
        "per_series": per_series_out,
        "equity_curve": equity_curve,
        "trades_sample": trades_sample,
    }


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
@app.get("/api/collector/status")
def api_collector_status():
    """Return status of the background tick collector, today's ticks, and tape empty-rate health."""
    global _collector_proc
    running = _collector_proc is not None and _collector_proc.poll() is None
    # Count total tick lines collected today
    today_ticks = 0
    today_file = (
        TICKS_DIR / f"ticks_{time.strftime('%Y-%m-%d', time.gmtime())}.jsonl"
    )
    if today_file.exists():
        today_ticks = _count_lines_fast(today_file)

    tape_empty_rate = None
    tape_recent_empty_rate = None
    tape_entries_total = 0
    tape_alert = False
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
        _collector_proc = subprocess.Popen(
            cmd, cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
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


# --- Live Trading Cockpit Endpoints ---


@app.get("/api/live/state")
def api_live_state():
    """Return real-time state snapshot of the Live Trading Cockpit engine."""
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
                   "verdict": "awaiting fills", "error": str(e)[:200]}
    cached["ts"] = now
    cached["payload"] = payload
    return payload


@app.get("/api/live/latency")
def api_live_latency(series: str = "btc-up-or-down-5m"):
    """Return real-time side-by-side stream metrics and latency lead times."""
    from strategy.streaming import SERIES_TO_SYMBOL
    symbol = SERIES_TO_SYMBOL.get(series, "btcusdt")
    engine = get_live_trader_engine()
    engine.ensure_telemetry_streaming()
    bridge_st = engine.stream_bridge.get_status()

    spot_price = None
    actual_price = None
    rtds_price = None
    price_diff = None
    price_diff_pct = None
    spot_drift = 0.0
    clob_mid = None
    latency_ms = None
    streaming_active = False
    updated_ts = None

    if series in engine.markets:
        m = engine.markets[series]
        spot_price = m.spot_price
        actual_price = m.actual_price
        rtds_price = m.rtds_price
        price_diff = m.price_diff
        price_diff_pct = m.price_diff_pct
        spot_drift = m.spot_drift
        streaming_active = m.streaming_active
        updated_ts = m.spot_updated_ts
        if m.up_bid is not None and m.up_ask is not None:
            clob_mid = round((m.up_bid + m.up_ask) / 2.0, 4)
        elif m.resting_up is not None:
            clob_mid = m.resting_up

    # Fallback to bridge prices if market price fields are not populated yet
    if actual_price is None and bridge_st.get("binance_prices"):
        actual_price = bridge_st["binance_prices"].get(symbol)
    if rtds_price is None and bridge_st.get("rtds_prices"):
        rtds_price = bridge_st["rtds_prices"].get(symbol)
    if price_diff is None and bridge_st.get("price_diffs"):
        price_diff = bridge_st["price_diffs"].get(symbol)
    if price_diff_pct is None and bridge_st.get("price_diff_pcts"):
        price_diff_pct = bridge_st["price_diff_pcts"].get(symbol)

    if spot_price is None and bridge_st.get("symbols"):
        spot_price = bridge_st["symbols"].get(symbol)
    if spot_price is None:
        spot_price = actual_price if actual_price is not None else rtds_price

    if updated_ts:
        raw_lat = abs(time.time() - updated_ts)
        if raw_lat <= 15.0:
            latency_ms = round(raw_lat * 1000.0, 1)

    return {
        "ok": True,
        "series": series,
        "symbol": symbol,
        "spot_price": spot_price,
        "actual_price": actual_price,
        "rtds_price": rtds_price,
        "price_diff": price_diff,
        "price_diff_pct": price_diff_pct,
        "spot_drift": round(spot_drift, 4),
        "clob_mid": clob_mid,
        "latency_ms": latency_ms,
        "streaming_active": streaming_active,
        "is_running": bridge_st.get("is_running", False),
        "binance_ws_connected": bridge_st.get("binance_ws_connected", False),
        "rtds_connected": bridge_st.get("rtds_connected", False),
        "active_spot_source": bridge_st.get("active_spot_source", "RTDS"),
        "clob_ws_connected": bridge_st.get("clob_ws_connected", False),
        "updated_ts": updated_ts,
    }


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
    entry_timeout_pct: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    exit_reversal: Optional[float] = Field(default=None, ge=0.001, le=0.50)
    reentry_drift_band: Optional[float] = Field(default=None, ge=0.0, le=0.50)
    exit_thresh_naked: Optional[float] = Field(default=None, ge=0.001, le=0.50)
    naked_leg_timeout_pct: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    reentry_require_pairable: Optional[bool] = None
    # Issue #137: patient undecided-band maker knobs. entry_delay_sec has no
    # upper bound (a delay past the window simply never quotes); entry_band
    # matches the engine's 0..0.50 clamp; max_pair_cost matches 0.50..1.00.
    preset: Optional[str] = None
    # No upper bound would let a typo (or inf) silently never quote, since a
    # delay past the window end never expires. 3600s is 4x the longest 900s
    # window — anything larger is rejected at the boundary instead.
    entry_delay_sec: Optional[float] = Field(default=None, ge=0.0, le=3600.0)
    entry_band: Optional[float] = Field(default=None, ge=0.0, le=0.50)
    stop_loss_enabled: Optional[bool] = None
    enable_leg_chase: Optional[bool] = None
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

    @field_validator("exit_thresh_naked", mode="before")
    @classmethod
    def normalize_exit_thresh_naked(cls, v: Any) -> Any:
        """Normalize whole-number naked stop values (1-50) entered as cents to decimals."""
        if v is not None:
            try:
                fv = float(v)
                if fv.is_integer() and 1.0 <= fv <= 50.0:
                    return fv / 100.0
                return fv
            except (ValueError, TypeError):
                pass
        return v

    @field_validator("naked_leg_timeout_pct", mode="before")
    @classmethod
    def normalize_naked_leg_timeout_pct(cls, v: Any) -> Any:
        """Normalize a whole-number percentage above 1 (2-100) to a decimal fraction."""
        if v is not None:
            try:
                fv = float(v)
                if 1.0 < fv <= 100.0:
                    return fv / 100.0
                return fv
            except (ValueError, TypeError):
                pass
        return v

    @field_validator("entry_timeout_pct", mode="before")
    @classmethod
    def normalize_entry_timeout_pct(cls, v: Any) -> Any:
        """Normalize a whole-number percentage above 1 (2-100) to a decimal (0.02-1.0).

        Values of 1.0 or below are passed through as decimals already in range, so
        `1` and `1.0` both mean a full window rather than one percent. The cockpit
        never relies on this ambiguity -- it divides its 1-100 field by 100 before
        posting -- but API callers can send either form.
        """
        if v is not None:
            try:
                fv = float(v)
                if 1.0 < fv <= 100.0:
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
            entry_timeout_pct=payload.entry_timeout_pct,
            exit_reversal=payload.exit_reversal,
            reentry_drift_band=payload.reentry_drift_band,
            exit_thresh_naked=payload.exit_thresh_naked,
            naked_leg_timeout_pct=payload.naked_leg_timeout_pct,
            reentry_require_pairable=payload.reentry_require_pairable,
            enable_leg_chase=payload.enable_leg_chase,
            max_pair_cost=payload.max_pair_cost,
            entry_delay_sec=payload.entry_delay_sec,
            entry_band=payload.entry_band,
            stop_loss_enabled=payload.stop_loss_enabled,
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
# Live scan progress: filename -> {"lines": int, "est_total": int, "started_at": float}
_VERIFY_SCAN_PROGRESS: dict[str, dict[str, Any]] = {}


def _file_fingerprint(path: Path) -> str:
    """Cheap identity for a tick file: size + mtime_ns."""
    st = path.stat()
    return f"{st.st_size}:{st.st_mtime_ns}"


def _verify_sidecar_path(filename: str) -> Path:
    """Path of the verify-report sidecar JSON for a tick file."""
    return TICKS_DIR / _VERIFY_CACHE_DIRNAME / f"{filename}.json"


def _write_verify_sidecar(target: Path, rep: dict[str, Any]) -> None:
    """Persist the full report (superset of the old counts sidecar), best-effort."""
    try:
        cache_dir = TICKS_DIR / _VERIFY_CACHE_DIRNAME
        cache_dir.mkdir(parents=True, exist_ok=True)
        payload = dict(rep)
        payload["ts"] = time.time()
        (cache_dir / f"{target.name}.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
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
                    cache_dir / f"{f.name}.json",
                    expected_fingerprint=_file_fingerprint(f),
                )
            except OSError:
                continue
            if side and "status" in side:
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

    if "/" in file or "\\" in file or ".." in file:
        return JSONResponse(status_code=400, content={"error": "invalid file param"})
    target = (TICKS_DIR / file).resolve()
    try:
        target.relative_to(TICKS_DIR.resolve())
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "invalid file path"})
    if not target.exists() or not target.is_file():
        return JSONResponse(status_code=404, content={"error": f"file not found: {file}"})

    fp = _file_fingerprint(target)
    entry = _VERIFY_REPORT_CACHE.get(file)

    # Fresh in-memory report for the exact current file: serve instantly.
    if not refresh and entry and entry.get("fingerprint") == fp:
        out = dict(entry["report"])
        out["cached"] = True
        return out

    # Stale-but-recent snapshot (e.g. today's file the collector is still
    # appending to): serve it immediately and rescan in the background.
    stale_rep: dict[str, Any] | None = None
    if not refresh and entry and (
        time.time() - float(entry.get("scanned_at") or 0) <= _VERIFY_RESCAN_COOLDOWN_SEC
    ):
        stale_rep = dict(entry["report"])
    else:
        side = _read_verify_cache(_verify_sidecar_path(file), expected_fingerprint=fp)
        if side and not refresh and "status" in side:
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


# --- Front-end SPA (Complete Hebrew RTL Studio: Live / Backtest Lab / Statistical Analysis / Tick Data Manager) ---

FULL_APP_HTML = r"""<!doctype html><html lang="en" dir="ltr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Crypto Spread — 5m/15m SPREAD-2 Engine & Lab</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='7' fill='%230a0d12' stroke='%23232a35' stroke-width='1.5'/><path d='M10 6v20' stroke='%2333c9b5' stroke-width='2' stroke-linecap='round'/><rect x='7' y='10' width='6' height='11' rx='2' fill='%2333c9b5'/><path d='M22 6v20' stroke='%23f0684d' stroke-width='2' stroke-linecap='round'/><rect x='19' y='11' width='6' height='11' rx='2' fill='%23f0684d'/></svg>">
<link rel="alternate icon" href="/favicon.ico">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
:root{--bg:#0a0d12;--panel:#12161d;--panel2:#171c24;--line:#232a35;--line-hi:#364152;--tx:#e7ebf3;--dim:#8792a6;--faint:#535e70;--up:#33c9b5;--upS:#12302c;--down:#f0684d;--downS:#311b18;--gold:#e8b84b;--proj:#7b9bf7;--disp:'Space Grotesk',system-ui;--mono:'IBM Plex Mono',monospace;--body:'IBM Plex Sans',system-ui;--sidebar-w-collapsed: 48px;--sidebar-w-expanded: 220px}
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
.tbl{width:100%;border-collapse:collapse;margin-top:10px;font-size:13px}
.tbl th{font:700 11px var(--disp);letter-spacing:.06em;text-transform:uppercase;color:var(--faint);text-align:left;padding:8px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
.tbl td{padding:10px 8px;border-bottom:1px solid #1a2029;font-size:13px;vertical-align:middle}
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
.live-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:8px}
@media(max-width:1000px){.live-grid{grid-template-columns:repeat(2,1fr)}}
.liveBox{background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:9px 10px}
.btn{background:var(--panel2);color:var(--tx);border:1px solid var(--line);border-radius:8px;padding:6px 12px;font:600 12px var(--disp);cursor:pointer;display:inline-flex;align-items:center;gap:6px}
.btn:hover{background:var(--line);border-color:var(--line-hi)}
.btn-primary{background:var(--up);color:#0a0d12;border:none;font-weight:700;position:relative;transition:all .2s ease}
.btn-primary:hover{background:#2bb5a2}
.btn-primary:disabled{opacity:0.75;cursor:wait}
.btn-primary.thinking{background:#2bb5a2;box-shadow:0 0 12px rgba(51,201,181,0.45);animation:pulse-glow 1.4s infinite alternate;pointer-events:none}
@keyframes pulse-glow{0%{box-shadow:0 0 4px rgba(51,201,181,0.3);transform:scale(0.995)}100%{box-shadow:0 0 16px rgba(51,201,181,0.7);transform:scale(1.015)}}
.spinner{width:12px;height:12px;border:2px solid rgba(10,13,18,0.25);border-top-color:#0a0d12;border-radius:50%;display:inline-block;animation:spin .7s linear infinite;vertical-align:middle;margin-left:4px}
@keyframes spin{to{transform:rotate(360deg)}}
.thinking-dots{display:inline-flex;align-items:center;gap:3px;margin-right:2px}
.thinking-dots span{width:4px;height:4px;background:#0a0d12;border-radius:50%;display:inline-block;animation:dot-blink 1.2s infinite ease-in-out}
.thinking-dots span:nth-child(2){animation-delay:0.2s}
.thinking-dots span:nth-child(3){animation-delay:0.4s}
@keyframes dot-blink{0%,80%,100%{opacity:0.2;transform:scale(0.8)}40%{opacity:1;transform:scale(1.2)}}
.btn-danger{background:rgba(240,104,77,.2);color:var(--down);border-color:rgba(240,104,77,.4)}
.btn-danger:hover{background:rgba(240,104,77,.3)}
.form-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
@media(max-width:900px){.form-grid{grid-template-columns:repeat(2,1fr)}}
.form-group{display:flex;flex-direction:column;gap:4px}
.form-group label{font:600 11px var(--disp);color:var(--dim);letter-spacing:.04em;text-align:left}
.form-group input, .form-group select{background:var(--panel2);color:var(--tx);border:1px solid var(--line);border-radius:8px;padding:7px 10px;font:500 13px var(--mono);transition:border-color .15s ease,box-shadow .15s ease,background .15s ease}
.form-group input::placeholder{color:var(--faint,#78879b);opacity:0.75}
.form-group input.input-invalid{border:1px solid var(--down,#f0684d) !important;box-shadow:0 0 6px rgba(240,104,77,0.45) !important;background:rgba(240,104,77,0.06) !important}
.form-group .input-hint{font:500 10px var(--mono);color:var(--faint,#78879b);margin-top:2px;display:block}
.form-group .input-hint.err{color:var(--down,#f0684d);font-weight:600}
.tab-content{display:none}
.tab-content.active{display:block}
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
.ot-pane .tbl thead th{position:sticky;top:0;z-index:3;background:var(--panel);box-shadow:0 1px 0 var(--line)}
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
.telemetry-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px}
@media(max-width:1000px){.telemetry-grid{grid-template-columns:repeat(2,1fr)}}
.tel-item{background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:8px 10px;text-align:center;display:flex;flex-direction:column;gap:4px}
.tel-lbl{font:600 9px var(--disp);letter-spacing:.07em;color:var(--faint);text-transform:uppercase}
.tel-val{font:700 16px var(--mono);color:var(--tx)}
.tel-badge{font:700 10px var(--disp);letter-spacing:.06em;padding:2px 8px;border-radius:99px;display:inline-block;margin:0 auto}
.tel-badge.ok{background:rgba(51,201,181,.15);color:var(--up);border:1px solid rgba(51,201,181,.3)}
.tel-badge.warn{background:rgba(243,186,47,.15);color:var(--gold);border:1px solid rgba(243,186,47,.3)}
.tel-badge.err{background:rgba(240,104,77,.15);color:var(--down);border:1px solid rgba(240,104,77,.3)}
.tel-badge.idle{background:rgba(120,135,155,.15);color:var(--dim);border:1px solid rgba(120,135,155,.3)}
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
.bt-section-desc{font:500 10px var(--mono);color:var(--faint);margin:0 0 8px;line-height:1.5}
.bt-section-dot{display:inline-block;width:8px;height:8px;border-radius:50%}
.bt-section-dot-green{background:var(--up)}
.bt-section-dot-amber{background:var(--gold)}
.bt-section-dot-blue{background:var(--proj)}
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
    <button class="sidebar-tab-btn active" id="tab-btn-cockpit" onclick="switchTab('cockpit')" title="Live Trading Cockpit">
      <span class="nav-icon">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
        </svg>
      </span>
      <span class="nav-label">Live Trading Cockpit</span>
    </button>
    <button class="sidebar-tab-btn" id="tab-btn-live" onclick="switchTab('live')" title="Live Books & Queue">
      <span class="nav-icon">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M4.93 4.93a10 10 0 0 1 14.14 0"></path>
          <path d="M7.76 7.76a6 6 0 0 1 8.48 0"></path>
          <circle cx="12" cy="12" r="2"></circle>
          <path d="M12 14v7"></path>
        </svg>
      </span>
      <span class="nav-label">Live Books & Queue</span>
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
    <button class="btn" id="btnToggleCollector" onclick="toggleCollector()">Start Polling (1s)</button>
    <button class="btn" onclick="pollOnce()">Poll Now (Once)</button>
  </div>
</div>

<div class="wrap">
  <!-- TAB 1: LIVE & RECENT WINDOWS -->
  <div id="tab-live" class="tab-content">
    <div id="goalBar" class="card" style="border-top:2px solid var(--gold)"></div>
    <div id="liveBar" class="card"></div>
    <div id="seriesGrid" class="grid"></div>
    <div id="windowsTableWrap"></div>
  </div>

  <!-- TAB 2: BACKTEST ENGINE & SWEEPER -->
  <div id="tab-backtest" class="tab-content">
    <div class="card" style="border-top:2px solid var(--up)">
      <h3>⚡ Backtest Parameters <span class="mono" id="btHash" style="font-size:11px;color:var(--dim)"></span></h3>
      <div class="bt-accordion" style="margin-top:12px">
        <!-- ── 1. OPERATOR CONTROLS (live-replicable) ─────────────────────── -->
        <div class="bt-section" id="btSecOperator">
          <button type="button" class="bt-section-head" aria-expanded="true" aria-controls="btSecOperatorBody" onclick="toggleBtSection(this,'btSecOperatorBody')">
            <span class="bt-section-dot bt-section-dot-green"></span>
            <span>Operator Controls — set these live on the book</span>
            <span class="bt-section-chevron" aria-hidden="true">▾</span>
          </button>
          <div class="bt-section-body" id="btSecOperatorBody">
          <div class="bt-section-desc">
            These are the parameters you actually control when trading live:
            where you rest, how much book you clear through, your cost ceiling,
            your stop placement, your sizing, and which windows you allow.
          </div>
          <div class="form-grid" style="margin-top:6px">
            <div class="form-group">
              <label>Tick File Dataset</label>
              <select id="btFileSelect">
                <option value="">All Files / 2,820 Windows (Default)</option>
              </select>
            </div>
            <div class="form-group">
              <label>Offset from Mid ($0.02 = 0.02 spread)</label>
              <input type="number" step="0.005" id="btOffset" value="0.02">
            </div>
            <div class="form-group">
              <label>Queue Depth Ahead (0 = no filter)</label>
              <input type="number" step="5" id="btQueue" value="0">
            </div>
            <div class="form-group">
              <label>Entry Delay, s (0 = off, max 3600)</label>
              <input type="number" min="0" max="3600" step="1" id="btEntryDelay" value="0">
            </div>
            <div class="form-group">
              <label>Entry Band (0 = off)</label>
              <input type="number" min="0" max="0.5" step="0.005" id="btEntryBand" value="0">
            </div>
            <div class="form-group">
              <div style="display:flex;justify-content:space-between;align-items:center">
                <label for="btPairCost">Max Pair Cost ($)</label>
                <label class="toggle-wrap" title="Enable or disable max pair cost filter">
                  <span id="btPairCostToggleLabel" class="mono" style="font-size:10px;font-weight:700;color:var(--dim)">OFF</span>
                  <div class="toggle-switch">
                    <input type="checkbox" id="btPairCostEnabled" onchange="togglePairCostInput()">
                    <span class="toggle-slider"></span>
                  </div>
                </label>
              </div>
              <input type="number" step="0.005" id="btPairCost" value="1.05" disabled style="opacity:0.45">
            </div>
            <div class="form-group">
              <label>Exit Stop Loss 5m ($)</label>
              <input type="number" step="0.01" id="btExit5m" value="0.05">
            </div>
            <div class="form-group">
              <label>Exit Stop Loss 15m ($)</label>
              <input type="number" step="0.01" id="btExit15m" value="0.05">
            </div>
            <div class="form-group">
              <label>BTC 5m Stop Loss ($)</label>
              <input type="number" step="0.01" id="btExitBtc" value="0.05">
            </div>
            <div class="form-group">
              <label>SOL 5m Stop Loss ($)</label>
              <input type="number" step="0.01" id="btExitSol" value="0.05">
            </div>
            <div class="form-group">
              <label>Order Shares per Leg (Min 5)</label>
              <input type="number" min="5" step="1" id="btSize" value="5">
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

        <!-- ── 2. EXECUTION / FILLS (assumption — not live-settable) ──────── -->
        <div class="bt-section">
          <button type="button" class="bt-section-head" aria-expanded="false" aria-controls="btSecExecutionBody" onclick="toggleBtSection(this,'btSecExecutionBody')">
            <span class="bt-section-dot bt-section-dot-amber"></span>
            <span>Execution Assumptions — model-side, not directly settable live</span>
            <span class="bt-section-chevron" aria-hidden="true">▾</span>
          </button>
          <div class="bt-section-body" id="btSecExecutionBody">
          <div class="bt-section-desc">
            These describe how we assume the book fills you. In live trading the
            venue decides fills — you cannot set a fill model on an order. They
            are tuning knobs for the replay, not operator controls.
          </div>
          <div class="form-grid" style="margin-top:6px">
            <div class="form-group">
              <label>Fill Model</label>
              <select id="btFillModel">
                <option value="cross" selected>Cross (Strict Through-Price Fill — Ask ≤ Resting Bid - 1¢)</option>
                <option value="tape">Tape (Conservative - executed trades)</option>
                <option value="book">Book (Optimistic - Ask crossing)</option>
                <option value="both">Both</option>
              </select>
            </div>
            <div class="form-group">
              <label>Gas Merge Cost (USD)</label>
              <input type="number" step="0.01" id="btGas" value="0.00">
            </div>
          </div>
          </div>
        </div>

        <!-- ── 3. WINDOW POLICY (research knobs) ─────────────────────────── -->
        <div class="bt-section">
          <button type="button" class="bt-section-head" aria-expanded="false" aria-controls="btSecPolicyBody" onclick="toggleBtSection(this,'btSecPolicyBody')">
            <span class="bt-section-dot bt-section-dot-blue"></span>
            <span>Window Policy — internal engine policy, mirrors live config</span>
            <span class="bt-section-chevron" aria-hidden="true">▾</span>
          </button>
          <div class="bt-section-body" id="btSecPolicyBody">
          <div class="bt-section-desc">
            These are engine policy knobs. They have a live counterpart in the
            trader config, but tuning them here is research work — for example
            how long a window must have left before you allow a re-entry, or how
            wide a drift band you tolerate.
          </div>
          <div class="form-grid" style="margin-top:6px">
            <div class="form-group">
              <label>Drift Re-Entry Band (0 = off)</label>
              <input type="number" min="0" max="0.5" step="0.005" id="btReentryBand" value="0.015">
            </div>
            <div class="form-group">
              <label>Min Window Left for Re-Entry (s)</label>
              <input type="number" min="0" step="5" id="btRequoteMin" value="300">
            </div>
          </div>
          </div>
        </div>
      </div>
      <div style="margin-top:14px;display:flex;gap:8px">
        <button class="btn btn-primary" id="btnRunSweep" onclick="runBacktest()"><span id="btnRunSweepIcon">▶</span> <span id="btnRunSweepText">Run Sweep</span></button>
        <button class="btn" id="btnResetParams" onclick="resetBtParams()">Reset to Defaults</button>
        <button class="btn" id="btnWinningConfig" onclick="applyWinningConfig()">🏆 Winning config</button>
      </div>
    </div>

    <div class="card" id="btOverallCard">
      <h3>📈 Overall Execution Results</h3>
      <div class="kpi" id="btKpiRow">
        <div class="box" title="Net cumulative P&L across all executed windows"><div class="lbl">Total P&L</div><div class="val" id="btTotalPnl" style="color:var(--up)">+$0.00</div><div class="sub" id="btAvgPnl">+$0.00 / window</div></div>
        <div class="box" title="Proportion of windows where both legs filled and merged for profit"><div class="lbl">Pair Capture Rate ℹ️</div><div class="val" id="btPairRate">0.0%</div><div class="sub" id="btPairsCount">0 / 0 pairs</div></div>
        <div class="box" title="Proportion of windows where safety stop exit was triggered on adverse drift"><div class="lbl">Exit Stop Rate ℹ️</div><div class="val" id="btExitRate" style="color:var(--down)">0.0%</div><div class="sub" id="btExitsCount">0 exits</div></div>
        <div class="box" title="Maximum peak-to-trough equity drawdown"><div class="lbl">Max Drawdown</div><div class="val" id="btMaxDd" style="color:var(--gold)">-$0.00</div><div class="sub">Peak to trough</div></div>
        <div class="box" title="Proportion of windows with net positive P&L (merged pairs + profitable exits)"><div class="lbl">Win Rate ℹ️</div><div class="val" id="btWinRate">0.0%</div><div class="sub" id="btWinsCount">0 / 0 profitable</div></div>
        <div class="box" title="Windows recovered by re-entry rule after initial adverse-open skip"><div class="lbl">Drift Re-Entry</div><div class="val" id="btReentry" style="color:var(--dim)">—</div><div class="sub" id="btReentryPnl">Windows recovered after drift skip</div></div>
      </div>
      <div style="background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:12px;margin-top:12px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
          <h4 style="margin:0;font:700 11px var(--disp);color:var(--faint)">Cumulative Equity Curve</h4>
          <span id="btEquityWarning" style="display:none;font-size:11px;font-weight:600;color:var(--gold);background:rgba(235,178,58,0.12);padding:2px 8px;border-radius:4px;border:1px solid rgba(235,178,58,0.3)">⚠️ 0 fills recorded in this run. Verify fill_model or tape data density.</span>
        </div>
        <canvas id="chartEquity" height="140"></canvas>
      </div>
    </div>

    <div class="card">
      <h3>📊 Per-Series Performance</h3>
      <div id="btSeriesTableWrap"></div>
    </div>

    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:12px">
        <h3 style="margin:0">📝 Executed Windows Log</h3>
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

  <!-- TAB 3: STATISTICAL ANALYSIS & DISTRIBUTIONS -->
  <div id="tab-summary" class="tab-content">
    <div class="hero" style="display:grid;grid-template-columns:1.2fr .8fr;gap:12px;margin-bottom:12px">
      <div class="card" style="border-top:2px solid var(--up)">
        <h3>Research Conclusion — SPREAD-2</h3>
        <div style="font:700 24px var(--mono);color:var(--up);margin:4px 0">74% of Windows Are Oscillating</div>
        <div style="font-size:12.5px;color:var(--dim);line-height:1.6">
          Across 2,820+ empirical windows measured in 5m and 15m: on 5m <b>73% oscillating</b> — both sides quoted dynamically at <code>mid - offset</code> (e.g. $0.48 on 50¢ mid, $0.96/pair) are filled and merged for +$0.04/share profit. On 15m <b>80% oscillating</b>.
        </div>
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
            <span>⚡</span> Live Trading Cockpit (5m Markets)
          </h3>
          <span id="cockpitStatusPill" class="pill pill-flat" style="font-size:11px;padding:3px 10px;font-weight:700">BOT: STOPPED</span>
          <span id="cockpitModePill" class="pill" style="font-size:11px;padding:3px 10px;background:rgba(51,201,181,0.15);color:var(--up);border-color:rgba(51,201,181,0.3);font-weight:700">PAPER TRADING</span>
          <span id="cockpitStreamPill" class="pill pill-flat" style="font-size:11px;padding:3px 10px;font-weight:700">📡 STREAM: CONNECTING...</span>
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
          <label>Spread Offset (Rest @ 0.50 - offset)</label>
          <input type="number" step="0.005" min="0.001" max="0.490" id="cockpitOffset" value="0.02" placeholder="0.001 – 0.490" oninput="validateCockpitInputs()">
        </div>
        <div class="form-group">
          <label>Safety Exit Cap ($)</label>
          <input type="number" step="0.005" min="0.001" max="0.500" id="cockpitExit" value="0.05" placeholder="0.001 – 0.500" oninput="validateCockpitInputs()">
        </div>
        <div class="form-group">
          <label>Stop Loss Trigger ($)</label>
          <input type="number" step="0.005" min="0.001" max="0.500" id="cockpitExitNaked" value="0.05" placeholder="0.001 – 0.500" oninput="validateCockpitInputs()">
        </div>
        <div class="form-group">
          <label>Naked Timeout (% of window)</label>
          <input type="number" min="0" max="100" step="5" id="cockpitNakedTimeout" value="70" placeholder="0 = off" oninput="validateCockpitInputs()">
        </div>
        <div class="form-group">
          <label>Re-entry must be pairable</label>
          <select id="cockpitReentryPairable">
            <option value="true" selected>Yes — gate late re-entry</option>
            <option value="false">No — issue #95 behavior</option>
          </select>
        </div>
        <div class="form-group">
          <label>Exit Reversal Buffer ($)</label>
          <input type="number" step="0.005" min="0.001" max="0.500" id="cockpitExitReversal" value="0.02" placeholder="0.001 – 0.500" oninput="validateCockpitInputs()">
        </div>
        <div class="form-group">
          <label>Share Size (per leg)</label>
          <input type="number" min="5" max="10000" step="1" id="cockpitShares" value="5" placeholder="5 – 10000" oninput="validateCockpitInputs()">
        </div>
        <div class="form-group">
          <label>Execution Mode</label>
          <select id="cockpitMode" onchange="onCockpitModeChange()">
            <option value="paper" selected>Paper Simulation (Live Book)</option>
            <option value="live">Live Polymarket Orders</option>
          </select>
        </div>
        <div class="form-group">
          <label>Entry Timeout (% of window)</label>
          <input type="number" min="1" max="100" step="5" id="cockpitEntryTimeout" value="100" placeholder="100 = full window" oninput="validateCockpitInputs()">
        </div>
        <div class="form-group" style="grid-column:span 2">
          <label id="lblCockpitWallet">Polymarket Wallet Address (Optional)</label>
          <input type="text" id="cockpitWallet" placeholder="0x... (Fetches Live Balance)" onchange="if ($('cockpitMode').value === 'live') onCockpitModeChange()">
        </div>
        <div class="form-group">
          <label id="lblCockpitStartBal">Starting Portfolio Balance ($)</label>
          <input type="number" min="5" step="10" id="cockpitStartBal" value="1000.00" placeholder="≥ 5.00" oninput="validateCockpitInputs()">
        </div>
        <div class="form-group" style="justify-content:flex-end;align-items:flex-end;gap:6px">
          <span id="cockpitParamsLockHint" style="display:none;font:700 10px var(--disp);color:var(--warn,#f0b90b);letter-spacing:0.04em;text-align:right">🔒 LOCKED WHILE BOT IS RUNNING — STOP THE BOT TO CHANGE PARAMETERS</span>
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
          <span id="cockpitFilterLockHint" style="display:none;font:700 10px var(--disp);color:var(--warn,#f0b90b);letter-spacing:0.04em">🔒 LOCKED WHILE BOT IS RUNNING — STOP THE BOT TO CHANGE MARKETS</span>
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

    <!-- Live Stream Telemetry (RTDS vs CLOB) Card -->
    <div class="card" id="card-stream-telemetry">
      <div class="card-title">LIVE STREAM TELEMETRY (RTDS vs CLOB)</div>
      <div class="telemetry-grid">
        <div class="tel-item"><span class="tel-lbl">ACTUAL SPOT (BINANCE)</span><span class="tel-val" id="telActualPrice">--</span></div>
        <div class="tel-item"><span class="tel-lbl">RTDS SPOT</span><span class="tel-val" id="telSpotPrice">--</span></div>
        <div class="tel-item"><span class="tel-lbl">PRICE SPREAD / DIFF</span><span class="tel-val" id="telPriceDiff">--</span></div>
        <div class="tel-item"><span class="tel-lbl">SPOT DRIFT</span><span class="tel-val" id="telSpotDrift">--</span></div>
        <div class="tel-item"><span class="tel-lbl">CLOB MID</span><span class="tel-val" id="telClobMid">--</span></div>
        <div class="tel-item"><span class="tel-lbl">LEAD LATENCY</span><span class="tel-val" id="telLeadLatency">--</span></div>
        <div class="tel-item"><span class="tel-lbl">FEED HEALTH</span><span class="tel-badge idle" id="telFeedStatus">IDLE</span></div>
      </div>
    </div>

    <!-- Live KPI Summary -->
    <div class="kpi" id="cockpitKpiBar">
      <div class="box">
        <div class="lbl">Total Realized P&L</div>
        <div class="val" id="cockpitRealizedPnl" style="color:var(--tx)">$0.00</div>
        <div class="sub" id="cockpitRealizedSub">+0.0%</div>
      </div>
      <div class="box">
        <div class="lbl">Portfolio Net Value</div>
        <div class="val" id="cockpitPortfolioVal" style="color:var(--gold)">$1,000.00</div>
        <div class="sub">Live Account Equity</div>
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

    <!-- Live Market Matrix -->
    <div class="card" style="margin-top:12px">
      <h3 style="margin:0 0 10px">
        <span>🎯 Live Market Matrix</span>
        <span id="cockpitActiveMarketsBadge" class="pill pill-flat" style="font-size:11px;padding:2px 8px;font-weight:600">5 ACTIVE MARKETS</span>
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
          <button class="btn" style="font-size:11px;padding:4px 10px" onclick="fetchCockpitState()">🔄 Refresh</button>
        </div>
      </div>

      <!-- Tab 1: Open Orders -->
      <div id="otPaneOrders" class="ot-pane active" style="max-height:280px;overflow-y:auto">
        <table class="tbl" id="cockpitOrdersTable">
          <thead>
            <tr>
              <th>Time</th>
              <th>Market</th>
              <th>Side</th>
              <th>Price</th>
              <th>Size</th>
              <th>Filled</th>
              <th>Total Cost</th>
              <th>Status</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody id="cockpitOrdersBody">
            <tr><td colspan="9" style="text-align:center;color:var(--dim);padding:18px">No orders are resting on the book.</td></tr>
          </tbody>
        </table>
      </div>

      <!-- Tab 2: Positions -->
      <div id="otPanePositions" class="ot-pane" style="max-height:280px;overflow-y:auto">
        <table class="tbl" id="cockpitPositionsTable">
          <thead>
            <tr>
              <th>Time</th>
              <th>Market</th>
              <th>Side</th>
              <th>Size</th>
              <th>Base Cost</th>
              <th>Market Value</th>
              <th>Unrealized $ (%)</th>
              <th>Realized $ (%)</th>
            </tr>
          </thead>
          <tbody id="cockpitPositionsBody">
            <tr><td colspan="8" style="text-align:center;color:var(--dim);padding:18px">No open positions held in account.</td></tr>
          </tbody>
        </table>
      </div>

      <!-- Tab 3: Closed Trades -->
      <div id="otPaneTrades" class="ot-pane" style="max-height:300px;overflow-y:auto">
        <table class="tbl" id="cockpitTradesTable">
          <thead>
            <tr>
              <th>Time</th>
              <th>Market</th>
              <th>Cause</th>
              <th>Shares</th>
              <th>Base Cost</th>
              <th>Exit Price</th>
              <th>Gain / Loss $ (%)</th>
              <th>Details</th>
            </tr>
          </thead>
          <tbody id="cockpitTradesBody">
            <tr><td colspan="8" style="text-align:center;color:var(--dim);padding:20px">No closed trades recorded in this session.</td></tr>
          </tbody>
        </table>
      </div>
      <!-- Issue #139: Queue Telemetry + PnL Distribution (inside tab-cockpit) -->
      <div class="card" id="queuePanel" style="margin-top:12px">
        <h3 style="margin:0 0 10px">
          <span>📊 Queue Telemetry (tape vs tapeq)</span>
          <span id="queueVerdict" class="pill pill-flat" style="font-size:11px;padding:2px 8px;font-weight:600">awaiting fills</span>
        </h3>
        <div id="queueSvgWrap" style="width:100%;min-height:150px"></div>
        <details id="queueFallbackWrap" style="margin-top:8px;font-size:11px;color:var(--dim)">
          <summary style="cursor:pointer">Data table</summary>
          <div id="queueFallback"></div>
        </details>
      </div>

      <div class="card" id="pnlHistPanel" style="margin-top:12px">
        <h3 style="margin:0 0 10px">
          <span>📈 PnL per Position</span>
          <span id="pnlHistStats" class="pill pill-flat" style="font-size:11px;padding:2px 8px;font-weight:600">No closed trades yet</span>
        </h3>
        <div id="pnlHistSvgWrap" style="width:100%;min-height:150px"></div>
        <details id="pnlHistFallbackWrap" style="margin-top:8px;font-size:11px;color:var(--dim)">
          <summary style="cursor:pointer">Data table</summary>
          <div id="pnlHistFallback"></div>
        </details>
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
// on the Live Matrix badges to human-readable labels so e.g. next-window
// pre-quotes never display raw ADVANCE_PRE_QUOTE and matrix cards read
// "Stopped Out" / "Timed Out" instead of STOP_EXIT-style tokens. Unknown
// statuses render unchanged (still raw).
const OT_STATUS_LABELS = {
  // order / venue statuses (Open Orders Status column)
  'ADVANCE_PRE_QUOTE': 'Pre-Quote',
  'PRE_QUOTE': 'Pre-Quote',
  // market-state statuses (Live Matrix badge)
  'IDLE': 'Idle',
  'QUOTING': 'Quoting Bids',
  'FILLED_UP': 'Filled Up',
  'FILLED_DOWN': 'Filled Down',
  'PAIR_MERGED': 'Pair Merged',
  'STOP_EXIT': 'Stopped Out',
  'STOP_EXIT_PENDING': 'Stop Exiting',
  'TIMEOUT_NO_FILL': 'Timed Out',
  'DRIFT_SKIPPED': 'Drift Skipped',
  'LATE_START_SKIPPED': 'Late Start Skipped'
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
        const price = m.fill_price_up != null ? m.fill_price_up : (m.resting_up != null ? m.resting_up : (m.up_mid != null ? Math.max(0.01, +(m.up_mid - 0.02).toFixed(2)) : 0.48));
        showToast({
          type: 'filled',
          title: 'Order Filled',
          message: `${mktLabel} (UP): ${shares} shares filled @ $${price.toFixed(2)}`
        });
      }

      // Check DOWN leg fill transition
      if (!prev.filled_down && m.filled_down) {
        const price = m.fill_price_down != null ? m.fill_price_down : (m.resting_down != null ? m.resting_down : (m.down_mid != null ? Math.max(0.01, +(m.down_mid - 0.02).toFixed(2)) : 0.48));
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

function switchTab(name){
  document.querySelectorAll('.sidebar-tab-btn').forEach(b=>b.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c=>c.classList.remove('active'));
  const btn = $('tab-btn-'+name);
  const cont = $('tab-'+name);
  if(btn) btn.classList.add('active');
  if(cont) cont.classList.add('active');
  if(name==='cockpit') fetchCockpitState();
  if(name==='backtest' && !equityChartInstance) runBacktest();
  if(name==='summary') renderSummaryCharts();
  if(name==='ticks') loadManifest();
}

let isCollectorActive = false;

// Friendly market label from a series slug: "btc-up-or-down-5m" -> "BTC 5m".
const ASSET_LABELS = {btc:'BTC', eth:'ETH', bnb:'BNB', sol:'SOL', xrp:'XRP'};
function marketName(series){
  const m = /^([a-z]{3})-up-or-down-(\d+m)$/.exec(String(series||''));
  return m ? `${ASSET_LABELS[m[1]]||m[1].toUpperCase()} ${m[2]}` : String(series||'');
}

async function refreshCollectorStatus(){
  try{
    const res = await fetch('/api/collector/status');
    const st = await res.json();
    isCollectorActive = st.running;
    const src = st.source || (st.running ? 'child' : 'none');
    if(src === 'external'){
      $('collectorBadge').textContent = `Collector: 🟡 External live · ${(st.total_ticks_collected||0).toLocaleString()} ticks today (standalone writer — Start blocked)`;
      $('collectorBadge').style.color = 'var(--warn, #e8b23f)';
      $('btnToggleCollector').textContent = 'Start blocked (external live)';
      $('btnToggleCollector').className = 'btn';
      $('btnToggleCollector').disabled = true;
    } else {
      $('collectorBadge').textContent = `Collector: ${st.running ? '🟢 Running (1s)' : '⚪ Paused'} · ${(st.total_ticks_collected||0).toLocaleString()} ticks today`;
      $('collectorBadge').style.color = st.running ? 'var(--up)' : 'var(--dim)';
      $('btnToggleCollector').textContent = st.running ? 'Stop Polling' : 'Start Polling (1s)';
      $('btnToggleCollector').className = st.running ? 'btn btn-danger' : 'btn';
      $('btnToggleCollector').disabled = false;
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
  }catch{}
}

async function toggleCollector(){
  const endpoint = isCollectorActive ? '/api/collector/stop' : '/api/collector/start';
  const res = await fetch(endpoint, {method:'POST'});
  if(res.status === 409){
    let reason = 'external collector live — Start blocked';
    try{ reason = (await res.json()).error || reason; }catch{}
    $('collectorBadge').textContent = 'Collector: 🟡 Start blocked (external live)';
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

async function tick(){
  let data; try{data=await (await fetch('/api/oscillation',{cache:'no-store'})).json();}catch(e){return;}
  const sum=data.summary||{}, per=sum.per_series||{}, live=data.live||{}, wins=data.windows||[];
  refreshCollectorStatus();

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
    $('goalBar').innerHTML=`<h3>🎯 Window Capture Targets</h3><div style="display:flex;gap:10px;flex-wrap:wrap">${bar(g5)}${bar(g15)}${tot}</div>`;
  })();

  // Live bar
  let liveHtml = '<h3>Live Windows — Books & Queue</h3><div class="live-grid">';
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
  let tbl='<div class="card"><h3 style="font-size:13px">Recent Windows — 50/50 Open (Click for Polymarket)</h3><table class="tbl"><thead><tr><th>Series</th><th>Window</th><th>Open UP / DOWN</th><th style="color:var(--up)">Max UP</th><th style="color:var(--down)">Max DOWN</th><th>Candle</th><th>Class</th><th>Link</th></tr></thead><tbody>';
  for(const w of wins.slice(0,60)){
    const sm = w.start_mid, cm=w.close_mid, mx=w.max_mid, mn=w.min_mid;
    const openUp = sm==null?'-':fmtPrice(sm);
    const openDown = sm==null?'-':fmtPrice(1-sm);
    const upHigh = mx==null?'-':fmtPrice(mx);
    const upExc = fmtPrice(w.max_up||0);
    const downHigh = mn==null?'-':fmtPrice(1-mn);
    const downExc = fmtPrice(w.max_down||0);
    const o = sm==null?50:sm*100, c = cm==null?o:cm*100, h = mx==null?o:mx*100, l = mn==null?o:mn*100;
    const bodyLeft = Math.min(o,c), bodyW = Math.abs(c-o);
    const wickLeft = l, wickW = h-l;
    const bodyColor = c>=o ? 'var(--up)' : 'var(--down)';
    const candle = `<div class="candle-wrap"><div class="candle-bar"><div class="candle-wick" style="left:${wickLeft}%;width:${wickW}%;"></div><div class="candle-body" style="left:${bodyLeft}%;width:${Math.max(2,bodyW)}%;background:${bodyColor};border:1px solid ${bodyColor}"></div><div style="position:absolute;left:50%;top:0;bottom:0;width:1px;background:var(--faint);opacity:.6"></div></div>    <div style="font-size:10.5px;color:var(--dim);margin-top:1px">Range ${fmtPrice(mx!=null&&mn!=null?mx-mn:0)} · Close ${fmtPrice(cm)}</div></div>`;
    const labelStr = esc(String(w.label||''));
    const slugStr = esc(String(w.slug||'').slice(-14));
    const startTs = w.start_ts ? new Date(w.start_ts*1000).toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit'}) : '-';
    tbl+=`<tr><td style="font-weight:700">${esc(marketName(w.series||w.label||''))}</td><td class="mono" style="font-size:12px;font-variant-numeric:tabular-nums">${slugStr}<div style="font-size:10.5px;color:var(--faint);font-variant-numeric:tabular-nums">${startTs}</div></td><td><span class="price-up">${openUp}</span> | <span class="price-down">${openDown}</span></td><td class="mono" style="font-variant-numeric:tabular-nums"><span class="price-up">${upHigh}</span> (+${upExc})</td><td class="mono" style="font-variant-numeric:tabular-nums"><span class="price-down">${downHigh}</span> (+${downExc})</td><td>${candle}</td><td>${clsPill(w.class)}</td><td><a href="${esc(w.url||'#')}" target="_blank" rel="noopener" style="font-size:12px;font-weight:700">Open ↗</a></td></tr>`;
  }
  tbl+='</tbody></table></div>';
  $('windowsTableWrap').innerHTML=tbl;
}

// Backtest execution
let equityChartInstance = null;
window.selectedBacktestFile = "";

function setBacktestLoadingState(isLoading){
  const btn = $('btnRunSweep');
  const icon = $('btnRunSweepIcon');
  const text = $('btnRunSweepText');
  if(!btn) return;
  if(isLoading){
    btn.disabled = true;
    btn.classList.add('thinking');
    if(icon) icon.innerHTML = '<span class="spinner"></span>';
    if(text) text.innerHTML = 'Simulating <span class="thinking-dots"><span></span><span></span><span></span></span>';
  } else {
    btn.disabled = false;
    btn.classList.remove('thinking');
    if(icon) icon.textContent = '▶';
    if(text) text.textContent = 'Run Sweep';
  }
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
  switchTab('backtest');
  runBacktest(filename);
}

function togglePairCostInput(){
  const enabled = $('btPairCostEnabled') ? $('btPairCostEnabled').checked : false;
  const inp = $('btPairCost');
  const lbl = $('btPairCostToggleLabel');
  if(inp){
    inp.disabled = !enabled;
    inp.style.opacity = enabled ? '1' : '0.45';
  }
  if(lbl){
    lbl.textContent = enabled ? 'ON' : 'OFF';
    lbl.style.color = enabled ? 'var(--up)' : 'var(--dim)';
  }
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
  const defaults = { btSecOperatorBody: true, btSecExecutionBody: false, btSecPolicyBody: false };
  Object.keys(defaults).forEach(function(id){
    const body = document.getElementById(id);
    const btn = body ? document.querySelector('.bt-section-head[aria-controls="' + id + '"]') : null;
    if(!body || !btn) return;
    const open = (id in state) ? !!state[id] : defaults[id];
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    body.hidden = !open;
  });
})();

async function runBacktest(fileOverride){
  setBacktestLoadingState(true);
  try {
    const getVal = (id, def) => {
      const el = $(id);
      if (!el) return def;
      const v = String(el.value).trim();
      return (v !== '' && Number.isFinite(Number(v))) ? Number(v) : def;
    };

    const offset = getVal('btOffset', 0.02);
    const queue = getVal('btQueue', 50);
    const pairCostEnabled = $('btPairCostEnabled') ? $('btPairCostEnabled').checked : false;
    const pairCost = pairCostEnabled ? getVal('btPairCost', 1.05) : 0.0;
    const exit5m = getVal('btExit5m', 0.05);
    const exit15m = getVal('btExit15m', 0.05);
    const exitBtc = getVal('btExitBtc', 0.05);
    const exitSol = getVal('btExitSol', 0.05);
    const fillModel = $('btFillModel') ? $('btFillModel').value : 'cross';
    const size = Math.max(5, Math.round(getVal('btSize', 5)));
    if ($('btSize')) $('btSize').value = size;
    const gas = getVal('btGas', 0.0);

    const maxStartDelay = getVal('btMaxStartDelay', 0.0);
    const reentryBand = getVal('btReentryBand', 0.015);
    const requoteMin = getVal('btRequoteMin', 300.0);
    const entryDelay = getVal('btEntryDelay', 0.0);
    const entryBand = getVal('btEntryBand', 0.0);

    const fileVal = fileOverride !== undefined ? fileOverride : ($('btFileSelect') ? $('btFileSelect').value : (window.selectedBacktestFile || ''));
    if (fileOverride !== undefined && $('btFileSelect')) {
      $('btFileSelect').value = fileOverride;
    }

    let url = `/api/backtest?offset=${offset}&queue=${queue}&pair_cost=${pairCost}&exit_default_5m=${exit5m}&exit_default_15m=${exit15m}&exit_btc_5m=${exitBtc}&exit_sol_5m=${exitSol}&fill_model=${fillModel}&size=${size}&gas=${gas}&max_start_delay=${maxStartDelay}&reentry_drift_band=${reentryBand}&min_requote_remaining_sec=${requoteMin}&entry_delay_sec=${entryDelay}&entry_band=${entryBand}`;
    if (fileVal) {
      url += `&file=${encodeURIComponent(fileVal)}`;
    }
    const res = await fetch(url);
    const data = await res.json();

    $('btHash').textContent = `Hash: ${data.params_hash} · ${data.n_windows} windows${fileVal ? ' · [' + fileVal + ']' : ''}`;
    const ov = data.overall || {};
    $('btTotalPnl').textContent = fmtUsd(ov.total_pnl_cents||0, true);
    $('btTotalPnl').style.color = (ov.total_pnl_cents||0)>=0 ? 'var(--up)' : 'var(--down)';
    $('btAvgPnl').textContent = fmtUsd(ov.avg_pnl_cents||0, true) + ' / window';
    $('btPairRate').textContent = ((ov.pair_rate||0)*100).toFixed(1) + '%';
    $('btPairsCount').textContent = `${ov.pairs||0} / ${ov.windows||0} pairs`;
    $('btExitRate').textContent = ((ov.exit_rate||0)*100).toFixed(1) + '%';
    $('btExitsCount').textContent = `${ov.exits||0} exits`;
    $('btMaxDd').textContent = '-' + fmtPrice((ov.max_drawdown_cents||0)/100);
    $('btWinRate').textContent = ((ov.win_rate||0)*100).toFixed(1) + '%';
    if ($('btWinsCount')) {
      $('btWinsCount').textContent = `${ov.wins||0} / ${ov.windows||0} profitable`;
    }
    const reentryCount = ov.reentry_count || 0;
    const reentryPnl = ov.reentry_pnl_cents || 0;
    $('btReentry').textContent = reentryCount > 0 ? `${reentryCount} recovered` : '—';
    $('btReentry').style.color = reentryCount > 0 ? 'var(--up)' : 'var(--dim)';
    $('btReentryPnl').textContent = reentryCount > 0 ? fmtUsd(reentryPnl, true) : 'Windows recovered after drift skip';

    // Equity Curve Chart
    const eqData = data.equity_curve || [];
    const labels = eqData.map(e => e.window_idx);
    const pnlValues = eqData.map(e => ((e.cumulative_pnl_cents||0)/100).toFixed(2));

    // Zero-fill / flatline warning diagnostic
    const fillsCount = (ov.pairs || 0) + (ov.exits || 0);
    const hasFills = fillsCount > 0 || (ov.total_pnl_cents || 0) !== 0;
    if ($('btEquityWarning')) {
      $('btEquityWarning').style.display = hasFills ? 'none' : 'inline-block';
    }

    destroyChartInstance('chartEquity');
    const ctx = $('chartEquity').getContext('2d');
    equityChartInstance = new Chart(ctx, {
      type: 'line',
      data: {
        labels: labels,
        datasets: [{
          label: 'Cumulative PnL ($)',
          data: pnlValues,
          borderColor: (ov.total_pnl_cents||0)>=0 ? '#33c9b5' : '#f0684d',
          backgroundColor: (ov.total_pnl_cents||0)>=0 ? 'rgba(51,201,181,0.1)' : 'rgba(240,104,77,0.1)',
          fill: true,
          tension: 0.1,
          pointRadius: labels.length > 100 ? 0 : 2,
        }]
      },
      options: {
        responsive: true,
        plugins: { legend: { display: false } },
        scales: {
          x: {
            title: { display: true, text: 'Window', color: '#8792a6' },
            ticks: { color: '#8792a6', maxTicksLimit: 12 },
            grid: { color: '#232a35' }
          },
          y: {
            title: { display: true, text: 'Cumulative P&L ($)', color: '#8792a6' },
            ticks: {
              color: '#8792a6',
              callback: function(v){ return '$' + Number(v).toFixed(2); }
            },
            grid: { color: '#232a35' }
          }
        }
      }
    });

    // Per series table with tooltips and execution vs oscillation clarity
    let stbl = '<table class="tbl"><thead><tr>'
      + '<th>Series</th>'
      + '<th>Windows</th>'
      + '<th title="Both legs filled & merged for profit. Note: Oscillating windows may not fill limit orders if price drifted rapidly before quotes rested or opposite leg never touched.">Pair Captured ℹ️</th>'
      + '<th title="One leg filled then adverse drift triggered safety stop exit before opposite leg filled.">Exits ℹ️</th>'
      + '<th>Total P&L ($)</th>'
      + '<th>Avg / Window ($)</th>'
      + '<th>Recovered</th>'
      + '<th title="Price excursion >= 2c in both directions vs 50c mid. Market oscillation does not guarantee limit order fills.">Oscillating ℹ️</th>'
      + '<th>Monotonic</th>'
      + '</tr></thead><tbody>';
    for(const [k,v] of Object.entries(data.per_series||{})){
      stbl+=`<tr><td style="font-weight:700">${esc(v.label)}</td><td class="mono" style="font-variant-numeric:tabular-nums">${v.windows}</td><td style="color:var(--up);font-weight:700;font-variant-numeric:tabular-nums">${(v.pair_rate*100).toFixed(1)}% (${v.pairs})</td><td style="color:var(--down);font-variant-numeric:tabular-nums">${(v.exit_rate*100).toFixed(1)}% (${v.exits})</td><td class="mono" style="font-weight:700;font-variant-numeric:tabular-nums;color:${v.total_pnl_cents>=0?'var(--up)':'var(--down)'}">${fmtUsd(v.total_pnl_cents,true)}</td><td class="mono" style="font-variant-numeric:tabular-nums">${fmtUsd(v.avg_pnl_cents,true)}</td><td class="mono" style="font-variant-numeric:tabular-nums;color:${(v.reentry_count||0)>0?'var(--up)':'var(--dim)'}">${(v.reentry_count||0)>0 ? v.reentry_count + ' (' + fmtUsd(v.reentry_pnl_cents||0,true) + ')' : '—'}</td><td class="mono" style="font-variant-numeric:tabular-nums">${v.oscillating}</td><td class="mono" style="font-variant-numeric:tabular-nums">${v.monotonic}</td></tr>`;
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
        if (t.series && t.label) seriesLabels.set(t.series, t.label);
      }
      let opts = '<option value="">All Series</option>';
      for (const [slug, label] of seriesLabels.entries()) {
        opts += `<option value="${esc(slug)}"${currentVal === slug ? ' selected' : ''}>${esc(label)}</option>`;
      }
      $('btLogSeriesFilter').innerHTML = opts;
    }

    renderBacktestTradesPage();
  } catch(err) {
    console.error('Error running backtest:', err);
  } finally {
    setBacktestLoadingState(false);
  }
}

// Client-side interactive pagination & filtering for Executed Windows Log
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

  let ttbl = '<table class="tbl"><thead><tr>'
    + '<th>Window</th>'
    + '<th>Series</th>'
    + '<th>Result</th>'
    + '<th>Entry Up</th>'
    + '<th>Entry Down</th>'
    + '<th>Exit Price</th>'
    + '<th>Exit Type</th>'
    + '<th>PnL / Window ($)</th>'
    + '<th>Delay / Partial</th>'
    + '</tr></thead><tbody>';

  if (pageTrades.length === 0) {
    ttbl += '<tr><td colspan="9" style="text-align:center;padding:18px;color:var(--dim)">No executed windows match the selected criteria.</td></tr>';
  } else {
    for (const t of pageTrades) {
      const pnlUsd = fmtUsd(t.pnl_cents, true);
      const resPill = t.both_filled
        ? pill('pill-osc', `PAIR CAPTURED ${pnlUsd}`)
        : t.exit_triggered
        ? pill('pill-mono', 'EXIT TRIGGERED')
        : pill('pill-flat', 'FLAT / UNRESOLVED');

      const entryUpStr = t.entry_up != null ? '$' + Number(t.entry_up).toFixed(3) : '—';
      const entryDnStr = t.entry_down != null ? '$' + Number(t.entry_down).toFixed(3) : '—';
      const exitPriceStr = t.exit_price != null ? '$' + Number(t.exit_price).toFixed(3) : '—';
      const exitTypeStr = t.exit_reason ? esc(t.exit_reason) : '—';

      const delayTag = t.is_partial
        ? `<span class="pill pill-mono" style="font-size:11px;color:var(--down)">Half (${t.start_delay_sec}s)</span>`
        : `<span class="mono" style="font-size:12px;color:var(--dim)">${t.start_delay_sec ? t.start_delay_sec + 's' : '0s'}</span>`;

      ttbl += `<tr>`
        + `<td class="mono" style="font-size:12px;font-variant-numeric:tabular-nums">${esc(t.slug.slice(-14))}</td>`
        + `<td style="font-weight:600">${esc(t.label || t.series)}</td>`
        + `<td>${resPill}</td>`
        + `<td class="mono" style="font-size:12px">${entryUpStr}</td>`
        + `<td class="mono" style="font-size:12px">${entryDnStr}</td>`
        + `<td class="mono" style="font-size:12px">${exitPriceStr}</td>`
        + `<td class="mono" style="font-size:12px;color:var(--dim)">${exitTypeStr}</td>`
        + `<td class="mono" style="font-size:12.5px;font-weight:700;font-variant-numeric:tabular-nums;color:${t.pnl_cents>=0?'var(--up)':'var(--down)'}">${pnlUsd}</td>`
        + `<td>${delayTag}</td>`
        + `</tr>`;
    }
  }
  ttbl += '</tbody></table>';
  $('btTradesTableWrap').innerHTML = ttbl;
}

function resetBtParams(){
  $('btOffset').value = "0.02";
  $('btQueue').value = "0";
  if ($('btPairCostEnabled')) {
    $('btPairCostEnabled').checked = false;
    togglePairCostInput();
  }
  $('btPairCost').value = "1.05";
  $('btExit5m').value = "0.05";
  $('btExit15m').value = "0.05";
  $('btExitBtc').value = "0.05";
  $('btExitSol').value = "0.05";
  $('btFillModel').value = "cross";
  $('btSize').value = "5";
  $('btGas').value = "0.00";
  if ($('btMaxStartDelay')) $('btMaxStartDelay').value = "0";
  if ($('btReentryBand')) $('btReentryBand').value = "0.015";
  if ($('btRequoteMin')) $('btRequoteMin').value = "300";
  if ($('btEntryDelay')) $('btEntryDelay').value = "0";
  if ($('btEntryBand')) $('btEntryBand').value = "0";
  if ($('btFileSelect')) $('btFileSelect').value = "";
  window.selectedBacktestFile = "";
  runBacktest();
}

// Winning config preset (issue #145): the EV-research-winning setup —
// offset 0.03, delay 60s, band 0.04, tape fills, pair cost 0.98, size 5,
// hold-to-settle (exits 0.49/0.50 = ex=none mirror). The remaining replay
// inputs are pinned to dashboard defaults (queue 0, gas 0, no partial
// filter, re-entry band 0.015, re-quote-min 300) so the button is a
// reproducible 1-click config, then auto-runs the backtest. Aborts without
// running if any required input is missing (no half-applied state).
function applyWinningConfig(){
  const required = ['btOffset','btQueue','btPairCost','btPairCostEnabled','btExit5m','btExit15m','btExitBtc','btExitSol','btFillModel','btSize','btGas','btMaxStartDelay','btReentryBand','btRequoteMin','btEntryDelay','btEntryBand'];
  for (const id of required) { if (!$(id)) return; }
  $('btOffset').value = "0.03";
  $('btQueue').value = "0";
  $('btEntryDelay').value = "60";
  $('btEntryBand').value = "0.04";
  $('btFillModel').value = "tape";
  $('btPairCost').value = "0.98";
  if ($('btPairCostEnabled') && !$('btPairCostEnabled').checked) {
    $('btPairCostEnabled').checked = true;
    togglePairCostInput();
  }
  $('btSize').value = "5";
  $('btExit5m').value = "0.49";
  $('btExit15m').value = "0.50";
  $('btExitBtc').value = "0.49";
  $('btExitSol').value = "0.49";
  $('btGas').value = "0.00";
  $('btMaxStartDelay').value = "0";
  $('btReentryBand').value = "0.015";
  $('btRequoteMin').value = "300";
  runBacktest();
}

// Statistical Summary Charts
function destroyChartInstance(canvasId){
  const existing = Chart.getChart(canvasId);
  if(existing) existing.destroy();
}

async function renderSummaryCharts(){
  const d=await (await fetch('/api/oscillation',{cache:'no-store'})).json();
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
      data:{
        labels:order,
        datasets:[
          {label:'oscillating',data:osc,backgroundColor:'#33c9b5'},
          {label:'monotonic',data:mono,backgroundColor:'#f0684d'}
        ]
      },
      options:{
        responsive:true,
        plugins:{legend:{position:'bottom',labels:{color:'#8792a6'}}},
        scales:{
          x:{ticks:{color:'#8792a6'},grid:{color:'#232a35'}},
          y:{ticks:{color:'#8792a6'},grid:{color:'#232a35'}}
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
      data:{labels:bLabels,datasets:[{label:'Windows',data:bCounts,backgroundColor:'#e8b84b'}]},
      options:{responsive:true,plugins:{legend:{display:false}},scales:{x:{ticks:{color:'#8792a6'}},y:{ticks:{color:'#8792a6'},grid:{color:'#232a35'}}}}
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
      data:{labels:sBuckets,datasets:[{data:sCounts,backgroundColor:['#33c9b5','#7b9bf7','#e8b84b','#f0684d','#535e70']}]},
      options:{responsive:true,plugins:{legend:{position:'bottom',labels:{color:'#8792a6'}}}}
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
      data:{labels:pBuckets,datasets:[{data:pCounts,backgroundColor:'#7b9bf7'}]},
      options:{responsive:true,plugins:{legend:{display:false}},scales:{x:{ticks:{color:'#8792a6'}},y:{ticks:{color:'#8792a6'},grid:{color:'#232a35'}}}}
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

async function loadManifest(){
  try{
    const res = await fetch('/api/ticks/manifest');
    const d = await res.json();

    const sel = $('btFileSelect');
    if(sel && d.files){
      const currentVal = sel.value || window.selectedBacktestFile;
      sel.innerHTML = '';
      const defOpt = document.createElement('option');
      defOpt.value = '';
      defOpt.textContent = 'All Files / 2,820 Windows (Default)';
      sel.appendChild(defOpt);

      for(const f of d.files){
        const opt = document.createElement('option');
        opt.value = f.name;
        const linesFormatted = (f.lines||0).toLocaleString();
        const estPrefix = f.lines_estimated ? '~' : '';
        opt.textContent = `${f.name} (${estPrefix}${linesFormatted} lines)`;
        sel.appendChild(opt);
      }
      if(currentVal && Array.from(sel.options).some(o => o.value === currentVal)){
        sel.value = currentVal;
      }
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
        const mb = ((a.total_bytes||0)/(1024*1024)).toFixed(1)+' MB';
        const lineFmt = (a.total_lines||0).toLocaleString();
        const linesVal = (a.total_lines_estimated ? '~' : '') + lineFmt;
        const winFmt = (a.total_windows||0).toLocaleString();
        const winVal = (a.windows_source === 'partial' ? '≥' : '') + winFmt;
        const tapeRate = m.tape_empty_rate !== undefined ? (m.tape_empty_rate * 100).toFixed(1) + '%' : '—';
        const tapeCrit = m.tape_empty_rate !== undefined && m.tape_empty_rate > 0.99;
        const tile = (label, val, color) => `
          <div style="flex:1;min-width:110px;background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:10px 12px;text-align:center">
            <div style="font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.04em">${label}</div>
            <div class="mono" style="font-size:15px;font-weight:700;color:${color||'var(--tx)'};margin-top:3px">${val}</div>
          </div>`;
        const tiles = document.createElement('div');
        tiles.style.cssText = 'display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px';
        tiles.innerHTML =
          tile('Files', a.total_files||0)
          + tile('Total Size', mb)
          + tile('Samples', linesVal, 'var(--up)')
          + tile('Windows', winVal, 'var(--gold)')
          + tile('Tape Entries', (a.tape_entries_total||0).toLocaleString())
          + tile('Tape Empty' + (m.day ? ' · ' + esc(m.day) : ''), tapeRate, tapeCrit ? 'var(--down)' : (m.tape_empty_rate !== undefined ? 'var(--up)' : 'var(--dim)'));
        aggWrap.appendChild(tiles);
      }
    }

    const tbl = document.createElement('table');
    tbl.className = 'tbl';
    const thead = document.createElement('tr');
    thead.innerHTML = '<th>File Name</th><th>Size</th><th>Lines / Samples</th><th>Last Modified</th><th>Actions</th>';
    tbl.appendChild(thead);

    if(!d.files || d.files.length === 0){
      const tr = document.createElement('tr');
      tr.innerHTML = '<td colspan="5" style="text-align:center;color:var(--faint);padding:18px">No files found in run/ticks/</td>';
      tbl.appendChild(tr);
    } else {
      let fileIdx = 0;
      for(const f of d.files){
        fileIdx++;
        const tr = document.createElement('tr');
        const mb = (f.bytes/(1024*1024)).toFixed(2)+' MB';
        const linesFormatted = (f.lines||0).toLocaleString();
        const linesHtml = f.lines_estimated
          ? `~${linesFormatted} <span style="color:var(--dim);font-size:10px">(est.)</span>`
          : linesFormatted;

        const tdName = document.createElement('td');
        tdName.className = 'mono';
        const btnName = document.createElement('button');
        btnName.type = 'button';
        btnName.style.cssText = 'background:transparent;border:none;padding:0;font:inherit;color:inherit;cursor:pointer;text-align:left;display:inline-flex;align-items:center;gap:2px';
        btnName.title = 'Toggle integrity report';
        btnName.setAttribute('aria-label', `Toggle integrity report for ${f.name}`);
        btnName.setAttribute('aria-expanded', 'false');
        btnName.innerHTML = `<span id="verify_arrow_${f.name}" style="display:inline-block;width:16px;color:var(--gold)" aria-hidden="true">▶</span>${esc(f.name)}`;
        btnName.addEventListener('click', () => {
          const expanded = btnName.getAttribute('aria-expanded') === 'true';
          btnName.setAttribute('aria-expanded', String(!expanded));
          toggleFileVerify(f.name);
        });
        tdName.appendChild(btnName);

        const tdSize = document.createElement('td');
        tdSize.className = 'mono';
        tdSize.textContent = mb;

        const tdLines = document.createElement('td');
        tdLines.className = 'mono';
        tdLines.innerHTML = linesHtml;

        const tdMtime = document.createElement('td');
        tdMtime.className = 'mono';
        tdMtime.textContent = new Date(f.mtime*1000).toLocaleString('en-US');

        const tdActions = document.createElement('td');
        tdActions.style.display = 'flex';
        tdActions.style.gap = '6px';
        tdActions.style.alignItems = 'center';

        const verifyBadge = document.createElement('span');
        verifyBadge.id = 'verify_badge_' + f.name;
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

        tdActions.appendChild(verifyBadge);
        tdActions.appendChild(btnRun);
        tdActions.appendChild(btnDel);

        tr.appendChild(tdName);
        tr.appendChild(tdSize);
        tr.appendChild(tdLines);
        tr.appendChild(tdMtime);
        tr.appendChild(tdActions);
        tbl.appendChild(tr);

        // Inline integrity accordion row: sits directly under the file row,
        // filled by verifyTickData() on load (no modal, no Verify button).
        const vRow = document.createElement('tr');
        vRow.id = 'verify_row_' + f.name;
        vRow.style.display = 'none';
        const vTd = document.createElement('td');
        vTd.colSpan = 5;
        vTd.id = 'verify_cell_' + f.name;
        vTd.style.cssText = 'background:var(--panel2);padding:12px 16px;border-top:1px solid var(--line)';
        vTd.innerHTML = '<div style="text-align:center;color:var(--faint);font-size:11px">Integrity report will load here…</div>';
        vRow.appendChild(vTd);
        tbl.appendChild(vRow);

        // Queue the integrity check for this file — runs sequentially after
        // the table is built so earlier files populate first (verify runs on
        // every load but is served from the fingerprint cache when unchanged).
        _verifyQueue.push(f.name);
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

function fileVerifyStatusBits(st){
  const s = st || 'UNKNOWN';
  const color = s === 'PASS' ? 'var(--up)' : s === 'WARN' ? 'var(--gold)' : 'var(--down)';
  const label = s === 'PASS' ? '✅ PASS' : s === 'WARN' ? '⚠️ WARN' : '❌ FAIL';
  return {color, label};
}

// Inline per-file integrity report — rendered under the file's row in the
// files table (accordion body). No modal.
function renderFileVerifyHtml(filename, d){
  const {color: statusColor, label: statusLabel} = fileVerifyStatusBits(d.status);

  let html = `
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
      <div>
        <div style="font:600 11px var(--disp);color:var(--dim);text-transform:uppercase;letter-spacing:.05em">Overall Integrity Status</div>
        <div style="font:700 17px var(--disp);color:${statusColor};margin-top:2px">${statusLabel}</div>
      </div>
      <div class="mono" style="font-size:12px;color:var(--dim)">🔍 Integrity Report · ${esc(filename)}</div>
    </div>

    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:10px">
      <div style="background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 10px;text-align:center">
        <div style="font:500 11px var(--body);color:var(--dim)">Valid Samples</div>
        <div class="mono" style="font-size:15px;font-weight:700;color:var(--up);margin-top:2px">${(d.valid_ticks||0).toLocaleString()}</div>
      </div>
      <div style="background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 10px;text-align:center">
        <div style="font:500 11px var(--body);color:var(--dim)">Corrupt Rows</div>
        <div class="mono" style="font-size:15px;font-weight:700;color:${(d.corrupt_lines||0)>0?'var(--down)':'var(--tx)'};margin-top:2px">${(d.corrupt_lines||0).toLocaleString()}</div>
      </div>
      <div style="background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 10px;text-align:center">
        <div style="font:500 11px var(--body);color:var(--dim)">Identified Windows</div>
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
    const durLabel = {300:'5m', 900:'15m'};
    const assetLabel = {btc:'BTC', eth:'ETH', bnb:'BNB', sol:'SOL', xrp:'XRP'};
    const marketName = (s) => {
      const m = /^([a-z]{3})-up-or-down-(\d+m)$/.exec(String(s||''));
      return m ? `${assetLabel[m[1]]||m[1].toUpperCase()} ${m[2]}` : String(s||'');
    };
    html += '<div style="font:700 12px var(--disp);color:var(--tx);letter-spacing:.05em;text-transform:uppercase;margin:12px 0 6px">Per-Market Breakdown</div>'
      + '<table style="width:100%;border-collapse:collapse;font-size:12px">'
      + '<thead><tr>'
      + '<th scope="col" style="text-align:left;font:600 11px var(--disp);color:var(--dim);text-transform:uppercase;letter-spacing:.05em;padding:4px 8px">Market</th>'
      + '<th scope="col" style="text-align:right;font:600 11px var(--disp);color:var(--dim);text-transform:uppercase;letter-spacing:.05em;padding:4px 8px">Windows</th>'
      + '<th scope="col" style="text-align:right;font:600 11px var(--disp);color:var(--dim);text-transform:uppercase;letter-spacing:.05em;padding:4px 8px">Trades</th>'
      + '<th scope="col" style="text-align:right;font:600 11px var(--disp);color:var(--dim);text-transform:uppercase;letter-spacing:.05em;padding:4px 8px">Avg / Window</th>'
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
  const cell = document.getElementById('verify_cell_' + filename);
  if(cell && !cell.dataset.loaded){
    cell.innerHTML = '<div style="text-align:center;padding:16px;color:var(--dim);font-size:13px">Running integrity check on ' + esc(filename) + '… <span class="spinner"></span></div>';
  }
  try{
    let d;
    for(;;){
      const res = await fetch('/api/ticks/verify?file=' + encodeURIComponent(filename) + (refresh ? '&refresh=1' : ''));
      d = await res.json();
      if(d.error){ throw new Error(d.error); }
      if(!d.pending){ break; }
      // Backend is scanning in the background — poll until the report lands.
      if(cell){
        const p = d.progress || {};
        const lines = (p.lines || 0).toLocaleString();
        const est = p.est_total || 0;
        const pct = est > 0 ? Math.min(99, Math.round((p.lines || 0) / est * 100)) : null;
        const elapsed = p.elapsed_sec != null ? p.elapsed_sec + 's' : '';
        const progHtml = est > 0
          ? `${lines} / ${est.toLocaleString()} lines · ${pct}% · ${elapsed}`
          : `${lines} lines processed · ${elapsed}`;
        cell.innerHTML = `<div style="text-align:center;padding:16px;color:var(--dim);font-size:13px">Scanning ${esc(filename)} in background… <span class="spinner"></span><div class="mono" style="margin-top:6px;font-size:12px;color:var(--faint);font-variant-numeric:tabular-nums">${progHtml}</div></div>`;
      }
      const badgeP = document.getElementById('verify_badge_' + filename);
      if(badgeP){ badgeP.textContent = '⏳'; badgeP.style.color = 'var(--dim)'; }
      await new Promise(r => setTimeout(r, 2000));
    }
    if(cell){
      cell.dataset.loaded = '1';
      cell.innerHTML = renderFileVerifyHtml(filename, d) + (d.stale
        ? '<div style="text-align:center;color:var(--faint);font-size:10px;margin-top:6px">Snapshot of a file still being written — rescanning in background.</div>'
        : '')
        + `<div style="text-align:center;margin-top:6px"><button type="button" class="btn" style="font-size:10px;padding:3px 10px" aria-label="Rescan integrity report for ${esc(filename)}" onclick="verifyTickData('${esc(filename)}', true)">↻ Rescan</button></div>`;
    }
    // Refresh the status badge on the file row.
    const badge = document.getElementById('verify_badge_' + filename);
    if(badge){
      const {color, label} = fileVerifyStatusBits(d.status);
      badge.textContent = label;
      badge.style.color = color;
    }
  }catch(e){
    if(cell){
      cell.innerHTML = `<div style="color:var(--down);padding:10px;text-align:center;font-size:12px">Error verifying ${esc(filename)}: ${esc(e.message)}</div>`;
    }
  }
}

// Expand/collapse the inline verify accordion under a file row.
function toggleFileVerify(filename){
  const row = document.getElementById('verify_row_' + filename);
  const arrow = document.getElementById('verify_arrow_' + filename);
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
    'cockpitExitReversal',
    'cockpitShares',
    'cockpitMode',
    'cockpitEntryTimeout',
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

function renderStreamTelemetry(data) {
  if (!data) return;
  const actualEl = $('telActualPrice');
  const spotEl = $('telSpotPrice');
  const diffEl = $('telPriceDiff');
  const driftEl = $('telSpotDrift');
  const clobEl = $('telClobMid');
  const latEl = $('telLeadLatency');
  const feedEl = $('telFeedStatus');

  if (actualEl) {
    const act = data.actual_price != null ? data.actual_price : (data.source === 'BINANCE_WS' ? data.price : null);
    if (act != null && !isNaN(Number(act))) {
      actualEl.textContent = '$' + Number(act).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
    } else if (data.actual_price !== undefined) {
      actualEl.textContent = '--';
    }
  }

  if (spotEl) {
    const rtds = data.rtds_price != null ? data.rtds_price : (data.source === 'RTDS' ? data.price : data.spot_price);
    if (rtds != null && !isNaN(Number(rtds))) {
      spotEl.textContent = '$' + Number(rtds).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
    } else if (data.spot_price !== undefined || data.rtds_price !== undefined) {
      spotEl.textContent = '--';
    }
  }

  if (diffEl) {
    if (data.price_diff != null && !isNaN(Number(data.price_diff))) {
      const d = Number(data.price_diff);
      const formattedD = d < 0 ? `-$${Math.abs(d).toFixed(2)}` : (d > 0 ? `+$${d.toFixed(2)}` : `$${d.toFixed(2)}`);
      let pctStr = '';
      if (data.price_diff_pct != null && !isNaN(Number(data.price_diff_pct))) {
        const pct = Number(data.price_diff_pct);
        pctStr = ` (${pct > 0 ? '+' : ''}${pct.toFixed(3)}%)`;
      }
      diffEl.textContent = `${formattedD}${pctStr}`;
      diffEl.style.color = d > 0 ? 'var(--up)' : d < 0 ? 'var(--down)' : 'var(--tx)';
    } else if (data.price_diff !== undefined) {
      diffEl.textContent = '--';
      diffEl.style.color = 'var(--tx)';
    }
  }

  if (driftEl) {
    if (data.spot_drift != null && !isNaN(Number(data.spot_drift))) {
      const d = Number(data.spot_drift);
      driftEl.textContent = (d >= 0 ? '+' : '') + (d * 100).toFixed(2) + '%';
      driftEl.style.color = d > 0 ? 'var(--up)' : d < 0 ? 'var(--down)' : 'var(--tx)';
    } else {
      driftEl.textContent = '--';
      driftEl.style.color = 'var(--tx)';
    }
  }

  if (clobEl) {
    if (data.clob_mid != null && !isNaN(Number(data.clob_mid))) {
      clobEl.textContent = (Number(data.clob_mid) * 100).toFixed(1) + '¢';
    } else {
      clobEl.textContent = '--';
    }
  }

  if (latEl) {
    if (data.latency_ms != null && !isNaN(Number(data.latency_ms)) && Number(data.latency_ms) <= 15000) {
      const ms = Number(data.latency_ms);
      latEl.textContent = ms.toFixed(0) + ' ms';
      latEl.style.color = ms < 500 ? 'var(--up)' : ms < 1500 ? 'var(--gold)' : 'var(--down)';
    } else {
      latEl.textContent = '--';
      latEl.style.color = 'var(--tx)';
    }
  }

  if (feedEl) {
    const isRunning = data.is_running !== false && (data.is_running || data.binance_ws_connected || data.rtds_connected || data.clob_ws_connected);
    const spot = !!(data.binance_ws_connected || data.rtds_connected);
    const clob = !!data.clob_ws_connected;
    if (!isRunning && !spot && !clob) {
      feedEl.textContent = 'IDLE';
      feedEl.className = 'tel-badge idle';
    } else if (spot && clob) {
      feedEl.textContent = 'CONNECTED';
      feedEl.className = 'tel-badge ok';
    } else if (spot || clob) {
      feedEl.textContent = 'DEGRADED';
      feedEl.className = 'tel-badge warn';
    } else {
      feedEl.textContent = 'DISCONNECTED';
      feedEl.className = 'tel-badge err';
    }
  }
}

async function fetchCockpitLatency() {
  try {
    const activeSlug = (cockpitState && cockpitState.markets && Object.keys(cockpitState.markets)[0]) || 'btc-up-or-down-5m';
    const res = await fetch('/api/live/latency?series=' + encodeURIComponent(activeSlug), { cache: 'no-store' });
    if (!res.ok) return;
    const data = await res.json();
    renderStreamTelemetry(data);
  } catch (e) {
    console.debug('Failed fetching live latency telemetry', e);
  }
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
  await fetchCockpitLatency();
  await fetchQueueTelemetry();
}

// Issue #139: queue-telemetry panel (tape vs tapeq evidence as it accumulates).
async function fetchQueueTelemetry() {
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

  // 1. Offset: 0.001 to 0.490
  const offsetEl = $('cockpitOffset');
  if (offsetEl) {
    const raw = offsetEl.value.trim();
    const val = parseFloat(raw);
    if (raw === '' || isNaN(val) || val < 0.001 || val > 0.490) {
      offsetEl.classList.add('input-invalid');
      allValid = false;
    } else {
      offsetEl.classList.remove('input-invalid');
    }
  }

  // 2. Exit Threshold: 0.001 to 0.500
  const exitEl = $('cockpitExit');
  if (exitEl) {
    const raw = exitEl.value.trim();
    const val = parseFloat(raw);
    if (raw === '' || isNaN(val) || val < 0.001 || val > 0.500) {
      exitEl.classList.add('input-invalid');
      allValid = false;
    } else {
      exitEl.classList.remove('input-invalid');
    }
  }

  // 2b. Exit Reversal: 0.001 to 0.500 (same range as exit threshold)
  const exitRevEl = $('cockpitExitReversal');
  if (exitRevEl) {
    const raw = exitRevEl.value.trim();
    const val = parseFloat(raw);
    if (raw === '' || isNaN(val) || val < 0.001 || val > 0.500) {
      exitRevEl.classList.add('input-invalid');
      allValid = false;
    } else {
      exitRevEl.classList.remove('input-invalid');
    }
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

  // 5. Entry Timeout: 1 to 100
  const timeoutEl = $('cockpitEntryTimeout');
  if (timeoutEl) {
    const raw = timeoutEl.value.trim();
    const val = parseFloat(raw);
    if (raw === '' || isNaN(val) || val < 1 || val > 100) {
      timeoutEl.classList.add('input-invalid');
      allValid = false;
    } else {
      timeoutEl.classList.remove('input-invalid');
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

  // Auto-convert whole numbers entered as cents (e.g. 2 -> 0.02, 5 -> 0.05)
  const offsetEl = $('cockpitOffset');
  if (offsetEl) {
    let ov = parseFloat(offsetEl.value);
    if (!isNaN(ov) && Number.isInteger(ov) && ov >= 1.0 && ov <= 49.0) {
      offsetEl.value = (ov / 100.0).toFixed(3).replace(/0+$/, '').replace(/\.$/, '');
    }
  }
  const exitEl = $('cockpitExit');
  if (exitEl) {
    let ev = parseFloat(exitEl.value);
    if (!isNaN(ev) && Number.isInteger(ev) && ev >= 1.0 && ev <= 50.0) {
      exitEl.value = (ev / 100.0).toFixed(3).replace(/0+$/, '').replace(/\.$/, '');
    }
  }
  const exitRevEl = $('cockpitExitReversal');
  if (exitRevEl) {
    let rv = parseFloat(exitRevEl.value);
    if (!isNaN(rv) && Number.isInteger(rv) && rv >= 1.0 && rv <= 50.0) {
      exitRevEl.value = (rv / 100.0).toFixed(3).replace(/0+$/, '').replace(/\.$/, '');
    }
  }

  if (!validateCockpitInputs()) {
    alert('Please correct the invalid parameters highlighted with a red border before applying.');
    return;
  }

  isApplyingCockpitConfig = true;
  const offset = parseFloat($('cockpitOffset').value) || 0.02;
  const exit_thresh = parseFloat($('cockpitExit').value) || 0.05;
  const exit_reversal = parseFloat($('cockpitExitReversal').value) || 0.02;
  const shares = parseInt($('cockpitShares').value, 10) || 5;
  const mode = $('cockpitMode').value || 'paper';
  const wallet = $('cockpitWallet').value.trim();
  const startBal = parseFloat($('cockpitStartBal').value) || 1000.0;
  const timeoutEl = $('cockpitEntryTimeout');
  const timeoutVal = timeoutEl ? parseFloat(timeoutEl.value) : 100;
  // The cockpit input is always a whole percentage (min=1, max=100), so divide
  // unconditionally. The old `timeoutVal > 1.0` guard left a typed 1 as 1.0,
  // silently turning a 1% timeout into a full-window timeout.
  const entry_timeout_pct = !isNaN(timeoutVal)
    ? Math.min(1.0, Math.max(0.01, timeoutVal / 100.0))
    : 1.0;
  const body = {
    offset,
    exit_thresh,
    exit_reversal,
    shares,
    mode,
    wallet_address: wallet,
    starting_balance: startBal,
    entry_timeout_pct,
  };
  // Issue #124: naked-leg risk knobs. Naked stop is entered like exit_thresh
  // (whole numbers normalized to decimals server-side); timeout as a whole
  // percentage normalized to a fraction server-side.
  const nakedEl = $('cockpitExitNaked');
  const nakedTimeoutEl = $('cockpitNakedTimeout');
  const pairableEl = $('cockpitReentryPairable');
  if (nakedEl && nakedEl.value) {
    body.exit_thresh_naked = parseFloat(nakedEl.value);
  }
  if (nakedTimeoutEl && nakedTimeoutEl.value !== '' && !isNaN(parseFloat(nakedTimeoutEl.value))) {
    body.naked_leg_timeout_pct = Math.min(100, Math.max(0, parseFloat(nakedTimeoutEl.value))) / 100.0;
  }
  if (pairableEl) {
    body.reentry_require_pairable = pairableEl.value === 'true';
  }
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

function renderCockpitUI(st) {
  if (!st) return;

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

  // Sync strategy parameter fields from engine state while running
  if (st.is_running) {
    if (st.params) {
      if ($('cockpitOffset') && st.params.offset != null) $('cockpitOffset').value = st.params.offset;
      if ($('cockpitExit') && st.params.exit_thresh != null) $('cockpitExit').value = st.params.exit_thresh;
      if ($('cockpitExitReversal') && st.params.exit_reversal != null) $('cockpitExitReversal').value = st.params.exit_reversal;
      if ($('cockpitShares') && st.params.shares != null) $('cockpitShares').value = st.params.shares;
      if ($('cockpitEntryTimeout') && st.params.entry_timeout_pct != null) $('cockpitEntryTimeout').value = Math.round(st.params.entry_timeout_pct * 100);
      if ($('cockpitExitNaked') && st.params.exit_thresh_naked != null) $('cockpitExitNaked').value = st.params.exit_thresh_naked;
      if ($('cockpitNakedTimeout') && st.params.naked_leg_timeout_pct != null) $('cockpitNakedTimeout').value = Math.round(st.params.naked_leg_timeout_pct * 100);
      if ($('cockpitReentryPairable') && st.params.reentry_require_pairable != null) $('cockpitReentryPairable').value = String(!!st.params.reentry_require_pairable);
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
      if ($('cockpitOffset') && st.params.offset != null) $('cockpitOffset').value = st.params.offset;
      if ($('cockpitExit') && st.params.exit_thresh != null) $('cockpitExit').value = st.params.exit_thresh;
      if ($('cockpitExitReversal') && st.params.exit_reversal != null) $('cockpitExitReversal').value = st.params.exit_reversal;
      if ($('cockpitShares') && st.params.shares != null) $('cockpitShares').value = st.params.shares;
      if ($('cockpitEntryTimeout') && st.params.entry_timeout_pct != null) $('cockpitEntryTimeout').value = Math.round(st.params.entry_timeout_pct * 100);
      if ($('cockpitExitNaked') && st.params.exit_thresh_naked != null) $('cockpitExitNaked').value = st.params.exit_thresh_naked;
      if ($('cockpitNakedTimeout') && st.params.naked_leg_timeout_pct != null) $('cockpitNakedTimeout').value = Math.round(st.params.naked_leg_timeout_pct * 100);
      if ($('cockpitReentryPairable') && st.params.reentry_require_pairable != null) $('cockpitReentryPairable').value = String(!!st.params.reentry_require_pairable);
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
    modePill.textContent = (st.mode || 'paper').toUpperCase() + ' TRADING';
    modePill.style.background = st.mode === 'live' ? 'rgba(240,104,77,0.15)' : 'rgba(51,201,181,0.15)';
    modePill.style.color = st.mode === 'live' ? 'var(--down)' : 'var(--up)';
    modePill.style.borderColor = st.mode === 'live' ? 'rgba(240,104,77,0.4)' : 'rgba(51,201,181,0.4)';
  }

  const streamPill = $('cockpitStreamPill');
  if (streamPill) {
    const sb = st.stream_bridge || {};
    if (sb.binance_ws_connected) {
      streamPill.textContent = '🟢 BINANCE WS: <1s';
      streamPill.className = 'pill pill-osc';
      streamPill.style.color = 'var(--up)';
    } else if (sb.rtds_connected || liveStreamConnected) {
      streamPill.textContent = '🟢 RTDS STREAM: 1s';
      streamPill.className = 'pill pill-osc';
      streamPill.style.color = 'var(--up)';
    } else {
      streamPill.textContent = '🟡 REST POLLING';
      streamPill.className = 'pill pill-flat';
      streamPill.style.color = 'var(--gold)';
    }
  }

  // Update Live Stream Telemetry card from engine state
  if (st && st.markets) {
    const mkKeys = Object.keys(st.markets);
    const targetSlug = mkKeys.find(k => k.startsWith('btc')) || mkKeys[0];
    if (targetSlug && st.markets[targetSlug]) {
      const m = st.markets[targetSlug];
      const sb = st.stream_bridge || {};
      const latencyVal = (m.spot_updated_ts && Math.abs(Date.now() - m.spot_updated_ts * 1000) <= 15000)
        ? Math.max(0, (Date.now() - m.spot_updated_ts * 1000))
        : null;
      renderStreamTelemetry({
        spot_price: m.spot_price,
        actual_price: m.actual_price,
        rtds_price: m.rtds_price,
        price_diff: m.price_diff,
        price_diff_pct: m.price_diff_pct,
        spot_drift: m.spot_drift,
        clob_mid: m.mid,
        latency_ms: latencyVal,
        is_running: sb.is_running,
        binance_ws_connected: sb.binance_ws_connected,
        rtds_connected: sb.rtds_connected || liveStreamConnected,
        clob_ws_connected: sb.clob_ws_connected,
      });
    }
  }

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

  // 3. Render Live Matrix Grid
  const gridEl = $('cockpitMarketGrid');
  const activeSeries = getActiveCockpitSeries();
  const activeBadge = $('cockpitActiveMarketsBadge');
  if (activeBadge) {
    activeBadge.textContent = `${activeSeries.length} ACTIVE MARKET${activeSeries.length === 1 ? '' : 'S'}`;
  }
  if (gridEl && st.markets) {
    let gridHtml = '';
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

      let posStr = 'FLAT';
      const actualUp = m.fill_price_up != null ? m.fill_price_up : (m.resting_up != null ? m.resting_up : (m.up_mid != null ? Math.max(0.01, +(m.up_mid - 0.02).toFixed(2)) : 0.48));
      const actualDown = m.fill_price_down != null ? m.fill_price_down : (m.resting_down != null ? m.resting_down : (m.down_mid != null ? Math.max(0.01, +(m.down_mid - 0.02).toFixed(2)) : 0.48));
      if (m.status === 'STOP_EXIT' || m.exit_taken) {
        posStr = 'FLAT (STOPPED OUT)';
      } else if (m.status === 'TIMEOUT_NO_FILL' || m.status === 'DRIFT_SKIPPED' || m.status === 'LATE_START_SKIPPED' || m.entry_cancelled_timeout) {
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
        || m.status === 'LATE_START_SKIPPED' || m.exit_taken || m.entry_cancelled_timeout;
      const bidsBoxDimCls = bidsCancelled ? ' mat-bids-cancelled' : '';
      // Human-readable cause shown inside the dimmed box (visible without
      // hovering) and mirrored as a native title tooltip on the box itself.
      let cancelReason = '';
      if (m.status === 'STOP_EXIT' || m.exit_taken) cancelReason = 'Bids cancelled — stop-loss exit';
      else if (m.status === 'STOP_EXIT_PENDING') cancelReason = 'Bids cancelling — stop-loss exit';
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
      } else if (m.status === 'PAIR_MERGED' || m.pair_captured) {
        bidsTextHtml = `Bids: <span style="color:var(--dim)">MERGED / COMPLETE</span>${fillsSub}`;
      } else if (!st.is_running) {
        bidsTextHtml = `Bids: <span style="color:var(--dim)">INACTIVE (BOT STOPPED)</span>`;
      } else {
        const restingUpDisp = m.resting_up != null ? m.resting_up : (m.up_mid != null ? Math.max(0.01, +(m.up_mid - 0.02).toFixed(2)) : 0.48);
        const restingDownDisp = m.resting_down != null ? m.resting_down : (m.down_mid != null ? Math.max(0.01, +(m.down_mid - 0.02).toFixed(2)) : 0.48);
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
              DN: ${fmtPrice(m.down_bid)} / ${fmtPrice(m.down_ask)}
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
              <span class="pill ${statusBadgeCls}" style="font-size:9px;padding:2px 6px">${statusText}</span>
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
  }

  // 4. Tab 1: Render Live Open Orders & Pre-Quotes Table (9 columns, grouped by pair)
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
      let ordHtml = '';
      for (const mktKey of Object.keys(groupedOrders)) {
        const grp = groupedOrders[mktKey];
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

  // 4b. Tab 2: Render Live Polymarket Open Positions Table (8 columns, grouped by pair)
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
      let posHtml = '';
      for (const mktKey of Object.keys(groupedPos)) {
        const grp = groupedPos[mktKey];
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
      let rowsHtml = '';
      for (const t of st.trades) {
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
    const lineColor = diffVal >= 0 ? '#33c9b5' : '#f0684d';

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
          <circle id="cockpitCrossDot" cx="0" cy="0" r="5" fill="#fff" stroke="${lineColor}" stroke-width="2.5"/>
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
          dotsSvg += `<circle cx="${xPos.toFixed(1)}" cy="${yPos.toFixed(1)}" r="4.5" fill="${s.color}" stroke="#fff" stroke-width="1.5"/>`;
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

function setupBacktestInputListeners(){
  const inputIds = [
    'btOffset', 'btQueue', 'btPairCost', 'btExit5m',
    'btExit15m', 'btExitBtc', 'btExitSol', 'btFillModel',
    'btSize', 'btGas', 'btFileSelect', 'btMaxStartDelay',
    'btReentryBand', 'btRequoteMin'
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

    if (el.tagName === 'SELECT') {
      el.addEventListener('change', () => {
        runBacktest();
      });
    }
  });
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
      const sp = $('cockpitStreamPill');
      if (sp) {
        const isBinance = cockpitState && cockpitState.stream_bridge && cockpitState.stream_bridge.binance_ws_connected;
        sp.textContent = isBinance ? '🟢 BINANCE WS: <1s' : '🟢 RTDS STREAM: 1s';
        sp.className = 'pill pill-osc';
        sp.style.color = 'var(--up)';
      }
      fetchCockpitLatency();
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

            const primarySlug = (targetSlugs && targetSlugs.length) ? targetSlugs[0] : (env.data.slug || Object.keys(cockpitState.markets)[0]);
            if (primarySlug && (primarySlug.startsWith('btc') || primarySlug === Object.keys(cockpitState.markets)[0])) {
              const pm = cockpitState.markets[primarySlug] || {};
              renderStreamTelemetry({
                actual_price: pm.actual_price != null ? pm.actual_price : env.data.actual_price,
                rtds_price: pm.rtds_price != null ? pm.rtds_price : env.data.rtds_price,
                price_diff: pm.price_diff != null ? pm.price_diff : env.data.price_diff,
                price_diff_pct: pm.price_diff_pct != null ? pm.price_diff_pct : env.data.price_diff_pct,
                spot_price: pm.spot_price != null ? pm.spot_price : env.data.price,
                spot_drift: pm.spot_drift,
                clob_mid: pm.mid,
              });
            }
          }
        }
      } catch (err) {
        console.debug('SSE parse error', err);
      }
    };
    liveEventSource.onerror = () => {
      liveStreamConnected = false;
      const sp = $('cockpitStreamPill');
      if (sp) {
        sp.textContent = '🟡 REST POLLING';
        sp.className = 'pill pill-flat';
        sp.style.color = 'var(--gold)';
      }
      fetchCockpitLatency();
      fetchCockpitState();
      ensureCockpitPolling();
    };
  } catch (e) {
    liveStreamConnected = false;
    ensureCockpitPolling();
  }
}

setupBacktestInputListeners();
switchOtTab(activeOtTab);
initSidebarState();

// Initialize real-time streams and polls
fetchCockpitState();
initLiveCockpitStream();
ensureCockpitPolling();
tick();
setInterval(tick, 3000);
loadManifest();   // tick files tab: load file list + run integrity verify on every dashboard load
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
    """Serve the complete unified 4-tab SPA interface."""
    return HTMLResponse(FULL_APP_HTML, headers={"Cache-Control": "no-cache"})


