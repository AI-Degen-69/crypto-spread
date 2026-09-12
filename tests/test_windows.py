"""Tests for strategy/windows.py shared window logic."""
import json

from strategy.windows import (
    classify_window,
    compute_summary,
    finalize_window,
    write_json_atomic,
)


def _meta(**over):
    base = {
        "series": "btc-up-or-down-5m",
        "label": "BTC 5m",
        "duration": 300,
        "cid": "0xABC",
        "slug": "btc-updown-5m-1",
        "start_ts": 1000.0,
        "end_ts": 1300.0,
        "closed_ts": 1301.0,
        "snaps": 300,
    }
    base.update(over)
    return base


def test_classify_window_thresholds():
    assert classify_window([]) == "no_data"
    assert classify_window([0.50, 0.505, 0.495]) == "flat"
    assert classify_window([0.50, 0.53, 0.54]) == "monotonic"
    assert classify_window([0.50, 0.46, 0.45]) == "monotonic"
    assert classify_window([0.50, 0.47, 0.53]) == "oscillating"


def test_finalize_window_schema_and_rounding():
    mids = [0.51234, 0.53456, 0.47654]
    rec = finalize_window(mids, [1.01, 1.02, 1.03], _meta())
    assert rec["series"] == "btc-up-or-down-5m"
    assert rec["cid"] == "0xABC"
    assert rec["snaps"] == 300
    assert rec["start_mid"] == round(0.51234, 4)
    assert rec["close_mid"] == round(0.47654, 4)
    assert rec["min_mid"] == round(0.47654, 4)
    assert rec["max_mid"] == round(0.53456, 4)
    assert rec["max_up"] == round(0.53456 - 0.50, 4)
    assert rec["max_down"] == round(0.50 - 0.47654, 4)
    assert rec["class"] == "oscillating"
    assert rec["touch_pair_median"] == 1.02
    assert rec["url"] == "https://polymarket.com/market/btc-updown-5m-1"


def test_finalize_window_empty_mids():
    rec = finalize_window([], [], _meta(slug=""))
    assert rec["class"] == "no_data"
    assert rec["start_mid"] is None
    assert rec["touch_pair_median"] is None
    assert rec["url"] == ""


def test_compute_summary_shape_and_zero_fill():
    rec = finalize_window(
        [0.50, 0.53], [1.0], _meta(series="btc-up-or-down-5m")
    )
    summary = compute_summary([rec])
    assert "ts" in summary
    per = summary["per_series"]
    assert per["btc-up-or-down-5m"]["windows"] == 1
    assert per["btc-up-or-down-5m"]["oscillating"] == 0
    assert per["btc-up-or-down-5m"]["monotonic"] == 1
    # Untouched series are zero-filled with the zero shape
    assert per["eth-up-or-down-5m"]["windows"] == 0
    assert per["eth-up-or-down-5m"]["pair_cost_median"] is None
    assert per["eth-up-or-down-5m"]["recent"] == []


def test_write_json_atomic(tmp_path):
    target = tmp_path / "summary.json"
    write_json_atomic(target, {"ts": 1.0, "per_series": {}})
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["ts"] == 1.0
    assert list(tmp_path.iterdir()) == [target]
