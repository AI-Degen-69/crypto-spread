"""Tests for scripts/build_pristine_dataset.py — pristine-window dataset extractor (issue #290)."""
from __future__ import annotations

import time

import pytest

from scripts.build_pristine_dataset import (
    PristineGateParams,
    evaluate_window_gates,
    evaluate_window_state,
    scan_windows,
)

BASE_TS = 1725000000.0


def make_tick(ts=None, cid="0xabc", err=None, **overrides) -> dict:
    """One fully valid snap (same shape tests/test_verify_tick_data.py uses)."""
    tick = {
        "ts": BASE_TS if ts is None else BASE_TS + ts,
        "iso": "2026-08-31T00:00:00+00:00",
        "series": "btc-up-or-down-5m",
        "duration": 300,
        "label": "BTC 5m",
        "cid": cid,
        "slug": "btc-up-or-down-5m-2026-08-31",
        "start_ts": BASE_TS,
        "end_ts": BASE_TS + 300.0,
        "t_rem": 300.0,
        "up_book": {
            "bids": {"0.49": 100.0},
            "asks": {"0.51": 100.0},
            "best_bid": 0.49,
            "best_ask": 0.51,
            "malformed": 0,
        },
        "down_book": {
            "bids": {"0.48": 100.0},
            "asks": {"0.51": 100.0},
            "best_bid": 0.48,
            "best_ask": 0.51,
            "malformed": 0,
        },
        "mid": 0.50,
        "touch_pair": 1.02,
        "tape_delta": [],
        "err": err,
    }
    tick.update(overrides)
    return tick


def pristine_ticks(n=300, cid="0xabc", **kw) -> list[dict]:
    """Perfectly sampled window: n ticks, 1s apart from window open."""
    return [make_tick(ts=float(i), cid=cid, **kw) for i in range(n)]


class TestPristineGateParams:
    def test_frozen_defaults_mirror_verify(self):
        p = PristineGateParams()
        assert (p.max_gap_sec, p.max_start_delay_sec, p.max_snap_interval_sec) == (6.0, 5.0, 3.0)

    def test_frozen_cannot_drift(self):
        p = PristineGateParams()
        with pytest.raises(Exception):
            p.max_gap_sec = 1.0


class TestEvaluateWindowGates:
    def test_pristine_window_passes(self):
        v = evaluate_window_gates(pristine_ticks(), PristineGateParams())
        assert v["passed"] is True
        assert v["failing_gates"] == []
        assert v["cid"] == "0xabc"
        assert v["start_ts"] == BASE_TS
        assert v["end_ts"] == BASE_TS + 300.0
        assert v["tick_count"] == 300

    def test_late_start_fails(self):
        ticks = pristine_ticks()
        for t in ticks:
            t["ts"] += 6.0
        v = evaluate_window_gates(ticks, PristineGateParams())
        assert v["passed"] is False
        assert "late_start" in v["failing_gates"]
        assert v["start_delay_sec"] == 6.0

    def test_early_cutoff_fails(self):
        ticks = pristine_ticks()[:295]  # last tick exactly 6s before window close
        v = evaluate_window_gates(ticks, PristineGateParams())
        assert "early_cutoff" in v["failing_gates"]
        assert v["end_cutoff_sec"] == 6.0

    def test_sampling_gap_fails(self):
        ticks = pristine_ticks()
        for t in ticks[150:]:
            t["ts"] += 7.0  # one 8s gap in the middle
        v = evaluate_window_gates(ticks, PristineGateParams())
        assert "sampling_gap" in v["failing_gates"]
        assert v["gaps_count"] == 1
        assert v["max_gap_sec"] == 8.0

    def test_time_reversal_fails(self):
        ticks = pristine_ticks()
        ticks[100]["ts"] = ticks[99]["ts"] - 1.0
        v = evaluate_window_gates(ticks, PristineGateParams())
        assert "time_reversal" in v["failing_gates"]
        assert v["time_reversals"] == 1

    def test_collector_error_fails(self):
        ticks = pristine_ticks()
        ticks[50] = make_tick(ts=50.0, err="up_book: boom")
        v = evaluate_window_gates(ticks, PristineGateParams())
        assert "collector_error" in v["failing_gates"]
        assert v["error_ticks"] == 1

    def test_snap_density_floor_catches_burst_gaps(self):
        # 5.9s spacing: no gap exceeds 6s, but only 51 snaps for a 300s window.
        ticks = [make_tick(ts=round(i * 5.9, 3)) for i in range(51)]
        v = evaluate_window_gates(ticks, PristineGateParams())
        assert "sampling_gap" not in v["failing_gates"]
        assert "snap_density" in v["failing_gates"]
        assert v["min_snaps"] == 100

    def test_density_boundary_exact_rate_passes(self):
        ticks = [make_tick(ts=float(i * 3)) for i in range(100)]  # exactly 1 per 3s
        v = evaluate_window_gates(ticks, PristineGateParams())
        assert v["passed"] is True

    def test_thresholds_are_overridable(self):
        ticks = pristine_ticks()
        for t in ticks:
            t["ts"] += 6.0  # late under default 5s, fine under 10s
        v = evaluate_window_gates(ticks, PristineGateParams(max_start_delay_sec=10.0))
        assert v["passed"] is True

    def test_empty_window_fails(self):
        v = evaluate_window_gates([], PristineGateParams())
        assert v["passed"] is False
        assert "no_ticks" in v["failing_gates"]

    def test_start_day_is_utc_day_of_start_ts(self):
        v = evaluate_window_gates(pristine_ticks(), PristineGateParams())
        assert v["start_day"] == time.strftime("%Y-%m-%d", time.gmtime(BASE_TS))


class TestScanWindows:
    def test_midnight_spanning_window_merges_and_keys_to_start_day(self):
        day1 = [make_tick(ts=float(i), cid="0xmid") for i in range(180)]
        day2 = [make_tick(ts=180.0 + float(i), cid="0xmid") for i in range(120)]
        states = scan_windows(
            [("ticks_2026-09-13.jsonl", day1), ("ticks_2026-09-14.jsonl", day2)]
        )
        st = states["0xmid"]
        assert st["tick_count"] == 300
        assert st["source_files"] == ["ticks_2026-09-13.jsonl", "ticks_2026-09-14.jsonl"]
        v = evaluate_window_state(st, PristineGateParams())
        assert v["passed"] is True
        assert v["start_day"] == time.strftime("%Y-%m-%d", time.gmtime(BASE_TS))

    def test_windows_stay_separate_by_cid(self):
        states = scan_windows(
            [("f.jsonl", pristine_ticks(cid="0xaaa") + pristine_ticks(cid="0xbbb"))]
        )
        assert set(states) == {"0xaaa", "0xbbb"}
        for st in states.values():
            assert st["source_files"] == ["f.jsonl"]
