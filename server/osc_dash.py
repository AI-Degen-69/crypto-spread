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
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.gzip import GZipMiddleware

from strategy.live_trader import get_live_trader_engine, fetch_polymarket_account_value
from strategy.streaming import DashboardEnvelope
from sse_starlette.sse import EventSourceResponse

ROOT = Path(__file__).resolve().parent.parent
RUN = ROOT / "run"
TICKS_DIR = RUN / "ticks"
RUN.mkdir(parents=True, exist_ok=True)
TICKS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Crypto Spread Lab")
app.add_middleware(GZipMiddleware, minimum_size=1000)

# In-memory collector process handle for UI controls
_collector_proc: subprocess.Popen | None = None
MAX_TEST_ORDER_SHARES = 10.0


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
        if p.port is not None and p.port not in (8802, 8000, 80, 443):
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
            },
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
        }
    )

    trades_sample = []
    for w in per_window:
        win_pnl = w.pnl_cents * size
        a = per_series_raw[w.series]
        a["windows"] += 1
        if w.pair_captured:
            a["pairs"] += 1
        if w.exit_taken:
            a["exits"] += 1
        if w.class_label == "oscillating":
            a["oscillating"] += 1
        elif w.class_label == "monotonic":
            a["monotonic"] += 1
        elif w.class_label == "flat":
            a["flat"] += 1
        a["total_pnl_cents"] += win_pnl

        if len(trades_sample) < 50:
            exit_info = f"exit_{w.exit_side}" if w.exit_taken else ("pair_merged" if w.pair_captured else "-")
            trades_sample.append({
                "slug": w.slug,
                "label": series_label_map.get(w.series, w.series),
                "series": w.series,
                "both_filled": w.pair_captured,
                "exit_triggered": w.exit_taken,
                "up_filled": w.filled_up,
                "down_filled": w.filled_down,
                "pnl_cents": round(win_pnl, 2),
                "exit_reason": exit_info,
                "start_delay_sec": w.start_delay_sec,
                "is_partial": w.is_partial,
            })

    total_windows = len(per_window)
    total_pairs = sum(a["pairs"] for a in per_series_raw.values())
    total_exits = sum(a["exits"] for a in per_series_raw.values())
    total_pnl = sum(a["total_pnl_cents"] for a in per_series_raw.values())

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
        }

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
        },
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

    return {
        "running": running,
        "pid": _collector_proc.pid if running else None,
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


@app.get("/api/live/stream")
async def api_live_stream(request: Request):
    """Real-time SSE stream broadcasting versioned DashboardEnvelope events."""
    _verify_safe_origin(request)
    engine = get_live_trader_engine()
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
                    msg = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield {"event": "message", "data": msg}
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
        engine.stop()
    elif action == "restart":
        engine.restart()
    elif action == "reset_pnl":
        engine.reset_pnl()
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
    shares: Optional[int] = Field(default=None, ge=1, le=10000)
    mode: Optional[str] = Field(default=None, pattern="^(paper|live)$")
    wallet_address: Optional[str] = None
    starting_balance: Optional[float] = Field(default=None, ge=0.0)
    selected_markets: Optional[list[str]] = None
    tokens: Optional[list[str]] = None
    durations: Optional[list[int]] = None


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
    ok = engine.cancel_live_order(order_id)
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


@app.get("/api/ticks/verify")
async def api_ticks_verify(
    request: Request,
    file: str | None = None,
    max_gap: float = 6.0,
    max_start_delay: float = 5.0,
):
    """Verify data integrity and quality of tick file(s) in run/ticks/."""
    _verify_safe_origin(request)
    from scripts.verify_tick_data import verify_tick_file, verify_ticks_dir

    if file:
        if "/" in file or "\\" in file or ".." in file:
            return JSONResponse(status_code=400, content={"error": "invalid file param"})
        target = (TICKS_DIR / file).resolve()
        try:
            target.relative_to(TICKS_DIR.resolve())
        except ValueError:
            return JSONResponse(status_code=400, content={"error": "invalid file path"})
        if not target.exists() or not target.is_file():
            return JSONResponse(status_code=404, content={"error": f"file not found: {file}"})
        rep = await asyncio.to_thread(
            verify_tick_file,
            target,
            max_gap_sec=max_gap,
            max_start_delay=max_start_delay,
        )
    else:
        rep = await asyncio.to_thread(
            verify_ticks_dir,
            TICKS_DIR,
            max_gap_sec=max_gap,
            max_start_delay=max_start_delay,
        )

    return rep


# --- Front-end SPA (Complete Hebrew RTL Studio: Live / Backtest Lab / Statistical Analysis / Tick Data Manager) ---

FULL_APP_HTML = r"""<!doctype html><html lang="he" dir="rtl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Crypto Spread — 5m/15m SPREAD-2 Engine & Lab</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
:root{--bg:#0a0d12;--panel:#12161d;--panel2:#171c24;--line:#232a35;--line-hi:#364152;--tx:#e7ebf3;--dim:#8792a6;--faint:#535e70;--up:#33c9b5;--upS:#12302c;--down:#f0684d;--downS:#311b18;--gold:#e8b84b;--proj:#7b9bf7;--disp:'Space Grotesk',system-ui;--mono:'IBM Plex Mono',monospace;--body:'IBM Plex Sans',system-ui}
*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--tx);font:13px/1.5 var(--body);-webkit-font-smoothing:antialiased}
a{color:var(--proj);text-decoration:none} a:hover{text-decoration:underline}
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
.tbl th{font:700 11px var(--disp);letter-spacing:.06em;text-transform:uppercase;color:var(--faint);text-align:right;padding:8px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
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
.form-group label{font:600 11px var(--disp);color:var(--dim);letter-spacing:.04em}
.form-group input, .form-group select{background:var(--panel2);color:var(--tx);border:1px solid var(--line);border-radius:8px;padding:7px 10px;font:500 13px var(--mono)}
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
.ot-pane{display:none}
.ot-pane.active{display:block}
.ot-pair-lead{border-top:1px solid var(--line-hi)}
.ot-tag{font:700 9px var(--disp);letter-spacing:.04em;padding:2px 6px;border-radius:4px;white-space:nowrap;display:inline-block}
.ot-tag-up{background:rgba(51,201,181,0.15);color:var(--up);border:1px solid rgba(51,201,181,0.3)}
.ot-tag-down{background:rgba(240,104,77,0.15);color:var(--down);border:1px solid rgba(240,104,77,0.3)}
.ot-tag-paired{background:rgba(51,201,181,0.12);color:var(--up);border:1px solid rgba(51,201,181,0.25)}
.ot-tag-partial{background:rgba(235,178,74,0.12);color:var(--gold);border:1px solid rgba(235,178,74,0.25)}
.ot-tag-unpaired{background:rgba(120,135,155,0.12);color:var(--dim);border:1px solid rgba(120,135,155,0.25)}
</style></head><body>
<div class="hdr" id="app-hdr">
  <h1><span>◆</span> Crypto Spread <span>5m/15m Engine</span></h1>
  <span class="tag">SPREAD-2 · POLYMARKET CLOB</span>
  <div class="nav-tabs" id="main-nav">
    <button class="tab-btn active" onclick="switchTab('cockpit')" id="tab-btn-cockpit">⚡ Live Trading Cockpit</button>
    <button class="tab-btn" onclick="switchTab('live')" id="tab-btn-live">📡 Live Books & Queue</button>
    <button class="tab-btn" onclick="switchTab('backtest')" id="tab-btn-backtest">⚡ Backtest Sweeper</button>
    <button class="tab-btn" onclick="switchTab('summary')" id="tab-btn-summary">📊 Stats Summary</button>
    <button class="tab-btn" onclick="switchTab('ticks')" id="tab-btn-ticks">💾 Tick Files</button>
  </div>
  <span style="flex:1"></span>
  <div style="display:flex;align-items:center;gap:8px">
    <span id="collectorBadge" class="mono" style="font-size:11px;padding:3px 8px;border-radius:6px;background:var(--panel2);border:1px solid var(--line)">קולקטור: טוען...</span>
    <span id="tapeBadge" class="mono" style="font-size:11px;padding:3px 8px;border-radius:6px;background:var(--panel2);border:1px solid var(--line)">Tape: טוען...</span>
    <button class="btn" id="btnToggleCollector" onclick="toggleCollector()">הפעל איסוף רציף (1s)</button>
    <button class="btn" onclick="pollOnce()">דגום עכשיו (Once)</button>
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
      <h3>⚡ הגדרות פרמטרים לבקטסט (Backtest Parameters) <span class="mono" id="btHash" style="font-size:11px;color:var(--dim)"></span></h3>
      <div class="form-grid" style="margin-top:12px">
        <div class="form-group">
          <label>קובץ דגימות לבדיקה (Tick File)</label>
          <select id="btFileSelect">
            <option value="">כל הקבצים / 2,820 חלונות (ברירת מחדל)</option>
          </select>
        </div>
        <div class="form-group">
          <label>Offset מ-Mid (ספרד $0.02 = 0.02)</label>
          <input type="number" step="0.005" id="btOffset" value="0.02">
        </div>
        <div class="form-group">
          <label>עומק תור (Queue ahead @ rest - 0 = ללא סינון)</label>
          <input type="number" step="5" id="btQueue" value="0">
        </div>
        <div class="form-group">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <label for="btPairCost">עלות מקסימלית לזוג (Pair Cost)</label>
            <label class="toggle-wrap" title="הפעל או כבה סינון לפי עלות מקסימלית לזוג">
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
          <label>רף יציאה 5m כללי (Exit Default)</label>
          <input type="number" step="0.01" id="btExit5m" value="0.05">
        </div>
        <div class="form-group">
          <label>רף יציאה 15m כללי (Exit Default)</label>
          <input type="number" step="0.01" id="btExit15m" value="0.05">
        </div>
        <div class="form-group">
          <label>רף יציאה ייעודי BTC 5m</label>
          <input type="number" step="0.01" id="btExitBtc" value="0.05">
        </div>
        <div class="form-group">
          <label>רף יציאה ייעודי SOL 5m</label>
          <input type="number" step="0.01" id="btExitSol" value="0.05">
        </div>
        <div class="form-group">
          <label>מודל מילוי (Fill Model)</label>
          <select id="btFillModel">
            <option value="cross" selected>Cross (חצייה מלאה ≤47¢ — מובטח)</option>
            <option value="tape">Tape (שמרני - עסקאות בפועל)</option>
            <option value="book">Book (אופטימי - חציית Ask)</option>
            <option value="both">Both</option>
          </select>
        </div>
        <div class="form-group">
          <label>גודל פוזיציה למניות (Shares - מינימום 5)</label>
          <input type="number" min="5" step="1" id="btSize" value="5">
        </div>
        <div class="form-group">
          <label>עלות גז מרג' בדולרים (Gas USD)</label>
          <input type="number" step="0.01" id="btGas" value="0.00">
        </div>
        <div class="form-group">
          <label>סינון חלונות חלקיים (Partial Windows)</label>
          <select id="btMaxStartDelay">
            <option value="0" selected>הכל (ללא סינון)</option>
            <option value="5.0">חלונות מלאים בלבד (≤5s)</option>
            <option value="2.0">הדוק במיוחד (≤2s)</option>
          </select>
        </div>
      </div>
      <div style="margin-top:14px;display:flex;gap:8px">
        <button class="btn btn-primary" id="btnRunSweep" onclick="runBacktest()"><span id="btnRunSweepIcon">▶</span> <span id="btnRunSweepText">הרץ סימולציה (Run Sweep)</span></button>
        <button class="btn" id="btnResetParams" onclick="resetBtParams()">איפוס לברירת מחדל</button>
      </div>
    </div>

    <div class="card" id="btOverallCard">
      <h3>📈 תוצאות בקטסט כוללות (Overall Execution)</h3>
      <div class="kpi" id="btKpiRow">
        <div class="box"><div class="lbl">רווח/הפסד כולל</div><div class="val" id="btTotalPnl" style="color:var(--up)">+$0.00</div><div class="sub" id="btAvgPnl">+$0.00 לחלון</div></div>
        <div class="box"><div class="lbl">אחוז לכידת זוג (Pair Rate)</div><div class="val" id="btPairRate">0.0%</div><div class="sub" id="btPairsCount">0 זוגות</div></div>
        <div class="box"><div class="lbl">אחוז הפעלת יציאה (Exit Rate)</div><div class="val" id="btExitRate" style="color:var(--down)">0.0%</div><div class="sub" id="btExitsCount">0 יציאות</div></div>
        <div class="box"><div class="lbl">Max Drawdown</div><div class="val" id="btMaxDd" style="color:var(--gold)">-$0.00</div><div class="sub">ירידה מרבית</div></div>
        <div class="box"><div class="lbl">Win Rate</div><div class="val" id="btWinRate">0.0%</div><div class="sub">עסקאות ברווח</div></div>
      </div>
      <div style="background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:12px;margin-top:12px">
        <h4 style="margin:0 0 6px;font:700 11px var(--disp);color:var(--faint)">עקומת PnL מצטברת (Cumulative Equity Curve)</h4>
        <canvas id="chartEquity" height="140"></canvas>
      </div>
    </div>

    <div class="card">
      <h3>📊 ביצועים לפי סדרה (Per-Series Performance)</h3>
      <div id="btSeriesTableWrap"></div>
    </div>

    <div class="card">
      <h3>📝 יומן עסקאות חלונות (Sample Executed Windows)</h3>
      <div id="btTradesTableWrap"></div>
    </div>
  </div>

  <!-- TAB 3: STATISTICAL ANALYSIS & DISTRIBUTIONS -->
  <div id="tab-summary" class="tab-content">
    <div class="hero" style="display:grid;grid-template-columns:1.2fr .8fr;gap:12px;margin-bottom:12px">
      <div class="card" style="border-top:2px solid var(--up)">
        <h3>מסקנת המחקר — SPREAD-2</h3>
        <div style="font:700 24px var(--mono);color:var(--up);margin:4px 0">74% מהחלונות תנודתיים (Oscillating)</div>
        <div style="font-size:12.5px;color:var(--dim);line-height:1.6">
          מתוך 2,820+ חלונות אמיתיים שנמדדו ב-5m ו-15m: ב-5m <b>73% oscillating</b> — שני הצדדים ב-$0.48 ($0.96 לזוג) נתפסים ומתמזגים ל-$0.04 רווח למניה. ב-15m <b>80% oscillating</b>.
        </div>
      </div>
      <div class="card" style="border-top:2px solid var(--gold)">
        <h3>המלצות רף יציאה</h3>
        <div style="font-size:12px;color:var(--dim)">הדוק = יציאה מוקדמת. BTC הכי מונוטוני:</div>
        <div class="mono" style="font-size:12px;margin-top:8px;display:flex;flex-direction:column;gap:4px">
          <div><b style="color:var(--down)">BTC 5m:</b> רף +$0.09 (יציאה ב-$0.59 UP)</div>
          <div><b style="color:var(--gold)">SOL 5m:</b> רף +$0.11 (יציאה ב-$0.61)</div>
          <div><b style="color:var(--up)">ETH/BNB/XRP 5m:</b> רף +$0.12 (יציאה ב-$0.62)</div>
          <div><b>15m כללי:</b> רף +$0.13</div>
        </div>
      </div>
    </div>
    <div class="grid">
      <div class="card"><h3>1. אחוז תנודתיות לפי נכס</h3><canvas id="cPerAsset" height="220"></canvas></div>
      <div class="card"><h3>2. התפלגות טווח תנועה (Max Excursion)</h3><canvas id="cHist" height="220"></canvas></div>
    </div>
    <div class="grid" style="margin-top:12px">
      <div class="card"><h3>3. סטיית פתיחה מ-50¢</h3><canvas id="cStart" height="200"></canvas></div>
      <div class="card"><h3>4. התפלגות Touch Pair</h3><canvas id="cPair" height="200"></canvas></div>
    </div>
  </div>

  <!-- TAB 4: TICKS FILE MANAGER & INGESTION -->
  <div id="tab-ticks" class="tab-content">
    <div class="card" style="border-top:2px solid var(--proj)">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
        <h3 style="margin:0">💾 קובצי Ticks בשרת (JSONL Repository)</h3>
        <div style="display:flex;gap:8px">
          <button class="btn" style="font-size:11px;padding:4px 10px;background:rgba(51,201,181,0.12);color:var(--up);border-color:rgba(51,201,181,0.3)" onclick="verifyTickData()">🔍 בדיקת תקינות מלאה (Verify All)</button>
          <button class="btn" style="font-size:11px;padding:4px 10px" onclick="loadManifest()">🔄 רענן רשימה</button>
        </div>
      </div>
      <div style="font-size:12px;color:var(--dim);margin-bottom:12px">הקולקטור כותב נתוני עומק וספר פקודות מלאים ל-<code>run/ticks/ticks_YYYY-MM-DD.jsonl</code>. ניתן להעלות קבצים נוספים לניתוח.</div>
      <div id="manifestNotice" style="display:none;padding:8px 12px;border-radius:6px;margin-bottom:10px;font-size:12px;font-weight:600"></div>
      <div id="manifestTableWrap">טוען קבצים...</div>
    </div>

    <!-- Integrity Verification Results Modal -->
    <div id="verifyModalOverlay" style="display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.75);z-index:9999;align-items:center;justify-content:center">
      <div style="background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:22px 24px;max-width:680px;width:95%;max-height:85vh;overflow-y:auto;box-shadow:0 12px 36px rgba(0,0,0,0.7)">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
          <h3 style="margin:0;font-size:16px;display:flex;align-items:center;gap:8px"><span>🔍</span> דוח תקינות נתונים (Tick Integrity Report)</h3>
          <button class="btn" style="padding:2px 8px;font-size:11px" onclick="closeVerifyModal()">✖ סגור</button>
        </div>
        <div id="verifyModalContent">טוען נתונים ומבצע בדיקה...</div>
      </div>
    </div>

    <!-- Custom Delete Confirmation Modal -->
    <div id="deleteModalOverlay" style="display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.7);z-index:9999;align-items:center;justify-content:center">
      <div style="background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:22px 24px;max-width:420px;width:90%;box-shadow:0 12px 36px rgba(0,0,0,0.6);text-align:center">
        <div style="font-size:32px;margin-bottom:8px">🗑️</div>
        <h3 style="margin:0 0 8px;font-size:16px;color:var(--tx)">אישור מחיקת קובץ</h3>
        <p style="margin:0 0 16px;font-size:13px;color:var(--dim);line-height:1.5">האם אתה בטוח שברצונך למחוק לצמיתות את הקובץ:<br><span id="deleteFileNameTarget" class="mono" style="color:var(--down);font-weight:700;word-break:break-all"></span>?</p>
        <div style="display:flex;gap:10px;justify-content:center">
          <button class="btn" style="padding:6px 16px" onclick="closeDeleteModal()">ביטול</button>
          <button id="confirmDeleteBtn" class="btn btn-danger" style="padding:6px 16px;font-weight:700" onclick="executeDeleteFile()">כן, מחק קובץ</button>
        </div>
      </div>
    </div>

    <div class="card">
      <h3>📤 העלאת קובץ JSONL (Streaming Ingestion)</h3>
      <div id="dropZone" style="border:2px dashed var(--line);border-radius:10px;padding:28px 20px;text-align:center;background:var(--panel2);transition:all 0.2s"
           ondragover="event.preventDefault();this.style.borderColor='var(--up)';this.style.background='rgba(51,201,181,0.06)'"
           ondragleave="this.style.borderColor='var(--line)';this.style.background='var(--panel2)'"
           ondrop="handleFileDrop(event)">
        <p style="margin:0 0 6px;font-size:14px;font-weight:600">גרור לכאן קובץ <code>.jsonl</code> או לחץ לבחירה</p>
        <p style="margin:0 0 14px;font-size:12px;color:var(--dim)">תומך בהעלאת קבצי ענק (10MB–1GB+) בהזרמה ישירה ללא עומס על הזיכרון</p>
        <input type="file" id="fileInput" accept=".jsonl,.txt" style="display:none" onchange="handleFileSelect(event)">
        <button class="btn btn-primary" onclick="document.getElementById('fileInput').click()">📁 בחר קובץ מהמחשב</button>
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
          <button id="btnSyncRealRun" class="btn" style="font-size:13px;padding:7px 14px;background:rgba(51,201,181,0.2);border-color:var(--up);color:var(--up);font-weight:700" onclick="syncRealRunTrades()">📥 סנכרן ריצה אמיתית (Polymarket)</button>
          <button class="btn btn-danger" style="font-size:13px;padding:7px 14px" onclick="resetCockpitPnL()">🗑 RESET P&L</button>
          <button id="btnPanicCancel" class="btn btn-danger" style="font-size:13px;padding:7px 14px;font-weight:700;background:rgba(240,104,77,0.3);border-color:var(--down)" onclick="panicCancelAllOrders()">🚨 PANIC CANCEL ALL</button>
        </div>
      </div>

      <!-- Config Inputs -->
      <div class="form-grid" style="margin-top:10px">
        <div class="form-group">
          <label>Spread Offset (Rest @ 0.50 - offset)</label>
          <input type="number" step="0.005" id="cockpitOffset" value="0.02">
        </div>
        <div class="form-group">
          <label>Exit Stop Loss Threshold ($)</label>
          <input type="number" step="0.01" id="cockpitExit" value="0.05">
        </div>
        <div class="form-group">
          <label>Share Size (per leg)</label>
          <input type="number" min="1" step="1" id="cockpitShares" value="5">
        </div>
        <div class="form-group">
          <label>Execution Mode</label>
          <select id="cockpitMode" onchange="onCockpitModeChange()">
            <option value="paper" selected>Paper Simulation (Live Book)</option>
            <option value="live">Live Polymarket Orders</option>
          </select>
        </div>
        <div class="form-group">
          <label id="lblCockpitWallet">Polymarket Wallet Address (Optional)</label>
          <input type="text" id="cockpitWallet" placeholder="0x... (Fetches Live Balance)" onchange="if ($('cockpitMode').value === 'live') onCockpitModeChange()">
        </div>
        <div class="form-group">
          <label id="lblCockpitStartBal">Starting Portfolio Balance ($)</label>
          <input type="number" step="10" id="cockpitStartBal" value="1000.00">
        </div>
        <div class="form-group" style="justify-content:flex-end">
          <button class="btn" style="background:rgba(51,201,181,0.15);border-color:var(--up);color:var(--up);font-weight:700;height:35px" onclick="applyCockpitConfig()">💾 APPLY PARAMETERS</button>
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
    </div>
  </div>
</div>

<script>
const $=s=>document.getElementById(s);
const esc=s=>String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
const pct=(a,b)=> b?Math.round(a/b*100):0;
const hms=s=>{s=Math.max(0,Math.floor(s));const h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=s%60;return h?`${h}h ${String(m).padStart(2,'0')}m`:`${m}m ${String(x).padStart(2,'0')}s`;};
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
function clsPill(c){return c==='oscillating'?pill('pill-osc','oscillating תנודתי'):c==='monotonic'?pill('pill-mono','monotonic חד-כיווני'):c==='flat'?pill('pill-flat','flat שטוח'):pill('pill-flat',esc(c));}

function switchTab(name){
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
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
async function refreshCollectorStatus(){
  try{
    const res = await fetch('/api/collector/status');
    const st = await res.json();
    isCollectorActive = st.running;
    $('collectorBadge').textContent = `קולקטור: ${st.running ? '🟢 פועל (1s)' : '⚪ מושהה'} · ${(st.total_ticks_collected||0).toLocaleString()} דגימות היום`;
    $('collectorBadge').style.color = st.running ? 'var(--up)' : 'var(--dim)';
    $('btnToggleCollector').textContent = st.running ? 'עצור איסוף' : 'הפעל איסוף רציף (1s)';
    $('btnToggleCollector').className = st.running ? 'btn btn-danger' : 'btn';

    const tb = $('tapeBadge');
    if(tb){
      if(st.tape_empty_rate !== null && st.tape_empty_rate !== undefined){
        const pctStr = (st.tape_empty_rate * 100).toFixed(1) + '%';
        if(st.tape_alert){
          tb.textContent = `⚠️ Tape שקט (${pctStr} ריק)`;
          tb.style.color = 'var(--down)';
          tb.style.borderColor = 'rgba(240,104,77,0.5)';
          tb.style.background = 'rgba(240,104,77,0.18)';
        } else {
          tb.textContent = `Tape ריק: ${pctStr} (${(st.tape_entries_total||0).toLocaleString()} עסקאות)`;
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
  await fetch(endpoint, {method:'POST'});
  refreshCollectorStatus();
}

async function pollOnce(){
  $('collectorBadge').textContent = 'דוגם נתונים כעת...';
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
      return {goal,n,any2,mono,osc,pctGoal,remain,label:dur===300?'5 דקות (300s)':'15 דקות (900s)', short:dur===300?'5m':'15m'};
    };
    const g5=fmt(300), g15=fmt(900);
    const gt=g.total||{n:0,any_2c:0,monotonic:0,oscillating:0};
    const bar=(x)=>`<div class="card" style="flex:1;min-width:280px;background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:12px"><div style="font:700 11px var(--disp);letter-spacing:.07em;color:var(--faint)">🎯 ${x.short} — ${x.label}</div><div class="mono" style="font-size:18px;font-weight:700;margin:6px 0">${x.goal} <span style="font-size:12px;color:var(--dim)">goal</span> / ${x.n} <span style="font-size:12px;color:var(--up)">passed</span> / ${x.any2} <span style="font-size:12px;color:var(--gold)">±$0.02</span> / ${x.mono} <span style="font-size:12px;color:var(--down)">mono</span></div><div style="display:flex;gap:6px;align-items:center"><div class="bar" style="flex:1;height:8px"><div class="fill ${x.pctGoal>=100?'up':x.pctGoal>=70?'gold':'warn'}" style="width:${x.pctGoal}%"></div></div><span class="mono" style="font-size:11px;color:var(--dim)">${x.pctGoal}%</span></div><div class="mono" style="font-size:10px;color:var(--dim);margin-top:4px">oscillating ${x.osc} · flat ${g[String(x.short==='5m'?300:900)]?.flat||0} · נותר ${x.remain} ליעד</div><div style="margin-top:6px;display:flex;gap:6px;align-items:center"><span class="mono" style="font-size:10px;color:var(--dim)">יעד:</span><input id="goalIn${x.short}" type="number" min="1" step="10" value="${x.goal}" style="width:90px;background:var(--bg);color:var(--tx);border:1px solid var(--line);border-radius:6px;padding:4px 6px;font:500 12px var(--mono)"><button onclick="(function(){const v=parseInt(document.getElementById('goalIn${x.short}').value,10);if(v>0){localStorage.setItem('goal_${x.short==='5m'?300:900}',v);tick();}})()" style="background:var(--panel);color:var(--tx);border:1px solid var(--line);border-radius:6px;padding:4px 10px;font:600 11px var(--disp);cursor:pointer">שמור</button></div></div>`;
    const tot=`<div class="card" style="flex:0 0 180px;min-width:160px;background:var(--panel);border:1px dashed var(--line);border-radius:10px;padding:12px;text-align:center"><div style="font:700 11px var(--disp);letter-spacing:.07em;color:var(--faint)">סה״כ</div><div class="mono" style="font-size:16px;font-weight:700;margin-top:4px">${gt.n} חלונות</div><div class="mono" style="font-size:10px;color:var(--dim)">${gt.any_2c} touched · ${gt.monotonic} mono · ${gt.oscillating} osc</div></div>`;
    $('goalBar').innerHTML=`<h3>🎯 Goal Count — יעדים לספירת חלונות</h3><div style="display:flex;gap:10px;flex-wrap:wrap">${bar(g5)}${bar(g15)}${tot}</div>`;
  })();

  // Live bar
  let liveHtml = '<h3>חלונות חיים עכשיו — Live Books & Queue</h3><div class="live-grid">';
  const order=['btc-up-or-down-5m','eth-up-or-down-5m','bnb-up-or-down-5m','sol-up-or-down-5m','xrp-up-or-down-5m','btc-up-or-down-15m','eth-up-or-down-15m','bnb-up-or-down-15m','sol-up-or-down-15m','xrp-up-or-down-15m'];
  for(const k of order){
    const s=live[k];
    if(!s){ liveHtml+=`<div class="liveBox"><div style="font:700 10px var(--disp);color:var(--faint)">${k}</div><div style="color:var(--dim);font-size:11px">טוען...</div></div>`; continue; }
    const mid=s.mid==null?'-':fmtPrice(s.mid);
    const tp=s.touch_pair==null?'-':s.touch_pair.toFixed(3);
    const rem=s.t_rem==null?'-':hms(s.t_rem);
    const q=s.queue_up==null?'-':Math.round(s.queue_up);
    liveHtml+=`<div class="liveBox"><div style="font:700 10px var(--disp);color:var(--faint)">${k}</div><div class="mono" style="font-size:12px">mid ${mid} · touch ${tp}</div><div class="mono" style="font-size:10px;color:var(--dim)">queue @rest ${q} · נותר ${rem}</div><div style="font-size:10px"><a href="https://polymarket.com/market/${s.slug}" target="_blank" rel="noopener">${esc(s.slug.slice(0,28))} ↗</a></div></div>`;
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
    grid+=`<div class="card"><h3>${esc(s.label)} — ${s.duration===300?'5 דקות':'15 דקות'} <span style="font-weight:400;color:var(--dim);text-transform:none;letter-spacing:0">· ${n} חלונות</span></h3>
      <div class="kpi">
        <div class="box"><div class="lbl">כל תנודה ≥$0.02</div><div class="val">${any2}/${n}</div><div class="sub">${p2}% זזו $0.02</div><div class="bar"><div class="fill up" style="width:${p2}%"></div></div></div>
        <div class="box"><div class="lbl">≥$0.03</div><div class="val">${any3}/${n}</div><div class="sub">${p3}%</div><div class="bar"><div class="fill gold" style="width:${p3}%"></div></div></div>
        <div class="box"><div class="lbl">oscillating</div><div class="val" style="color:var(--up)">${osc}/${n}</div><div class="sub">${po}%</div><div class="bar"><div class="fill up" style="width:${po}%"></div></div></div>
        <div class="box"><div class="lbl">monotonic</div><div class="val" style="color:var(--down)">${mono}/${n}</div><div class="sub">${pm}%</div><div class="bar"><div class="fill down" style="width:${pm}%"></div></div></div>
      </div>
      <div class="mono" style="font-size:10px;color:var(--dim)">מדד touch pair חציוני: ${s.pair_cost_median==null?'-':s.pair_cost_median.toFixed(3)} · flat ${flat}/${n}</div>
    </div>`;
  }
  $('seriesGrid').innerHTML=grid;

  // Recent windows table
  let tbl='<div class="card"><h3 style="font-size:13px">חלונות אחרונים — פתיחה 50/50 (לחיץ ל-Polymarket)</h3><table class="tbl"><tr><th>סדרה</th><th>חלון</th><th>פתיחה UP / DOWN</th><th style="color:var(--up)">שיא UP</th><th style="color:var(--down)">שיא DOWN</th><th>נר יפני</th><th>סיווג</th><th>קישור</th></tr>';
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
    const candle = `<div class="candle-wrap"><div class="candle-bar"><div class="candle-wick" style="left:${wickLeft}%;width:${wickW}%;"></div><div class="candle-body" style="left:${bodyLeft}%;width:${Math.max(2,bodyW)}%;background:${bodyColor};border:1px solid ${bodyColor}"></div><div style="position:absolute;left:50%;top:0;bottom:0;width:1px;background:var(--faint);opacity:.6"></div></div><div style="font-size:9px;color:var(--dim);margin-top:1px">טווח ${fmtPrice(mx!=null&&mn!=null?mx-mn:0)} · סגירה ${fmtPrice(cm)}</div></div>`;
    const labelStr = esc(String(w.label||''));
    const slugStr = esc(String(w.slug||'').slice(-14));
    const startTs = w.start_ts ? new Date(w.start_ts*1000).toLocaleTimeString('he-IL',{hour:'2-digit',minute:'2-digit'}) : '-';
    tbl+=`<tr><td style="font-weight:700">${labelStr}</td><td class="mono" style="font-size:12px">${slugStr}<div style="font-size:10px;color:var(--faint)">${startTs}</div></td><td><span class="price-up">${openUp}</span> | <span class="price-down">${openDown}</span></td><td><span class="price-up">${upHigh}</span> (+${upExc})</td><td><span class="price-down">${downHigh}</span> (+${downExc})</td><td>${candle}</td><td>${clsPill(w.class)}</td><td><a href="${esc(w.url||'#')}" target="_blank" rel="noopener" style="font-size:12px;font-weight:700">פתח ↗</a></td></tr>`;
  }
  tbl+='</table></div>';
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
    if(text) text.innerHTML = 'מחשב סימולציה <span class="thinking-dots"><span></span><span></span><span></span></span>';
  } else {
    btn.disabled = false;
    btn.classList.remove('thinking');
    if(icon) icon.textContent = '▶';
    if(text) text.textContent = 'הרץ סימולציה (Run Sweep)';
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

async function runBacktest(fileOverride){
  setBacktestLoadingState(true);
  try {
    const getVal = (id, def) => {
      const el = $(id);
      if (!el) return def;
      const v = String(el.value).trim();
      return (v !== '' && !isNaN(Number(v))) ? Number(v) : def;
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

    const fileVal = fileOverride !== undefined ? fileOverride : ($('btFileSelect') ? $('btFileSelect').value : (window.selectedBacktestFile || ''));
    if (fileOverride !== undefined && $('btFileSelect')) {
      $('btFileSelect').value = fileOverride;
    }

    let url = `/api/backtest?offset=${offset}&queue=${queue}&pair_cost=${pairCost}&exit_default_5m=${exit5m}&exit_default_15m=${exit15m}&exit_btc_5m=${exitBtc}&exit_sol_5m=${exitSol}&fill_model=${fillModel}&size=${size}&gas=${gas}&max_start_delay=${maxStartDelay}`;
    if (fileVal) {
      url += `&file=${encodeURIComponent(fileVal)}`;
    }
    const res = await fetch(url);
    const data = await res.json();

    $('btHash').textContent = `Hash: ${data.params_hash} · ${data.n_windows} חלונות${fileVal ? ' · [' + fileVal + ']' : ''}`;
    const ov = data.overall || {};
    $('btTotalPnl').textContent = fmtUsd(ov.total_pnl_cents||0, true);
    $('btTotalPnl').style.color = (ov.total_pnl_cents||0)>=0 ? 'var(--up)' : 'var(--down)';
    $('btAvgPnl').textContent = fmtUsd(ov.avg_pnl_cents||0, true) + ' לחלון';
    $('btPairRate').textContent = ((ov.pair_rate||0)*100).toFixed(1) + '%';
    $('btPairsCount').textContent = `${ov.pairs||0} / ${ov.windows||0} זוגות`;
    $('btExitRate').textContent = ((ov.exit_rate||0)*100).toFixed(1) + '%';
    $('btExitsCount').textContent = `${ov.exits||0} יציאות`;
    $('btMaxDd').textContent = '-' + fmtPrice((ov.max_drawdown_cents||0)/100);
    $('btWinRate').textContent = ((ov.win_rate||0)*100).toFixed(1) + '%';

    // Equity Curve Chart
    const eqData = data.equity_curve || [];
    const labels = eqData.map(e => e.window_idx);
    const pnlValues = eqData.map(e => ((e.cumulative_pnl_cents||0)/100).toFixed(2));

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
            title: { display: true, text: 'חלון', color: '#8792a6' },
            ticks: { color: '#8792a6', maxTicksLimit: 12 },
            grid: { color: '#232a35' }
          },
          y: {
            title: { display: true, text: 'רווח/הפסד מצטבר ($)', color: '#8792a6' },
            ticks: {
              color: '#8792a6',
              callback: function(v){ return '$' + Number(v).toFixed(2); }
            },
            grid: { color: '#232a35' }
          }
        }
      }
    });

    // Per series table
    let stbl = '<table class="tbl"><tr><th>סדרה</th><th>חלונות</th><th>לכידת זוג (Pair)</th><th>יציאות (Exits)</th><th>PnL כולל ($)</th><th>ממוצע לחלון ($)</th><th>Oscillating</th><th>Monotonic</th></tr>';
    for(const [k,v] of Object.entries(data.per_series||{})){
      stbl+=`<tr><td style="font-weight:700">${esc(v.label)}</td><td>${v.windows}</td><td style="color:var(--up);font-weight:700">${(v.pair_rate*100).toFixed(1)}% (${v.pairs})</td><td style="color:var(--down)">${(v.exit_rate*100).toFixed(1)}% (${v.exits})</td><td class="mono" style="font-weight:700;color:${v.total_pnl_cents>=0?'var(--up)':'var(--down)'}">${fmtUsd(v.total_pnl_cents,true)}</td><td class="mono">${fmtUsd(v.avg_pnl_cents,true)}</td><td>${v.oscillating}</td><td>${v.monotonic}</td></tr>`;
    }
    stbl+='</table>';
    $('btSeriesTableWrap').innerHTML=stbl;

    // Trades sample table
    let ttbl = '<table class="tbl"><tr><th>חלון</th><th>סדרה</th><th>תוצאה</th><th>סטטוס מילוי</th><th>PnL לחלון ($)</th><th>Delay / חלקי</th><th>סיבת יציאה</th></tr>';
    for(const t of (data.trades_sample||[]).slice(0,30)){
      const pnlUsd = fmtUsd(t.pnl_cents, true);
      const resPill = t.both_filled ? pill('pill-osc',`PAIR CAPTURED ${pnlUsd}`) : t.exit_triggered ? pill('pill-mono','EXIT TRIGGERED') : pill('pill-flat','FLAT / UNRESOLVED');
      const delayTag = t.is_partial
        ? `<span class="pill pill-mono" style="font-size:10px;color:var(--down)">חצי (${t.start_delay_sec}s)</span>`
        : `<span class="mono" style="font-size:11px;color:var(--dim)">${t.start_delay_sec ? t.start_delay_sec + 's' : '0s'}</span>`;
      ttbl+=`<tr><td class="mono" style="font-size:11px">${esc(t.slug.slice(-14))}</td><td style="font-weight:600">${esc(t.label)}</td><td>${resPill}</td><td class="mono" style="font-size:11px">${t.both_filled?'UP+DOWN':t.up_filled?'UP only':t.down_filled?'DOWN only':'-'}</td><td class="mono" style="font-weight:700;color:${t.pnl_cents>=0?'var(--up)':'var(--down)'}">${pnlUsd}</td><td>${delayTag}</td><td style="font-size:11px;color:var(--dim)">${esc(t.exit_reason||'-')}</td></tr>`;
    }
    ttbl+='</table>';
    $('btTradesTableWrap').innerHTML=ttbl;
  } catch(err) {
    console.error('Error running backtest:', err);
  } finally {
    setBacktestLoadingState(false);
  }
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
  if ($('btFileSelect')) $('btFileSelect').value = "";
  window.selectedBacktestFile = "";
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
      data:{labels:bLabels,datasets:[{label:'חלונות',data:bCounts,backgroundColor:'#e8b84b'}]},
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
      defOpt.textContent = 'כל הקבצים / 2,820 חלונות (ברירת מחדל)';
      sel.appendChild(defOpt);

      for(const f of d.files){
        const opt = document.createElement('option');
        opt.value = f.name;
        const linesFormatted = (f.lines||0).toLocaleString();
        const estPrefix = f.lines_estimated ? '~' : '';
        opt.textContent = `${f.name} (${estPrefix}${linesFormatted} שורות)`;
        sel.appendChild(opt);
      }
      if(currentVal && Array.from(sel.options).some(o => o.value === currentVal)){
        sel.value = currentVal;
      }
    }

    const wrap = $('manifestTableWrap');
    if(!wrap) return;
    wrap.innerHTML = '';

    if(d.manifest && d.manifest.tape_empty_rate !== undefined){
      const m = d.manifest;
      const ratePct = (m.tape_empty_rate * 100).toFixed(1) + '%';
      const isCrit = m.tape_empty_rate > 0.99;
      const noticeDiv = document.createElement('div');
      noticeDiv.style.cssText = `padding:10px 14px;border-radius:8px;margin-bottom:12px;display:flex;align-items:center;justify-content:space-between;background:${isCrit?'rgba(240,104,77,0.15)':'var(--panel2)'};border:1px solid ${isCrit?'rgba(240,104,77,0.4)':'var(--line)'}`;
      noticeDiv.innerHTML = `<div style="font-size:12px;color:${isCrit?'var(--down)':'var(--tx)'};font-weight:600">${isCrit?'⚠️ התראה: שיעור עסקאות ריקות גבוה (>99%) — בדוק את תקינות ה-CLOB Trade Stream':'📊 סטטוס Tape (CLOB Trade Feed)'}</div><div class="mono" style="font-size:12px;color:${isCrit?'var(--down)':'var(--up)'}">ריק: ${ratePct} · עסקאות: ${(m.tape_entries_total||0).toLocaleString()} · יום: ${esc(m.day||'-')}</div>`;
      wrap.appendChild(noticeDiv);
    }

    const tbl = document.createElement('table');
    tbl.className = 'tbl';
    const thead = document.createElement('tr');
    thead.innerHTML = '<th>שם קובץ</th><th>גודל</th><th>שורות / דגימות</th><th>עדכון אחרון</th><th>פעולות</th>';
    tbl.appendChild(thead);

    if(!d.files || d.files.length === 0){
      const tr = document.createElement('tr');
      tr.innerHTML = '<td colspan="5" style="text-align:center;color:var(--faint);padding:18px">אין קבצים בתיקיית ticks/</td>';
      tbl.appendChild(tr);
    } else {
      for(const f of d.files){
        const tr = document.createElement('tr');
        const mb = (f.bytes/(1024*1024)).toFixed(2)+' MB';
        const linesFormatted = (f.lines||0).toLocaleString();
        const linesHtml = f.lines_estimated
          ? `~${linesFormatted} <span style="color:var(--dim);font-size:10px">(הערכה)</span>`
          : linesFormatted;

        const tdName = document.createElement('td');
        tdName.className = 'mono';
        tdName.style.fontWeight = '700';
        tdName.textContent = f.name;

        const tdSize = document.createElement('td');
        tdSize.className = 'mono';
        tdSize.textContent = mb;

        const tdLines = document.createElement('td');
        tdLines.className = 'mono';
        tdLines.innerHTML = linesHtml;

        const tdMtime = document.createElement('td');
        tdMtime.className = 'mono';
        tdMtime.textContent = new Date(f.mtime*1000).toLocaleString('he-IL');

        const tdActions = document.createElement('td');
        tdActions.style.display = 'flex';
        tdActions.style.gap = '6px';
        tdActions.style.alignItems = 'center';

        const btnVerify = document.createElement('button');
        btnVerify.className = 'btn';
        btnVerify.style.cssText = 'padding:4px 10px;font-size:11px;background:rgba(51,201,181,0.12);color:var(--up);border-color:rgba(51,201,181,0.3);cursor:pointer';
        btnVerify.textContent = '🔍 בדוק';
        btnVerify.addEventListener('click', () => verifyTickData(f.name));

        const btnRun = document.createElement('button');
        btnRun.className = 'btn';
        btnRun.style.cssText = 'padding:4px 10px;font-size:11px';
        btnRun.textContent = 'הרץ בקטסט ⚡';
        btnRun.addEventListener('click', () => runBacktestOnFile(f.name));

        const btnDel = document.createElement('button');
        btnDel.className = 'btn';
        btnDel.style.cssText = 'padding:4px 10px;font-size:11px;background:rgba(255,87,87,0.12);color:var(--down);border-color:rgba(255,87,87,0.3);cursor:pointer';
        btnDel.textContent = '🗑️ מחק';
        btnDel.addEventListener('click', () => deleteTickFile(f.name));

        tdActions.appendChild(btnVerify);
        tdActions.appendChild(btnRun);
        tdActions.appendChild(btnDel);

        tr.appendChild(tdName);
        tr.appendChild(tdSize);
        tr.appendChild(tdLines);
        tr.appendChild(tdMtime);
        tr.appendChild(tdActions);
        tbl.appendChild(tr);
      }
    }
    wrap.appendChild(tbl);
  }catch(err){
    $('manifestTableWrap').innerHTML = '<div style="color:var(--down);padding:12px">שגיאה בטעינת רשימת הקבצים</div>';
  }
}

async function verifyTickData(filename){
  const modal = $('verifyModalOverlay');
  const content = $('verifyModalContent');
  if(!modal || !content) return;

  modal.style.display = 'flex';
  content.innerHTML = '<div style="text-align:center;padding:24px;color:var(--dim);font-size:14px">מבצע בדיקת תקינות מקיפה... <span class="spinner"></span></div>';

  try{
    let url = '/api/ticks/verify';
    if(filename) url += '?file=' + encodeURIComponent(filename);
    const res = await fetch(url);
    const d = await res.json();

    const st = d.status || 'UNKNOWN';
    const statusColor = st === 'PASS' ? 'var(--up)' : st === 'WARN' ? 'var(--gold)' : 'var(--down)';
    const statusLabel = st === 'PASS' ? '✅ תקין (PASS)' : st === 'WARN' ? '⚠️ אזהרות סבירות (WARN)' : '❌ כשל תקינות (FAIL)';

    let html = `
      <div style="background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin-bottom:14px;display:flex;align-items:center;justify-content:space-between">
        <div>
          <div style="font-size:11px;color:var(--dim);text-transform:uppercase">סטטוס תקינות כללי</div>
          <div style="font-size:16px;font-weight:700;color:${statusColor};margin-top:2px">${statusLabel}</div>
        </div>
        <div class="mono" style="font-size:12px;color:var(--dim)">
          ${filename ? 'קובץ: ' + esc(filename) : 'כלל הקבצים בתיקייה (' + (d.files_checked||0) + ')'}
        </div>
      </div>

      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:14px">
        <div style="background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:8px 10px;text-align:center">
          <div style="font-size:10px;color:var(--dim)">דגימות תקינות</div>
          <div class="mono" style="font-size:15px;font-weight:700;color:var(--up);margin-top:2px">${(d.total_valid_ticks||d.valid_ticks||0).toLocaleString()}</div>
        </div>
        <div style="background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:8px 10px;text-align:center">
          <div style="font-size:10px;color:var(--dim)">שורות פגומות (Corrupt)</div>
          <div class="mono" style="font-size:15px;font-weight:700;color:${(d.total_corrupt_lines||d.corrupt_lines||0)>0?'var(--down)':'var(--tx)'};margin-top:2px">${(d.total_corrupt_lines||d.corrupt_lines||0).toLocaleString()}</div>
        </div>
        <div style="background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:8px 10px;text-align:center">
          <div style="font-size:10px;color:var(--dim)">חלונות שזוהו</div>
          <div class="mono" style="font-size:15px;font-weight:700;margin-top:2px">${(d.total_windows||d.windows_count||0).toLocaleString()}</div>
        </div>
        <div style="background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:8px 10px;text-align:center">
          <div style="font-size:10px;color:var(--dim)">Crossed Books</div>
          <div class="mono" style="font-size:15px;font-weight:700;color:${(d.total_crossed_books||d.crossed_books||0)>0?'var(--down)':'var(--tx)'};margin-top:2px">${(d.total_crossed_books||d.crossed_books||0).toLocaleString()}</div>
        </div>
        <div style="background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:8px 10px;text-align:center">
          <div style="font-size:10px;color:var(--dim)">פערי דגימה (>6s)</div>
          <div class="mono" style="font-size:15px;font-weight:700;color:${(d.total_sampling_gaps||d.sampling_gaps_count||0)>0?'var(--gold)':'var(--tx)'};margin-top:2px">${(d.total_sampling_gaps||d.sampling_gaps_count||0).toLocaleString()}</div>
        </div>
        <div style="background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:8px 10px;text-align:center">
          <div style="font-size:10px;color:var(--dim)">שגיאות קולקטור (err)</div>
          <div class="mono" style="font-size:15px;font-weight:700;color:${(d.total_collector_errors||d.collector_errors||0)>0?'var(--gold)':'var(--tx)'};margin-top:2px">${(d.total_collector_errors||d.collector_errors||0).toLocaleString()}</div>
        </div>
      </div>
    `;

    if(d.files && d.files.length > 0){
      html += '<h4 style="margin:12px 0 6px;font-size:12px">פירוט לפי קובץ:</h4><table class="tbl" style="margin-top:4px"><tr><th>קובץ</th><th>סטטוס</th><th>דגימות</th><th>פגומים</th><th>חלונות</th><th>פערים</th></tr>';
      for(const fr of d.files){
        const fCol = fr.status === 'PASS' ? 'var(--up)' : fr.status === 'WARN' ? 'var(--gold)' : 'var(--down)';
        html += `<tr><td class="mono" style="font-weight:600">${esc(fr.file)}</td><td style="color:${fCol};font-weight:700">${esc(fr.status)}</td><td class="mono">${(fr.valid_ticks||0).toLocaleString()}</td><td class="mono" style="color:${fr.corrupt_lines>0?'var(--down)':'inherit'}">${fr.corrupt_lines||0}</td><td class="mono">${fr.windows_count||0}</td><td class="mono">${fr.sampling_gaps_count||0}</td></tr>`;
      }
      html += '</table>';
    }

    if(d.sample_issues && d.sample_issues.length > 0){
      html += '<h4 style="margin:14px 0 6px;font-size:12px;color:var(--down)">דוגמאות לחריגות שנמצאו:</h4><div style="background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:8px 12px;max-height:140px;overflow-y:auto;font-size:11px" class="mono">';
      for(const is of d.sample_issues.slice(0, 10)){
        html += `<div style="margin-bottom:4px;color:var(--dim)">• שורה ${is.line}: <span style="color:var(--tx)">${esc(is.detail)}</span></div>`;
      }
      html += '</div>';
    }

    content.innerHTML = html;
  }catch(e){
    content.innerHTML = `<div style="color:var(--down);padding:16px;text-align:center">שגיאה בביצוע בדיקת תקינות: ${esc(e.message)}</div>`;
  }
}

function closeVerifyModal(){
  const modal = $('verifyModalOverlay');
  if(modal) modal.style.display = 'none';
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
    confirmBtn.textContent = 'מוחק...';
  }
  await executeDeleteFileDirect(filename);
  closeDeleteModal();
  if(confirmBtn){
    confirmBtn.disabled = false;
    confirmBtn.textContent = 'כן, מחק קובץ';
  }
}

async function executeDeleteFileDirect(filename){
  try{
    const res = await fetch('/api/ticks/file?filename=' + encodeURIComponent(filename), { method:'DELETE' });
    const data = await res.json();
    if(data.ok){
      showNotice('✅ הקובץ ' + filename + ' נמחק בהצלחה', false);
      loadManifest();
    } else {
      showNotice('❌ שגיאה במחיקת הקובץ: ' + (data.error || 'שגיאה לא ידועה'), true);
    }
  }catch(e){
    showNotice('❌ שגיאת תקשורת במחיקת הקובץ', true);
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
  progText.textContent = `מתחיל העלאה במקטעים: ${file.name} (${totalMb} MB, ${totalChunks} מקטעים)...`;
  statusEl.textContent = `מעלה מקטע 1 מתוך ${totalChunks}...`;

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
              progText.textContent = `מעלה: ${loadedMb} MB / ${totalMb} MB (${percent}%) | מקטע ${chunkIndex + 1}/${totalChunks}`;
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
              let errMsg = xhr.statusText || 'שגיאת שרת';
              try {
                const errObj = JSON.parse(xhr.responseText);
                if (errObj.error) errMsg = errObj.error;
              } catch (_) {}
              reject(new Error(`קוד ${xhr.status}: ${errMsg}`));
            }
          };

          xhr.onerror = function() {
            reject(new Error('שגיאת תקשורת ברשת'));
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
          statusEl.textContent = `ניסיון ${attempt} נכשל במקטע ${chunkIndex + 1}, מנסה שוב בעוד שניה...`;
          await new Promise(r => setTimeout(r, 1000));
        }
      }
    }

    if (!success) {
      progBar.style.background = 'var(--down)';
      statusEl.innerHTML = `<span style="color:var(--down);font-weight:700">❌ שגיאה בהעלאת מקטע ${chunkIndex + 1}/${totalChunks}: ${esc(lastError)}</span>`;
      return;
    }

    if (chunkIndex === totalChunks - 1 && responseData) {
      progBar.style.width = '100%';
      progText.textContent = '100% - הושלם בהצלחה!';
      const linesStr = (responseData.lines || 0).toLocaleString();
      const winStr = (responseData.windows_indexed || 0).toLocaleString();
      statusEl.innerHTML = `<span style="color:var(--up);font-weight:700">✅ הועלה בהצלחה: ${esc(responseData.filename || file.name)} (${linesStr} שורות, ${winStr} חלונות אונדקסו)</span>`;
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

async function fetchCockpitState() {
  try {
    const res = await fetch('/api/live/state', { cache: 'no-store' });
    if (!res.ok) return;
    const data = await res.json();
    cockpitState = data;
    renderCockpitUI(data);
  } catch (e) {
    console.error('Failed fetching cockpit state', e);
  }
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
    cockpitState = st;
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
    renderCockpitUI(st);
  } catch (e) {
    alert('Error loading demo data: ' + e);
  }
}

async function syncRealRunTrades() {
  const btn = $('btnSyncRealRun');
  try {
    if (btn) { btn.disabled = true; btn.textContent = '⏳ מסנכרן מפולימרקט...'; }
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
    if (btn) { btn.disabled = false; btn.textContent = '📥 סנכרן ריצה אמיתית (Polymarket)'; }
  } catch (e) {
    alert('Error syncing real run: ' + e);
    if (btn) { btn.disabled = false; btn.textContent = '📥 סנכרן ריצה אמיתית (Polymarket)'; }
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

async function applyCockpitConfig() {
  if (isApplyingCockpitConfig) return;
  isApplyingCockpitConfig = true;
  const offset = parseFloat($('cockpitOffset').value) || 0.02;
  const exit_thresh = parseFloat($('cockpitExit').value) || 0.05;
  const shares = parseInt($('cockpitShares').value, 10) || 5;
  const mode = $('cockpitMode').value || 'paper';
  const wallet = $('cockpitWallet').value.trim();
  const startBal = parseFloat($('cockpitStartBal').value) || 1000.0;
  const body = {
    offset,
    exit_thresh,
    shares,
    mode,
    wallet_address: wallet,
    starting_balance: startBal,
  };
  // Market selection is immutable while the bot runs; only send filters when stopped
  if (!areCockpitFiltersLocked()) {
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
      alert('Configuration rejected: ' + (st.error || res.statusText));
      await fetchCockpitState();
      syncCockpitFiltersFromState(cockpitState);
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

  // Sync asset/duration filters from engine state on first receipt, and whenever
  // the bot is running (selection is engine-owned and immutable mid-run).
  if (!hasInitializedCockpitFilters && st.selected_series) {
    hasInitializedCockpitFilters = true;
    syncCockpitFiltersFromState(st);
  } else if (st.is_running && st.selected_series) {
    syncCockpitFiltersFromState(st);
  } else {
    updateCockpitFilterUI();
  }

  // 1. Status Badges & Buttons
  const isRun = !!st.is_running;
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
    if (sb.rtds_connected || liveStreamConnected) {
      streamPill.textContent = '🟢 RTDS STREAM: 1s';
      streamPill.className = 'pill pill-osc';
      streamPill.style.color = 'var(--up)';
    } else {
      streamPill.textContent = '🟡 REST POLLING';
      streamPill.className = 'pill pill-flat';
      streamPill.style.color = 'var(--gold)';
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
      const mktPnl = m.total_pnl_usd || 0.0;
      const pnlColor = mktPnl > 0 ? 'var(--up)' : mktPnl < 0 ? 'var(--down)' : 'var(--tx)';

      let statusBadgeCls = 'pill-flat';
      let statusText = m.status || 'IDLE';
      if (m.status === 'QUOTING') { statusBadgeCls = 'pill-flat'; statusText = 'QUOTING BIDS'; }
      else if (m.status === 'FILLED_UP') { statusBadgeCls = 'pill-mono'; statusText = 'FILLED UP'; }
      else if (m.status === 'FILLED_DOWN') { statusBadgeCls = 'pill-mono'; statusText = 'FILLED DOWN'; }
      else if (m.status === 'PAIR_MERGED') { statusBadgeCls = 'pill-osc'; statusText = 'PAIR MERGED'; }
      else if (m.status === 'STOP_EXIT') { statusBadgeCls = 'pill-mono'; statusText = 'STOPPED OUT'; }

      let posStr = 'FLAT';
      const actualUp = m.fill_price_up != null ? m.fill_price_up : (m.resting_up || 0.48);
      const actualDown = m.fill_price_down != null ? m.fill_price_down : (m.resting_down || 0.48);
      if (m.filled_up && m.filled_down) { posStr = `MERGED PAIR (${m.order_shares || 5}) @ $${actualUp.toFixed(2)} + $${actualDown.toFixed(2)}`; }
      else if (m.filled_up) { posStr = `LONG UP (${m.order_shares || 5}) @ $${actualUp.toFixed(2)}`; }
      else if (m.filled_down) { posStr = `LONG DOWN (${m.order_shares || 5}) @ $${actualDown.toFixed(2)}`; }

      const fillsSub = (m.fill_price_up != null || m.fill_price_down != null)
        ? ` · Fills: $${(m.fill_price_up != null ? m.fill_price_up.toFixed(2) : '-')} / $${(m.fill_price_down != null ? m.fill_price_down.toFixed(2) : '-')}`
        : '';

      gridHtml += `
        <div class="card" style="background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:12px;margin:0;display:flex;flex-direction:column;justify-content:space-between">
          <div>
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
              <div style="display:flex;align-items:center;gap:6px">
                <span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${item.color}"></span>
                <span style="font:700 13px var(--disp);letter-spacing:0.04em">${item.label}</span>
              </div>
              <span class="mono" style="font-size:11px;color:var(--gold);font-weight:600">⏱ ${remStr}</span>
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

            <div style="background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:6px 8px;margin-bottom:8px">
              <div style="font-size:9px;color:var(--faint);font-weight:700;text-transform:uppercase;margin-bottom:2px">Orders & Position</div>
              <div class="mono" style="font-size:11px;font-weight:600;color:var(--tx)">${posStr}</div>
              <div class="mono" style="font-size:10px;color:var(--dim)">Bids: $${(m.resting_up || 0.48).toFixed(2)} / $${(m.resting_down || 0.48).toFixed(2)}${fillsSub}</div>
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
  }

  // 4. Render Live Open Orders & Pre-Quotes Table
  const ordersBodyEl = $('cockpitOrdersBody');
  const ordersCountEl = $('cockpitOrdersCount');
  const openOrders = st.open_orders || [];
  if (ordersCountEl) ordersCountEl.textContent = String(openOrders.length);
  if (ordersBodyEl) {
    if (openOrders.length === 0) {
      ordersBodyEl.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--dim);padding:16px">No resting orders currently active.</td></tr>';
    } else {
      let ordHtml = '';
      for (const o of openOrders) {
        const oId = o.order_id || '-';
        const mktLabel = o.market || o.token_id || '-';
        const sideStr = o.side || 'BUY';
        const priceStr = o.price != null ? `$${Number(o.price).toFixed(2)}` : '-';
        const sizeStr = o.size != null ? String(o.size) : '-';
        const statusStr = o.status || 'OPEN';
        const sourceStr = o.source || 'CLOB';
        const canCancel = oId && oId !== '-';

        ordHtml += `
          <tr>
            <td class="mono" style="font-size:10.5px;color:var(--gold);max-width:140px;overflow:hidden;text-overflow:ellipsis" title="${esc(oId)}">${esc(oId.length > 16 ? oId.slice(0, 16) + '...' : oId)}</td>
            <td style="font-weight:700">${esc(mktLabel)}</td>
            <td class="mono" style="color:${sideStr.includes('UP') || sideStr === 'BUY' ? 'var(--up)' : 'var(--down)'}">${esc(sideStr)}</td>
            <td class="mono">${priceStr}</td>
            <td class="mono">${sizeStr}</td>
            <td><span class="pill pill-mono" style="font-size:9px;padding:2px 6px">${esc(statusStr)}</span></td>
            <td style="font-size:10.5px;color:var(--dim)">${esc(sourceStr)}</td>
            <td>
              ${canCancel ? `<button class="btn btn-danger cancel-order-btn" style="font-size:10px;padding:2px 7px" data-order-id="${esc(oId)}">✖ Cancel</button>` : '-'}
            </td>
          </tr>
        `;
      }
      ordersBodyEl.innerHTML = ordHtml;
      ordersBodyEl.querySelectorAll('.cancel-order-btn').forEach(btn => {
        btn.addEventListener('click', () => cancelSingleOrder(btn.dataset.orderId));
      });
    }
  }

  // 4b. Render Live Polymarket Open Positions Table
  const posBodyEl = $('cockpitPositionsBody');
  const posCountEl = $('cockpitPositionsCount');
  const openPos = st.open_positions || st.positions || [];
  if (posCountEl) posCountEl.textContent = String(openPos.length);
  if (posBodyEl) {
    if (openPos.length === 0) {
      posBodyEl.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--dim);padding:16px">No open positions on Polymarket account.</td></tr>';
    } else {
      let posHtml = '';
      for (const p of openPos) {
        const titleStr = p.title || p.asset || '-';
        const outcomeStr = p.outcome || '-';
        const sizeStr = p.size != null ? Number(p.size).toFixed(2) : '-';
        const avgPriceStr = p.avgPrice != null ? `$${Number(p.avgPrice).toFixed(3)}` : '-';
        const curPriceStr = p.curPrice != null ? `$${Number(p.curPrice).toFixed(3)}` : '-';
        const cashPnl = p.cashPnl != null ? Number(p.cashPnl) : null;
        const pnlCol = cashPnl != null ? (cashPnl >= 0 ? 'var(--up)' : 'var(--down)') : 'var(--tx)';
        const pnlStr = cashPnl != null ? `${cashPnl >= 0 ? '+' : ''}$${cashPnl.toFixed(2)}` : '-';

        posHtml += `
          <tr>
            <td style="font-weight:600;max-width:240px;overflow:hidden;text-overflow:ellipsis" title="${esc(titleStr)}">${esc(titleStr)}</td>
            <td><span class="pill pill-mono" style="font-size:9px;padding:2px 6px">${esc(outcomeStr)}</span></td>
            <td class="mono">${sizeStr}</td>
            <td class="mono">${avgPriceStr}</td>
            <td class="mono">${curPriceStr}</td>
            <td class="mono" style="font-weight:700;color:${pnlCol}">${pnlStr}</td>
          </tr>
        `;
      }
      posBodyEl.innerHTML = posHtml;
    }
  }

  // 5. Render Execution Trade Log Table
  const bodyEl = $('cockpitTradesBody');
  if (bodyEl && st.trades) {
    if (st.trades.length === 0) {
      bodyEl.innerHTML = '<tr><td colspan="9" style="text-align:center;color:var(--dim);padding:20px">No completed executions in this session yet.</td></tr>';
    } else {
      let rowsHtml = '';
      for (const t of st.trades) {
        const pnlCol = t.pnl_usd >= 0 ? 'var(--up)' : 'var(--down)';
        const actBadge = t.action === 'PAIR_MERGE'
          ? '<span class="pill pill-osc">PAIR MERGE</span>'
          : t.action.startsWith('STOP')
          ? '<span class="pill pill-mono">STOP EXIT</span>'
          : '<span class="pill pill-flat">SETTLE</span>';

        const entryStr = t.entry_price_up && t.entry_price_down
          ? `$${t.entry_price_up.toFixed(2)} + $${t.entry_price_down.toFixed(2)}`
          : t.entry_price_up
          ? `UP @ $${t.entry_price_up.toFixed(2)}`
          : t.entry_price_down
          ? `DN @ $${t.entry_price_down.toFixed(2)}`
          : '-';

        const exitStr = t.exit_price != null ? `$${t.exit_price.toFixed(2)}` : '-';

        rowsHtml += `
          <tr>
            <td class="mono" style="font-size:11px;color:var(--faint)">${esc(t.timestamp)}</td>
            <td style="font-weight:700">${esc(t.label)}</td>
            <td>${actBadge}</td>
            <td class="mono">${t.shares}</td>
            <td class="mono">${entryStr}</td>
            <td class="mono">${exitStr}</td>
            <td class="mono" style="font-weight:700;color:${pnlCol}">${t.pnl_usd >= 0 ? '+' : ''}$${t.pnl_usd.toFixed(2)}</td>
            <td class="mono" style="color:${pnlCol}">${t.pnl_pct >= 0 ? '+' : ''}${t.pnl_pct.toFixed(1)}%</td>
            <td style="font-size:11px;color:var(--dim)">${esc(t.notes || '')}</td>
          </tr>
        `;
      }
      bodyEl.innerHTML = rowsHtml;
    }
  }

  // 6. Render Chart
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
    'btSize', 'btGas', 'btFileSelect', 'btMaxStartDelay'
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
    cockpitPollTimer = setInterval(fetchCockpitState, 5000);
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
        sp.textContent = '🟢 RTDS STREAM: 1s';
        sp.className = 'pill pill-osc';
        sp.style.color = 'var(--up)';
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
            const slug = env.data.slug || (function() {
              const sym = (env.data.symbol || '').toLowerCase();
              const prefix = sym.replace('usdt', '');
              for (const k in cockpitState.markets) {
                if (k.startsWith(prefix)) return k;
              }
              return null;
            })();
            if (slug && cockpitState.markets[slug]) {
              const m = cockpitState.markets[slug];
              const price = env.data.price;
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
      fetchCockpitState();
      ensureCockpitPolling();
    };
  } catch (e) {
    liveStreamConnected = false;
    ensureCockpitPolling();
  }
}

setupBacktestInputListeners();

// Initialize real-time streams and polls
fetchCockpitState();
initLiveCockpitStream();
ensureCockpitPolling();
tick();
setInterval(tick, 3000);
</script></body></html>
"""


@app.get("/", response_class=HTMLResponse)
@app.get("/oscillation", response_class=HTMLResponse)
@app.get("/summary", response_class=HTMLResponse)
@app.get("/analysis", response_class=HTMLResponse)
def root_spa_page():
    """Serve the complete unified 4-tab SPA interface."""
    return HTMLResponse(FULL_APP_HTML, headers={"Cache-Control": "no-cache"})

