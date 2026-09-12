"""Shared window finalization, classification, and summary logic.

Single source of truth used by both the live tick collector
(`scripts/collect_ticks.py`) and the offline rebuild
(`scripts/rebuild_windows.py`), so both paths produce byte-identical
window records. Classification math (base 0.50, 0.02 threshold) is frozen.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from strategy.series import SERIES


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


def finalize_window(
    mids: list[float], touch_pairs: list[float], meta: dict[str, Any]
) -> dict[str, Any]:
    """Build one window record from accumulated mids and touch pairs.

    Reproduces exactly the record schema emitted by
    `build_windows_from_ticks` (4-decimal rounding, Polymarket URL format).
    `meta` must carry: series, label, duration, cid, slug, start_ts,
    end_ts, closed_ts, snaps.
    """
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

    slug = meta.get("slug", "")
    url = f"https://polymarket.com/market/{slug}" if slug else ""

    return {
        "series": meta.get("series", ""),
        "label": meta.get("label", ""),
        "duration": meta.get("duration", 300),
        "cid": meta.get("cid", ""),
        "slug": slug,
        "start_ts": meta.get("start_ts", 0.0),
        "end_ts": meta.get("end_ts", 0.0),
        "closed_ts": meta.get("closed_ts", 0.0),
        "snaps": meta.get("snaps", 0),
        "start_mid": start_mid,
        "close_mid": close_mid,
        "max_up": max_up,
        "max_down": max_down,
        "min_mid": min_mid,
        "max_mid": max_mid,
        "class": cls,
        "touch_pair_median": tp_med,
        "url": url,
    }


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


def write_json_atomic(path: Path, data: Any) -> None:
    """Write JSON to path atomically via temp file + os.replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
