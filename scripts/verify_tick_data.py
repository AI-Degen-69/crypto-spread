"""Tick data integrity verification engine for 5m/15m crypto spread capture.

Validates .jsonl / .jsonl.gz tick files in run/ticks for:
1. JSON syntax and schema completeness.
2. Order book sanity (crossed books, price bounds 0.00-1.00, positive sizes).
3. Window timing continuity (monotonicity, sample gaps >2s, late starts >5s, early cutoffs).
4. Midpoint and touch pair bounds.
5. Tape delta trade sanity.
6. Collector error rates and per-series coverage.

Usage:
  python -m scripts.verify_tick_data
  python -m scripts.verify_tick_data run/ticks
  python -m scripts.verify_tick_data run/ticks/ticks_2026-08-31.jsonl --verbose
  python -m scripts.verify_tick_data --json
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable

from strategy.series import SERIES

ROOT = Path(__file__).resolve().parent.parent

READINESS_POLICY_VERSION = "2026-09-20.v2"
EXPECTED_MARKETS = frozenset((slug, duration) for slug, duration, _label in SERIES)
READINESS_POLICIES = {
    "EXPLORATORY": {
        "min_valid_ticks": 1_000,
        "min_windows": 30,
        "min_tape_entries": 30,
        "min_time_blocks": 1,
        "min_market_duration_pairs": 1,
        "min_windows_per_market": 1,
        "max_corrupt_rate": 0.0,
        "max_schema_error_rate": 0.05,
        "max_gap_rate": 0.50,
        "max_collector_error_rate": 0.01,
    },
    "RESEARCH_READY": {
        "min_valid_ticks": 10_000,
        "min_windows": 100,
        "min_tape_entries": 100,
        "min_time_blocks": 3,
        "min_market_duration_pairs": len(EXPECTED_MARKETS),
        "min_windows_per_market": 10,
        "max_corrupt_rate": 0.0,
        "max_schema_error_rate": 0.05,
        "max_gap_rate": 0.10,
        "max_collector_error_rate": 0.0,
    },
}

READINESS_METRIC_LABELS = {
    "valid_ticks": "Tick snapshots",
    "independent_windows": "Market windows",
    "tape_entries": "Tape entries",
    "market_duration_coverage": "Market-duration pairs",
    "minimum_windows_per_market": "Windows per market-duration pair",
    "time_blocks": "Time blocks / days",
    "corrupt_rate": "Corrupt JSON rows",
    "schema_error_rate": "Schema error rate",
    "sampling_gap_rate": "Sampling gap rate",
    "collector_error_rate": "Collector error rate",
}

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TICKS_DIR = ROOT / "run" / "ticks"

REQUIRED_FIELDS = (
    "ts",
    "cid",
    "series",
    "duration",
    "start_ts",
    "end_ts",
    "up_book",
    "down_book",
)


def _open_tick_file(path: Path):
    """Open .jsonl or .jsonl.gz file in text mode with replace encoding error handler."""
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, "r", encoding="utf-8", errors="replace")


def est_total_lines(path: Path) -> int:
    """Cheap line-count estimate (bytes / avg-record-size), matching the dashboard.

    Same heuristic as osc_dash's manifest aggregate: files >= 20MB are estimated
    at ~950 bytes/line; smaller files are counted exactly (they're cheap).
    """
    path = Path(path)
    size = path.stat().st_size
    if size >= 20_000_000:
        return max(1, int(size / 950))
    try:
        opener = gzip.open if path.suffix == ".gz" else open
        n = 0
        with opener(path, "rb") as f:  # type: ignore[operator]
            for _ in f:
                n += 1
        return max(1, n)
    except Exception:
        return max(1, int(size / 950))


def verify_book(book: Any, label: str = "book") -> list[str]:
    """Verify order book dictionary integrity.
    
    Returns list of issue descriptions (empty if clean).
    """
    issues: list[str] = []
    if not isinstance(book, dict):
        return [f"{label}: not a dictionary"]

    bids = book.get("bids")
    asks = book.get("asks")
    best_bid = book.get("best_bid")
    best_ask = book.get("best_ask")

    if not isinstance(bids, dict):
        issues.append(f"{label}: bids is not a dict")
    else:
        for p_str, size in bids.items():
            try:
                p = float(p_str)
                if p < 0.0 or p > 1.0:
                    issues.append(f"{label}: bid price {p} out of bounds [0.0, 1.0]")
                if not isinstance(size, (int, float)) or size <= 0:
                    issues.append(f"{label}: bid size {size} not positive number")
            except (ValueError, TypeError):
                issues.append(f"{label}: unparseable bid price '{p_str}'")

    if not isinstance(asks, dict):
        issues.append(f"{label}: asks is not a dict")
    else:
        for p_str, size in asks.items():
            try:
                p = float(p_str)
                if p < 0.0 or p > 1.0:
                    issues.append(f"{label}: ask price {p} out of bounds [0.0, 1.0]")
                if not isinstance(size, (int, float)) or size <= 0:
                    issues.append(f"{label}: ask size {size} not positive number")
            except (ValueError, TypeError):
                issues.append(f"{label}: unparseable ask price '{p_str}'")

    if best_bid is not None:
        if not isinstance(best_bid, (int, float)) or best_bid < 0.0 or best_bid > 1.0:
            issues.append(f"{label}: best_bid {best_bid} invalid or out of bounds")
    if best_ask is not None:
        if not isinstance(best_ask, (int, float)) or best_ask < 0.0 or best_ask > 1.0:
            issues.append(f"{label}: best_ask {best_ask} invalid or out of bounds")

    # Crossed book check
    if (
        best_bid is not None
        and best_ask is not None
        and isinstance(best_bid, (int, float))
        and isinstance(best_ask, (int, float))
    ):
        if best_bid >= best_ask:
            issues.append(f"{label}: crossed book (best_bid {best_bid} >= best_ask {best_ask})")

    return issues


def verify_tick_record(tick: Any) -> list[str]:
    """Verify single tick record schema and values.
    
    Returns list of issue descriptions (empty if clean).
    """
    if not isinstance(tick, dict):
        return ["record is not a dictionary"]

    issues: list[str] = []

    # Check required fields
    for field in REQUIRED_FIELDS:
        if field not in tick or tick[field] is None:
            issues.append(f"missing or null required field: '{field}'")

    ts = tick.get("ts")
    if ts is None or not isinstance(ts, (int, float)) or ts <= 0:
        issues.append(f"invalid timestamp: {ts}")

    start_ts = tick.get("start_ts")
    end_ts = tick.get("end_ts")
    if (
        isinstance(start_ts, (int, float))
        and isinstance(end_ts, (int, float))
        and start_ts > end_ts
    ):
        issues.append(f"start_ts ({start_ts}) > end_ts ({end_ts})")

    mid = tick.get("mid")
    if mid is not None:
        if not isinstance(mid, (int, float)) or mid < -0.01 or mid > 1.01:
            issues.append(f"mid price {mid} out of bounds [-0.01, 1.01]")

    touch_pair = tick.get("touch_pair")
    if touch_pair is not None:
        if not isinstance(touch_pair, (int, float)) or touch_pair < 0.50 or touch_pair > 1.50:
            issues.append(f"touch_pair {touch_pair} out of sane bounds [0.50, 1.50]")

    # Verify books
    issues.extend(verify_book(tick.get("up_book"), label="up_book"))
    issues.extend(verify_book(tick.get("down_book"), label="down_book"))

    # Verify tape delta
    tape_delta = tick.get("tape_delta")
    if tape_delta is not None:
        if not isinstance(tape_delta, list):
            issues.append("tape_delta is not a list")
        else:
            for i, trade in enumerate(tape_delta):
                if not isinstance(trade, dict):
                    issues.append(f"tape_delta[{i}] is not a dict")
                    continue
                price = trade.get("price")
                size = trade.get("size")
                if price is not None and (
                    not isinstance(price, (int, float)) or price < 0.0 or price > 1.0
                ):
                    issues.append(f"tape_delta[{i}]: price {price} out of bounds")
                if size is not None and (not isinstance(size, (int, float)) or size <= 0):
                    issues.append(f"tape_delta[{i}]: size {size} not positive")

    return issues


def _check(name: str, measured: float, required: float, *, direction: str = "min") -> dict[str, Any]:
    """Create one serializable readiness check with progress direction."""
    ok = measured >= required if direction == "min" else measured <= required
    return {
        "name": name,
        "label": READINESS_METRIC_LABELS.get(name, name),
        "measured": measured,
        "required": required,
        "direction": direction,
        "ok": ok,
    }


def apply_hash_crosscheck(day_file: Path, report: dict[str, Any]) -> None:
    """Flag a day file whose recorded generation was replaced without a log (issue #302).

    The collector grows the live day file by append, so the current file hash
    naturally drifts from any previously recorded one — that is healthy, not
    suspicious. The crosscheck therefore records the hash of the file's
    GROWING PREFIX: the first ``len(recorded_bytes)`` bytes of the current
    file must be byte-identical to what was verified before. A prefix that
    changed means an existing generation was modified or replaced in place —
    an unexplained rewrite (loud sample_issues entry + status raised to WARN,
    FAIL for a completed past day, whose bytes must never change). Afterwards
    the store records the current state so the same change flags exactly once.
    """
    from scripts.tick_safety import (
        read_hash_store, read_rewrite_events, update_hash_store)
    from scripts.ship_to_drive import sha256_of

    report["hash_crosscheck"] = {"flagged": False}
    out_dir = day_file.parent
    name = day_file.name
    try:
        recorded = read_hash_store(out_dir)
        entry = recorded.get(name)
        last_sha, last_size = (entry.split(":", 1) + [""])[:2] if entry else (None, None)
        last_size = int(last_size) if last_size else None
        current = sha256_of(day_file)
    except (OSError, ValueError):
        return

    if last_sha is None:
        update_hash_store(out_dir, name, f"{current}:{day_file.stat().st_size}")
        return

    # Append-only growth: the file grew but its verified prefix is intact.
    if (day_file.stat().st_size >= (last_size or 0)
            and _prefix_sha256(day_file, last_size) == last_sha):
        update_hash_store(out_dir, name, f"{current}:{day_file.stat().st_size}")
        return

    # The verified prefix itself changed — a real replacement or in-place edit.
    events = [e for e in read_rewrite_events(out_dir) if e.get("day_file") == name]
    explained = any(e.get("old_sha256") == last_sha for e in events)
    if explained:
        update_hash_store(out_dir, name, f"{current}:{day_file.stat().st_size}")
        return

    completed_past_day = bool(report.get("capture_state", {}).get("label") == "COMPLETE CAPTURE")
    flagged_status = "FAIL" if completed_past_day else "WARN"
    detail = {
        "kind": "unexplained_rewrite",
        "file": name,
        "recorded_sha256": last_sha,
        "current_sha256": current,
        "note": "the previously verified prefix of this day file changed without "
                "a logged rewrite event — possible silent rewrite; the previous "
                "generation was not backed up",
    }
    report.setdefault("sample_issues", []).insert(0, detail)
    report["hash_crosscheck"] = {"flagged": True, **detail}
    if report.get("status") in ("PASS", "WARN") and flagged_status == "FAIL":
        report["status"] = "FAIL"
        report["capture_state"] = capture_state("FAIL", report)
    elif report.get("status") == "PASS":
        report["status"] = "WARN"
        report["capture_state"] = capture_state("WARN", report)
    update_hash_store(out_dir, name, f"{current}:{day_file.stat().st_size}")


def _prefix_sha256(path: Path, size: int) -> str:
    """SHA-256 of the first `size` bytes of a file (streamed, day-file safe)."""
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        remaining = size
        while remaining > 0:
            chunk = f.read(min(1 << 20, remaining))
            if not chunk:
                break
            h.update(chunk)
            remaining -= len(chunk)
    return h.hexdigest()


def capture_state(status: str, report: dict[str, Any]) -> dict[str, str]:
    """Describe the capture state and safe action while preserving raw status."""
    if status == "PASS":
        return {"label": "COMPLETE CAPTURE", "description": "The file was captured without detected integrity or continuity problems.", "action": "Replay is allowed; check readiness before research."}
    if status == "WARN":
        reasons = []
        for key, label in (("schema_errors", "schema issues"), ("sampling_gaps_count", "timestamp gaps"), ("collector_errors", "collector errors"), ("late_starts_count", "late starts"), ("early_cutoffs_count", "early cutoffs"), ("time_reversals", "time reversals")):
            if report.get(key, 0):
                reasons.append(f"{report[key]:,} {label}")
        return {"label": "PARTIAL CAPTURE", "description": "; ".join(reasons) or "The file is readable but the capture is incomplete.", "action": "Use for limited exploration; do not treat it as a complete capture."}
    reasons = []
    if report.get("error"):
        reasons.append(str(report["error"]))
    if report.get("corrupt_lines", 0):
        reasons.append(f"{report['corrupt_lines']:,} corrupt rows")
    if report.get("schema_errors", 0):
        reasons.append(f"{report['schema_errors']:,} schema issues")
    return {"label": "CORRUPTED DATA", "description": "; ".join(reasons) or "The file could not be read or contains invalid data.", "action": "Do not use for a serious Backtest; recapture or repair the file."}


def assess_readiness(
    *,
    valid_ticks: int,
    windows_count: int,
    tape_entries: int,
    market_breakdown: list[dict[str, Any]],
    time_blocks: list[str],
    raw_lines: int,
    corrupt_lines: int,
    schema_errors: int,
    sampling_gaps: int,
    collector_errors: int = 0,
) -> dict[str, Any]:
    """Classify readiness and expose both policy milestones for every metric."""
    market_keys = {(m.get("series"), m.get("duration")) for m in market_breakdown}
    windows_per_market = {
        f"{series}:{duration}": int(m.get("windows", 0))
        for m in market_breakdown
        for series, duration in [(m.get("series"), m.get("duration"))]
    }
    min_windows_per_market = min(windows_per_market.values(), default=0)
    gap_rate = sampling_gaps / max(windows_count, 1)
    corrupt_rate = corrupt_lines / max(raw_lines, 1)
    schema_error_rate = schema_errors / max(valid_ticks, 1)
    collector_error_rate = collector_errors / max(valid_ticks, 1)

    def checks_for(level: str) -> list[dict[str, Any]]:
        """Build measured readiness checks for one policy level."""
        p = READINESS_POLICIES[level]
        return [
            _check("valid_ticks", valid_ticks, p["min_valid_ticks"]),
            _check("independent_windows", windows_count, p["min_windows"]),
            _check("tape_entries", tape_entries, p["min_tape_entries"]),
            _check("market_duration_coverage", len(market_keys), p["min_market_duration_pairs"]),
            _check("minimum_windows_per_market", min_windows_per_market, p["min_windows_per_market"]),
            _check("time_blocks", len(time_blocks), p["min_time_blocks"]),
            _check("corrupt_rate", round(corrupt_rate, 6), p["max_corrupt_rate"], direction="max"),
            _check("schema_error_rate", round(schema_error_rate, 6), p["max_schema_error_rate"], direction="max"),
            _check("sampling_gap_rate", round(gap_rate, 6), p["max_gap_rate"], direction="max"),
            _check("collector_error_rate", round(collector_error_rate, 6), p["max_collector_error_rate"], direction="max"),
        ]

    exploratory = checks_for("EXPLORATORY")
    research = checks_for("RESEARCH_READY")
    if all(c["ok"] for c in research) and market_keys == EXPECTED_MARKETS:
        level = "RESEARCH_READY"
    elif all(c["ok"] for c in exploratory) and market_keys:
        level = "EXPLORATORY"
    else:
        level = "INSUFFICIENT"
    return {
        "level": level,
        "policy_version": READINESS_POLICY_VERSION,
        "checks": research if level == "RESEARCH_READY" else exploratory,
        "research_checks": research,
        "exploratory_checks": exploratory,
        "missing_markets": sorted(f"{s}:{d}" for s, d in EXPECTED_MARKETS - market_keys),
        "market_duration_count": len(market_keys),
        "targets": {"exploratory": READINESS_POLICIES["EXPLORATORY"], "research_ready": READINESS_POLICIES["RESEARCH_READY"]},
    }


def verify_window_continuity(
    ticks: list[dict[str, Any]],
    max_gap_sec: float = 6.0,
    max_start_delay: float = 5.0,
) -> dict[str, Any]:
    """Analyze time continuity and sampling quality for ticks of a single window.
    
    Returns metrics dict.
    """
    if not ticks:
        return {
            "tick_count": 0,
            "gaps_count": 0,
            "max_gap_sec": 0.0,
            "late_start": False,
            "start_delay_sec": 0.0,
            "early_cutoff": False,
            "end_cutoff_sec": 0.0,
            "time_reversals": 0,
            "span_sec": 0.0,
            "expected_duration": 0.0,
            "issues": ["no ticks in window"],
        }

    first_tick = ticks[0]
    last_tick = ticks[-1]

    start_ts = first_tick.get("start_ts", 0.0)
    end_ts = first_tick.get("end_ts", 0.0)
    duration = first_tick.get("duration", 300)
    first_ts = first_tick.get("ts", 0.0)
    last_ts = last_tick.get("ts", 0.0)

    start_delay = max(0.0, first_ts - start_ts) if start_ts > 0 else 0.0
    end_cutoff = max(0.0, end_ts - last_ts) if end_ts > 0 else 0.0

    gaps_count = 0
    max_gap = 0.0
    time_reversals = 0
    issues: list[str] = []

    for i in range(1, len(ticks)):
        prev_ts = ticks[i - 1].get("ts", 0.0)
        curr_ts = ticks[i].get("ts", 0.0)
        delta = curr_ts - prev_ts
        if delta < 0:
            time_reversals += 1
            issues.append(f"time reversal at tick {i}: {curr_ts} < {prev_ts}")
        elif delta > max_gap_sec:
            gaps_count += 1
            if delta > max_gap:
                max_gap = delta

    is_late_start = start_delay > max_start_delay
    is_early_cutoff = end_cutoff > max_start_delay

    if is_late_start:
        issues.append(f"late start: {start_delay:.1f}s after window open")
    if is_early_cutoff:
        issues.append(f"early cutoff: {end_cutoff:.1f}s before window close")
    if gaps_count > 0:
        issues.append(f"{gaps_count} sampling gap(s) > {max_gap_sec}s (max gap {max_gap:.1f}s)")

    span_sec = max(0.0, last_ts - first_ts)

    return {
        "cid": first_tick.get("cid", ""),
        "series": first_tick.get("series", ""),
        "slug": first_tick.get("slug", ""),
        "tick_count": len(ticks),
        "expected_duration": duration,
        "span_sec": round(span_sec, 2),
        "first_ts": first_ts,
        "last_ts": last_ts,
        "start_delay_sec": round(start_delay, 2),
        "end_cutoff_sec": round(end_cutoff, 2),
        "late_start": is_late_start,
        "early_cutoff": is_early_cutoff,
        "gaps_count": gaps_count,
        "max_gap_sec": round(max_gap, 2),
        "time_reversals": time_reversals,
        "issues": issues,
    }


def verify_tick_file(
    file_path: Path,
    max_gap_sec: float = 6.0,
    max_start_delay: float = 5.0,
    max_sample_issues: int = 20,
    progress_cb: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Verify integrity of a single .jsonl or .jsonl.gz file using streaming memory-efficient tracking.
    
    Returns structured verification report dict.

    progress_cb: optional callable(lines_done, est_total) invoked periodically
    (roughly every 50k lines) so callers can surface scan progress.
    """
    path = Path(file_path)
    if not path.is_file():
        return {
            "file": path.name,
            "path": str(path),
            "status": "FAIL",
            "error": f"file not found: {path}",
            "raw_lines": 0,
            "corrupt_lines": 0,
            "valid_ticks": 0,
            "capture_state": capture_state("FAIL", {"error": f"file not found: {path}"}),
        }

    raw_lines = 0
    empty_lines = 0
    corrupt_lines = 0
    valid_ticks = 0
    schema_errors = 0
    crossed_books = 0
    book_anomalies = 0
    collector_errors = 0

    # Lightweight streaming window tracker: cid -> dict
    windows_tracker: dict[tuple[str, str], dict[str, Any]] = {}
    series_counts: dict[str, int] = defaultdict(int)
    missing_fields: dict[str, int] = defaultdict(int)
    # Issue #109: per-market (series x duration) breakdown — windows + tape
    market_windows: dict[tuple[str, int], set[str]] = defaultdict(set)
    market_trades: dict[tuple[str, int], int] = defaultdict(int)
    sample_issues: list[dict[str, Any]] = []

    try:
        with _open_tick_file(path) as f:
            for line_no, line in enumerate(f, start=1):
                raw_lines += 1
                if progress_cb is not None and raw_lines % 50_000 == 0:
                    try:
                        progress_cb(raw_lines, est_total_lines(path))
                    except Exception:
                        pass
                stripped = line.strip()
                if not stripped:
                    empty_lines += 1
                    continue

                try:
                    record = json.loads(stripped)
                except Exception as e:
                    corrupt_lines += 1
                    if len(sample_issues) < max_sample_issues:
                        sample_issues.append({
                            "line": line_no,
                            "type": "json_decode_error",
                            "detail": str(e),
                        })
                    continue

                valid_ticks += 1
                for required in REQUIRED_FIELDS:
                    if required not in record or record[required] is None:
                        missing_fields[required] += 1
                issues = verify_tick_record(record)
                if issues:
                    schema_errors += len(issues)
                    for issue in issues:
                        if "crossed book" in issue:
                            crossed_books += 1
                        elif "book" in issue:
                            book_anomalies += 1
                        if len(sample_issues) < max_sample_issues:
                            sample_issues.append({
                                "line": line_no,
                                "type": "schema_or_book_issue",
                                "detail": issue,
                            })

                if record.get("err"):
                    collector_errors += 1

                cid = record.get("cid")
                ts = record.get("ts")
                series = record.get("series")
                if series:
                    series_counts[series] += 1
                duration = record.get("duration")
                if series and isinstance(duration, int):
                    market_windows[(series, duration)].add(cid)
                    tape = record.get("tape_delta")
                    if isinstance(tape, list):
                        market_trades[(series, duration)] += len(tape)

                if cid and isinstance(ts, (int, float)):
                    window_key = (str(series or ""), str(cid))
                    if window_key not in windows_tracker:
                        windows_tracker[window_key] = {
                            "cid": cid,
                            "series": series or "",
                            "slug": record.get("slug", ""),
                            "start_ts": record.get("start_ts", 0.0),
                            "end_ts": record.get("end_ts", 0.0),
                            "duration": record.get("duration", 300),
                            "first_ts": ts,
                            "last_ts": ts,
                            "prev_ts": ts,
                            "tick_count": 1,
                            "gaps_count": 0,
                            "max_gap_sec": 0.0,
                            "time_reversals": 0,
                        }
                    else:
                        w = windows_tracker[window_key]
                        w["tick_count"] += 1
                        prev_ts = w["prev_ts"]
                        delta = ts - prev_ts
                        if delta < 0:
                            w["time_reversals"] += 1
                        elif delta > max_gap_sec:
                            w["gaps_count"] += 1
                            if delta > w["max_gap_sec"]:
                                w["max_gap_sec"] = delta
                        w["prev_ts"] = ts
                        w["last_ts"] = ts

    except Exception as e:
        return {
            "file": path.name,
            "path": str(path),
            "status": "FAIL",
            "error": f"read error: {e}",
            "raw_lines": raw_lines,
            "corrupt_lines": corrupt_lines,
            "valid_ticks": valid_ticks,
            "capture_state": capture_state("FAIL", {"error": f"read error: {e}"}),
        }

    # Finalize window metrics
    total_gaps = 0
    total_late_starts = 0
    total_early_cutoffs = 0
    total_reversals = 0

    for cid, w in windows_tracker.items():
        start_ts = w["start_ts"]
        end_ts = w["end_ts"]
        first_ts = w["first_ts"]
        last_ts = w["last_ts"]

        start_delay = max(0.0, first_ts - start_ts) if start_ts > 0 else 0.0
        end_cutoff = max(0.0, end_ts - last_ts) if end_ts > 0 else 0.0

        if start_delay > max_start_delay:
            total_late_starts += 1
        if end_cutoff > max_start_delay:
            total_early_cutoffs += 1

        total_gaps += w["gaps_count"]
        total_reversals += w["time_reversals"]

    time_blocks = sorted({
        time.strftime("%Y-%m-%d", time.gmtime(w["first_ts"]))
        for w in windows_tracker.values()
        if isinstance(w.get("first_ts"), (int, float)) and w.get("first_ts", 0) > 0
    })
    market_breakdown = [
        {
            "series": s,
            "duration": du,
            "windows": len(market_windows[(s, du)]),
            "trades": market_trades[(s, du)],
            "trades_per_window": (
                round(market_trades[(s, du)] / len(market_windows[(s, du)]), 1)
                if market_windows[(s, du)] else 0.0
            ),
        }
        for (s, du) in sorted(market_windows)
    ]
    tape_entries = sum(m["trades"] for m in market_breakdown)
    readiness = assess_readiness(
        valid_ticks=valid_ticks,
        windows_count=len(windows_tracker),
        tape_entries=tape_entries,
        market_breakdown=market_breakdown,
        time_blocks=time_blocks,
        raw_lines=raw_lines,
        corrupt_lines=corrupt_lines,
        schema_errors=schema_errors,
        sampling_gaps=total_gaps,
        collector_errors=collector_errors,
    )

    # Determine status
    if corrupt_lines > 0 or schema_errors > (valid_ticks * 0.05 if valid_ticks > 0 else 1):
        status = "FAIL"
    elif (
        crossed_books > 0
        or total_gaps > 0
        or total_late_starts > 0
        or total_early_cutoffs > 0
        or collector_errors > 0
        or total_reversals > 0
    ):
        status = "WARN"
    else:
        status = "PASS"

    return {
        "file": path.name,
        "path": str(path),
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "status": status,
        "capture_state": capture_state(status, {
            "corrupt_lines": corrupt_lines,
            "schema_errors": schema_errors,
            "sampling_gaps_count": total_gaps,
            "collector_errors": collector_errors,
            "late_starts_count": total_late_starts,
            "early_cutoffs_count": total_early_cutoffs,
            "time_reversals": total_reversals,
        }),
        "raw_lines": raw_lines,
        "empty_lines": empty_lines,
        "corrupt_lines": corrupt_lines,
        "valid_ticks": valid_ticks,
        "windows_count": len(windows_tracker),
        "series_counts": dict(series_counts),
        "market_breakdown": market_breakdown,
        "tape_entries": tape_entries,
        "missing_fields": dict(missing_fields),
        "time_blocks": time_blocks,
        "readiness": readiness,
        "schema_errors": schema_errors,
        "crossed_books": crossed_books,
        "book_anomalies": book_anomalies,
        "collector_errors": collector_errors,
        "sampling_gaps_count": total_gaps,
        "late_starts_count": total_late_starts,
        "early_cutoffs_count": total_early_cutoffs,
        "time_reversals": total_reversals,
        "sample_issues": sample_issues,
    }


def verify_ticks_dir(
    dir_path: Path,
    max_gap_sec: float = 6.0,
    max_start_delay: float = 5.0,
) -> dict[str, Any]:
    """Scan all .jsonl and .jsonl.gz files in directory and return aggregated verification report."""
    path = Path(dir_path)
    if not path.is_dir():
        return {
            "status": "FAIL",
            "error": f"directory not found: {dir_path}",
            "files_checked": 0,
            "files": [],
        }

    candidates = sorted(
        list(path.glob("ticks_*.jsonl")) + list(path.glob("ticks_*.jsonl.gz")),
        key=lambda p: p.name,
    )
    if not candidates:
        candidates = sorted(
            list(path.glob("*.jsonl")) + list(path.glob("*.jsonl.gz")),
            key=lambda p: p.name,
        )

    file_reports = []
    tot_raw_lines = 0
    tot_corrupt_lines = 0
    tot_valid_ticks = 0
    tot_windows = 0
    tot_crossed_books = 0
    tot_schema_errors = 0
    tot_collector_errors = 0
    tot_gaps = 0
    tot_late_starts = 0
    tot_early_cutoffs = 0
    tot_reversals = 0
    tot_tape_entries = 0
    tot_missing_fields: dict[str, int] = defaultdict(int)
    aggregated_market: dict[tuple[str, int], dict[str, Any]] = {}
    aggregated_time_blocks: set[str] = set()
    aggregated_series: dict[str, int] = defaultdict(int)

    for f in candidates:
        rep = verify_tick_file(
            f,
            max_gap_sec=max_gap_sec,
            max_start_delay=max_start_delay,
        )
        apply_hash_crosscheck(f, rep)
        file_reports.append(rep)
        tot_raw_lines += rep.get("raw_lines", 0)
        tot_corrupt_lines += rep.get("corrupt_lines", 0)
        tot_valid_ticks += rep.get("valid_ticks", 0)
        tot_windows += rep.get("windows_count", 0)
        tot_crossed_books += rep.get("crossed_books", 0)
        tot_schema_errors += rep.get("schema_errors", 0)
        tot_collector_errors += rep.get("collector_errors", 0)
        tot_gaps += rep.get("sampling_gaps_count", 0)
        tot_late_starts += rep.get("late_starts_count", 0)
        tot_early_cutoffs += rep.get("early_cutoffs_count", 0)
        tot_reversals += rep.get("time_reversals", 0)
        tot_tape_entries += rep.get("tape_entries", 0)
        aggregated_time_blocks.update(rep.get("time_blocks", []))
        for field, count in rep.get("missing_fields", {}).items():
            tot_missing_fields[field] += count
        for market in rep.get("market_breakdown", []):
            key = (market.get("series", ""), int(market.get("duration", 0)))
            item = aggregated_market.setdefault(key, {"series": key[0], "duration": key[1], "windows": 0, "trades": 0})
            item["windows"] += int(market.get("windows", 0))
            item["trades"] += int(market.get("trades", 0))
        for s, count in rep.get("series_counts", {}).items():
            aggregated_series[s] += count

    aggregate_market = []
    for market in sorted(aggregated_market.values(), key=lambda m: (m["series"], m["duration"])):
        market["trades_per_window"] = round(market["trades"] / market["windows"], 1) if market["windows"] else 0.0
        aggregate_market.append(market)
    aggregate_readiness = assess_readiness(
        valid_ticks=tot_valid_ticks,
        windows_count=tot_windows,
        tape_entries=tot_tape_entries,
        market_breakdown=aggregate_market,
        time_blocks=sorted(aggregated_time_blocks),
        raw_lines=tot_raw_lines,
        corrupt_lines=tot_corrupt_lines,
        schema_errors=tot_schema_errors,
        sampling_gaps=tot_gaps,
        collector_errors=tot_collector_errors,
    )

    # Aggregated verdict
    if any(r.get("status") == "FAIL" for r in file_reports):
        verdict = "FAIL"
    elif any(r.get("status") == "WARN" for r in file_reports):
        verdict = "WARN"
    elif not candidates:
        verdict = "WARN"
    else:
        verdict = "PASS"

    hash_issues = sum(1 for r in file_reports if r.get("hash_crosscheck", {}).get("flagged"))

    return {
        "status": verdict,
        "capture_state": capture_state(verdict, {
            "corrupt_lines": tot_corrupt_lines,
            "schema_errors": tot_schema_errors,
            "sampling_gaps_count": tot_gaps,
            "collector_errors": tot_collector_errors,
            "late_starts_count": tot_late_starts,
            "early_cutoffs_count": tot_early_cutoffs,
            "time_reversals": tot_reversals,
        }),
        "files_checked": len(file_reports),
        "total_raw_lines": tot_raw_lines,
        "total_corrupt_lines": tot_corrupt_lines,
        "total_valid_ticks": tot_valid_ticks,
        "total_windows": tot_windows,
        "total_crossed_books": tot_crossed_books,
        "total_schema_errors": tot_schema_errors,
        "total_collector_errors": tot_collector_errors,
        "total_sampling_gaps": tot_gaps,
        "total_late_starts": tot_late_starts,
        "total_early_cutoffs": tot_early_cutoffs,
        "total_time_reversals": tot_reversals,
        "series_counts": dict(aggregated_series),
        "market_breakdown": aggregate_market,
        "total_tape_entries": tot_tape_entries,
        "missing_fields": dict(tot_missing_fields),
        "time_blocks": sorted(aggregated_time_blocks),
        "readiness": aggregate_readiness,
        "hash_rewrites_unexplained": hash_issues,
        "files": file_reports,
    }


def format_report_text(report: dict[str, Any], verbose: bool = False) -> str:
    """Format verification report into clean human-readable terminal output."""
    lines = []
    lines.append("=" * 70)
    lines.append(f"TICK DATA INTEGRITY REPORT  ·  Status: {report.get('status', 'UNKNOWN')}")
    lines.append("=" * 70)

    if "files_checked" in report:
        lines.append(f"Files Checked       : {report['files_checked']}")
        lines.append(f"Total Lines         : {report['total_raw_lines']:,}")
        lines.append(f"Valid Ticks         : {report['total_valid_ticks']:,}")
        lines.append(f"Corrupt Lines       : {report['total_corrupt_lines']:,}")
        lines.append(f"Windows Found       : {report['total_windows']:,}")
        lines.append(f"Crossed Books       : {report['total_crossed_books']:,}")
        lines.append(f"Sampling Gaps (>2s) : {report['total_sampling_gaps']:,}")
        lines.append(f"Late Starts (>5s)   : {report['total_late_starts']:,}")
        lines.append(f"Early Cutoffs (>5s) : {report['total_early_cutoffs']:,}")
        lines.append(f"Collector Errors    : {report['total_collector_errors']:,}")
        lines.append(f"Time Reversals      : {report['total_time_reversals']:,}")
        if report.get("readiness"):
            lines.append(f"Research Readiness  : {report['readiness'].get('level', 'PENDING')}")
        if report.get("hash_rewrites_unexplained"):
            lines.append(f"Unexplained Rewrites : {report['hash_rewrites_unexplained']:,} "
                         "(day file bytes changed without a logged rewrite event)")

        lines.append("\nPer-File Summary:")
        for fr in report.get("files", []):
            st = fr.get("status", "UNKNOWN")
            lines.append(
                f"  [{st}] {fr.get('file')} : {fr.get('valid_ticks', 0):,} ticks, "
                f"{fr.get('windows_count', 0)} windows, {fr.get('corrupt_lines', 0)} corrupt, "
                f"{fr.get('sampling_gaps_count', 0)} gaps, {fr.get('collector_errors', 0)} errs, "
                f"readiness={fr.get('readiness', {}).get('level', 'PENDING')}"
            )
            if fr.get("hash_crosscheck", {}).get("flagged"):
                lines.append("      ⚠ UNEXPLAINED REWRITE — bytes changed without a "
                             "logged rewrite event (previous generation not backed up)")
            if verbose and fr.get("sample_issues"):
                for issue in fr["sample_issues"][:5]:
                    lines.append(f"      • Line {issue.get('line')}: {issue.get('detail')}")
    else:
        lines.append(f"File                : {report.get('file')}")
        lines.append(f"Valid Ticks         : {report.get('valid_ticks', 0):,}")
        lines.append(f"Corrupt Lines       : {report.get('corrupt_lines', 0):,}")
        lines.append(f"Windows Found       : {report.get('windows_count', 0):,}")
        lines.append(f"Crossed Books       : {report.get('crossed_books', 0):,}")
        lines.append(f"Sampling Gaps (>2s) : {report.get('sampling_gaps_count', 0):,}")
        lines.append(f"Late Starts (>5s)   : {report.get('late_starts_count', 0):,}")
        lines.append(f"Early Cutoffs (>5s) : {report.get('early_cutoffs_count', 0):,}")
        lines.append(f"Collector Errors    : {report.get('collector_errors', 0):,}")
        if report.get("readiness"):
            lines.append(f"Research Readiness  : {report['readiness'].get('level', 'PENDING')}")
        hc = report.get("hash_crosscheck") or {}
        if hc.get("flagged"):
            lines.append(f"UNEXPLAINED REWRITE : bytes changed without a logged "
                         "rewrite event (previous generation not backed up)")

        if verbose and report.get("sample_issues"):
            lines.append("\nSample Discrepancies (sample_issues, max 20 — see docs/operations.md):")
            for issue in report["sample_issues"]:
                lines.append(f"  • Line {issue.get('line')}: {issue.get('detail')}")

    lines.append("=" * 70)
    return "\n".join(lines)


def main() -> int:
    """CLI entry point for tick data verification tool."""
    parser = argparse.ArgumentParser(
        description="Verify integrity of tick data in run/ticks for replay & backtesting."
    )
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=DEFAULT_TICKS_DIR,
        help="Path to ticks directory or specific .jsonl/.jsonl.gz file (default: run/ticks)",
    )
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show sample discrepancies (see docs/operations.md#sample-discrepancies--replay-integrity)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with code 1 on warnings as well as failures",
    )
    parser.add_argument(
        "--max-gap",
        type=float,
        default=6.0,
        help="Max allowable timestamp gap between ticks before warning (default: 6.0s)",
    )
    parser.add_argument(
        "--max-start-delay",
        type=float,
        default=5.0,
        help="Max start delay before flagging late start (default: 5.0s)",
    )

    args = parser.parse_args()
    target: Path = args.path

    if not target.exists():
        print(f"Error: Target path does not exist: {target}", file=sys.stderr)
        return 1

    if target.is_file():
        report = verify_tick_file(
            target,
            max_gap_sec=args.max_gap,
            max_start_delay=args.max_start_delay,
        )
        apply_hash_crosscheck(target, report)
    else:
        report = verify_ticks_dir(
            target,
            max_gap_sec=args.max_gap,
            max_start_delay=args.max_start_delay,
        )

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(format_report_text(report, verbose=args.verbose))

    status = report.get("status")
    if status == "FAIL":
        return 1
    if args.strict and status == "WARN":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
