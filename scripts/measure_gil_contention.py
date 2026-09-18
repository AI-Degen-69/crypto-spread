"""Empirical measurement of GIL contention on live trading ticks during backtest sweeps (Issue #221).

Measures live tick interval distributions:
1. Baseline distribution while engine is idle.
2. Contention distribution while a CPU-heavy replay runs in a threadpool worker via api_backtest.
3. Compares p50 / p95 / max and reports whether GIL delay is negligible for 5m window entry timing.
"""
from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import json
import logging
import math
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategy.live_trader import LiveTraderEngine
from server.osc_dash import api_backtest, shutdown_backtest_pool, TICKS_DIR

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")


def _find_default_tick_file() -> Optional[str]:
    """Find the best available tick file in run/ticks/ for replay load."""
    if not TICKS_DIR.exists():
        return None
    candidates = sorted(TICKS_DIR.glob("ticks_*.jsonl"), key=lambda p: p.stat().st_size, reverse=True)
    for c in candidates:
        if c.stat().st_size > 100_000:  # Prefer files with real data > 100KB
            return c.name
    return candidates[0].name if candidates else None


def _make_synthetic_poll(slug: str, now: float) -> Dict[str, Any]:
    """Generate realistic market snapshot for live strategy update without network overhead."""
    t_mod = (now % 300)
    drift = math.sin(now / 15.0) * 0.04
    mid_up = max(0.10, min(0.90, 0.50 + drift))
    mid_dn = round(1.0 - mid_up, 4)

    return {
        "market": {
            "conditionId": f"0xcond_{slug}",
            "slug": slug,
            "start_ts": now - t_mod,
            "end_ts": now - t_mod + 300.0,
            "up_token": f"tok_up_{slug}",
            "down_token": f"tok_dn_{slug}",
            "series": slug,
        },
        "next_market": {
            "conditionId": f"0xnext_{slug}",
            "slug": f"next_{slug}",
            "start_ts": now - t_mod + 300.0,
            "end_ts": now - t_mod + 600.0,
            "up_token": f"tok_next_up_{slug}",
            "down_token": f"tok_next_dn_{slug}",
            "series": slug,
        },
        "up_book": {
            "bids": [{"price": round(mid_up - 0.01, 3), "size": 100.0}],
            "asks": [{"price": round(mid_up + 0.01, 3), "size": 100.0}],
        },
        "down_book": {
            "bids": [{"price": round(mid_dn - 0.01, 3), "size": 100.0}],
            "asks": [{"price": round(mid_dn + 0.01, 3), "size": 100.0}],
        },
    }


async def run_benchmark(
    idle_ticks: int = 30,
    duration: Optional[int] = None,
    tick_file: Optional[str] = None,
    output_path: Optional[str] = None,
    verbose: bool = True,
) -> Dict[str, Any]:
    """Execute the two-phase GIL contention measurement."""
    if idle_ticks < 2:
        raise ValueError("idle_ticks must be at least 2 to compute interval timing")

    if tick_file is None:
        tick_file = _find_default_tick_file()

    if not tick_file or not (TICKS_DIR / tick_file).exists():
        raise FileNotFoundError(f"No valid tick file found in {TICKS_DIR}. Required for backtest load.")

    file_size_mb = round((TICKS_DIR / tick_file).stat().st_size / (1024 * 1024), 2)
    if verbose:
        print("=" * 80)
        print("GIL CONTENTION MEASUREMENT — LIVE TICK VS BACKTEST SWEEP (Issue #221)")
        print("=" * 80)
        print(f"Target replay file: {tick_file} ({file_size_mb} MB)")
        print(f"Idle calibration:   {idle_ticks} ticks (target interval: 1000.00 ms)")
        if duration is not None:
            print(f"Stress duration:    {duration} ticks limit")
        print("-" * 80)

    engine = LiveTraderEngine(load_persisted=False)
    loop = asyncio.get_running_loop()

    # Monkeypatch poll_single_market to isolate pure GIL contention from network jitter
    def synthetic_poll(slug: str) -> Dict[str, Any]:
        """Generate synthetic market poll snapshot for deterministic timing."""
        return _make_synthetic_poll(slug, time.time())

    engine._poll_single_market = synthetic_poll  # type: ignore[assignment]

    # -------------------------------------------------------------------------
    # PHASE 1: Baseline Idle Measurement
    # -------------------------------------------------------------------------
    if verbose:
        print("[Phase 1] Collecting baseline tick intervals with idle system...")

    engine.reset_tick_timing_stats()
    start_idle_t = time.perf_counter()

    for i in range(idle_ticks):
        await engine._tick_all_markets()
        await asyncio.sleep(1.0)
        if verbose and (i + 1) % 10 == 0:
            print(f"  Completed {i + 1}/{idle_ticks} idle ticks...")

    idle_stats = engine.get_tick_timing_stats()
    idle_duration = time.perf_counter() - start_idle_t

    if verbose:
        print(f"  Phase 1 done in {idle_duration:.2f}s. Samples: {idle_stats['count']}")
        print(f"  Idle: p50={idle_stats['p50_ms']}ms | p95={idle_stats['p95_ms']}ms | max={idle_stats['max_ms']}ms")
        print("-" * 80)

    # -------------------------------------------------------------------------
    # PHASE 2: Stress Measurement under Concurrent Backtest Load
    # -------------------------------------------------------------------------
    if verbose:
        print(f"[Phase 2] Launching backtest sweep ({tick_file}) in worker thread...")

    engine.reset_tick_timing_stats()
    backtest_future: Optional[concurrent.futures.Future] = None
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    def run_sync_backtest():
        """Run backtest simulation in dedicated process via api_backtest."""
        res = api_backtest(file=tick_file, offset=0.02, queue=0.0)
        if asyncio.iscoroutine(res):
            return asyncio.run(res)
        return res

    t_bt_start = time.perf_counter()
    backtest_future = executor.submit(run_sync_backtest)

    stress_ticks = 0
    while not backtest_future.done():
        await engine._tick_all_markets()
        stress_ticks += 1
        if duration is not None and stress_ticks >= duration:
            break
        await asyncio.sleep(1.0)
        if verbose and stress_ticks % 5 == 0:
            print(f"  Under backtest: {stress_ticks} live ticks elapsed...")

    bt_duration = time.perf_counter() - t_bt_start
    bt_result = backtest_future.result() if backtest_future.done() else {}
    executor.shutdown(wait=backtest_future.done())

    n_snaps = bt_result.get("n_snaps", 0) if isinstance(bt_result, dict) else 0
    n_windows = bt_result.get("n_windows", 0) if isinstance(bt_result, dict) else 0

    contention_stats = engine.get_tick_timing_stats()

    if verbose:
        print(f"  Backtest completed in {bt_duration:.2f}s ({n_snaps} snaps, {n_windows} windows).")
        print(f"  Live ticks captured under load: {contention_stats['count']}")
        print(f"  Under Load: p50={contention_stats['p50_ms']}ms | p95={contention_stats['p95_ms']}ms | max={contention_stats['max_ms']}ms")
        print("=" * 80)

    # -------------------------------------------------------------------------
    # PHASE 3: Comparison & Analysis
    # -------------------------------------------------------------------------
    if not idle_stats["count"] or not contention_stats["count"]:
        raise RuntimeError("Insufficient tick interval samples for comparison")

    delta_p50 = contention_stats["p50_ms"] - idle_stats["p50_ms"]
    delta_p95 = contention_stats["p95_ms"] - idle_stats["p95_ms"]
    delta_max = contention_stats["max_ms"] - idle_stats["max_ms"]

    # Verdict criteria:
    # A tick target is 1000ms. In a 5-minute (300s) window:
    # Jitter < 50ms is completely negligible (< 5% timing drift).
    # Jitter < 150ms is acceptable with zero missed intervals.
    # Jitter > 500ms indicates heavy GIL blockage.
    is_negligible = delta_p95 < 50.0 and delta_max < 250.0

    verdict_text = (
        "NEGLIGIBLE — GIL contention is well within tolerances (<50ms p95 delta). "
        "Running backtest sweeps concurrently with live trading on a threadpool is SAFE."
        if is_negligible
        else "NOTABLE — Backtest sweep introduced measurable GIL latency on live ticks."
    )

    report = {
        "timestamp": time.time(),
        "tick_file": tick_file,
        "file_size_mb": file_size_mb,
        "backtest_duration_sec": round(bt_duration, 2),
        "backtest_n_snaps": n_snaps,
        "backtest_n_windows": n_windows,
        "idle_baseline": idle_stats,
        "under_backtest": contention_stats,
        "deltas_ms": {
            "p50_ms": round(delta_p50, 2),
            "p95_ms": round(delta_p95, 2),
            "max_ms": round(delta_max, 2),
        },
        "verdict": {
            "is_negligible": is_negligible,
            "conclusion": verdict_text,
        },
    }

    if verbose:
        print("COMPARISON SUMMARY (ms)")
        print(f"{'Metric':<10} | {'Idle Baseline':<15} | {'Under Backtest':<15} | {'Delta':<10}")
        print("-" * 58)
        print(f"{'Count':<10} | {idle_stats['count']:<15} | {contention_stats['count']:<15} | -")
        print(f"{'p50':<10} | {idle_stats['p50_ms'] or 0.0:<15.2f} | {contention_stats['p50_ms'] or 0.0:<15.2f} | {delta_p50:+<10.2f}")
        print(f"{'p95':<10} | {idle_stats['p95_ms'] or 0.0:<15.2f} | {contention_stats['p95_ms'] or 0.0:<15.2f} | {delta_p95:+<10.2f}")
        print(f"{'Max':<10} | {idle_stats['max_ms'] or 0.0:<15.2f} | {contention_stats['max_ms'] or 0.0:<15.2f} | {delta_max:+<10.2f}")
        print("=" * 80)
        print(f"VERDICT: {verdict_text}")
        print("=" * 80)

    if output_path:
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(report, indent=2), encoding="utf-8")
        if verbose:
            print(f"Report saved to: {out_p}")

    shutdown_backtest_pool()
    return report


def main():
    """CLI entry point for empirical GIL contention benchmark."""
    parser = argparse.ArgumentParser(description="Empirical GIL Contention Benchmark (Issue #221)")
    parser.add_argument("--idle-ticks", type=int, default=30, help="Number of ticks for idle calibration (default: 30)")
    parser.add_argument("--duration", type=int, default=None, help="Max duration (stress ticks) to collect during backtest")
    parser.add_argument("--tick-file", type=str, default=None, help="Name of tick file in run/ticks/ to replay")
    parser.add_argument("--output", type=str, default=None, help="Path to write JSON benchmark report")
    parser.add_argument("--json", action="store_true", help="Print only JSON output")
    args = parser.parse_args()

    try:
        report = asyncio.run(
            run_benchmark(
                idle_ticks=args.idle_ticks,
                duration=args.duration,
                tick_file=args.tick_file,
                output_path=args.output,
                verbose=not args.json,
            )
        )
        if args.json:
            print(json.dumps(report, indent=2))
    finally:
        shutdown_backtest_pool()


if __name__ == "__main__":
    main()
