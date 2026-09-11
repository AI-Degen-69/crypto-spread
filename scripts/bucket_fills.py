"""Bucket live fill telemetry by fill_ratio and report subsequent-window PnL.

Reads per-fill queue-position lines (issue #138) and joins each fill's window
settlement PnL, printing the live tape-vs-tapeq verdict table.

Usage:
  python -m scripts.bucket_fills run/live_fill_telemetry.jsonl
  python -m scripts.bucket_fills run/live_fill_telemetry.jsonl --trades run/live_trades.jsonl
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FILLS = ROOT / "run" / "live_fill_telemetry.jsonl"
DEFAULT_TRADES = ROOT / "run" / "live_trades.jsonl"

BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("0.00-0.25", 0.0, 0.25),
    ("0.25-0.50", 0.25, 0.5),
    ("0.50-1.00", 0.5, 1.0),
    ("1.00+", 1.0, math.inf),
)


def _read_jsonl(path: Path) -> list[dict]:
    """Read a JSONL file into dicts, skipping blanks and bad lines."""
    rows: list[dict] = []
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _settlement_pnl_by_market(trades_path: Path) -> dict[str, float]:
    """market_slug -> summed WINDOW_SETTLE pnl_usd (one window may settle once)."""
    pnl: dict[str, float] = {}
    for t in _read_jsonl(trades_path):
        if t.get("action") != "WINDOW_SETTLE":
            continue
        slug = str(t.get("market_slug") or "")
        if not slug:
            continue
        try:
            pnl[slug] = pnl.get(slug, 0.0) + float(t.get("pnl_usd") or 0)
        except (TypeError, ValueError):
            continue
    return pnl


def bucketize(fills: list[dict], settle: dict[str, float]) -> list[dict[str, Any]]:
    """Group fills with a usable ratio into buckets with mean subsequent PnL."""
    table: list[dict[str, Any]] = [
        {"bucket": name, "count": 0, "pnl_sum": 0.0, "pnl_n": 0}
        for name, _, _ in BUCKETS
    ]
    for f in fills:
        ratio = f.get("fill_ratio")
        if not isinstance(ratio, (int, float)) or isinstance(ratio, bool):
            continue
        if ratio < 0:
            continue
        for i, (_, lo, hi) in enumerate(BUCKETS):
            if lo <= ratio < hi or (hi == math.inf and ratio >= lo):
                table[i]["count"] += 1
                pnl = settle.get(str(f.get("market_slug") or ""))
                if pnl is not None:
                    table[i]["pnl_sum"] += pnl
                    table[i]["pnl_n"] += 1
                break
    out = []
    for row in table:
        mean = row["pnl_sum"] / row["pnl_n"] if row["pnl_n"] else None
        out.append({"bucket": row["bucket"], "count": row["count"],
                    "mean_settle_pnl_usd": mean})
    return out


def format_table(table: list[dict[str, Any]]) -> str:
    """Render bucket rows as an aligned text table."""
    lines = ["bucket    fills  mean_settle_pnl_usd"]
    for row in table:
        mean = row["mean_settle_pnl_usd"]
        mean_txt = f"{mean:+.2f}" if mean is not None else "n/a"
        lines.append(f"{row['bucket']:<9} {row['count']:>5}  {mean_txt:>19}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry: bucket fills, print the verdict table, exit 0 (even if empty)."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("fills", nargs="?", default=str(DEFAULT_FILLS),
                    help="fill telemetry JSONL (default: run/live_fill_telemetry.jsonl)")
    ap.add_argument("--trades", default=str(DEFAULT_TRADES),
                    help="live trades JSONL for settlement PnL")
    args = ap.parse_args(argv)
    fills = _read_jsonl(args.fills)
    if not fills:
        print(f"No fill telemetry in {args.fills} — nothing to bucket.")
        return 0
    settle = _settlement_pnl_by_market(Path(args.trades))
    print(format_table(bucketize(fills, settle)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
