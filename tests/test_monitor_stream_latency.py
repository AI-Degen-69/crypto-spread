"""Unit tests for scripts/monitor_stream_latency.py stream synchronizer and monitor."""
import time
import pytest

from scripts.monitor_stream_latency import (
    StreamTickSnapshot,
    StreamSynchronizer,
)


def test_snapshot_to_dict():
    """Verify StreamTickSnapshot serializes cleanly to dict for JSON output."""
    snap = StreamTickSnapshot(
        timestamp=1788394715.0,
        time_str="12:45:15",
        symbol="btcusdt",
        series_slug="btc-up-or-down-5m",
        spot_price=65420.50,
        spot_drift_pct=0.0015,
        up_bid=0.48,
        up_ask=0.52,
        up_mid=0.500,
        down_bid=0.48,
        down_ask=0.52,
        down_mid=0.500,
        clob_mid=0.500,
        latency_ms=125.4,
        spot_source="RTDS",
        clob_source="WS",
    )
    d = snap.to_dict()
    assert d["timestamp"] == 1788394715.0
    assert d["time_str"] == "12:45:15"
    assert d["symbol"] == "btcusdt"
    assert d["series"] == "btc-up-or-down-5m"
    assert d["spot_price"] == 65420.50
    assert pytest.approx(d["spot_drift_pct"], 0.0001) == 0.0015
    assert d["up_bid"] == 0.48
    assert d["up_ask"] == 0.52
    assert d["up_mid"] == 0.500
    assert d["down_bid"] == 0.48
    assert d["down_ask"] == 0.52
    assert d["down_mid"] == 0.500
    assert d["clob_mid"] == 0.500
    assert d["latency_ms"] == 125.4
    assert d["spot_source"] == "RTDS"
    assert d["clob_source"] == "WS"


def test_snapshot_format_row():
    """Verify console table row formatting displays all critical fields."""
    snap = StreamTickSnapshot(
        timestamp=1788394715.0,
        time_str="12:45:15",
        symbol="btcusdt",
        series_slug="btc-up-or-down-5m",
        spot_price=65420.50,
        spot_drift_pct=0.0015,
        up_bid=0.48,
        up_ask=0.52,
        up_mid=0.500,
        down_bid=0.48,
        down_ask=0.52,
        down_mid=0.500,
        clob_mid=0.500,
        latency_ms=125.0,
        spot_source="RTDS",
        clob_source="WS",
    )
    row = snap.format_row()
    assert "[12:45:15]" in row
    assert "$ 65420.50" in row or "65420.50" in row
    assert "+0.15%" in row
    assert "UP: 0.48/0.52" in row
    assert "DN: 0.48/0.52" in row
    assert "CLOB Mid: 0.500" in row
    assert "125ms" in row


def test_synchronizer_tick_generation():
    """Verify StreamSynchronizer aligns spot and book state into a snapshot."""
    sync = StreamSynchronizer(series_slug="btc-up-or-down-5m")
    
    # Update spot price baseline (t0)
    now_ms = int(time.time() * 1000)
    sync.update_spot(60000.0, now_ms)
    
    # Update books
    sync.update_up_book(best_bid=0.48, best_ask=0.52, updated_ts=now_ms / 1000.0)
    sync.update_down_book(best_bid=0.48, best_ask=0.52, updated_ts=now_ms / 1000.0)
    
    # Advance spot price (+0.5%)
    sync.update_spot(60300.0, now_ms + 1000)
    
    snap = sync.create_snapshot(now_ts=now_ms / 1000.0 + 1.0)
    assert snap is not None
    assert snap.symbol == "btcusdt"
    assert snap.series_slug == "btc-up-or-down-5m"
    assert snap.spot_price == 60300.0
    assert pytest.approx(snap.spot_drift_pct, 0.0001) == 0.005  # +0.5%
    assert snap.up_bid == 0.48
    assert snap.up_ask == 0.52
    assert snap.up_mid == 0.50
    assert snap.clob_mid == 0.50
    assert snap.latency_ms >= 0.0


def test_parse_args_defaults():
    """Verify default CLI arguments."""
    from scripts.monitor_stream_latency import parse_args
    args = parse_args([])
    assert args.series == "btc-up-or-down-5m"
    assert args.duration == 0
    assert args.ticks == 0
    assert pytest.approx(args.threshold, 0.0001) == 0.001
    assert not args.json
    assert not args.audit


def test_parse_args_custom():
    """Verify custom CLI argument parsing."""
    from scripts.monitor_stream_latency import parse_args
    args = parse_args([
        "--series", "eth-up-or-down-5m",
        "--duration", "45",
        "--ticks", "10",
        "--threshold", "0.002",
        "--json",
        "--audit",
    ])
    assert args.series == "eth-up-or-down-5m"
    assert args.duration == 45
    assert args.ticks == 10
    assert pytest.approx(args.threshold, 0.0001) == 0.002
    assert args.json
    assert args.audit


def test_run_monitor_ticks_limit(capsys):
    """Verify run_monitor terminates when requested ticks limit is reached."""
    from scripts.monitor_stream_latency import run_monitor, parse_args
    from unittest.mock import patch, MagicMock

    args = parse_args(["--series", "btc-up-or-down-5m", "--ticks", "2", "--json"])

    with patch("scripts.monitor_stream_latency.fetch_spot_price", return_value=65000.0), \
         patch("scripts.monitor_stream_latency.fetch_clob_books", return_value=(0.48, 0.52, 0.48, 0.52)):
        count = run_monitor(args, sleep_interval=0.01)
        assert count == 2

    captured = capsys.readouterr()
    lines = [ln for ln in captured.out.strip().split("\n") if ln.strip()]
    assert len(lines) == 2
    for ln in lines:
        import json
        data = json.loads(ln)
        assert data["symbol"] == "btcusdt"
        assert data["spot_price"] == 65000.0
        assert data["clob_mid"] == 0.50

