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
import gzip
import json
import math
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from scripts.rebuild_windows import iter_ticks
from scripts.ship_to_drive import sha256_of
from strategy.windows import write_json_atomic

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TICKS_DIR = ROOT / "run" / "ticks"
DEFAULT_OUT_DIR = DEFAULT_TICKS_DIR / "pristine"
MANIFEST_NAME = "pristine_manifest.json"

DAY_RE = re.compile(r"^ticks_(\d{4}-\d{2}-\d{2})\.jsonl(\.gz)?$")
CID_RE = re.compile(r'"cid"\s*:\s*"([^"]+)"')

VERIFY_POLICY_NOTE = (
    "gate defaults mirror scripts/verify_tick_data.verify_window_continuity "
    "(6.0s gaps, 5.0s start delay); density floor sets min_snaps = ceil(duration / 3.0)"
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
    """Verdict one window's in-memory ticks against every gate.

    Folds the ticks into the same scalar aggregates the streaming scan builds, then
    judges through `judge_window` — one gate-decision path for both APIs, so `passed`
    and `failing_gates` can never disagree between paths.
    """
    if not ticks:
        return judge_window({"tick_count": 0, "cid": "", "series": "", "slug": "",
                             "duration": 0, "start_ts": 0.0, "end_ts": 0.0}, params)
    agg = _new_window_state(ticks[0], "")
    for tick in ticks[1:]:
        _add_tick_to_state(agg, tick, "", params.max_gap_sec)
    return judge_window(agg, params)


def _open_tick_text(path: Path):
    """Open a day file for verbatim line reading (jsonl or jsonl.gz)."""
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, "r", encoding="utf-8", errors="replace")


def _cid_of_line(raw: str) -> str | None:
    """Extract `cid` from one raw tick line without building the full dict.

    Finds `"cid": "<value>"` near the line head; a malformed line yields None and is
    excluded from output exactly like iter_ticks would exclude it.
    """
    m = CID_RE.search(raw[:1024])
    return m.group(1) if m else None


def day_key_of(ts: float) -> str:
    """The UTC day key of a unix timestamp (the collector's day-key convention)."""
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def evaluate_window_state(state: dict[str, Any], params: PristineGateParams) -> dict[str, Any]:
    """Verdict one aggregated window state (as built by scan_windows).

    Callers must scan with params.max_gap_sec (build_pristine_dataset does); gap
    counting happens at aggregation time, exactly once, under the caller's rule.
    """
    verdict = judge_window(state, params)
    verdict["source_files"] = list(state["source_files"])
    return verdict


def source_day_files(ticks_dir: Path) -> list[Path]:
    """Day files sorted by name (deterministic). Exactly the collector's naming."""
    found = [p for p in ticks_dir.glob("ticks_*.jsonl*") if DAY_RE.match(p.name) and p.is_file()]
    return sorted(found, key=lambda p: p.name)


def _new_window_state(first_tick: dict[str, Any], source_name: str) -> dict[str, Any]:
    """Scalar accumulators for one window. NO tick dicts are retained — pass 1 must
    stay O(windows) in memory even over multi-GB day files."""
    return {
        "cid": first_tick.get("cid", ""),
        "series": first_tick.get("series", ""),
        "slug": first_tick.get("slug", ""),
        "duration": first_tick.get("duration", 300) or 300,
        "start_ts": first_tick.get("start_ts", 0.0) or 0.0,
        "end_ts": first_tick.get("end_ts", 0.0) or 0.0,
        "first_ts": first_tick.get("ts", 0.0) or 0.0,
        "last_ts": first_tick.get("ts", 0.0) or 0.0,
        "prev_ts": first_tick.get("ts", 0.0) or 0.0,
        "gaps_count": 0,
        "max_gap": 0.0,
        "time_reversals": 0,
        "error_ticks": 0,
        "tick_count": 1,
        "source_files": [source_name] if source_name else [],
        "_seen": {source_name} if source_name else set(),
    }


def _add_tick_to_state(
    st: dict[str, Any], tick: dict[str, Any], source_name: str, max_gap_sec: float
) -> None:
    """Fold one tick into the scalar state. The gap threshold comes from the caller's
    params so every aggregation path counts gaps against the same rule."""
    ts = tick.get("ts", 0.0) or 0.0
    delta = ts - st["prev_ts"]
    if delta < 0:
        st["time_reversals"] += 1
    elif delta > max_gap_sec:
        st["gaps_count"] += 1
        if delta > st["max_gap"]:
            st["max_gap"] = delta
    st["prev_ts"] = ts
    st["last_ts"] = ts
    if tick.get("err"):
        st["error_ticks"] += 1
    st["tick_count"] += 1
    if source_name and source_name not in st["_seen"]:
        st["_seen"].add(source_name)
        st["source_files"].append(source_name)


def scan_windows(
    sources: Iterable[tuple[str, Iterable[dict[str, Any]]]], max_gap_sec: float = 6.0
) -> dict[str, dict[str, Any]]:
    """Aggregate ticks into one scalar state per cid, merged across source files.

    The collector splits midnight-spanning windows across two day files; merging by cid
    here is what keeps the derived set whole-window. States hold counters only — never
    the ticks themselves — so a six-day, multi-GB scan stays O(windows) in memory.
    `source_files` is first-seen order, never a set (determinism).
    """
    states: dict[str, dict[str, Any]] = {}
    for source_name, ticks in sources:
        if source_name:
            print(f"pass1: scanning {source_name}", flush=True)
        for tick in ticks:
            cid = tick.get("cid")
            if not cid:
                continue
            st = states.get(cid)
            if st is None:
                states[cid] = _new_window_state(tick, source_name)
            else:
                _add_tick_to_state(st, tick, source_name, max_gap_sec)
    for st in states.values():
        st.pop("_seen", None)
    return states


def judge_window(agg: dict[str, Any], params: PristineGateParams) -> dict[str, Any]:
    """Verdict one window from its scalar aggregates — the single gate-decision path.

    Both the streaming scan (evaluate_window_state) and the direct list API
    (evaluate_window_gates) funnel through here, so `passed` and `failing_gates`
    can never disagree between paths.
    """
    failing: list[str] = []
    tick_count = agg.get("tick_count", 0)
    if tick_count == 0:
        return {
            "passed": False,
            "failing_gates": ["no_ticks"],
            "cid": agg.get("cid", ""),
            "series": agg.get("series", ""),
            "slug": agg.get("slug", ""),
            "duration": agg.get("duration", 0),
            "start_ts": agg.get("start_ts", 0.0),
            "end_ts": agg.get("end_ts", 0.0),
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

    start_ts = agg["start_ts"]
    end_ts = agg["end_ts"]
    first_ts = agg["first_ts"]
    last_ts = agg["last_ts"]
    start_delay = max(0.0, first_ts - start_ts) if start_ts > 0 else 0.0
    end_cutoff = max(0.0, end_ts - last_ts) if end_ts > 0 else 0.0
    gaps_count = agg["gaps_count"]
    max_gap = agg["max_gap"]
    time_reversals = agg["time_reversals"]
    error_ticks = agg["error_ticks"]
    min_snaps = min_snaps_for(agg["duration"], params)

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
    if tick_count < min_snaps:
        failing.append("snap_density")

    return {
        "passed": not failing,
        "failing_gates": failing,
        "cid": agg["cid"],
        "series": agg["series"],
        "slug": agg["slug"],
        "duration": agg["duration"],
        "start_ts": start_ts,
        "end_ts": end_ts,
        "start_day": day_key_of(start_ts),
        "tick_count": tick_count,
        "min_snaps": min_snaps,
        "start_delay_sec": round(start_delay, 2),
        "end_cutoff_sec": round(end_cutoff, 2),
        "gaps_count": gaps_count,
        "max_gap_sec": round(max_gap, 2),
        "time_reversals": time_reversals,
        "error_ticks": error_ticks,
    }


def build_pristine_dataset(
    ticks_dir: Path,
    out_dir: Path,
    params: PristineGateParams = PristineGateParams(),
    quiet: bool = False,
) -> dict[str, Any]:
    """One build pass: gate every window and write the pristine dataset.

    Two streaming passes over the day files (memory stays O(windows), never O(ticks)):
    pass 1 gates every merged window, pass 2 copies surviving windows' raw lines to
    per-start-day output files. The manifest is written last so it never references a
    missing output file. Sources are only ever opened for reading.
    """
    ticks_dir = Path(ticks_dir)
    out_dir = Path(out_dir)
    if not ticks_dir.is_dir():
        raise ValueError(f"ticks dir not found: {ticks_dir}")
    src_files = source_day_files(ticks_dir)
    if not src_files:
        raise ValueError(f"no tick day files in {ticks_dir} (expected ticks_<YYYY-MM-DD>.jsonl[.gz])")
    if out_dir.resolve() == ticks_dir.resolve():
        raise ValueError(
            f"refusing --out {out_dir}: outputs share the ticks_<day>.jsonl naming and would clobber sources"
        )

    digests = {p.name: sha256_of(p) for p in src_files}

    # ---- Pass 1: aggregate per cid across files, gate every window.
    from scripts.verify_tick_data import verify_ticks_dir  # local: heavy module import

    states = scan_windows(
        ((p.name, iter_ticks([p])) for p in src_files), params.max_gap_sec
    )
    verdicts = [evaluate_window_state(st, params) for st in states.values()]
    verdicts.sort(key=lambda v: (v["start_ts"], v["cid"]))
    passed_cids = {v["cid"] for v in verdicts if v["passed"]}
    if not quiet:
        print(
            f"pass1: {len(verdicts)} windows, {len(passed_cids)} pristine "
            f"({len(verdicts) - len(passed_cids)} failed)",
            flush=True,
        )

    per_pair: dict[str, int] = defaultdict(int)
    for v in verdicts:
        if v["passed"]:
            per_pair[f"{v['series']}:{v['duration']}"] += 1

    # ---- Pass 2: stream again; copy surviving windows' ORIGINAL raw lines.
    # Lines are copied verbatim (no re-parse-and-redump): the output stays byte-faithful
    # to the capture and the pass stays O(1) in memory per line.
    out_dir.mkdir(parents=True, exist_ok=True)
    # A re-run must not leave day files from a previous build that this one no longer
    # produces (thresholds changed, sources removed) — the manifest would lie.
    for stale in out_dir.glob("ticks_*.jsonl*"):
        if DAY_RE.match(stale.name):
            stale.unlink()
    out_files: dict[str, Any] = {}
    ticks_written = 0
    try:
        for p in src_files:
            if not quiet:
                print(f"pass2: streaming {p.name}", flush=True)
            with _open_tick_text(p) as f:
                for raw in f:
                    raw = raw.strip()
                    if not raw:
                        continue
                    head = _cid_of_line(raw)
                    if head is None or head not in passed_cids:
                        continue
                    try:
                        tick = json.loads(raw)
                    except ValueError:
                        continue  # corrupt line: pass 1 excluded it too, stay consistent
                    day = day_key_of(tick.get("start_ts", 0.0) or 0.0)
                    fh = out_files.get(day)
                    if fh is None:
                        fh = open(out_dir / f"ticks_{day}.jsonl", "w", encoding="utf-8", newline="\n")
                        out_files[day] = fh
                    fh.write(raw + "\n")
                    ticks_written += 1
    finally:
        for fh in out_files.values():
            fh.close()

    manifest_keys = (
        "passed", "failing_gates", "cid", "series", "slug", "duration",
        "start_ts", "end_ts", "start_day", "tick_count", "min_snaps",
        "start_delay_sec", "end_cutoff_sec", "gaps_count", "max_gap_sec",
        "time_reversals", "error_ticks", "source_files",
    )
    windows_manifest = [
        {**{k: v[k] for k in manifest_keys},
         "source_sha256s": {n: digests[n] for n in v["source_files"]}}
        for v in verdicts
    ]
    manifest = {
        "policy_note": VERIFY_POLICY_NOTE,
        "gates": dataclasses.asdict(params),
        "totals": {
            "windows_total": len(verdicts),
            "windows_passed": len(passed_cids),
            "ticks_written": ticks_written,
            "output_files": sorted(out_files),
            "source_files": {p.name: digests[p.name] for p in src_files}
        },
        "per_pair_pristine_counts": dict(sorted(per_pair.items())),
        "windows": windows_manifest,
    }
    report = {"manifest": manifest, "passed_cids": passed_cids}

    # ---- Self-certification: verify the freshly written output, embed the verdict.
    output_verify = verify_ticks_dir(out_dir)
    ov_keys = (
        "status", "files_checked", "total_valid_ticks", "total_windows",
        "total_late_starts", "total_early_cutoffs", "total_sampling_gaps",
        "total_time_reversals", "total_collector_errors",
    )
    manifest["output_verify"] = {k: output_verify[k] for k in ov_keys}

    write_json_atomic(out_dir / MANIFEST_NAME, manifest)
    if not quiet:
        print(
            f"wrote {len(passed_cids)} pristine windows ({ticks_written} ticks) to {out_dir}; "
            f"output verify: {manifest['output_verify']['status']}",
            flush=True,
        )
    return report


def main(argv: list[str] | None = None) -> int:
    """CLI: build the pristine dataset from a ticks directory."""
    ap = argparse.ArgumentParser(description="Build the pristine-window tick dataset (issue #290).")
    ap.add_argument("ticks_dir", nargs="?", type=Path, default=DEFAULT_TICKS_DIR)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument("--max-gap-sec", type=float, default=6.0)
    ap.add_argument("--max-start-delay-sec", type=float, default=5.0)
    ap.add_argument("--max-snap-interval-sec", type=float, default=3.0)
    ap.add_argument("--quiet", action="store_true", help="Suppress progress output")
    e = ap.parse_args(argv)
    params = PristineGateParams(
        max_gap_sec=e.max_gap_sec,
        max_start_delay_sec=e.max_start_delay_sec,
        max_snap_interval_sec=e.max_snap_interval_sec,
    )
    try:
        build_pristine_dataset(e.ticks_dir, e.out, params=params, quiet=e.quiet)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
