"""Tests for LiveTraderEngine integration with RTDS spot feeds and fast stop-loss execution."""
import time
from unittest.mock import MagicMock, patch

import pytest

from strategy.live_trader import LiveTraderEngine, MarketLiveState


def test_live_trader_streaming_state_fields():
    """Verify MarketLiveState contains streaming and spot price fields."""
    engine = LiveTraderEngine()
    btc = engine.markets["btc-up-or-down-5m"]
    assert hasattr(btc, "spot_price")
    assert hasattr(btc, "spot_open_price")
    assert hasattr(btc, "spot_updated_ts")
    assert hasattr(btc, "spot_drift")
    assert hasattr(btc, "streaming_active")
    assert btc.spot_drift == 0.0
    assert not btc.streaming_active


def test_on_spot_tick_updates_market_state():
    """Verify on_spot_tick updates spot price, drift, and timestamp."""
    engine = LiveTraderEngine()
    now_ms = int(time.time() * 1000)

    # First tick sets open price
    engine.on_spot_tick("btcusdt", now_ms, 80000.0)
    btc = engine.markets["btc-up-or-down-5m"]
    assert btc.spot_price == 80000.0
    assert btc.spot_open_price == 80000.0
    assert btc.spot_drift == 0.0
    assert btc.streaming_active

    # Second tick updates drift (1% up)
    engine.on_spot_tick("btcusdt", now_ms + 1000, 80800.0)
    assert btc.spot_price == 80800.0
    assert pytest.approx(btc.spot_drift, 0.0001) == 0.01


def test_on_spot_tick_fast_stop_loss_trigger():
    """Verify RTDS leading spot tick immediately triggers fast stop-loss on adverse drift."""
    engine = LiveTraderEngine()
    engine.is_running = True
    btc = engine.markets["btc-up-or-down-5m"]

    # Simulate filled UP leg at 0.48
    btc.filled_up = True
    btc.filled_down = False
    btc.fill_price_up = 0.48
    btc.up_bid = 0.43
    btc.down_bid = 0.52
    btc.up_token = "token_up_1"
    btc.down_token = "token_dn_1"

    now_ms = int(time.time() * 1000)
    # Open spot price
    engine.on_spot_tick("btcusdt", now_ms, 80000.0)
    assert not btc.exit_taken

    # Small drift (-0.125%) does not trigger fast stop (spot_exit_drift is 0.3% / 0.003)
    engine.on_spot_tick("btcusdt", now_ms + 500, 79900.0)
    assert not btc.exit_taken

    # Spot price drops adversely by 0.375% (<= -0.003)
    engine.on_spot_tick("btcusdt", now_ms + 1000, 79700.0)

    # Spot drift is (79700 - 80000) / 80000 = -0.00375 <= -0.003
    assert btc.exit_taken
    assert btc.exit_side == "UP"
    assert btc.status == "STOP_EXIT"
    assert btc.stops_count == 1


def test_on_spot_tick_fast_stop_loss_trigger_down():
    """Verify RTDS leading spot tick immediately triggers fast stop-loss on adverse drift for DOWN position."""
    engine = LiveTraderEngine()
    engine.is_running = True
    eth = engine.markets["eth-up-or-down-5m"]

    # Simulate filled DOWN leg at 0.48
    eth.filled_up = False
    eth.filled_down = True
    eth.fill_price_down = 0.48
    eth.up_bid = 0.52
    eth.down_bid = 0.43
    eth.up_token = "token_up_eth"
    eth.down_token = "token_dn_eth"

    now_ms = int(time.time() * 1000)
    # Open spot price
    engine.on_spot_tick("ethusdt", now_ms, 3000.0)
    assert not eth.exit_taken

    # Small drift (+0.167%) does not trigger fast stop (spot_exit_drift is 0.3% / 0.003)
    engine.on_spot_tick("ethusdt", now_ms + 500, 3005.0)
    assert not eth.exit_taken

    # Spot price rallies adversely by 0.5% (>= +0.003)
    engine.on_spot_tick("ethusdt", now_ms + 1000, 3015.0)

    # Spot drift is (3015 - 3000) / 3000 = +0.005 >= 0.003
    assert eth.exit_taken
    assert eth.exit_side == "DOWN"
    assert eth.status == "STOP_EXIT"
    assert eth.stops_count == 1


def test_api_live_state_contains_stream_bridge():
    """Verify /api/live/state includes stream_bridge status."""
    from fastapi.testclient import TestClient
    from server.osc_dash import app

    client = TestClient(app)
    res = client.get("/api/live/state")
    assert res.status_code == 200
    data = res.json()
    assert "stream_bridge" in data
    assert "is_running" in data["stream_bridge"]
    assert "rtds_connected" in data["stream_bridge"]


@pytest.mark.anyio
async def test_api_live_stream_sse_endpoint():
    """Verify /api/live/stream sends initial snapshot envelope."""
    import json
    from unittest.mock import AsyncMock, MagicMock
    from server.osc_dash import api_live_stream

    req = MagicMock()
    req.client.host = "127.0.0.1"
    req.headers = {}
    req.is_disconnected = AsyncMock(return_value=True)

    resp = await api_live_stream(req)
    gen = resp.body_iterator
    try:
        first_item = await anext(gen)
        raw = first_item["data"]
        payload = json.loads(raw)
        assert payload["type"] == "snapshot"
        assert payload["stream_id"] == "state"
        assert "markets" in payload["data"]
    finally:
        await gen.aclose()


def test_cockpit_html_contains_streaming_ui():
    """Verify Tab 1 HTML contains cockpitStreamPill and Spot 1s gauges."""
    from fastapi.testclient import TestClient
    from server.osc_dash import app

    client = TestClient(app)
    res = client.get("/")
    assert res.status_code == 200
    html = res.text
    assert "cockpitStreamPill" in html
    assert "initLiveCockpitStream" in html
    assert "Spot 1s:" in html
