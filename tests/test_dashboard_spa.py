"""Tests for the 4-tab SPA dashboard and API endpoints."""
import json
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
    """Verify that root endpoint serves the full 4-tab SPA HTML."""
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
    assert "switchTab" in html


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


def test_api_collector_status():
    """Verify collector status endpoint returns running flag and collected tick count."""
    response = client.get("/api/collector/status")
    assert response.status_code == 200
    data = response.json()
    assert "running" in data


def test_api_backtest_fake_file(tmp_path, monkeypatch):
    """Verify backtest simulation on an isolated deterministic 4-window fixture."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    fake_file = tmp_path / "fake_round.jsonl"

    # Generate 4 distinct windows
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

    response = client.get("/api/backtest?file=fake_round.jsonl")
    assert response.status_code == 200
    data = response.json()
    assert "params_hash" in data
    assert "overall" in data
    assert "per_series" in data
    assert "equity_curve" in data
    assert "trades_sample" in data
    assert data["n_windows"] == 4


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

    # Retry final chunk after upload_dir was removed
    res1_retry = client.post(
        f"/api/ticks/upload-chunk?filename={filename}&uploadId={upload_id}&chunkIndex=1&totalChunks=2",
        content=part2,
        headers={"Content-Type": "application/octet-stream"}
    )
    assert res1_retry.status_code == 200
    assert res1_retry.json().get("lines") == 2

    # Delete uploaded file
    res_del = client.delete(f"/api/ticks/file?filename={filename}")
    assert res_del.status_code == 200
    assert not (tmp_path / filename).exists()


def test_api_safe_origin_rejected():
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


