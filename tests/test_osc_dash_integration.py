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
    assert "chip-token-BTC" in html
    assert "chip-token-ETH" in html
    assert "btnDur5m" in html
    assert "cockpitActiveMarketsBadge" in html
    assert "toggleCockpitToken" in html
    assert "cockpitPositionsTable" in html
    assert "cockpitPositionsBody" in html
    assert "cockpitPositionsCount" in html
    assert 'rel="icon"' in html
    assert 'rel="alternate icon"' in html


def test_favicon_served():
    """Verify that /favicon.ico returns 200 OK with SVG content and correct media type."""
    response = client.get("/favicon.ico")
    assert response.status_code == 200
    assert "image/svg+xml" in response.headers["content-type"]
    assert "<svg" in response.text
    assert "#33c9b5" in response.text
    assert "#f0684d" in response.text


def test_root_ltr_layout_and_attributes():
    """Verify that the dashboard root HTML uses LTR direction and English language with left-aligned headers."""
    import re
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    # Parse and directly verify root html element attributes
    html_tag_match = re.search(r"<html\s+([^>]+)>", html)
    assert html_tag_match is not None, "<html> root tag not found in dashboard"
    attrs = html_tag_match.group(1)
    assert 'lang="en"' in attrs
    assert 'dir="ltr"' in attrs
    assert 'dir="rtl"' not in attrs
    assert 'lang="he"' not in attrs

    # Verify that required selectors explicitly set left text alignment
    assert re.search(r"\.tbl\s+th\s*\{[^}]*text-align:\s*left", html) is not None
    assert re.search(r"\.form-group\s+label\s*\{[^}]*text-align:\s*left", html) is not None


def test_no_hebrew_characters_in_dashboard():
    """Verify that all legacy Hebrew strings in server/osc_dash.py have been standardized to English."""
    import re
    from pathlib import Path
    server_file = Path(__file__).resolve().parent.parent / "server" / "osc_dash.py"
    with open(server_file, "r", encoding="utf-8") as f:
        hebrew_lines = [
            (idx, line.strip())
            for idx, line in enumerate(f, 1)
            if re.search(r"[\u0590-\u05ff]", line)
        ]
    assert len(hebrew_lines) == 0, f"Found {len(hebrew_lines)} lines with Hebrew in osc_dash.py: {hebrew_lines[:5]}"



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
    mock_acct = {
        "success": True,
        "net_value": 1234.56,
        "cash_balance": 1000.0,
        "positions_value": 234.56,
        "positions": [],
        "wallet_address": "0x1234567890abcdef1234567890abcdef12345678",
    }
    monkeypatch.setattr("server.osc_dash.fetch_polymarket_account_value", lambda *a, **kw: mock_acct)
    monkeypatch.setattr("strategy.live_trader.fetch_polymarket_account_value", lambda *a, **kw: mock_acct)
    try:
        # 1. GET state
        res_state = client.get("/api/live/state")
        assert res_state.status_code == 200
        d_state = res_state.json()
        assert "is_running" in d_state
        assert "portfolio_value" in d_state
        assert "markets" in d_state
        assert "open_positions" in d_state
        assert "positions" in d_state
        assert len(d_state["markets"]) == 5
        assert "timeline" in d_state
        for m in d_state["markets"].values():
            assert "fill_price_up" in m
            assert "fill_price_down" in m

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

        # 3. GET /api/live/account
        res_acc = client.get("/api/live/account")
        assert res_acc.status_code == 200
        d_acc = res_acc.json()
        assert "net_value" in d_acc
        assert "cash_balance" in d_acc
        assert "positions_value" in d_acc
        assert "positions" in d_acc
        assert isinstance(d_acc["positions"], list)

        # 4. POST config in LIVE mode (locks starting balance)
        res_live_cfg = client.post("/api/live/config", json={
            "mode": "live",
            "starting_balance": 9999.0,
        })
        assert res_live_cfg.status_code == 200
        d_live = res_live_cfg.json()
        assert d_live["mode"] == "live"

        # 5. POST control start/stop/restart
        res_start = client.post("/api/live/control", json={"action": "start"})
        assert res_start.status_code == 200
        assert res_start.json()["is_running"] is True

        res_stop = client.post("/api/live/control", json={"action": "stop"})
        assert res_stop.status_code == 200
        assert res_stop.json()["is_running"] is False

        res_restart = client.post("/api/live/control", json={"action": "restart"})
        assert res_restart.status_code == 200
        assert res_restart.json()["is_running"] is True

        # Stop before seeding demo data
        client.post("/api/live/control", json={"action": "stop"})

        # 6. POST control demo_data
        res_demo = client.post("/api/live/control", json={"action": "demo_data"})
        assert res_demo.status_code == 200
        d_demo = res_demo.json()
        assert d_demo["total_trades"] == 7
        assert d_demo["pairs_merged"] == 6
        assert len(d_demo["trades"]) == 7
        assert len(d_demo["timeline"]) == 120
        assert len(d_demo["open_positions"]) > 0
        assert d_demo["markets"]["eth-up-or-down-5m"]["fill_price_up"] == 0.485

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


def test_osc_dash_live_execution_endpoints(monkeypatch):
    """Verify live order execution endpoints: /orders, /cancel_all, /cancel_order, /test_order."""
    from unittest.mock import MagicMock
    from strategy.live_trader import get_live_trader_engine

    engine = get_live_trader_engine()
    fake_client = MagicMock()
    fake_client.create_and_post_order.return_value = {"orderID": "ord_mock_123", "status": "delayed"}
    fake_client.cancel.return_value = {"success": True}
    fake_client.cancel_all.return_value = {"success": True}
    fake_client.get_orders.return_value = [
        {"id": "ord_mock_123", "asset_id": "tok_test_up", "side": "BUY", "price": "0.05", "original_size": "1"}
    ]
    monkeypatch.setattr(engine, "_clob_client", fake_client)
    saved_markets = {
        slug: (
            m.order_id_up,
            m.order_id_down,
            m.next_order_id_up,
            m.next_order_id_down,
            m.order_status_up,
            m.order_status_down,
            m.next_quoted,
            m.status,
            m.last_action,
        )
        for slug, m in engine.markets.items()
    }
    saved_halted = engine.quoting_halted

    try:
        # 1. GET /api/live/orders
        res_orders = client.get("/api/live/orders")
        assert res_orders.status_code == 200
        orders_data = res_orders.json()
        assert "orders" in orders_data
        assert len(orders_data["orders"]) >= 1

        # 2. POST /api/live/test_order
        res_test_ord = client.post("/api/live/test_order", json={
            "token_id": "tok_test_up",
            "price": 0.05,
            "size": 1.0,
            "side": "BUY",
        })
        assert res_test_ord.status_code == 200
        ord_data = res_test_ord.json()
        assert ord_data["order_id"] == "ord_mock_123"

        # 2b. POST /api/live/test_order rejects invalid payloads before touching the CLOB
        fake_client.create_and_post_order.reset_mock()
        for payload in (
            {"token_id": "tok_test_up", "price": "invalid"},
            {"token_id": "tok_test_up", "size": "invalid"},
            {"token_id": "tok_test_up", "price": 0.0},
            {"token_id": "tok_test_up", "price": 1.0},
            {"token_id": "tok_test_up", "size": 11.0},
            {"token_id": "tok_test_up", "side": "INVALID"},
            {"token_id": "", "price": 0.05},
        ):
            res_reject = client.post("/api/live/test_order", json=payload)
            assert res_reject.status_code == 400, payload
        fake_client.create_and_post_order.assert_not_called()

        # 3. POST /api/live/cancel_order
        res_cancel_single = client.post("/api/live/cancel_order", json={"order_id": "ord_mock_123"})
        assert res_cancel_single.status_code == 200
        assert res_cancel_single.json()["ok"] is True

        # 4. POST /api/live/cancel_all
        res_cancel_all = client.post("/api/live/cancel_all")
        assert res_cancel_all.status_code == 200
        assert res_cancel_all.json()["ok"] is True
    finally:
        engine.quoting_halted = saved_halted
        for slug, saved in saved_markets.items():
            m = engine.markets.get(slug)
            if m:
                (
                    m.order_id_up,
                    m.order_id_down,
                    m.next_order_id_up,
                    m.next_order_id_down,
                    m.order_status_up,
                    m.order_status_down,
                    m.next_quoted,
                    m.status,
                    m.last_action,
                ) = saved


def test_api_live_config_market_selection():
    engine = osc_dash.get_live_trader_engine()
    # Reset engine to default 5m markets
    engine.update_config(selected_markets=["btc-up-or-down-5m", "eth-up-or-down-5m", "bnb-up-or-down-5m", "sol-up-or-down-5m", "xrp-up-or-down-5m"])

    # 1. Update selection via tokens and durations
    res = client.post("/api/live/config", json={"tokens": ["SOL"], "durations": [900]})
    assert res.status_code == 200
    data = res.json()
    assert set(data["markets"].keys()) == {"sol-up-or-down-15m"}

    # 2. Update selection via selected_markets directly
    res2 = client.post("/api/live/config", json={"selected_markets": ["btc-up-or-down-5m", "eth-up-or-down-15m"]})
    assert res2.status_code == 200
    data2 = res2.json()
    assert set(data2["markets"].keys()) == {"btc-up-or-down-5m", "eth-up-or-down-15m"}

    # Reset
    engine.update_config(selected_markets=["btc-up-or-down-5m", "eth-up-or-down-5m", "bnb-up-or-down-5m", "sol-up-or-down-5m", "xrp-up-or-down-5m"])


def test_api_live_config_invalid_selection_returns_400():
    # Invalid token
    res = client.post("/api/live/config", json={"tokens": ["DOGE"]})
    assert res.status_code == 400
    assert "error" in res.json()

    # Invalid duration
    res2 = client.post("/api/live/config", json={"durations": [12345]})
    assert res2.status_code == 400
    assert "error" in res2.json()


def test_api_live_config_open_position_deselection_rejection():
    engine = osc_dash.get_live_trader_engine()
    engine.update_config(selected_markets=["btc-up-or-down-5m", "eth-up-or-down-5m"])
    m_btc = engine.markets["btc-up-or-down-5m"]
    m_btc.filled_up = True
    m_btc.exit_taken = False

    try:
        # Deselecting btc while filled leg is unhedged and open must return 400
        res = client.post("/api/live/config", json={"selected_markets": ["eth-up-or-down-5m"]})
        assert res.status_code == 400
        assert "Cannot deselect active market" in res.json().get("error", "")
    finally:
        m_btc.filled_up = False
        engine.update_config(selected_markets=["btc-up-or-down-5m", "eth-up-or-down-5m", "bnb-up-or-down-5m", "sol-up-or-down-5m", "xrp-up-or-down-5m"])


def test_api_live_state_includes_series_metadata():
    res = client.get("/api/live/state")
    assert res.status_code == 200
    data = res.json()
    assert "available_series" in data
    assert len(data["available_series"]) == 10
    # Check shape of available_series entries
    first = data["available_series"][0]
    assert "slug" in first
    assert "token" in first
    assert "duration" in first
    assert "label" in first
    assert "color" in first
    assert "selected_series" in data


def test_api_live_config_rejects_changes_while_running():
    """Verify /api/live/config returns HTTP 400 if user tries to change markets or parameters while bot is running."""
    engine = osc_dash.get_live_trader_engine()
    engine.is_running = True
    try:
        # Market change while running rejected
        res = client.post("/api/live/config", json={"tokens": ["SOL"]})
        assert res.status_code == 400
        assert "Cannot change market selection while the trading bot is running" in res.json().get("error", "")

        # Parameter changes (offset, shares) while running are also rejected
        res2 = client.post("/api/live/config", json={"offset": 0.04})
        assert res2.status_code == 400
        assert "Cannot change strategy parameters while the trading bot is running" in res2.json().get("error", "")

        # Idempotent call with matching active parameters is accepted
        res_idempotent = client.post("/api/live/config", json={"offset": engine.offset, "shares": engine.shares})
        assert res_idempotent.status_code == 200

        # Once stopped, updating parameters is accepted
        engine.is_running = False
        res3 = client.post("/api/live/config", json={"offset": 0.025, "shares": 6})
        assert res3.status_code == 200
        assert engine.offset == 0.025
        assert engine.shares == 6
    finally:
        engine.is_running = False
        engine.update_config(
            offset=0.02,
            shares=5,
            selected_markets=["btc-up-or-down-5m", "eth-up-or-down-5m", "bnb-up-or-down-5m", "sol-up-or-down-5m", "xrp-up-or-down-5m"],
        )


def test_cockpit_ui_locks_market_filters_while_running():
    """Verify the cockpit page ships the client-side lock for market filters during a run."""
    res = client.get("/")
    assert res.status_code == 200
    html = res.text

    # Lock helpers exist and every filter handler consults the lock before mutating
    assert "function areCockpitFiltersLocked()" in html
    assert "function applyCockpitFilterLock(" in html
    assert "function syncCockpitFiltersFromState(" in html
    for handler in ("toggleCockpitToken", "setCockpitTokensAll", "setCockpitDuration", "applyCockpitConfig"):
        body_start = html.index(f"function {handler}(")
        assert "areCockpitFiltersLocked()" in html[body_start:body_start + 900], handler

    # Lock hint element and ids for the All/Clear buttons the lock disables
    assert 'id="cockpitFilterLockHint"' in html
    assert 'id="btnTokensAll"' in html
    assert 'id="btnTokensClear"' in html


def test_api_live_config_selection_roundtrip_while_stopped():
    """Verify filters chosen in the UI while stopped drive the engine's active market set."""
    engine = osc_dash.get_live_trader_engine()
    assert not engine.is_running
    try:
        res = client.post("/api/live/config", json={"tokens": ["BTC", "ETH"], "durations": [900]})
        assert res.status_code == 200
        state = res.json()
        assert sorted(state["selected_series"]) == ["btc-up-or-down-15m", "eth-up-or-down-15m"]
        assert sorted(state["markets"].keys()) == ["btc-up-or-down-15m", "eth-up-or-down-15m"]
        assert sorted(engine.markets.keys()) == ["btc-up-or-down-15m", "eth-up-or-down-15m"]

        # Both durations for a single token
        res2 = client.post("/api/live/config", json={"tokens": ["SOL"], "durations": [300, 900]})
        assert res2.status_code == 200
        assert sorted(res2.json()["selected_series"]) == ["sol-up-or-down-15m", "sol-up-or-down-5m"]
    finally:
        engine.update_config(selected_markets=["btc-up-or-down-5m", "eth-up-or-down-5m", "bnb-up-or-down-5m", "sol-up-or-down-5m", "xrp-up-or-down-5m"])


def test_cockpit_ui_preserves_non_rectangular_selection():
    """Verify the cockpit resubmits an exact slug set it cannot express as token x duration."""
    res = client.get("/")
    assert res.status_code == 200
    html = res.text

    assert "let cockpitExactSelection = null;" in html
    assert "function cockpitFilterProductSlugs(" in html
    assert "body.selected_markets = cockpitExactSelection;" in html

    # Every explicit filter click drops back to the product representation
    for handler in ("toggleCockpitToken", "setCockpitTokensAll", "setCockpitDuration"):
        body_start = html.index(f"function {handler}(")
        assert "cockpitExactSelection = null;" in html[body_start:body_start + 400], handler


def test_api_live_config_accepts_non_rectangular_selection():
    """Verify a mixed-duration selection survives a parameter-only reapply."""
    engine = osc_dash.get_live_trader_engine()
    try:
        mixed = ["btc-up-or-down-5m", "eth-up-or-down-15m"]
        res = client.post("/api/live/config", json={"selected_markets": mixed})
        assert res.status_code == 200
        assert sorted(res.json()["selected_series"]) == sorted(mixed)

        # Resubmitting the exact set alongside parameters must not widen it
        res2 = client.post("/api/live/config", json={"offset": 0.03, "selected_markets": mixed})
        assert res2.status_code == 200
        assert sorted(res2.json()["selected_series"]) == sorted(mixed)
        assert sorted(engine.markets.keys()) == sorted(mixed)
    finally:
        engine.update_config(selected_markets=["btc-up-or-down-5m", "eth-up-or-down-5m", "bnb-up-or-down-5m", "sol-up-or-down-5m", "xrp-up-or-down-5m"])

