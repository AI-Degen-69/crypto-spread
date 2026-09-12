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
import os
from pathlib import Path
from typing import Any, Iterable

from strategy.windows import (
    classify_window,
    compute_summary,
    finalize_window,
    write_json_atomic,
)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TICKS_DIR = ROOT / "run" / "ticks"
DEFAULT_WINDOWS_FILE = ROOT / "run" / "oscillation_windows.jsonl"
DEFAULT_SUMMARY_FILE = ROOT / "run" / "oscillation_summary.json"


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
        meta = {
            "series": w["series"],
            "label": w["label"],
            "duration": w["duration"],
            "cid": cid,
            "slug": w["slug"],
            "start_ts": w["start_ts"],
            "end_ts": w["end_ts"],
            "closed_ts": w["last_ts"],
            "snaps": w["snap_count"],
        }
        results.append(
            finalize_window(w["mids"], w["touch_pairs"], meta)
        )

    # Sort chronologically by end_ts ascending
    results.sort(key=lambda x: (x.get("end_ts") or 0.0, x.get("start_ts") or 0.0))
    return results


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

    out_windows = Path(out_windows)
    out_windows.parent.mkdir(parents=True, exist_ok=True)
    tmp_windows = out_windows.with_name(out_windows.name + ".tmp")
    with open(tmp_windows, "w", encoding="utf-8") as f:
        for w in windows:
            f.write(json.dumps(w) + "\n")
    os.replace(tmp_windows, out_windows)

    summary = compute_summary(windows)
    write_json_atomic(Path(out_summary), summary)

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
