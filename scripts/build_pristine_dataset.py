"""Build the pristine-window tick dataset from the existing capture days (issue #290).

Post-charter analysis of docs/golden-tick-dataset.md showed no historical day passes the
§1.2 day-level gates (boundary-round bursts put the sampling-gap rate at ~0.2-0.4 per day),
yet 87% of 5m and 67% of 15m windows are individually pristine. This tool harvests that
value from data already on disk: it scans every run/ticks/ticks_*.jsonl[.gz] day file,
verdicts each captured window against strict per-window continuity gates, and writes a
derived, quality-certified dataset containing only pristine windows.

Whole-window output: ALL tick lines of a surviving window are written, keyed to the
window's START day. The collector splits midnight-spanning windows across two day files;
the derived set must not, or the verifier would flag fake early cutoffs.

Sources are never modified. A re-run over unchanged inputs is byte-identical: no
wall-clock fields anywhere, deterministic ordering, manifest written last.

Usage:
  python -m scripts.build_pristine_dataset run/ticks --out run/ticks/pristine
  python -m scripts.build_pristine_dataset run/ticks --out run/ticks/pristine \
      --max-gap-sec 6.0 --max-start-delay-sec 5.0 --max-snap-interval-sec 3.0
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import math
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from scripts.rebuild_windows import iter_ticks
from scripts.ship_to_drive import sha256_of

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TICKS_DIR = ROOT / "run" / "ticks"
DEFAULT_OUT_DIR = DEFAULT_TICKS_DIR / "pristine"
MANIFEST_NAME = "pristine_manifest.json"

DAY_RE_PATTERN = r"^ticks_(\d{4}-\d{2}-\d{2})\.jsonl(\.gz)?$"

VERIFY_POLICY_NOTE = (
    "gate defaults mirror scripts/verify_tick_data.verify_window_continuity "
    "(6.0s gaps, 5.0s start delay); density floor adds >=1 snap per 3s of duration"
)


@dataclasses.dataclass(frozen=True)
class PristineGateParams:
    """Per-window continuity gates, frozen so thresholds cannot drift between windows.

    Defaults mirror verify_tick_data; CLI flags override for sensitivity analysis.
    """

    max_gap_sec: float = 6.0
    max_start_delay_sec: float = 5.0
    max_snap_interval_sec: float = 3.0


def min_snaps_for(duration: float, params: PristineGateParams) -> int:
    """Snap-density floor: >=1 snap per `max_snap_interval_sec` of window duration."""
    return math.ceil(duration / params.max_snap_interval_sec)


def evaluate_window_gates(ticks: list[dict[str, Any]], params: PristineGateParams) -> dict[str, Any]:
    """Verdict one window's ticks against every gate.

    Gate math mirrors scripts/verify_tick_data.verify_window_continuity; the density
    floor and the collector-error gate are the additions the issue specifies. `passed`
    and `failing_gates` are computed together so they can never disagree.
    """
    failing: list[str] = []
    if not ticks:
        return {
            "passed": False,
            "failing_gates": ["no_ticks"],
            "cid": "",
            "series": "",
            "slug": "",
            "duration": 0,
            "start_ts": 0.0,
            "end_ts": 0.0,
            "start_day": "",
            "tick_count": 0,
            "min_snaps": 0,
            "start_delay_sec": 0.0,
            "end_cutoff_sec": 0.0,
            "gaps_count": 0,
            "max_gap_sec": 0.0,
            "time_reversals": 0,
            "error_ticks": 0,
        }

    first = ticks[0]
    duration = first.get("duration", 300) or 300
    start_ts = first.get("start_ts", 0.0) or 0.0
    end_ts = first.get("end_ts", 0.0) or 0.0
    first_ts = first.get("ts", 0.0) or 0.0
    last_ts = ticks[-1].get("ts", 0.0) or 0.0

    start_delay = max(0.0, first_ts - start_ts) if start_ts > 0 else 0.0
    end_cutoff = max(0.0, end_ts - last_ts) if end_ts > 0 else 0.0

    gaps_count = 0
    max_gap = 0.0
    time_reversals = 0
    error_ticks = 0
    prev_ts = first_ts
    for tick in ticks:
        if tick.get("err"):
            error_ticks += 1
        ts = tick.get("ts", 0.0) or 0.0
        delta = ts - prev_ts
        if delta < 0:
            time_reversals += 1
        elif delta > params.max_gap_sec:
            gaps_count += 1
            max_gap = max(max_gap, delta)
        prev_ts = ts

    min_snaps = min_snaps_for(duration, params)

    if start_delay > params.max_start_delay_sec:
        failing.append("late_start")
    if end_cutoff > params.max_start_delay_sec:
        failing.append("early_cutoff")
    if gaps_count > 0:
        failing.append("sampling_gap")
    if time_reversals > 0:
        failing.append("time_reversal")
    if error_ticks > 0:
        failing.append("collector_error")
    if len(ticks) < min_snaps:
        failing.append("snap_density")

    return {
        "passed": not failing,
        "failing_gates": failing,
        "cid": first.get("cid", ""),
        "series": first.get("series", ""),
        "slug": first.get("slug", ""),
        "duration": duration,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "start_day": day_key_of(start_ts),
        "tick_count": len(ticks),
        "min_snaps": min_snaps,
        "start_delay_sec": round(start_delay, 2),
        "end_cutoff_sec": round(end_cutoff, 2),
        "gaps_count": gaps_count,
        "max_gap_sec": round(max_gap, 2),
        "time_reversals": time_reversals,
        "error_ticks": error_ticks,
    }


def day_key_of(ts: float) -> str:
    """The UTC day key of a unix timestamp (the collector's day-key convention)."""
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def evaluate_window_state(state: dict[str, Any], params: PristineGateParams) -> dict[str, Any]:
    """Verdict one aggregated window state (as built by scan_windows)."""
    verdict = evaluate_window_gates(state["ticks"], params)
    verdict["source_files"] = list(state["source_files"])
    return verdict


def scan_windows(sources: Iterable[tuple[str, Iterable[dict[str, Any]]]]) -> dict[str, dict[str, Any]]:
    """Aggregate ticks into one state per cid, merged across source files.

    The collector splits midnight-spanning windows across two day files; merging by cid
    here is what keeps the derived set whole-window. `ticks` are appended in stream
    (file, line) order so original line order is preserved; `source_files` is
    first-seen order, never a set (determinism).
    """
    states: dict[str, dict[str, Any]] = {}
    for source_name, ticks in sources:
        for tick in ticks:
            cid = tick.get("cid")
            if not cid:
                continue
            st = states.get(cid)
            if st is None:
                st = {
                    "cid": cid,
                    "ticks": [],
                    "tick_count": 0,
                    "source_files": [],
                    "_seen": set(),
                }
                states[cid] = st
            st["ticks"].append(tick)
            st["tick_count"] += 1
            if source_name not in st["_seen"]:
                st["_seen"].add(source_name)
                st["source_files"].append(source_name)
    for st in states.values():
        st.pop("_seen", None)
    return states
