"""Baseline measurement harness for the Backtest tab cost centres (Issue #371).

Measures, against a running dashboard on 127.0.0.1:5515:
1. `/api/oscillation` latency while the dashboard is idle.
2. `/api/oscillation` latency while a streaming backtest runs.
3. Stream progress cadence: gaps between progress envelopes, envelope sizes,
   and total run time.

Writes JSON results to docs/measurements/issue-371-backtest-tab.json so the
findings document (docs/issues/371-backtest-tab-lag-findings.md) can quote
numbers, and so the after-fix run can be compared like-for-like.

Usage:
    python -m scripts.measure_backtest_tab_cost --base http://127.0.0.1:5515

The harness never changes engine behaviour: it only issues read/param requests
the dashboard itself already issues. A meaningful stream measurement needs a
machine with the run/ticks corpus (the "All Files" scope replays run/ticks, not
the full golden corpus).
"""
from __future__ import annotations

import argparse
import json
import statistics
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "docs" / "measurements" / "issue-371-backtest-tab.json"

# Pinned stream parameters: a like-for-like before/after comparison needs the
# same file and filters on every run. Override with --file; empty means the
# server default selection ("All Files" over run/ticks).
PINNED_PARAMS: Dict[str, Any] = {
    "offset": 0.02,
    "queue": 0.0,
    "pair_cost": 0.99,
    "exit_default_5m": 0.05,
    "size": 5,
}


def _measure_poll_latency(base: str, count: int, timeout: float) -> Dict[str, Any]:
    """Replay the dashboard's own 3 s poll and time each /api/oscillation call."""
    latencies: List[float] = []
    errors = 0
    for _ in range(count):
        t0 = time.perf_counter()
        try:
            r = requests.get(f"{base}/api/oscillation", timeout=timeout,
                             headers={"cache-control": "no-store"})
            r.raise_for_status()
        except Exception:
            errors += 1
            continue
        latencies.append((time.perf_counter() - t0) * 1000.0)
        time.sleep(0.2)  # gentle cadence; not the dashboard's 3 s pace
    if not latencies:
        return {"count": 0, "errors": errors}
    return {
        "count": len(latencies),
        "errors": errors,
        "p50_ms": round(statistics.median(latencies), 1),
        "p95_ms": round(sorted(latencies)[int(0.95 * (len(latencies) - 1))], 1),
        "max_ms": round(max(latencies), 1),
    }


def _measure_stream(base: str, file: str, timeout: float,
                    concurrent_poll: bool) -> Dict[str, Any]:
    """Run one streaming backtest; record envelope cadence and sizes.

    With concurrent_poll=True a 3 s /api/oscillation poll runs alongside —
    the exact contention the dashboard's Backtest tab used to create.
    """
    params = dict(PINNED_PARAMS)
    if file:
        params["file"] = file
    gaps_ms: List[float] = []
    envelope_bytes: List[int] = []
    points_per_envelope: List[int] = []
    final_type: Optional[str] = None
    stop_polling = {"flag": False}
    poll_latencies: List[float] = []
    poll_errors = {"count": 0}

    def poll_loop() -> None:
        while not stop_polling["flag"]:
            t0 = time.perf_counter()
            try:
                requests.get(f"{base}/api/oscillation", timeout=timeout,
                             headers={"cache-control": "no-store"})
                poll_latencies.append((time.perf_counter() - t0) * 1000.0)
            except Exception:
                # A failed poll contributes no sample; count it so the report
                # cannot silently lose contention evidence.
                poll_errors["count"] += 1
            time.sleep(3.0)

    poller = None
    if concurrent_poll:
        poller = threading.Thread(target=poll_loop, daemon=True)
        poller.start()

    t_start = time.perf_counter()
    last_env = t_start
    try:
        with requests.get(f"{base}/api/backtest/stream", params=params,
                          stream=True, timeout=(3.05, timeout)) as resp:
            for raw_line in resp.iter_lines(decode_unicode=True):
                if not raw_line or not str(raw_line).startswith("data:"):
                    continue
                payload = str(raw_line)[5:].strip()
                if not payload:
                    continue
                try:
                    ev = json.loads(payload)
                except ValueError:
                    continue
                now = time.perf_counter()
                etype = ev.get("type")
                if etype == "progress":
                    gaps_ms.append((now - last_env) * 1000.0)
                    last_env = now
                    envelope_bytes.append(len(payload))
                    points_per_envelope.append(len(ev.get("points") or []))
                elif etype in ("final", "error"):
                    final_type = etype
                    break
    finally:
        stop_polling["flag"] = True
    total_s = time.perf_counter() - t_start

    out: Dict[str, Any] = {
        "file": file or "(server default: All Files / run/ticks)",
        "concurrent_poll": concurrent_poll,
        "final_type": final_type,
        "total_s": round(total_s, 1),
        "envelopes": len(gaps_ms),
        "points_per_envelope_max": max(points_per_envelope) if points_per_envelope else 0,
    }
    if gaps_ms:
        out["progress_gap_p50_ms"] = round(statistics.median(gaps_ms), 1)
        out["progress_gap_max_ms"] = round(max(gaps_ms), 1)
        out["envelope_bytes_max"] = max(envelope_bytes)
    if poll_latencies:
        out["poll_p50_ms_during_stream"] = round(statistics.median(poll_latencies), 1)
        out["poll_max_ms_during_stream"] = round(max(poll_latencies), 1)
    out["poll_errors_during_stream"] = poll_errors["count"]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://127.0.0.1:5515")
    ap.add_argument("--file", default="", help="tick file name; empty = All Files")
    ap.add_argument("--polls", type=int, default=10, help="idle poll samples")
    ap.add_argument("--timeout", type=float, default=900.0)
    ap.add_argument("--skip-stream", action="store_true", help="poll latency only")
    ap.add_argument("--out", default=str(OUT_PATH))
    args = ap.parse_args()

    result: Dict[str, Any] = {
        "issue": 371,
        "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "base": args.base,
        "dataset_scope": (
            "All Files replays run/ticks only, not the full golden corpus. "
            "Run on a machine that has the tick corpus for meaningful numbers."
        ),
    }
    result["poll_idle"] = _measure_poll_latency(args.base, args.polls, 30.0)
    if not args.skip_stream:
        result["stream_no_poll"] = _measure_stream(args.base, args.file, args.timeout, False)
        result["stream_with_poll"] = _measure_stream(args.base, args.file, args.timeout, True)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    print(f"\nwritten: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
