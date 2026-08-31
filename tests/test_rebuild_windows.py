"""Tests for scripts/rebuild_windows.py."""
import gzip
import json
from pathlib import Path

import pytest
from scripts.rebuild_windows import (
    build_windows_from_ticks,
    classify_window,
    compute_summary,
    rebuild_windows,
)


def test_classify_window():
    assert classify_window([]) == "no_data"
    assert classify_window([0.50, 0.505, 0.495]) == "flat"
    assert classify_window([0.50, 0.53, 0.54]) == "monotonic"
    assert classify_window([0.50, 0.46, 0.45]) == "monotonic"
    assert classify_window([0.50, 0.47, 0.53]) == "oscillating"


def test_build_windows_from_ticks():
    ticks = [
        {
            "ts": 1000.0,
            "series": "btc-up-or-down-5m",
            "label": "BTC 5m",
            "duration": 300,
            "cid": "0x111",
            "slug": "btc-5m-1",
            "start_ts": 1000.0,
            "end_ts": 1300.0,
            "mid": 0.50,
            "touch_pair": 1.01,
        },
        {
            "ts": 1001.0,
            "series": "btc-up-or-down-5m",
            "label": "BTC 5m",
            "duration": 300,
            "cid": "0x111",
            "slug": "btc-5m-1",
            "start_ts": 1000.0,
            "end_ts": 1300.0,
            "mid": 0.53,
            "touch_pair": 1.03,
        },
        {
            "ts": 1002.0,
            "series": "btc-up-or-down-5m",
            "label": "BTC 5m",
            "duration": 300,
            "cid": "0x111",
            "slug": "btc-5m-1",
            "start_ts": 1000.0,
            "end_ts": 1300.0,
            "mid": 0.47,
            "touch_pair": 1.02,
        },
        # Window 2
        {
            "ts": 2000.0,
            "series": "eth-up-or-down-5m",
            "label": "ETH 5m",
            "duration": 300,
            "cid": "0x222",
            "slug": "eth-5m-2",
            "start_ts": 2000.0,
            "end_ts": 2300.0,
            "mid": 0.50,
            "touch_pair": 1.00,
        },
    ]

    windows = build_windows_from_ticks(ticks)
    assert len(windows) == 2

    w1 = next(w for w in windows if w["cid"] == "0x111")
    assert w1["series"] == "btc-up-or-down-5m"
    assert w1["snaps"] == 3
    assert w1["start_mid"] == 0.50
    assert w1["close_mid"] == 0.47
    assert w1["min_mid"] == 0.47
    assert w1["max_mid"] == 0.53
    assert w1["max_up"] == 0.03
    assert w1["max_down"] == 0.03
    assert w1["class"] == "oscillating"
    assert w1["touch_pair_median"] == 1.02
    assert w1["url"] == "https://polymarket.com/market/btc-5m-1"

    w2 = next(w for w in windows if w["cid"] == "0x222")
    assert w2["series"] == "eth-up-or-down-5m"
    assert w2["snaps"] == 1
    assert w2["class"] == "flat"


def test_compute_summary():
    windows = [
        {
            "series": "btc-up-or-down-5m",
            "label": "BTC 5m",
            "duration": 300,
            "cid": "0x111",
            "slug": "btc-5m-1",
            "start_ts": 1000.0,
            "end_ts": 1300.0,
            "max_up": 0.03,
            "max_down": 0.03,
            "class": "oscillating",
            "touch_pair_median": 1.02,
        },
        {
            "series": "btc-up-or-down-5m",
            "label": "BTC 5m",
            "duration": 300,
            "cid": "0x112",
            "slug": "btc-5m-2",
            "start_ts": 1300.0,
            "end_ts": 1600.0,
            "max_up": 0.01,
            "max_down": 0.00,
            "class": "flat",
            "touch_pair_median": 1.00,
        },
    ]

    summary = compute_summary(windows)
    assert "per_series" in summary
    btc = summary["per_series"]["btc-up-or-down-5m"]
    assert btc["windows"] == 2
    assert btc["oscillating"] == 1
    assert btc["flat"] == 1
    assert btc["any_2c"] == 1
    assert btc["any_3c"] == 1
    assert len(btc["recent"]) == 2

    # Series with 0 windows should be populated cleanly
    eth = summary["per_series"]["eth-up-or-down-5m"]
    assert eth["windows"] == 0
    assert eth["oscillating"] == 0


def test_rebuild_windows_integration(tmp_path: Path):
    ticks_dir = tmp_path / "ticks"
    ticks_dir.mkdir()

    raw_ticks_1 = [
        {"ts": 100.0, "series": "btc-up-or-down-5m", "cid": "0xAAA", "slug": "btc-1", "mid": 0.50, "start_ts": 100.0, "end_ts": 400.0, "touch_pair": 1.01},
        {"ts": 101.0, "series": "btc-up-or-down-5m", "cid": "0xAAA", "slug": "btc-1", "mid": 0.55, "start_ts": 100.0, "end_ts": 400.0, "touch_pair": 1.02},
    ]
    raw_ticks_2 = [
        {"ts": 500.0, "series": "sol-up-or-down-5m", "cid": "0xBBB", "slug": "sol-1", "mid": 0.45, "start_ts": 500.0, "end_ts": 800.0, "touch_pair": 1.04},
    ]

    f1 = ticks_dir / "ticks_2026-08-30.jsonl"
    with open(f1, "w", encoding="utf-8") as f:
        for t in raw_ticks_1:
            f.write(json.dumps(t) + "\n")

    f2 = ticks_dir / "ticks_2026-08-31.jsonl.gz"
    with gzip.open(f2, "wt", encoding="utf-8") as f:
        for t in raw_ticks_2:
            f.write(json.dumps(t) + "\n")

    out_win = tmp_path / "oscillation_windows.jsonl"
    out_sum = tmp_path / "oscillation_summary.json"

    num_files, num_windows = rebuild_windows(
        ticks_dir=ticks_dir,
        out_windows=out_win,
        out_summary=out_sum,
        quiet=True,
    )

    assert num_files == 2
    assert num_windows == 2
    assert out_win.exists()
    assert out_sum.exists()

    win_lines = [json.loads(line) for line in out_win.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(win_lines) == 2
    assert win_lines[0]["cid"] == "0xAAA"
    assert win_lines[0]["class"] == "monotonic"
    assert win_lines[1]["cid"] == "0xBBB"
