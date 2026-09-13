"""Bucket live fill telemetry by fill_ratio and report subsequent-window PnL.

Reads per-fill queue-position lines (issue #138) and joins each fill's window
settlement PnL, printing the live tape-vs-tapeq verdict table.

Usage:
  python -m scripts.bucket_fills run/live_fill_telemetry.jsonl
  python -m scripts.bucket_fills run/live_fill_telemetry.jsonl --trades run/live_trades.jsonl
  python -m scripts.bucket_fills run/live_fill_telemetry.jsonl --tape-source ws

Issue #173: `fill_ratio` is only comparable within one tape. The socket tape
sees nearly every print; the REST data-api tape was measured at ~1.4% capture
(issue #165), so a REST-sourced ratio is biased low by up to two orders of
magnitude. Pooling both into one bucket table averages two different
measurements of the world. `--tape-source` restricts the table to one of them;
lines written before the field existed carry no `tape_source` and are treated
as `rest`, which is what they were.
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
        fh = open(path, "r", encoding="utf-8")
    except OSError:
        return rows
    with fh:
        for line in fh:
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
    """market_slug -> that window's settlement PnL (last WINDOW_SETTLE wins).

    market_slug is unique per window, so the normal case has exactly one
    settle line; last-wins keeps a duplicated settle from inflating the join.
    """
    pnl: dict[str, float] = {}
    for t in _read_jsonl(trades_path):
        if t.get("action") != "WINDOW_SETTLE":
            continue
        slug = str(t.get("market_slug") or "")
        if not slug:
            continue
        try:
            pnl[slug] = float(t.get("pnl_usd") or 0)
        except (TypeError, ValueError):
            continue
    return pnl


TAPE_SOURCES: tuple[str, ...] = ("ws", "rest", "none")
LEGACY_TAPE_SOURCE = "rest"


def tape_source_of(fill: dict) -> str:
    """The tape that produced this line's `printed_size` (issue #173).

    Lines written before the field existed are `rest`: that is the only tape
    the sidecar ever read back then, so calling them anything else would
    misreport history.
    """
    raw = fill.get("tape_source")
    return raw if raw in TAPE_SOURCES else LEGACY_TAPE_SOURCE


def bucketize(fills: list[dict], settle: dict[str, float],
              tape_source: str | None = None) -> list[dict[str, Any]]:
    """Group fills with a finite, non-negative ratio into buckets.

    Each fill is attributed its own window's settlement PnL (joined on
    market_slug); fills without a matching settle still count. `tape_source`
    restricts the table to lines measured against one tape (issue #173);
    None pools every line, which is only meaningful when they all share a tape.
    """
    table: list[dict[str, Any]] = [
        {"bucket": name, "count": 0, "pnl_sum": 0.0, "pnl_n": 0}
        for name, _, _ in BUCKETS
    ]
    for f in fills:
        if tape_source is not None and tape_source_of(f) != tape_source:
            continue
        ratio = f.get("fill_ratio")
        if not isinstance(ratio, (int, float)) or isinstance(ratio, bool):
            continue
        if not math.isfinite(ratio) or ratio < 0:
            continue
        for i, (_, lo, hi) in enumerate(BUCKETS):
            if lo <= ratio < hi:
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


def source_counts(fills: list[dict]) -> dict[str, int]:
    """How many lines each tape produced, so a mixed file is visible at a glance."""
    counts = {name: 0 for name in TAPE_SOURCES}
    for f in fills:
        counts[tape_source_of(f)] += 1
    return counts


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
    ap.add_argument("--tape-source", choices=TAPE_SOURCES, default=None,
                    help="restrict to fills measured against one tape (issue "
                         "#173); lines predating the field count as 'rest'")
    args = ap.parse_args(argv)
    fills = _read_jsonl(args.fills)
    if not fills:
        print(f"No fill telemetry in {args.fills} — nothing to bucket.")
        return 0
    counts = source_counts(fills)
    mixed = sum(1 for n in counts.values() if n) > 1
    if args.tape_source is None and mixed:
        # Pooling two tapes that disagree by ~2 orders of magnitude produces a
        # table that describes neither of them. Say so rather than print it
        # silently — this is the exact bias issue #173 set out to remove.
        print("WARNING: mixed tape sources ("
              + ", ".join(f"{k}={v}" for k, v in counts.items() if v)
              + "); fill_ratio is not comparable across them. "
                "Re-run with --tape-source ws or --tape-source rest.")
    settle = _settlement_pnl_by_market(Path(args.trades))
    if args.tape_source is not None:
        print(f"tape_source={args.tape_source} "
              f"({counts[args.tape_source]} of {len(fills)} lines)")
    print(format_table(bucketize(fills, settle, args.tape_source)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
