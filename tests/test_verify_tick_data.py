"""Unit and integration tests for tick data integrity verification tool."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
import pytest

from scripts.verify_tick_data import (
    verify_book,
    verify_tick_record,
    verify_window_continuity,
    verify_tick_file,
    verify_ticks_dir,
    format_report_text,
    assess_readiness,
    capture_state,
    READINESS_POLICIES,
    READINESS_POLICY_VERSION,
)


def make_sample_tick(
    cid: str = "0xabc",
    series: str = "btc-up-or-down-5m",
    ts: float = 1725000000.0,
    start_ts: float = 1725000000.0,
    end_ts: float = 1725000300.0,
    duration: int = 300,
    mid: float = 0.50,
    touch_pair: float = 1.02,
    best_bid: float = 0.49,
    best_ask: float = 0.51,
) -> dict:
    """Helper to generate valid tick dictionary."""
    return {
        "ts": ts,
        "iso": "2026-08-31T00:00:00+00:00",
        "series": series,
        "duration": duration,
        "label": "BTC 5m",
        "cid": cid,
        "slug": "btc-up-or-down-5m-2026-08-31",
        "start_ts": start_ts,
        "end_ts": end_ts,
        "t_rem": end_ts - ts,
        "up_book": {
            "bids": {str(best_bid): 100.0},
            "asks": {str(best_ask): 100.0},
            "best_bid": best_bid,
            "best_ask": best_ask,
            "malformed": 0,
        },
        "down_book": {
            "bids": {"0.48": 100.0},
            "asks": {"0.51": 100.0},
            "best_bid": 0.48,
            "best_ask": 0.51,
            "malformed": 0,
        },
        "tape_delta": [{"asset": "token1", "price": 0.50, "size": 10.0}],
        "mid": mid,
        "touch_pair": touch_pair,
        "resting_pair": 0.96,
        "queue_up": 100.0,
        "queue_down": 100.0,
        "err": None,
    }


def test_assess_readiness_has_explicit_exploratory_and_research_levels():
    # Use the canonical 5m/15m series universe rather than synthetic names.
    markets = [
        {"series": f"{asset}-up-or-down-{duration // 60}m", "duration": duration,
         "windows": 10, "trades": 10, "trades_per_window": 1.0}
        for asset, duration in [("btc", 300), ("eth", 300), ("bnb", 300), ("sol", 300), ("xrp", 300),
                                ("btc", 900), ("eth", 900), ("bnb", 900), ("sol", 900), ("xrp", 900)]
    ]
    result = assess_readiness(
        valid_ticks=10000, windows_count=100, tape_entries=100,
        market_breakdown=markets, time_blocks=["2026-09-01", "2026-09-02", "2026-09-03"],
        raw_lines=10000, corrupt_lines=0, schema_errors=0, sampling_gaps=0,
    )
    assert result["level"] == "RESEARCH_READY"
    assert result["missing_markets"] == []
    exploratory = assess_readiness(
        valid_ticks=1000, windows_count=30, tape_entries=30,
        market_breakdown=markets[:1], time_blocks=["2026-09-01"],
        raw_lines=1000, corrupt_lines=0, schema_errors=0, sampling_gaps=0,
    )
    assert exploratory["level"] == "EXPLORATORY"


def test_readiness_exposes_shared_targets_and_progress_direction():
    result = assess_readiness(
        valid_ticks=12,
        windows_count=1,
        tape_entries=0,
        market_breakdown=[{"series": "btc-up-or-down-5m", "duration": 300, "windows": 1, "trades": 0}],
        time_blocks=[], raw_lines=12, corrupt_lines=0, schema_errors=0,
        sampling_gaps=0, collector_errors=0,
    )
    assert result["policy_version"] == READINESS_POLICY_VERSION
    assert result["targets"]["exploratory"] == READINESS_POLICIES["EXPLORATORY"]
    assert result["targets"]["research_ready"] == READINESS_POLICIES["RESEARCH_READY"]
    checks = {c["name"]: c for c in result["exploratory_checks"]}
    assert checks["valid_ticks"]["required"] == 1_000
    assert checks["valid_ticks"]["direction"] == "min"
    assert checks["sampling_gap_rate"]["direction"] == "max"
    assert result["level"] == "INSUFFICIENT"


def test_zero_window_readiness_has_deterministic_progress_values():
    """Empty market coverage reports zero progress without division errors."""
    result = assess_readiness(
        valid_ticks=0,
        windows_count=0,
        tape_entries=0,
        market_breakdown=[],
        time_blocks=[],
        raw_lines=0,
        corrupt_lines=0,
        schema_errors=0,
        sampling_gaps=0,
        collector_errors=0,
    )
    checks = {c["name"]: c for c in result["exploratory_checks"]}
    assert result["level"] == "INSUFFICIENT"
    assert checks["minimum_windows_per_market"]["measured"] == 0
    assert checks["sampling_gap_rate"]["measured"] == 0
    assert checks["sampling_gap_rate"]["direction"] == "max"


def test_capture_state_describes_file_and_action():
    assert capture_state("PASS", {})["label"] == "COMPLETE CAPTURE"
    partial = capture_state("WARN", {"sampling_gaps_count": 4, "collector_errors": 2})
    assert partial["label"] == "PARTIAL CAPTURE"
    assert "timestamp gaps" in partial["description"]
    assert "exploration" in partial["action"]
    broken = capture_state("FAIL", {"corrupt_lines": 3, "schema_errors": 4})
    assert broken["label"] == "CORRUPTED DATA"
    assert "corrupt rows" in broken["description"]


def test_verify_book_clean():
    book = {
        "bids": {"0.49": 100.0, "0.48": 50.0},
        "asks": {"0.51": 100.0, "0.52": 50.0},
        "best_bid": 0.49,
        "best_ask": 0.51,
    }
    issues = verify_book(book, label="up_book")
    assert issues == []


def test_verify_book_crossed():
    book = {
        "bids": {"0.52": 100.0},
        "asks": {"0.50": 100.0},
        "best_bid": 0.52,
        "best_ask": 0.50,
    }
    issues = verify_book(book, label="up_book")
    assert any("crossed book" in i for i in issues)


def test_verify_book_invalid_prices_and_sizes():
    book = {
        "bids": {"-0.10": 100.0, "0.50": -5.0},
        "asks": {"1.20": 0.0},
        "best_bid": -0.10,
        "best_ask": 1.20,
    }
    issues = verify_book(book, label="test_book")
    assert any("out of bounds" in i for i in issues)
    assert any("not positive" in i for i in issues)


def test_verify_tick_record_clean():
    tick = make_sample_tick()
    issues = verify_tick_record(tick)
    assert issues == []


def test_verify_tick_record_missing_required_field():
    tick = make_sample_tick()
    del tick["cid"]
    issues = verify_tick_record(tick)
    assert any("missing or null required field: 'cid'" in i for i in issues)


def test_verify_tick_record_invalid_bounds():
    tick = make_sample_tick(mid=1.5, touch_pair=2.0)
    issues = verify_tick_record(tick)
    assert any("mid price" in i for i in issues)
    assert any("touch_pair" in i for i in issues)


def test_verify_tick_tape_delta_invalid():
    tick = make_sample_tick()
    tick["tape_delta"] = [{"asset": "tok", "price": 1.50, "size": -10.0}]
    issues = verify_tick_record(tick)
    assert any("tape_delta" in i for i in issues)


def test_verify_window_continuity_clean():
    ticks = [
        make_sample_tick(ts=1725000000.0 + i)
        for i in range(300)
    ]
    metrics = verify_window_continuity(ticks)
    assert metrics["tick_count"] == 300
    assert metrics["gaps_count"] == 0
    assert metrics["late_start"] is False
    assert metrics["early_cutoff"] is False
    assert metrics["time_reversals"] == 0
    assert metrics["issues"] == []


def test_verify_window_continuity_gaps_and_late_start():
    ticks = [
        make_sample_tick(ts=1725000010.0),  # 10s late start (>5s)
        make_sample_tick(ts=1725000011.0),
        make_sample_tick(ts=1725000020.0),  # 9s gap (>2s)
        make_sample_tick(ts=1725000280.0),  # stops at 280s (20s early cutoff >5s)
    ]
    metrics = verify_window_continuity(ticks)
    assert metrics["tick_count"] == 4
    assert metrics["late_start"] is True
    assert metrics["early_cutoff"] is True
    assert metrics["gaps_count"] >= 1
    assert metrics["max_gap_sec"] == 260.0
    assert any("late start" in i for i in metrics["issues"])


def test_verify_tick_file_early_cutoff_is_partial_capture(tmp_path: Path):
    """An otherwise readable file that ends before its window is partial."""
    f = tmp_path / "ticks_early_cutoff.jsonl"
    tick = make_sample_tick(ts=1725000010.0, start_ts=1725000000.0, end_ts=1725000300.0)
    f.write_text(json.dumps(tick) + "\n", encoding="utf-8")

    report = verify_tick_file(f)
    assert report["status"] == "WARN"
    assert report["capture_state"]["label"] == "PARTIAL CAPTURE"
    assert "early cutoffs" in report["capture_state"]["description"]


def test_verify_window_continuity_time_reversal():
    ticks = [
        make_sample_tick(ts=1725000005.0),
        make_sample_tick(ts=1725000002.0),  # time goes backwards
    ]
    metrics = verify_window_continuity(ticks)
    assert metrics["time_reversals"] == 1
    assert any("time reversal" in i for i in metrics["issues"])


def test_verify_tick_file_clean_and_corrupt(tmp_path: Path):
    clean_file = tmp_path / "ticks_2026-09-01.jsonl"
    ticks = [make_sample_tick(ts=1725000000.0 + i, end_ts=1725000009.0) for i in range(10)]
    clean_file.write_text("\n".join(json.dumps(t) for t in ticks) + "\n", encoding="utf-8")

    rep = verify_tick_file(clean_file)
    assert rep["status"] == "PASS"
    assert rep["valid_ticks"] == 10
    assert rep["corrupt_lines"] == 0
    assert rep["capture_state"]["label"] == "COMPLETE CAPTURE"
    assert "targets" in rep["readiness"]

    # Add corrupt lines
    corrupt_file = tmp_path / "ticks_corrupt.jsonl"
    corrupt_content = json.dumps(ticks[0]) + "\n{INVALID_JSON\n" + json.dumps(ticks[1]) + "\n"
    corrupt_file.write_text(corrupt_content, encoding="utf-8")

    rep_corrupt = verify_tick_file(corrupt_file)
    assert rep_corrupt["status"] == "FAIL"
    assert rep_corrupt["corrupt_lines"] == 1
    assert rep_corrupt["valid_ticks"] == 2


def test_verify_tick_file_market_breakdown(tmp_path: Path):
    """Issue #109: per-market (series x duration) windows + tape trade counts."""
    f = tmp_path / "ticks_2026-09-01.jsonl"
    ticks = []
    # btc 5m: 2 windows, 3 trades total
    for cid, n_trades in (("w1", 2), ("w2", 1)):
        for i in range(3):
            t = make_sample_tick(cid=cid, ts=1725000000.0 + i)
            t["tape_delta"] = [{"price": 0.5, "size": 10}] * n_trades if i == 0 else []
            ticks.append(t)
    # eth 15m: 1 window, 5 trades
    for i in range(3):
        t = make_sample_tick(cid="w3", series="eth-up-or-down-15m", duration=900, ts=1725000000.0 + i)
        t["tape_delta"] = [{"price": 0.5, "size": 1}] * 5 if i == 0 else []
        ticks.append(t)
    f.write_text("\n".join(json.dumps(t) for t in ticks) + "\n", encoding="utf-8")

    rep = verify_tick_file(f)
    bd = {(b["series"], b["duration"]): b for b in rep["market_breakdown"]}
    btc = bd[("btc-up-or-down-5m", 300)]
    eth = bd[("eth-up-or-down-15m", 900)]
    assert btc["windows"] == 2
    assert btc["trades"] == 3
    assert btc["trades_per_window"] == 1.5
    assert eth["windows"] == 1
    assert eth["trades"] == 5
    assert eth["trades_per_window"] == 5.0


def test_verify_ticks_dir_aggregation(tmp_path: Path):
    f1 = tmp_path / "ticks_2026-08-31.jsonl"
    f2 = tmp_path / "ticks_2026-09-01.jsonl"
    t1 = [make_sample_tick(cid="cid1", ts=1725000000.0 + i, start_ts=1725000000.0, end_ts=1725000004.0) for i in range(5)]
    t2 = [make_sample_tick(cid="cid2", ts=1725000100.0 + i, start_ts=1725000100.0, end_ts=1725000104.0) for i in range(5)]
    f1.write_text("\n".join(json.dumps(t) for t in t1) + "\n", encoding="utf-8")
    f2.write_text("\n".join(json.dumps(t) for t in t2) + "\n", encoding="utf-8")

    rep = verify_ticks_dir(tmp_path)
    assert rep["status"] == "PASS"
    assert rep["files_checked"] == 2
    assert rep["total_valid_ticks"] == 10
    assert rep["total_windows"] == 2


def test_cli_execution(tmp_path: Path):
    f1 = tmp_path / "ticks_2026-08-31.jsonl"
    t1 = [make_sample_tick(cid="cid1", ts=1725000000.0 + i, start_ts=1725000000.0, end_ts=1725000004.0) for i in range(5)]
    f1.write_text("\n".join(json.dumps(t) for t in t1) + "\n", encoding="utf-8")

    res = subprocess.run(
        [sys.executable, "-m", "scripts.verify_tick_data", str(tmp_path), "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert res.returncode == 0
    data = json.loads(res.stdout)
    assert data["status"] == "PASS"
    assert data["total_valid_ticks"] == 5


def test_format_report_text():
    report = {
        "status": "PASS",
        "files_checked": 1,
        "total_raw_lines": 100,
        "total_valid_ticks": 100,
        "total_corrupt_lines": 0,
        "total_windows": 1,
        "total_crossed_books": 0,
        "total_sampling_gaps": 0,
        "total_late_starts": 0,
        "total_early_cutoffs": 0,
        "total_collector_errors": 0,
        "total_time_reversals": 0,
        "files": [],
    }
    txt = format_report_text(report)
    assert "TICK DATA INTEGRITY REPORT" in txt
    assert "Status: PASS" in txt


def test_verify_tick_file_progress_cb(tmp_path):
    """progress_cb fires periodically during scan with (lines_done, est_total)."""
    from scripts.verify_tick_data import verify_tick_file as vtf

    # 60k lines so the 50k throttle fires at least once.
    p = tmp_path / "ticks_test.jsonl"
    with p.open("w", encoding="utf-8") as f:
        for i in range(60_000):
            f.write(json.dumps({"series": "s", "cid": "w", "ts": float(i)}) + "\n")

    calls: list[tuple[int, int]] = []
    vtf(p, progress_cb=lambda n, t: calls.append((n, t)))
    assert calls, "progress_cb never fired"
    assert calls[0][0] == 50_000
    assert all(t >= n for n, t in calls)


def test_est_total_lines(tmp_path):
    """est_total_lines counts small files exactly, estimates large ones."""
    from scripts.verify_tick_data import est_total_lines

    p = tmp_path / "small.jsonl"
    p.write_text("\n".join(["{}"] * 100) + "\n", encoding="utf-8")
    assert est_total_lines(p) == 100

    big = tmp_path / "big.jsonl"
    big.write_bytes(b"x" * 21_000_000)
    assert est_total_lines(big) == 21_000_000 // 950


def _crosscheck_day(tmp_path, *, body: bytes) -> Path:
    """A real day file verified once, so the hash store records its baseline."""
    from scripts.verify_tick_data import apply_hash_crosscheck

    day = tmp_path / "ticks_2026-09-13.jsonl"
    day.write_bytes(body)
    rep = verify_tick_file(day)
    apply_hash_crosscheck(day, rep)
    assert rep["hash_crosscheck"]["flagged"] is False  # baseline recorded
    return day


def test_hash_crosscheck_flags_unexplained_change(tmp_path):
    """Issue #302: bytes changed with no logged rewrite event → loud flag.
    The crosscheck only ever raises status, never lowers it (this tiny file
    already fails for other reasons, so it stays FAIL)."""
    from scripts.verify_tick_data import apply_hash_crosscheck

    day = _crosscheck_day(tmp_path, body=b'{"a": 1}\n')
    day.write_bytes(b'{"a": 999}\n')  # silent mutation
    rep = verify_tick_file(day)
    apply_hash_crosscheck(day, rep)

    assert rep["hash_crosscheck"]["flagged"] is True
    assert any(i.get("kind") == "unexplained_rewrite" for i in rep["sample_issues"])
    assert rep["status"] == "FAIL"  # pre-existing failure is preserved
    # Second verification does not re-flag the same change (store was updated).
    rep2 = verify_tick_file(day)
    apply_hash_crosscheck(day, rep2)
    assert rep2["hash_crosscheck"]["flagged"] is False


def test_hash_crosscheck_raises_pass_to_warn_on_unexplained_change(tmp_path):
    """Issue #302: a passing report with an unexplained change is raised to WARN."""
    from scripts.verify_tick_data import apply_hash_crosscheck

    day = tmp_path / "ticks_2026-09-13.jsonl"
    day.write_bytes(b'{"a": 1}\n')
    apply_hash_crosscheck(day, {"status": "PASS",
                                "capture_state": {"label": "PARTIAL CAPTURE"}})
    day.write_bytes(b'{"a": 2}\n')
    rep = {"status": "PASS", "capture_state": {"label": "PARTIAL CAPTURE"}}
    apply_hash_crosscheck(day, rep)
    assert rep["status"] == "WARN" and rep["hash_crosscheck"]["flagged"] is True


def test_hash_crosscheck_explained_rewrite_does_not_flag(tmp_path):
    """Issue #302: a change with a matching old→new rewrite event is legitimate."""
    from scripts.tick_safety import record_rewrite_event
    from scripts.verify_tick_data import apply_hash_crosscheck
    from scripts.ship_to_drive import sha256_of

    day = _crosscheck_day(tmp_path, body=b'{"a": 1}\n')
    old_sha = sha256_of(day)
    day.write_bytes(b'{"a": 2}\n')
    new_sha = sha256_of(day)
    record_rewrite_event(tmp_path, day.name, old_sha, new_sha, "backup/x")

    rep = verify_tick_file(day)
    apply_hash_crosscheck(day, rep)
    assert rep["hash_crosscheck"]["flagged"] is False


def test_hash_crosscheck_fails_completed_past_day_on_unexplained_change(tmp_path):
    """Issue #302: an unexplained change to a COMPLETE-past-day file is FAIL."""
    from scripts.verify_tick_data import apply_hash_crosscheck
    from tests.test_build_golden_dataset import _write_day

    _write_day(tmp_path, "ticks_2026-09-13.jsonl", n_windows=1)
    day = tmp_path / "ticks_2026-09-13.jsonl"
    rep = verify_tick_file(day)
    assert (rep.get("capture_state") or {}).get("label") == "COMPLETE CAPTURE"
    apply_hash_crosscheck(day, rep)
    assert rep["hash_crosscheck"]["flagged"] is False

    day.write_bytes(b'{"tampered": true}\n')
    rep2 = verify_tick_file(day)
    apply_hash_crosscheck(day, rep2)
    assert rep2["hash_crosscheck"]["flagged"] is True
    assert rep2["status"] == "FAIL"
