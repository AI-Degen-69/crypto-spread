"""Rebuild oscillation_windows.jsonl and oscillation_summary.json from real run/ticks data.

Scans all tick files in run/ticks (full-depth CLOB snapshots), aggregates per-window
metrics (mids, touch pairs, max excursion, classification), and writes clean
run/oscillation_windows.jsonl and run/oscillation_summary.json for the dashboard.

Usage:
  python -m scripts.rebuild_windows
  python -m scripts.rebuild_windows --ticks-dir run/ticks
  python -m scripts.rebuild_windows --out-windows run/oscillation_windows.jsonl
"""
from __future__ import annotations

import argparse
import gzip
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from strategy.series import SERIES

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TICKS_DIR = ROOT / "run" / "ticks"
DEFAULT_WINDOWS_FILE = ROOT / "run" / "oscillation_windows.jsonl"
DEFAULT_SUMMARY_FILE = ROOT / "run" / "oscillation_summary.json"


def classify_window(mids: list[float]) -> str:
    """Classify 5m/15m window based on mid price excursions from 0.50 base."""
    if not mids:
        return "no_data"
    base = 0.50
    max_up = max(mids) - base
    max_down = base - min(mids)
    up2 = max_up >= 0.02
    down2 = max_down >= 0.02
    if up2 and down2:
        return "oscillating"
    if up2 or down2:
        return "monotonic"
    return "flat"


def _open_tick_file(path: Path):
    """Open .jsonl or .jsonl.gz file in text mode."""
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, "r", encoding="utf-8", errors="replace")


def iter_ticks(file_paths: Iterable[Path]) -> Iterable[dict[str, Any]]:
    """Yield parsed tick dicts from an iterable of file paths."""
    for path in file_paths:
        if not path.is_file():
            continue
        try:
            with _open_tick_file(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except Exception:
                        continue
        except Exception:
            continue


def build_windows_from_ticks(
    tick_records: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Aggregate raw tick records into window summary records."""
    windows: dict[str, dict[str, Any]] = {}

    for tick in tick_records:
        cid = tick.get("cid")
        if not cid:
            continue

        if cid not in windows:
            windows[cid] = {
                "series": tick.get("series", ""),
                "label": tick.get("label", ""),
                "duration": tick.get("duration", 300),
                "cid": cid,
                "slug": tick.get("slug", ""),
                "start_ts": tick.get("start_ts", 0.0),
                "end_ts": tick.get("end_ts", 0.0),
                "last_ts": tick.get("ts", 0.0),
                "mids": [],
                "touch_pairs": [],
                "snap_count": 0,
            }

        w = windows[cid]
        w["snap_count"] += 1
        ts = tick.get("ts", 0.0)
        if ts > w["last_ts"]:
            w["last_ts"] = ts
        if not w["slug"] and tick.get("slug"):
            w["slug"] = tick.get("slug")
        if not w["series"] and tick.get("series"):
            w["series"] = tick.get("series")
        if not w["label"] and tick.get("label"):
            w["label"] = tick.get("label")
        if not w["duration"] and tick.get("duration"):
            w["duration"] = tick.get("duration")
        if not w["start_ts"] and tick.get("start_ts"):
            w["start_ts"] = tick.get("start_ts")
        if not w["end_ts"] and tick.get("end_ts"):
            w["end_ts"] = tick.get("end_ts")

        mid = tick.get("mid")
        if mid is not None:
            try:
                w["mids"].append(float(mid))
            except (ValueError, TypeError):
                pass

        touch_pair = tick.get("touch_pair")
        if touch_pair is not None:
            try:
                w["touch_pairs"].append(float(touch_pair))
            except (ValueError, TypeError):
                pass

    results: list[dict[str, Any]] = []
    for cid, w in windows.items():
        mids = w["mids"]
        touch_pairs = w["touch_pairs"]

        if mids:
            start_mid = round(mids[0], 4)
            close_mid = round(mids[-1], 4)
            min_mid = round(min(mids), 4)
            max_mid = round(max(mids), 4)
            max_up = round(max(mids) - 0.50, 4)
            max_down = round(0.50 - min(mids), 4)
            cls = classify_window(mids)
        else:
            start_mid = close_mid = min_mid = max_mid = None
            max_up = max_down = 0.0
            cls = "no_data"

        tp_med = (
            round(sorted(touch_pairs)[len(touch_pairs) // 2], 4)
            if touch_pairs
            else None
        )

        slug = w["slug"]
        url = f"https://polymarket.com/market/{slug}" if slug else ""

        results.append({
            "series": w["series"],
            "label": w["label"],
            "duration": w["duration"],
            "cid": cid,
            "slug": slug,
            "start_ts": w["start_ts"],
            "end_ts": w["end_ts"],
            "closed_ts": w["last_ts"],
            "snaps": w["snap_count"],
            "start_mid": start_mid,
            "close_mid": close_mid,
            "max_up": max_up,
            "max_down": max_down,
            "min_mid": min_mid,
            "max_mid": max_mid,
            "class": cls,
            "touch_pair_median": tp_med,
            "url": url,
        })

    # Sort chronologically by end_ts ascending
    results.sort(key=lambda x: (x.get("end_ts") or 0.0, x.get("start_ts") or 0.0))
    return results


def compute_summary(windows_list: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute per-series aggregate summary matching measure_5m_oscillation schema."""
    per_series = defaultdict(list)
    for w in windows_list:
        per_series[w["series"]].append(w)

    summary = {}
    for series_slug, duration, label in SERIES:
        ws = per_series.get(series_slug, [])
        n = len(ws)
        if n == 0:
            summary[series_slug] = {
                "label": label,
                "duration": duration,
                "windows": 0,
                "any_2c": 0,
                "any_3c": 0,
                "oscillating": 0,
                "monotonic": 0,
                "flat": 0,
                "pair_cost_median": None,
                "recent": [],
            }
            continue

        any2 = sum(1 for w in ws if max(w["max_up"], w["max_down"]) >= 0.02)
        any3 = sum(1 for w in ws if max(w["max_up"], w["max_down"]) >= 0.03)
        mono = sum(1 for w in ws if w["class"] == "monotonic")
        flat = sum(1 for w in ws if w["class"] == "flat")
        osc = sum(1 for w in ws if w["class"] == "oscillating")

        pcs = [
            w.get("touch_pair_median")
            for w in ws
            if w.get("touch_pair_median") is not None
        ]
        pcs_median = sorted(pcs)[len(pcs) // 2] if pcs else None

        # Last 10 windows, newest first
        recent = sorted(ws, key=lambda x: x.get("end_ts", 0), reverse=True)[:10]

        summary[series_slug] = {
            "label": label,
            "duration": duration,
            "windows": n,
            "any_2c": any2,
            "any_3c": any3,
            "oscillating": osc,
            "monotonic": mono,
            "flat": flat,
            "pair_cost_median": pcs_median,
            "recent": recent,
        }

    return {"ts": time.time(), "per_series": summary}


def rebuild_windows(
    ticks_dir: Path = DEFAULT_TICKS_DIR,
    out_windows: Path = DEFAULT_WINDOWS_FILE,
    out_summary: Path = DEFAULT_SUMMARY_FILE,
    pattern: str = "ticks_*.jsonl*",
    quiet: bool = False,
) -> tuple[int, int]:
    """Scan tick files and write oscillation_windows.jsonl and oscillation_summary.json.

    Returns (num_files_scanned, num_windows_built).
    """
    tick_files = sorted(
        [p for p in ticks_dir.glob(pattern) if not p.name.endswith(".idx")]
    )
    if not tick_files:
        # Fallback to any .jsonl / .jsonl.gz if no ticks_*.jsonl matches
        tick_files = sorted(
            [
                p
                for p in ticks_dir.glob("*.jsonl*")
                if not p.name.endswith(".idx") and not p.name.startswith("fake_")
            ]
        )

    if not quiet:
        print(f"Found {len(tick_files)} tick files in {ticks_dir}")

    windows = build_windows_from_ticks(iter_ticks(tick_files))

    out_windows.parent.mkdir(parents=True, exist_ok=True)
    with open(out_windows, "w", encoding="utf-8") as f:
        for w in windows:
            f.write(json.dumps(w) + "\n")

    summary = compute_summary(windows)
    out_summary.parent.mkdir(parents=True, exist_ok=True)
    out_summary.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    if not quiet:
        print(
            f"Wrote {len(windows)} windows to {out_windows} and summary to {out_summary}"
        )

    return len(tick_files), len(windows)


def main():
    """Rebuild oscillation_windows.jsonl and oscillation_summary.json from tick files."""
    parser = argparse.ArgumentParser(
        description="Rebuild oscillation windows and summary from run/ticks data"
    )
    parser.add_argument(
        "--ticks-dir",
        type=Path,
        default=DEFAULT_TICKS_DIR,
        help=f"Directory containing tick files (default: {DEFAULT_TICKS_DIR})",
    )
    parser.add_argument(
        "--out-windows",
        type=Path,
        default=DEFAULT_WINDOWS_FILE,
        help=f"Path to output windows jsonl (default: {DEFAULT_WINDOWS_FILE})",
    )
    parser.add_argument(
        "--out-summary",
        type=Path,
        default=DEFAULT_SUMMARY_FILE,
        help=f"Path to output summary json (default: {DEFAULT_SUMMARY_FILE})",
    )
    parser.add_argument(
        "--pattern",
        type=str,
        default="ticks_*.jsonl*",
        help="Glob pattern for tick files (default: ticks_*.jsonl*)",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="Suppress progress output"
    )

    args = parser.parse_args()
    rebuild_windows(
        ticks_dir=args.ticks_dir,
        out_windows=args.out_windows,
        out_summary=args.out_summary,
        pattern=args.pattern,
        quiet=args.quiet,
    )


if __name__ == "__main__":
    main()
