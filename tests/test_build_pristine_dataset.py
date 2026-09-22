"""Tests for scripts/build_pristine_dataset.py — pristine-window dataset extractor (issue #290)."""
from __future__ import annotations

import dataclasses
import json
import time

import pytest

from scripts.build_pristine_dataset import (
    MANIFEST_NAME,
    VERIFY_POLICY_NOTE,
    PristineGateParams,
    build_pristine_dataset,
    evaluate_window_gates,
    evaluate_window_state,
    scan_windows,
    sha256_of,
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


def write_ticks_dir(tmp_path, days: dict[str, list[dict]]):
    """Materialize a ticks dir like the collector writes it (one file per UTC day)."""
    for day_name, ticks in days.items():
        p = tmp_path / day_name
        with open(p, "w", encoding="utf-8") as f:
            for t in ticks:
                f.write(json.dumps(t) + "\n")
    return tmp_path


def day_of(ts):
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


@pytest.fixture()
def two_day_dir(tmp_path):
    """A pristine 5m window truly spanning midnight (23:55 -> 00:00) plus a wrecked one."""
    start = (BASE_TS // 86400) * 86400 + 86100.0  # 23:55:00 UTC of BASE_TS's day
    good = [
        make_tick(ts=start - BASE_TS + float(i), start_ts=start, end_ts=start + 300.0)
        for i in range(302)  # last two ticks land past midnight -> collector splits across day files
    ]
    bad = [
        make_tick(ts=start + 300.0 - BASE_TS + round(i * 5.9, 3),
                  start_ts=start + 300.0, end_ts=start + 600.0, cid="0xbad")
        for i in range(40)
    ]
    d1 = day_of(start)
    d2 = day_of(start + 301.0)  # the day the window's final ticks land on
    write_ticks_dir(tmp_path, {
        f"ticks_{d1}.jsonl": good[:150],
        f"ticks_{d2}.jsonl": good[150:] + bad,
    })  # d2 gets the window's tail (150..299) plus the next window's wrecked ticks
    return tmp_path, good, bad


class TestWritePristineDataset:
    def test_whole_windows_keyed_to_start_day(self, two_day_dir):
        ticks_dir, good, _ = two_day_dir
        out = ticks_dir / "pristine"
        report = build_pristine_dataset(ticks_dir, out)
        day_file = out / f"ticks_{day_of(good[0]['start_ts'])}.jsonl"
        assert day_file.is_file()
        lines = [json.loads(x) for x in day_file.read_text(encoding="utf-8").splitlines() if x.strip()]
        assert lines == good  # whole window, original order, no early cutoff

    def test_failing_window_absent_from_output(self, two_day_dir):
        ticks_dir, _, bad = two_day_dir
        report = build_pristine_dataset(ticks_dir, ticks_dir / "pristine")
        every_line_cid = {
            json.loads(x)["cid"]
            for f in (ticks_dir / "pristine").glob("ticks_*.jsonl")
            for x in f.read_text(encoding="utf-8").splitlines() if x.strip()
        }
        assert "0xbad" not in every_line_cid
        assert "0xbad" not in report["passed_cids"]

    def test_manifest_records_verdicts_counts_and_hashes(self, two_day_dir):
        ticks_dir, good, bad = two_day_dir
        out = ticks_dir / "pristine"
        report = build_pristine_dataset(ticks_dir, out)
        man = json.loads((out / MANIFEST_NAME).read_text(encoding="utf-8"))
        assert man["policy_note"] == VERIFY_POLICY_NOTE
        assert man["gates"] == dataclasses.asdict(PristineGateParams())
        assert {w["cid"] for w in man["windows"]} == {"0xabc", "0xbad"}
        good_w = next(w for w in man["windows"] if w["cid"] == "0xabc")
        assert good_w["passed"] is True and good_w["failing_gates"] == []
        assert good_w["source_files"] == [
            f"ticks_{day_of(good[0]['start_ts'])}.jsonl",
            f"ticks_{day_of(good[-1]['ts'])}.jsonl",
        ]
        assert good_w["source_sha256s"] and all(len(h) == 64 for h in good_w["source_sha256s"].values())
        bad_w = next(w for w in man["windows"] if w["cid"] == "0xbad")
        assert bad_w["passed"] is False
        assert "snap_density" in bad_w["failing_gates"]
        assert man["per_pair_pristine_counts"]["btc-up-or-down-5m:300"] == 1
        assert man["totals"]["windows_total"] == 2
        assert man["totals"]["ticks_written"] == len(good)
        assert man["totals"]["windows_passed"] == 1

    def test_sources_untouched_hashes_before_after(self, two_day_dir):
        ticks_dir, _, _ = two_day_dir
        src = sorted(p.name for p in ticks_dir.glob("ticks_*.jsonl"))
        before = {n: sha256_of(ticks_dir / n) for n in src}
        build_pristine_dataset(ticks_dir, ticks_dir / "pristine")
        after = {n: sha256_of(ticks_dir / n) for n in src}
        assert before == after

    def test_rerun_is_byte_identical(self, two_day_dir):
        ticks_dir, _, _ = two_day_dir
        out = ticks_dir / "pristine"
        build_pristine_dataset(ticks_dir, out)
        first = {p.name: p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file()}
        build_pristine_dataset(ticks_dir, out)
        second = {p.name: p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file()}
        assert first == second

    def test_overwrite_prunes_stale_outputs(self, two_day_dir):
        ticks_dir, _, _ = two_day_dir
        out = ticks_dir / "pristine"
        build_pristine_dataset(ticks_dir, out)
        stale = out / f"ticks_{day_of(BASE_TS + 86400)}.jsonl"
        stale.write_text("{\"cid\": \"stale\"}\n", encoding="utf-8")
        build_pristine_dataset(ticks_dir, out)
        assert not stale.exists()

    def test_refuses_out_inside_ticks_dir(self, two_day_dir):
        ticks_dir, _, _ = two_day_dir
        with pytest.raises(ValueError):
            build_pristine_dataset(ticks_dir, ticks_dir)  # exact overlap clobbers sources

    def test_empty_input_dir_exits_clean(self, tmp_path):
        with pytest.raises(ValueError):
            build_pristine_dataset(tmp_path, tmp_path / "pristine")

    def test_output_verify_embedded(self, two_day_dir):
        ticks_dir, _, _ = two_day_dir
        out = ticks_dir / "pristine"
        report = build_pristine_dataset(ticks_dir, out)
        ov = report["manifest"]["output_verify"]
        assert ov["status"] == "PASS"
        assert ov["files_checked"] == 1
        for k in ("total_late_starts", "total_early_cutoffs", "total_sampling_gaps",
                  "total_time_reversals", "total_collector_errors"):
            assert ov[k] == 0
