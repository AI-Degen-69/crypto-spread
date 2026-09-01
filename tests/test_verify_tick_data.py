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
    ticks = [make_sample_tick(ts=1725000000.0 + i) for i in range(10)]
    clean_file.write_text("\n".join(json.dumps(t) for t in ticks) + "\n", encoding="utf-8")

    rep = verify_tick_file(clean_file)
    assert rep["status"] == "PASS"
    assert rep["valid_ticks"] == 10
    assert rep["corrupt_lines"] == 0

    # Add corrupt lines
    corrupt_file = tmp_path / "ticks_corrupt.jsonl"
    corrupt_content = json.dumps(ticks[0]) + "\n{INVALID_JSON\n" + json.dumps(ticks[1]) + "\n"
    corrupt_file.write_text(corrupt_content, encoding="utf-8")

    rep_corrupt = verify_tick_file(corrupt_file)
    assert rep_corrupt["status"] == "FAIL"
    assert rep_corrupt["corrupt_lines"] == 1
    assert rep_corrupt["valid_ticks"] == 2


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
