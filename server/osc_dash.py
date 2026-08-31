"""Oscillation & Backtest Lab dashboard for 5m/15m crypto spread capture.

Unified 4-tab SPA:
- Tab 1: Live Observation & Recent Closed Windows
- Tab 2: Backtest Simulator Sweeper with Equity Curve
- Tab 3: Statistical Analysis & Distributions
- Tab 4: Ticks File Repository & Ingestion Manager

Serves on :8802
"""
from __future__ import annotations

import collections
import gzip
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.middleware.gzip import GZipMiddleware

ROOT = Path(__file__).resolve().parent.parent
RUN = ROOT / "run"
TICKS_DIR = RUN / "ticks"
RUN.mkdir(parents=True, exist_ok=True)
TICKS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Crypto Spread Lab")
app.add_middleware(GZipMiddleware, minimum_size=1000)

# In-memory collector process handle for UI controls
_collector_proc: subprocess.Popen | None = None


def _verify_safe_origin(request: Request) -> None:
    """Verify request is originating locally and reject suspicious cross-site requests."""
    client_host = request.client.host if request.client else "unknown"
    if client_host not in ("127.0.0.1", "::1", "localhost", "testclient"):
        raise HTTPException(status_code=403, detail="Forbidden: local access only")
    origin = request.headers.get("origin")
    if origin:
        from urllib.parse import urlparse
        p = urlparse(origin)
        if p.hostname not in ("127.0.0.1", "localhost", "::1", "testclient"):
            raise HTTPException(status_code=403, detail="Forbidden: cross-origin request rejected")
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
    queue: float = 50.0,
    pair_cost: float = 0.995,
    exit_default_5m: float = 0.12,
    exit_default_15m: float = 0.13,
    exit_btc_5m: float = 0.09,
    exit_sol_5m: float = 0.11,
    exit_reversal: float = 0.02,
    size: int = 120,
    fill_model: str = "tape",
    gas: float = 0.05,
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
    }

    params = BacktestParams(
        offset=offset,
        queue_gate=queue,
        pair_cost_gate=pair_cost,
        exit_thresh_by_slug=exit_thresh,
        exit_reversal=exit_reversal,
        quote_shares=size,
        fill_model=fill_model,
        merge_gas_usd=gas,
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
    else:
        source = TICKS_DIR

    grouped = group_by_cid(iter_ticks(source))
    if not grouped:
        return {
            "error": "no snaps",
            "params_hash": params.params_hash(),
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

    if limit_windows and limit_windows > 0:
        grouped = grouped[:limit_windows]

    per_window = [_simulate_window(g, params) for _cid, g in grouped]
    n_snaps = sum(len(g) for _cid, g in grouped)

    # Compute Equity Curve and Max Drawdown
    cum_pnl = 0.0
    peak_pnl = 0.0
    max_dd = 0.0
    equity_curve = []
    winning_windows = 0

    for idx, w in enumerate(per_window):
        cum_pnl += w.pnl_cents
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
            "pnl_cents": round(w.pnl_cents, 2),
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
        a["total_pnl_cents"] += w.pnl_cents

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
                "pnl_cents": round(w.pnl_cents, 2),
                "exit_reason": exit_info,
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
    """Return status of the background tick collector and today's total ticks."""
    global _collector_proc
    running = _collector_proc is not None and _collector_proc.poll() is None
    # Count total tick lines collected today
    today_ticks = 0
    today_file = (
        TICKS_DIR / f"ticks_{time.strftime('%Y-%m-%d', time.gmtime())}.jsonl"
    )
    if today_file.exists():
        today_ticks = _count_lines_fast(today_file)
    return {
        "running": running,
        "pid": _collector_proc.pid if running else None,
        "total_ticks_collected": today_ticks,
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

    upload_dir = RUN / "_uploads" / uploadId
    upload_dir.mkdir(parents=True, exist_ok=True)

    chunk_path = upload_dir / f"chunk_{chunkIndex:06d}"
    body = await request.body()
    chunk_path.write_bytes(body)

    if chunkIndex == totalChunks - 1:
        target_file = TICKS_DIR / filename
        with open(target_file, "wb") as out_f:
            for i in range(totalChunks):
                part = upload_dir / f"chunk_{i:06d}"
                if not part.exists():
                    return JSONResponse(status_code=400, content={"error": f"missing chunk {i}"})
                out_f.write(part.read_bytes())

        import shutil
        shutil.rmtree(upload_dir, ignore_errors=True)

        lines_count = _count_lines_fast(target_file)
        windows_indexed = 0
        try:
            from backtest.index import build_index, load_index
            idx_path = build_index(target_file)
            windows_indexed = len(load_index(idx_path))
        except Exception:
            pass

        return {
            "ok": True,
            "filename": filename,
            "lines": lines_count,
            "windows_indexed": windows_indexed,
        }

    return {"ok": True, "chunkIndex": chunkIndex}


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
</style></head><body>
<div class="hdr" id="app-hdr">
  <h1><span>◆</span> Crypto Spread <span>5m/15m Engine</span></h1>
  <span class="tag">SPREAD-2 · POLYMARKET CLOB</span>
  <div class="nav-tabs" id="main-nav">
    <button class="tab-btn active" onclick="switchTab('live')" id="tab-btn-live">📡 תצפיות ו-Live Books</button>
    <button class="tab-btn" onclick="switchTab('backtest')" id="tab-btn-backtest">⚡ סימולטור בקטסט (Sweeper)</button>
    <button class="tab-btn" onclick="switchTab('summary')" id="tab-btn-summary">📊 סיכום סטטיסטי</button>
    <button class="tab-btn" onclick="switchTab('ticks')" id="tab-btn-ticks">💾 קובצי Ticks & Ingestion</button>
  </div>
  <span style="flex:1"></span>
  <div style="display:flex;align-items:center;gap:8px">
    <span id="collectorBadge" class="mono" style="font-size:11px;padding:3px 8px;border-radius:6px;background:var(--panel2);border:1px solid var(--line)">קולקטור: טוען...</span>
    <button class="btn" id="btnToggleCollector" onclick="toggleCollector()">הפעל איסוף רציף (1s)</button>
    <button class="btn" onclick="pollOnce()">דגום עכשיו (Once)</button>
  </div>
</div>

<div class="wrap">
  <!-- TAB 1: LIVE & RECENT WINDOWS -->
  <div id="tab-live" class="tab-content active">
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
          <label>Offset מ-Mid (ספרד 2¢ = 0.02)</label>
          <input type="number" step="0.005" id="btOffset" value="0.02">
        </div>
        <div class="form-group">
          <label>עומק תור (Queue ahead @ rest)</label>
          <input type="number" step="10" id="btQueue" value="50">
        </div>
        <div class="form-group">
          <label>עלות מקסימלית לזוג (Pair Cost Ceiling)</label>
          <input type="number" step="0.005" id="btPairCost" value="0.995">
        </div>
        <div class="form-group">
          <label>רף יציאה 5m כללי (Exit Default)</label>
          <input type="number" step="0.01" id="btExit5m" value="0.12">
        </div>
        <div class="form-group">
          <label>רף יציאה 15m כללי (Exit Default)</label>
          <input type="number" step="0.01" id="btExit15m" value="0.13">
        </div>
        <div class="form-group">
          <label>רף יציאה ייעודי BTC 5m</label>
          <input type="number" step="0.01" id="btExitBtc" value="0.09">
        </div>
        <div class="form-group">
          <label>רף יציאה ייעודי SOL 5m</label>
          <input type="number" step="0.01" id="btExitSol" value="0.11">
        </div>
        <div class="form-group">
          <label>מודל מילוי (Fill Model)</label>
          <select id="btFillModel">
            <option value="tape" selected>Tape (שמרני - עסקאות בפועל)</option>
            <option value="book">Book (אופטימי - חציית Ask)</option>
            <option value="both">Both</option>
          </select>
        </div>
        <div class="form-group">
          <label>גודל פוזיציה למניות (Shares)</label>
          <input type="number" step="10" id="btSize" value="120">
        </div>
        <div class="form-group">
          <label>עלות גז מרג' בסנטים (Gas Cents)</label>
          <input type="number" step="0.01" id="btGas" value="0.05">
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
        <div class="box"><div class="lbl">רווח/הפסד כולל</div><div class="val" id="btTotalPnl" style="color:var(--up)">+0.00¢</div><div class="sub" id="btAvgPnl">0.00¢ לחלון</div></div>
        <div class="box"><div class="lbl">אחוז לכידת זוג (Pair Rate)</div><div class="val" id="btPairRate">0.0%</div><div class="sub" id="btPairsCount">0 זוגות</div></div>
        <div class="box"><div class="lbl">אחוז הפעלת יציאה (Exit Rate)</div><div class="val" id="btExitRate" style="color:var(--down)">0.0%</div><div class="sub" id="btExitsCount">0 יציאות</div></div>
        <div class="box"><div class="lbl">Max Drawdown</div><div class="val" id="btMaxDd" style="color:var(--gold)">0.00¢</div><div class="sub">ירידה מרבית</div></div>
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
          מתוך 2,820+ חלונות אמיתיים שנמדדו ב-5m ו-15m: ב-5m <b>73% oscillating</b> — שני הצדדים ב-0.96 נתפסים ומתמזגים ל-4¢ רווח. ב-15m <b>80% oscillating</b>.
        </div>
      </div>
      <div class="card" style="border-top:2px solid var(--gold)">
        <h3>המלצות רף יציאה</h3>
        <div style="font-size:12px;color:var(--dim)">הדוק = יציאה מוקדמת. BTC הכי מונוטוני:</div>
        <div class="mono" style="font-size:12px;margin-top:8px;display:flex;flex-direction:column;gap:4px">
          <div><b style="color:var(--down)">BTC 5m:</b> רף +9¢ (יציאה ב-59¢ UP)</div>
          <div><b style="color:var(--gold)">SOL 5m:</b> רף +11¢ (יציאה ב-61¢)</div>
          <div><b style="color:var(--up)">ETH/BNB/XRP 5m:</b> רף +12¢ (יציאה ב-62¢)</div>
          <div><b>15m כללי:</b> רף +13¢</div>
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
        <button class="btn" style="font-size:11px;padding:3px 8px" onclick="loadManifest()">🔄 רענן רשימה</button>
      </div>
      <div style="font-size:12px;color:var(--dim);margin-bottom:12px">הקולקטור כותב נתוני עומק וספר פקודות מלאים ל-<code>run/ticks/ticks_YYYY-MM-DD.jsonl</code>. ניתן להעלות קבצים נוספים לניתוח.</div>
      <div id="manifestNotice" style="display:none;padding:8px 12px;border-radius:6px;margin-bottom:10px;font-size:12px;font-weight:600"></div>
      <div id="manifestTableWrap">טוען קבצים...</div>
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
</div>

<script>
const $=s=>document.getElementById(s);
const esc=s=>String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;');
const pct=(a,b)=> b?Math.round(a/b*100):0;
const hms=s=>{s=Math.max(0,Math.floor(s));const h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=s%60;return h?`${h}h ${String(m).padStart(2,'0')}m`:`${m}m ${String(x).padStart(2,'0')}s`;};
function pill(cls,txt){return `<span class="pill ${cls}">${txt}</span>`;}
function clsPill(c){return c==='oscillating'?pill('pill-osc','oscillating תנודתי'):c==='monotonic'?pill('pill-mono','monotonic חד-כיווני'):c==='flat'?pill('pill-flat','flat שטוח'):pill('pill-flat',esc(c));}

function switchTab(name){
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c=>c.classList.remove('active'));
  const btn = $('tab-btn-'+name);
  const cont = $('tab-'+name);
  if(btn) btn.classList.add('active');
  if(cont) cont.classList.add('active');
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
    const bar=(x)=>`<div class="card" style="flex:1;min-width:280px;background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:12px"><div style="font:700 11px var(--disp);letter-spacing:.07em;color:var(--faint)">🎯 ${x.short} — ${x.label}</div><div class="mono" style="font-size:18px;font-weight:700;margin:6px 0">${x.goal} <span style="font-size:12px;color:var(--dim)">goal</span> / ${x.n} <span style="font-size:12px;color:var(--up)">passed</span> / ${x.any2} <span style="font-size:12px;color:var(--gold)">±2¢</span> / ${x.mono} <span style="font-size:12px;color:var(--down)">mono</span></div><div style="display:flex;gap:6px;align-items:center"><div class="bar" style="flex:1;height:8px"><div class="fill ${x.pctGoal>=100?'up':x.pctGoal>=70?'gold':'warn'}" style="width:${x.pctGoal}%"></div></div><span class="mono" style="font-size:11px;color:var(--dim)">${x.pctGoal}%</span></div><div class="mono" style="font-size:10px;color:var(--dim);margin-top:4px">oscillating ${x.osc} · flat ${g[String(x.short==='5m'?300:900)]?.flat||0} · נותר ${x.remain} ליעד</div><div style="margin-top:6px;display:flex;gap:6px;align-items:center"><span class="mono" style="font-size:10px;color:var(--dim)">יעד:</span><input id="goalIn${x.short}" type="number" min="1" step="10" value="${x.goal}" style="width:90px;background:var(--bg);color:var(--tx);border:1px solid var(--line);border-radius:6px;padding:4px 6px;font:500 12px var(--mono)"><button onclick="(function(){const v=parseInt(document.getElementById('goalIn${x.short}').value,10);if(v>0){localStorage.setItem('goal_${x.short==='5m'?300:900}',v);tick();}})()" style="background:var(--panel);color:var(--tx);border:1px solid var(--line);border-radius:6px;padding:4px 10px;font:600 11px var(--disp);cursor:pointer">שמור</button></div></div>`;
    const tot=`<div class="card" style="flex:0 0 180px;min-width:160px;background:var(--panel);border:1px dashed var(--line);border-radius:10px;padding:12px;text-align:center"><div style="font:700 11px var(--disp);letter-spacing:.07em;color:var(--faint)">סה״כ</div><div class="mono" style="font-size:16px;font-weight:700;margin-top:4px">${gt.n} חלונות</div><div class="mono" style="font-size:10px;color:var(--dim)">${gt.any_2c} touched · ${gt.monotonic} mono · ${gt.oscillating} osc</div></div>`;
    $('goalBar').innerHTML=`<h3>🎯 Goal Count — יעדים לספירת חלונות</h3><div style="display:flex;gap:10px;flex-wrap:wrap">${bar(g5)}${bar(g15)}${tot}</div>`;
  })();

  // Live bar
  let liveHtml = '<h3>חלונות חיים עכשיו — Live Books & Queue</h3><div class="live-grid">';
  const order=['btc-up-or-down-5m','eth-up-or-down-5m','bnb-up-or-down-5m','sol-up-or-down-5m','xrp-up-or-down-5m','btc-up-or-down-15m','eth-up-or-down-15m','bnb-up-or-down-15m','sol-up-or-down-15m','xrp-up-or-down-15m'];
  for(const k of order){
    const s=live[k];
    if(!s){ liveHtml+=`<div class="liveBox"><div style="font:700 10px var(--disp);color:var(--faint)">${k}</div><div style="color:var(--dim);font-size:11px">טוען...</div></div>`; continue; }
    const mid=s.mid==null?'-':(s.mid*100).toFixed(1)+'¢';
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
        <div class="box"><div class="lbl">כל תנודה ≥2¢</div><div class="val">${any2}/${n}</div><div class="sub">${p2}% זזו 2¢</div><div class="bar"><div class="fill up" style="width:${p2}%"></div></div></div>
        <div class="box"><div class="lbl">≥3¢</div><div class="val">${any3}/${n}</div><div class="sub">${p3}%</div><div class="bar"><div class="fill gold" style="width:${p3}%"></div></div></div>
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
    const openUp = sm==null?'-':(sm*100).toFixed(1)+'¢';
    const openDown = sm==null?'-':((1-sm)*100).toFixed(1)+'¢';
    const upHigh = mx==null?'-':(mx*100).toFixed(1)+'¢';
    const upExc = ((w.max_up||0)*100).toFixed(1);
    const downHigh = mn==null?'-':((1-mn)*100).toFixed(1)+'¢';
    const downExc = ((w.max_down||0)*100).toFixed(1);
    const o = sm==null?50:sm*100, c = cm==null?o:cm*100, h = mx==null?o:mx*100, l = mn==null?o:mn*100;
    const bodyLeft = Math.min(o,c), bodyW = Math.abs(c-o);
    const wickLeft = l, wickW = h-l;
    const bodyColor = c>=o ? 'var(--up)' : 'var(--down)';
    const candle = `<div class="candle-wrap"><div class="candle-bar"><div class="candle-wick" style="left:${wickLeft}%;width:${wickW}%;"></div><div class="candle-body" style="left:${bodyLeft}%;width:${Math.max(2,bodyW)}%;background:${bodyColor};border:1px solid ${bodyColor}"></div><div style="position:absolute;left:50%;top:0;bottom:0;width:1px;background:var(--faint);opacity:.6"></div></div><div style="font-size:9px;color:var(--dim);margin-top:1px">טווח ${((h-l)).toFixed(1)}¢ · סגירה ${(c).toFixed(1)}¢</div></div>`;
    const labelStr = esc(String(w.label||''));
    const slugStr = esc(String(w.slug||'').slice(-14));
    const startTs = w.start_ts ? new Date(w.start_ts*1000).toLocaleTimeString('he-IL',{hour:'2-digit',minute:'2-digit'}) : '-';
    tbl+=`<tr><td style="font-weight:700">${labelStr}</td><td class="mono" style="font-size:12px">${slugStr}<div style="font-size:10px;color:var(--faint)">${startTs}</div></td><td><span class="price-up">${openUp}</span> | <span class="price-down">${openDown}</span></td><td><span class="price-up">${upHigh}</span> (+${upExc}¢)</td><td><span class="price-down">${downHigh}</span> (+${downExc}¢)</td><td>${candle}</td><td>${clsPill(w.class)}</td><td><a href="${esc(w.url||'#')}" target="_blank" rel="noopener" style="font-size:12px;font-weight:700">פתח ↗</a></td></tr>`;
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
    const pairCost = getVal('btPairCost', 0.995);
    const exit5m = getVal('btExit5m', 0.12);
    const exit15m = getVal('btExit15m', 0.13);
    const exitBtc = getVal('btExitBtc', 0.09);
    const exitSol = getVal('btExitSol', 0.11);
    const fillModel = $('btFillModel') ? $('btFillModel').value : 'tape';
    const size = Math.round(getVal('btSize', 120));
    const gas = getVal('btGas', 0.05);

    const fileVal = fileOverride !== undefined ? fileOverride : ($('btFileSelect') ? $('btFileSelect').value : (window.selectedBacktestFile || ''));
    if (fileOverride !== undefined && $('btFileSelect')) {
      $('btFileSelect').value = fileOverride;
    }

    let url = `/api/backtest?offset=${offset}&queue=${queue}&pair_cost=${pairCost}&exit_default_5m=${exit5m}&exit_default_15m=${exit15m}&exit_btc_5m=${exitBtc}&exit_sol_5m=${exitSol}&fill_model=${fillModel}&size=${size}&gas=${gas}`;
    if (fileVal) {
      url += `&file=${encodeURIComponent(fileVal)}`;
    }
    const res = await fetch(url);
    const data = await res.json();

    $('btHash').textContent = `Hash: ${data.params_hash} · ${data.n_windows} חלונות${fileVal ? ' · [' + fileVal + ']' : ''}`;
    const ov = data.overall || {};
    $('btTotalPnl').textContent = ((ov.total_pnl_cents||0)>=0?'+':'') + (ov.total_pnl_cents||0).toFixed(2) + '¢';
    $('btTotalPnl').style.color = (ov.total_pnl_cents||0)>=0 ? 'var(--up)' : 'var(--down)';
    $('btAvgPnl').textContent = ((ov.avg_pnl_cents||0)>=0?'+':'') + (ov.avg_pnl_cents||0).toFixed(2) + '¢ לחלון';
    $('btPairRate').textContent = ((ov.pair_rate||0)*100).toFixed(1) + '%';
    $('btPairsCount').textContent = `${ov.pairs||0} / ${ov.windows||0} זוגות`;
    $('btExitRate').textContent = ((ov.exit_rate||0)*100).toFixed(1) + '%';
    $('btExitsCount').textContent = `${ov.exits||0} יציאות`;
    $('btMaxDd').textContent = '-' + (ov.max_drawdown_cents||0).toFixed(2) + '¢';
    $('btWinRate').textContent = ((ov.win_rate||0)*100).toFixed(1) + '%';

    // Chart
    const eq = data.equity_curve || [];
    const ctx = $('chartEquity').getContext('2d');
    if(equityChartInstance) equityChartInstance.destroy();
    equityChartInstance = new Chart(ctx, {
      type: 'line',
      data: {
        labels: eq.map(e=>e.window_idx),
        datasets: [{
          label: 'PnL מצטבר (סנטים)',
          data: eq.map(e=>e.cumulative_pnl_cents),
          borderColor: '#33c9b5',
          backgroundColor: 'rgba(51,201,181,0.08)',
          fill: true,
          tension: 0.1,
          pointRadius: 0
        }]
      },
      options: {
        responsive: true,
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { color: '#8792a6' }, grid: { color: '#232a35' } },
          y: { ticks: { color: '#8792a6' }, grid: { color: '#232a35' } }
        }
      }
    });

    // Per series table
    let stbl = '<table class="tbl"><tr><th>סדרה</th><th>חלונות</th><th>לכידת זוג (Pair)</th><th>יציאות (Exits)</th><th>PnL כולל</th><th>ממוצע לחלון</th><th>Oscillating</th><th>Monotonic</th></tr>';
    for(const [k,v] of Object.entries(data.per_series||{})){
      stbl+=`<tr><td style="font-weight:700">${esc(v.label)}</td><td>${v.windows}</td><td style="color:var(--up);font-weight:700">${(v.pair_rate*100).toFixed(1)}% (${v.pairs})</td><td style="color:var(--down)">${(v.exit_rate*100).toFixed(1)}% (${v.exits})</td><td class="mono" style="font-weight:700;color:${v.total_pnl_cents>=0?'var(--up)':'var(--down)'}">${v.total_pnl_cents>=0?'+':''}${v.total_pnl_cents.toFixed(2)}¢</td><td class="mono">${v.avg_pnl_cents>=0?'+':''}${v.avg_pnl_cents.toFixed(2)}¢</td><td>${v.oscillating}</td><td>${v.monotonic}</td></tr>`;
    }
    stbl+='</table>';
    $('btSeriesTableWrap').innerHTML=stbl;

    // Trades sample table
    let ttbl = '<table class="tbl"><tr><th>חלון</th><th>סדרה</th><th>תוצאה</th><th>סטטוס מילוי</th><th>PnL לחלון</th><th>סיבת יציאה</th></tr>';
    for(const t of (data.trades_sample||[]).slice(0,30)){
      const resPill = t.both_filled ? pill('pill-osc','PAIR CAPTURED +4¢') : t.exit_triggered ? pill('pill-mono','EXIT TRIGGERED') : pill('pill-flat','FLAT / UNRESOLVED');
      ttbl+=`<tr><td class="mono" style="font-size:11px">${esc(t.slug.slice(-14))}</td><td style="font-weight:600">${esc(t.label)}</td><td>${resPill}</td><td class="mono" style="font-size:11px">${t.both_filled?'UP+DOWN':t.up_filled?'UP only':t.down_filled?'DOWN only':'-'}</td><td class="mono" style="font-weight:700;color:${t.pnl_cents>=0?'var(--up)':'var(--down)'}">${t.pnl_cents>=0?'+':''}${t.pnl_cents.toFixed(2)}¢</td><td style="font-size:11px;color:var(--dim)">${esc(t.exit_reason||'-')}</td></tr>`;
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
  $('btQueue').value = "50";
  $('btPairCost').value = "0.995";
  $('btExit5m').value = "0.12";
  $('btExit15m').value = "0.13";
  $('btExitBtc').value = "0.09";
  $('btExitSol').value = "0.11";
  $('btFillModel').value = "tape";
  $('btSize').value = "120";
  $('btGas').value = "0.05";
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
  const bLabels=['0-10¢','10-20¢','20-30¢','30-40¢','40-50¢'];
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
  const sBuckets=['0-1¢','1-2¢','2-5¢','5-10¢','10¢+'];
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
      let opts = '<option value="">כל הקבצים / 2,820 חלונות (ברירת מחדל)</option>';
      for(const f of d.files){
        const linesFormatted = (f.lines||0).toLocaleString();
        const estPrefix = f.lines_estimated ? '~' : '';
        opts += `<option value="${esc(f.name)}">${esc(f.name)} (${estPrefix}${linesFormatted} שורות)</option>`;
      }
      sel.innerHTML = opts;
      if(currentVal && Array.from(sel.options).some(o => o.value === currentVal)){
        sel.value = currentVal;
      }
    }

    let tbl = '<table class="tbl"><tr><th>שם קובץ</th><th>גודל</th><th>שורות / דגימות</th><th>עדכון אחרון</th><th>פעולות</th></tr>';
    if(!d.files || d.files.length === 0){
      tbl += '<tr><td colspan="5" style="text-align:center;color:var(--faint);padding:18px">אין קבצים בתיקיית ticks/</td></tr>';
    } else {
      for(const f of d.files){
        const mb = (f.bytes/(1024*1024)).toFixed(2)+' MB';
        const linesFormatted = (f.lines||0).toLocaleString();
        const linesCell = f.lines_estimated
          ? `~${linesFormatted} <span style="color:var(--dim);font-size:10px">(הערכה)</span>`
          : linesFormatted;
        tbl+=`<tr>
          <td class="mono" style="font-weight:700">${esc(f.name)}</td>
          <td class="mono">${mb}</td>
          <td class="mono">${linesCell}</td>
          <td class="mono">${new Date(f.mtime*1000).toLocaleString('he-IL')}</td>
          <td style="display:flex;gap:6px;align-items:center">
            <button class="btn" style="padding:4px 10px;font-size:11px" onclick="runBacktestOnFile('${esc(f.name)}')">הרץ בקטסט ⚡</button>
            <button class="btn" style="padding:4px 10px;font-size:11px;background:rgba(255,87,87,0.12);color:var(--down);border-color:rgba(255,87,87,0.3);cursor:pointer" onclick="deleteTickFile('${esc(f.name)}')">🗑️ מחק</button>
          </td>
        </tr>`;
      }
    }
    tbl+='</table>';
    $('manifestTableWrap').innerHTML = tbl;
  }catch(err){
    $('manifestTableWrap').innerHTML = '<div style="color:var(--down);padding:12px">שגיאה בטעינת רשימת הקבצים</div>';
  }
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

function setupBacktestInputListeners(){
  const inputIds = [
    'btOffset', 'btQueue', 'btPairCost', 'btExit5m',
    'btExit15m', 'btExitBtc', 'btExitSol', 'btFillModel',
    'btSize', 'btGas', 'btFileSelect'
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

setupBacktestInputListeners();

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

