"""Integration tests for the 4-tab dashboard SPA and FastAPI API endpoints."""
import json
import subprocess
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
import server.osc_dash as osc_dash
from server.osc_dash import app

client = TestClient(app)


def _make_fake_tick(ts: float, cid: str, slug: str, series: str, mid: float, tape: list | None = None) -> dict:
    """Build minimal tick dictionary for backtest simulation testing."""
    up_tok = f"{cid}_up"
    dn_tok = f"{cid}_dn"
    return {
        "ts": ts,
        "iso": "2026-08-31T00:00:00+00:00",
        "series": series,
        "duration": 300,
        "label": "BTC 5m",
        "cid": cid,
        "slug": slug,
        "start_ts": ts - 10,
        "end_ts": ts + 290,
        "t_rem": 290,
        "up_token": up_tok,
        "down_token": dn_tok,
        "up_book": {"token_id": up_tok, "bids": {"0.48": 10}, "asks": {}, "best_bid": mid - 0.005, "best_ask": mid + 0.005, "malformed": 0},
        "down_book": {"token_id": dn_tok, "bids": {"0.48": 10}, "asks": {}, "best_bid": 0.485, "best_ask": 0.495, "malformed": 0},
        "tape_delta": tape or [],
        "mid": mid,
        "touch_pair": 0.99,
        "resting_pair": 0.96,
        "queue_up": 10,
        "queue_down": 10,
        "err": None,
    }


def test_root_returns_4tab_spa():
    """Verify that root endpoint serves the full 4-tab SPA HTML with all containers."""
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text
    assert "tab-btn-live" in html
    assert "tab-btn-backtest" in html
    assert "tab-btn-summary" in html
    assert "tab-btn-ticks" in html
    assert "tab-live" in html
    assert "tab-backtest" in html
    assert "tab-summary" in html
    assert "tab-ticks" in html
    assert "collectorBadge" in html
    assert "tapeBadge" in html
    assert "switchTab" in html
    assert "loadManifest" in html
    assert "uploadFileStream" in html


def test_api_oscillation():
    """Verify oscillation payload returns summary, windows, live snapshots, and goals."""
    response = client.get("/api/oscillation")
    assert response.status_code == 200
    data = response.json()
    assert "summary" in data
    assert "windows" in data
    assert "live" in data
    assert "goals" in data


def test_api_ticks_manifest(tmp_path, monkeypatch):
    """Verify manifest endpoint returns file listings with byte sizes and line estimates."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    test_file = tmp_path / "test_ticks.jsonl"
    test_file.write_text('{"a": 1}\n{"a": 2}\n', encoding="utf-8")

    response = client.get("/api/ticks/manifest")
    assert response.status_code == 200
    data = response.json()
    assert "files" in data
    assert len(data["files"]) == 1
    assert data["files"][0]["name"] == "test_ticks.jsonl"
    assert data["files"][0]["lines"] == 2
    assert data["files"][0]["lines_estimated"] is False


def test_api_collector_lifecycle_and_status(monkeypatch):
    """Verify collector start, status, and stop workflow with mocked process."""
    class DummyProc:
        def __init__(self):
            self.pid = 99999
            self._running = True

        def poll(self):
            return None if self._running else 0

        def terminate(self):
            self._running = False

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self._running = False

    monkeypatch.setattr(osc_dash.subprocess, "Popen", lambda *args, **kwargs: DummyProc())
    try:
        # Start collector
        res_start = client.post("/api/collector/start")
        assert res_start.status_code == 200
        assert res_start.json().get("running") is True
        assert res_start.json().get("pid") == 99999

        # Check status
        res_status = client.get("/api/collector/status")
        assert res_status.status_code == 200
        assert res_status.json().get("running") is True
        assert res_status.json().get("pid") == 99999

        # Stop collector
        res_stop = client.post("/api/collector/stop")
        assert res_stop.status_code == 200
        assert res_stop.json().get("running") is False
    finally:
        osc_dash._collector_proc = None


def test_api_collector_poll_once(monkeypatch):
    """Verify single poll collector endpoint invokes single collection pass via subprocess."""
    def _mock_run(*args, **kwargs):
        class DummyResult:
            returncode = 0
            stdout = "Collected 10 series successfully"
            stderr = ""
        return DummyResult()

    monkeypatch.setattr(subprocess, "run", _mock_run)
    response = client.post("/api/collector/poll-once")
    assert response.status_code == 200
    data = response.json()
    assert data.get("ok") is True
    assert "Collected 10 series" in data.get("output")


def test_api_collector_status_tape_metrics(tmp_path, monkeypatch):
    """Verify collector status endpoint surfaces tape empty rate and alert flag."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    
    # Without manifest
    res = client.get("/api/collector/status")
    assert res.status_code == 200
    assert res.json()["tape_empty_rate"] is None
    assert res.json()["tape_alert"] is False

    # With normal tape empty rate (e.g. 98.8%)
    mf = tmp_path / "manifest.json"
    mf.write_text(
        json.dumps({
            "lines": 500,
            "tape_empty_count": 494,
            "tape_non_empty_count": 6,
            "tape_empty_rate": 0.988,
            "tape_entries_total": 12,
        }),
        encoding="utf-8",
    )
    res = client.get("/api/collector/status")
    assert res.status_code == 200
    d = res.json()
    assert d["tape_empty_rate"] == 0.988
    assert d["tape_entries_total"] == 12
    assert d["tape_alert"] is False

    # With high tape silence (>99%)
    mf.write_text(
        json.dumps({
            "lines": 1000,
            "tape_empty_count": 995,
            "tape_non_empty_count": 5,
            "tape_empty_rate": 0.995,
            "tape_entries_total": 6,
        }),
        encoding="utf-8",
    )
    res = client.get("/api/collector/status")
    assert res.status_code == 200
    d = res.json()
    assert d["tape_empty_rate"] == 0.995
    assert d["tape_alert"] is True



def test_api_backtest_simulation(tmp_path, monkeypatch):
    """Verify backtest simulation on an isolated deterministic 4-window fixture."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    fake_file = tmp_path / "fake_round.jsonl"

    ticks = []
    for i in range(4):
        cid = f"0xCID_000{i}"
        slug = f"btc-updown-5m-100{i}"
        base_ts = 1000.0 + i * 500
        ticks.append(_make_fake_tick(base_ts, cid, slug, "btc-up-or-down-5m", 0.50, tape=[{"asset": f"{cid}_up", "price": 0.48, "size": 100}]))
        ticks.append(_make_fake_tick(base_ts + 1, cid, slug, "btc-up-or-down-5m", 0.48))
        ticks.append(_make_fake_tick(base_ts + 2, cid, slug, "btc-up-or-down-5m", 0.52, tape=[{"asset": f"{cid}_dn", "price": 0.46, "size": 100}]))
        ticks.append(_make_fake_tick(base_ts + 3, cid, slug, "btc-up-or-down-5m", 0.50))

    with open(fake_file, "w", encoding="utf-8") as f:
        for t in ticks:
            f.write(json.dumps(t) + "\n")

    url = "/api/backtest?file=fake_round.jsonl&offset=0.03&queue=75&pair_cost=0.98&exit_default_5m=0.15&fill_model=book&size=150&gas=0.02"
    response = client.get(url)
    assert response.status_code == 200
    data = response.json()
    assert "params_hash" in data
    assert "params" in data
    assert data["params"]["offset"] == 0.03
    assert data["params"]["queue"] == 75.0
    assert data["params"]["pair_cost"] == 0.98
    assert data["params"]["exit_default_5m"] == 0.15
    assert data["params"]["fill_model"] == "book"
    assert data["params"]["size"] == 150
    assert data["params"]["gas"] == 0.02
    assert "overall" in data
    assert "max_drawdown_cents" in data["overall"]
    assert "win_rate" in data["overall"]
    assert "per_series" in data
    assert len(data["per_series"]) == 10
    assert "equity_curve" in data
    assert len(data["equity_curve"]) == 4
    assert "trades_sample" in data
    assert len(data["trades_sample"]) == 4
    assert data["n_windows"] == 4

    # Test with fill_model=cross
    url_cross = "/api/backtest?file=fake_round.jsonl&offset=0.02&fill_model=cross"
    res_cross = client.get(url_cross)
    assert res_cross.status_code == 200
    assert res_cross.json()["params"]["fill_model"] == "cross"



def test_api_upload_stream_ingest(tmp_path, monkeypatch):
    """Verify single direct stream upload of JSONL payload with index creation."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    monkeypatch.setattr(osc_dash, "RUN", tmp_path)

    content = '{"ts": 100}\n{"ts": 200}\n{"ts": 300}\n'.encode("utf-8")
    filename = "streamed_ticks.jsonl"

    res = client.post(
        f"/api/ticks/upload-stream?filename={filename}",
        content=content,
        headers={"Content-Type": "application/octet-stream"}
    )
    assert res.status_code == 200
    d = res.json()
    assert d.get("ok") is True
    assert d.get("filename") == filename
    assert d.get("lines") == 3
    assert (tmp_path / filename).exists()
    assert (tmp_path / f"{filename}.idx").exists()


def test_api_upload_chunk_and_delete(tmp_path, monkeypatch):
    """Verify chunked upload assembly, retries, bounds checking, and deletion."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    monkeypatch.setattr(osc_dash, "RUN", tmp_path)

    content = '{"ts": 1}\n{"ts": 2}\n'.encode("utf-8")
    part1 = content[:8]
    part2 = content[8:]
    upload_id = "up_test_upload_123"
    filename = "uploaded_ticks.jsonl"

    # Out of range bounds checks
    res_bad_idx = client.post(
        f"/api/ticks/upload-chunk?filename={filename}&uploadId={upload_id}&chunkIndex=5&totalChunks=2",
        content=part1,
        headers={"Content-Type": "application/octet-stream"}
    )
    assert res_bad_idx.status_code == 400

    res_bad_total = client.post(
        f"/api/ticks/upload-chunk?filename={filename}&uploadId={upload_id}&chunkIndex=0&totalChunks=20000",
        content=part1,
        headers={"Content-Type": "application/octet-stream"}
    )
    assert res_bad_total.status_code == 400

    # Send Chunk 0
    res0 = client.post(
        f"/api/ticks/upload-chunk?filename={filename}&uploadId={upload_id}&chunkIndex=0&totalChunks=2",
        content=part1,
        headers={"Content-Type": "application/octet-stream"}
    )
    assert res0.status_code == 200
    assert res0.json().get("ok") is True

    # Send Chunk 1 (final)
    res1 = client.post(
        f"/api/ticks/upload-chunk?filename={filename}&uploadId={upload_id}&chunkIndex=1&totalChunks=2",
        content=part2,
        headers={"Content-Type": "application/octet-stream"}
    )
    assert res1.status_code == 200
    d1 = res1.json()
    assert d1.get("ok") is True
    assert d1.get("lines") == 2
    assert (tmp_path / filename).exists()
    assert (tmp_path / f"{filename}.idx").exists()

    # Retry final chunk after upload_dir was removed
    res1_retry = client.post(
        f"/api/ticks/upload-chunk?filename={filename}&uploadId={upload_id}&chunkIndex=1&totalChunks=2",
        content=part2,
        headers={"Content-Type": "application/octet-stream"}
    )
    assert res1_retry.status_code == 200
    assert res1_retry.json().get("lines") == 2

    # Delete uploaded file and verify index cleanup
    res_del = client.delete(f"/api/ticks/file?filename={filename}")
    assert res_del.status_code == 200
    assert not (tmp_path / filename).exists()
    assert not (tmp_path / f"{filename}.idx").exists()


def test_api_security_origin_and_path_traversal():
    """Verify cross-origin, invalid port, and path traversal requests are rejected."""
    # Malicious external origin
    res = client.post(
        "/api/collector/start",
        headers={"Origin": "https://malicious-site.evil.com"}
    )
    assert res.status_code == 403

    # Invalid port on loopback origin
    res_port = client.post(
        "/api/collector/start",
        headers={"Origin": "http://127.0.0.1:9999"}
    )
    assert res_port.status_code == 403

    # Invalid uploadId format / path traversal
    res_traversal = client.post(
        "/api/ticks/upload-chunk?filename=test.jsonl&uploadId=../../etc&chunkIndex=0&totalChunks=1",
        content=b"test",
        headers={"Content-Type": "application/octet-stream"}
    )
    assert res_traversal.status_code == 400

    # Path traversal in stream upload
    res_bad_stream = client.post(
        "/api/ticks/upload-stream?filename=../bad.jsonl",
        content=b'{"a":1}',
        headers={"Content-Type": "application/octet-stream"}
    )
    assert res_bad_stream.status_code == 400


def test_api_backtest_with_max_start_delay_filter(tmp_path, monkeypatch):
    """Verify api_backtest filters late-started windows when max_start_delay or filter_partial is supplied."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    fake_file = tmp_path / "fake_partial_test.jsonl"

    # Window 1: delay = 10s (ts=1000, start_ts=990) -> partial
    # Window 2: delay = 1s (ts=2000, start_ts=1999) -> full
    ticks = []
    # w1 (late start delay 10s)
    t1 = _make_fake_tick(1000.0, "0xW1", "btc-updown-5m-1", "btc-up-or-down-5m", 0.50)
    t1["start_ts"] = 990.0
    ticks.append(t1)
    # w2 (early start delay 1s)
    t2 = _make_fake_tick(2000.0, "0xW2", "btc-updown-5m-2", "btc-up-or-down-5m", 0.50)
    t2["start_ts"] = 1999.0
    ticks.append(t2)

    fake_file.write_text("\n".join(json.dumps(t) for t in ticks) + "\n", encoding="utf-8")

    # Default (no filter) -> 2 windows
    res_all = client.get("/api/backtest?file=fake_partial_test.jsonl")
    assert res_all.status_code == 200
    d_all = res_all.json()
    assert d_all["n_windows"] == 2
    assert d_all["trades_sample"][0]["is_partial"] is True
    assert d_all["trades_sample"][0]["start_delay_sec"] == 10.0

    # Filter with max_start_delay=5.0 -> 1 window
    res_filtered = client.get("/api/backtest?file=fake_partial_test.jsonl&max_start_delay=5.0")
    assert res_filtered.status_code == 200
    d_filtered = res_filtered.json()
    assert d_filtered["n_windows"] == 1
    assert d_filtered["trades_sample"][0]["is_partial"] is False
    assert d_filtered["trades_sample"][0]["start_delay_sec"] == 1.0

    # Filter with filter_partial=true -> 1 window
    res_flag = client.get("/api/backtest?file=fake_partial_test.jsonl&filter_partial=true")
    assert res_flag.status_code == 200
    d_flag = res_flag.json()
    assert d_flag["n_windows"] == 1


def test_api_ticks_verify_endpoint(tmp_path, monkeypatch):
    """Verify /api/ticks/verify endpoint validates directories and individual files."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    clean_file = tmp_path / "ticks_2026-09-01.jsonl"
    t = _make_fake_tick(1725000000.0, "0xCID", "btc-5m-1", "btc-up-or-down-5m", 0.50)
    clean_file.write_text(json.dumps(t) + "\n", encoding="utf-8")

    # Directory verification
    res_dir = client.get("/api/ticks/verify")
    assert res_dir.status_code == 200
    d_dir = res_dir.json()
    assert "status" in d_dir
    assert d_dir["total_valid_ticks"] == 1
    assert d_dir["total_corrupt_lines"] == 0

    # Single file verification
    res_file = client.get("/api/ticks/verify?file=ticks_2026-09-01.jsonl")
    assert res_file.status_code == 200
    d_file = res_file.json()
    assert d_file["valid_ticks"] == 1
    assert d_file["corrupt_lines"] == 0

    # Invalid file path traversal
    res_bad = client.get("/api/ticks/verify?file=../bad.jsonl")
    assert res_bad.status_code == 400

    # Missing file
    res_missing = client.get("/api/ticks/verify?file=nonexistent.jsonl")
    assert res_missing.status_code == 404


def test_api_live_cockpit_endpoints(monkeypatch):
    """Verify live trading cockpit endpoints for state, control, and config."""
    from strategy.live_trader import LiveTraderEngine
    monkeypatch.setattr(LiveTraderEngine, "_poll_single_market", lambda self, slug: None)
    try:
        # 1. GET state
        res_state = client.get("/api/live/state")
        assert res_state.status_code == 200
        d_state = res_state.json()
        assert "is_running" in d_state
        assert "portfolio_value" in d_state
        assert "markets" in d_state
        assert len(d_state["markets"]) == 5
        assert "timeline" in d_state

        # 2. POST config
        res_cfg = client.post("/api/live/config", json={
            "offset": 0.025,
            "exit_thresh": 0.06,
            "shares": 8,
            "mode": "paper",
            "starting_balance": 1500.0,
        })
        assert res_cfg.status_code == 200
        d_cfg = res_cfg.json()
        assert d_cfg["params"]["offset"] == 0.025
        assert d_cfg["params"]["exit_thresh"] == 0.06
        assert d_cfg["params"]["shares"] == 8
        assert d_cfg["starting_balance"] == 1500.0

        # 3. POST control (start, stop, restart, reset_pnl)
        res_start = client.post("/api/live/control", json={"action": "start"})
        assert res_start.status_code == 200
        assert res_start.json()["is_running"] is True

        res_stop = client.post("/api/live/control", json={"action": "stop"})
        assert res_stop.status_code == 200
        assert res_stop.json()["is_running"] is False

        res_restart = client.post("/api/live/control", json={"action": "restart"})
        assert res_restart.status_code == 200
        assert res_restart.json()["is_running"] is True

        # Stop before resetting
        client.post("/api/live/control", json={"action": "stop"})
        res_reset = client.post("/api/live/control", json={"action": "reset_pnl"})
        assert res_reset.status_code == 200
        assert res_reset.json()["total_trades"] == 0
    finally:
        # Restore default engine state
        client.post("/api/live/control", json={"action": "stop"})
        client.post("/api/live/config", json={
            "offset": 0.02,
            "exit_thresh": 0.05,
            "shares": 5,
            "mode": "paper",
            "starting_balance": 1000.0,
        })
        client.post("/api/live/control", json={"action": "reset_pnl"})



