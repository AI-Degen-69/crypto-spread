"""Multi-Market Empirical Spot Drift and CLOB Lead-Lag Latency Audit Orchestrator.

Iterates the full 10-series crypto universe (BTC, ETH, BNB, SOL, XRP on 5m and 15m),
executes the empirical LatencyAuditor loop across each market, and produces
consolidated cross-venue comparison benchmarks and JSON artifacts.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from scripts.monitor_stream_latency import LatencyAuditor, run_monitor
from strategy.series import (
    SERIES,
    filter_series,
    supported_durations,
    supported_tokens,
    token_for_slug,
)
from strategy.streaming import RTDS_SYMBOLS


def parse_args(args: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse CLI command line flags for multi-market audit."""
    parser = argparse.ArgumentParser(
        description="Multi-market empirical spot drift and CLOB latency audit orchestrator."
    )
    parser.add_argument(
        "-d", "--duration",
        type=float,
        default=60.0,
        help="Audit duration in seconds per series (default: 60.0)",
    )
    parser.add_argument(
        "-t", "--ticks",
        type=int,
        default=0,
        help="Maximum tick snapshots per series before advancing (default: 0 = duration-driven)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.001,
        help="Drift threshold for shock detection (default: 0.001 = 0.10%%)",
    )
    parser.add_argument(
        "--tokens",
        nargs="+",
        default=None,
        help="Optional subset of token symbols to audit (e.g. BTC ETH SOL)",
    )
    parser.add_argument(
        "--durations",
        type=int,
        nargs="+",
        default=None,
        help="Optional subset of durations to audit (e.g. 300 900)",
    )
    parser.add_argument(
        "-o", "--output",
        type=str,
        default=None,
        help="Optional file path for JSON audit artifact output",
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress per-tick console streaming rows during audit runs",
    )
    return parser.parse_args(args)


def get_series_to_audit(
    tokens: Optional[List[str]] = None,
    durations: Optional[List[int]] = None,
) -> tuple[tuple[str, int, str], ...]:
    """Retrieve filtered series tuples (slug, duration_sec, label) to benchmark."""
    return filter_series(tokens=tokens, durations=durations)


def format_comparison_table(results: List[Dict[str, Any]]) -> str:
    """Format cross-market empirical latency comparison table for console and reports."""
    lines = [
        "=" * 138,
        "CROSS-MARKET EMPIRICAL LATENCY AUDIT BENCHMARK (Spot -> CLOB Book Response)",
        "=" * 138,
        f"{'Asset':<6} | {'Window':<6} | {'Transport':<9} | {'Bot Feed':<8} | {'Shocks':<6} | {'Reacted':<7} | {'Rate':<7} | "
        f"{'Min Lat':<10} | {'Med Lat':<10} | {'Mean Lat':<10} | {'P95 Lat':<10} | {'Mean Drift':<10} | {'P95 Drift':<10}",
        "-" * 138,
    ]

    for item in results:
        token = item.get("token", "")
        win = item.get("duration_label", "")
        transport = item.get("transport", "REST")
        bot_feed = item.get("bot_stream_mode", "RTDS" if f"{token.lower()}usdt" in RTDS_SYMBOLS else "REST")
        s = item.get("summary", {})
        shocks = s.get("total_shocks", 0)
        reacted = s.get("reaction_count", 0)
        rate = f"{s.get('reaction_rate_pct', 0.0):.1f}%" if shocks > 0 else "--"

        if reacted > 0:
            min_lat = f"{s.get('min_latency_ms', 0.0):.1f} ms"
            med_lat = f"{s.get('median_latency_ms', 0.0):.1f} ms"
            mean_lat = f"{s.get('mean_latency_ms', 0.0):.1f} ms"
            p95_lat = f"{s.get('p95_latency_ms', 0.0):.1f} ms"
        else:
            min_lat = med_lat = mean_lat = p95_lat = "--"

        if shocks > 0:
            mean_drift = f"{s.get('mean_drift_pct', 0.0) * 100:.2f}%"
            p95_drift = f"{s.get('p95_drift_pct', 0.0) * 100:.2f}%"
        else:
            mean_drift = p95_drift = "--"

        lines.append(
            f"{token:<6} | {win:<6} | {transport:<9} | {bot_feed:<8} | {shocks:<6} | {reacted:<7} | {rate:<7} | "
            f"{min_lat:<10} | {med_lat:<10} | {mean_lat:<10} | {p95_lat:<10} | {mean_drift:<10} | {p95_drift:<10}"
        )

    lines.append("=" * 138)
    lines.append(
        "Note: Probe samples Binance REST ticker & Polymarket CLOB books (1s poll). 'Bot Feed' column indicates live "
        "trading bridge stream mode (RTDS relay vs. BNB REST fallback path in strategy/streaming.py)."
    )
    lines.append("=" * 138)
    return "\n".join(lines)


def run_all_audits(
    tokens: Optional[List[str]] = None,
    durations: Optional[List[int]] = None,
    duration: float = 60.0,
    ticks: int = 0,
    threshold: float = 0.001,
    quiet: bool = False,
) -> List[Dict[str, Any]]:
    """Execute audit across selected series and collect benchmark metrics."""
    series_list = get_series_to_audit(tokens=tokens, durations=durations)
    results: List[Dict[str, Any]] = []

    try:
        for series_slug, dur_sec, label in series_list:
            token = token_for_slug(series_slug)
            dur_label = "5m" if dur_sec == 300 else f"{dur_sec // 60}m"
            symbol = f"{token.lower()}usdt"
            transport = "REST"
            bot_stream_mode = "RTDS" if symbol in RTDS_SYMBOLS else "REST"

            if not quiet:
                print(f"\n>>> Starting Latency Audit for {label} ({series_slug}) [Probe: {transport} | Bot: {bot_stream_mode}] for {duration:.0f}s ...")

            series_args = argparse.Namespace(
                series=series_slug,
                duration=duration,
                ticks=ticks,
                threshold=threshold,
                json=False,
                audit=True,
                quiet=quiet,
            )

            auditor = run_monitor(series_args, quiet=quiet)
            if isinstance(auditor, LatencyAuditor):
                summary = auditor.get_summary()
            else:
                summary = {
                    "total_shocks": 0,
                    "reaction_count": 0,
                    "reaction_rate_pct": 0.0,
                    "min_latency_ms": 0.0,
                    "median_latency_ms": 0.0,
                    "mean_latency_ms": 0.0,
                    "p95_latency_ms": 0.0,
                    "min_drift_pct": 0.0,
                    "median_drift_pct": 0.0,
                    "mean_drift_pct": 0.0,
                    "p95_drift_pct": 0.0,
                }

            results.append({
                "series": series_slug,
                "token": token,
                "duration_sec": dur_sec,
                "duration_label": dur_label,
                "transport": transport,
                "bot_stream_mode": bot_stream_mode,
                "summary": summary,
            })
    except KeyboardInterrupt:
        print("\n[!] Multi-market audit interrupted by user. Returning collected results so far.", file=sys.stderr)

    return results


def save_audit_artifact(
    results: List[Dict[str, Any]],
    threshold: float,
    output_path: Optional[str] = None,
) -> str:
    """Save aggregated audit results to JSON artifact file under logs/ directory."""
    if output_path:
        out_path = Path(output_path)
    else:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = Path("logs") / f"latency_audit_{ts}.json"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": datetime.datetime.now().isoformat(),
        "threshold": threshold,
        "series_audits": results,
    }

    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return str(out_path)


def main() -> None:
    """CLI application entry point for multi-market audit."""
    args = parse_args()
    results = run_all_audits(
        tokens=args.tokens,
        durations=args.durations,
        duration=args.duration,
        ticks=args.ticks,
        threshold=args.threshold,
        quiet=args.quiet,
    )

    table = format_comparison_table(results)
    print("\n" + table)

    artifact_file = save_audit_artifact(results, threshold=args.threshold, output_path=args.output)
    print(f"\nAudit results artifact saved to: {artifact_file}")


if __name__ == "__main__":
    main()
