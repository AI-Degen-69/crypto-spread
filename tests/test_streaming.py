"""Unit tests for strategy/streaming.py real-time streaming bridge."""
import asyncio
import time
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from strategy.streaming import (
    RTDSStreamClient,
    CLOBMarketWSClient,
    UserSpecStreamClient,
    UnifiedStreamBridge,
    DashboardEnvelope,
    SYMBOL_TO_SERIES,
    SERIES_TO_SYMBOL,
)


def test_symbol_to_series_mapping():
    """Verify symbol to series slug mapping consistency."""
    assert SYMBOL_TO_SERIES["btcusdt"] == "btc-up-or-down-5m"
    assert SYMBOL_TO_SERIES["ethusdt"] == "eth-up-or-down-5m"
    assert SYMBOL_TO_SERIES["solusdt"] == "sol-up-or-down-5m"
    assert SYMBOL_TO_SERIES["xrpusdt"] == "xrp-up-or-down-5m"
    assert SERIES_TO_SYMBOL["btc-up-or-down-5m"] == "btcusdt"
    assert SERIES_TO_SYMBOL["bnb-up-or-down-5m"] == "bnbusdt"


def test_dashboard_envelope_serialization():
    """Verify dashboard envelope schema matches spec."""
    envelope = DashboardEnvelope(
        version="1.0",
        type="snapshot",
        stream_id="spot",
        seq=1,
        server_time=1788394715000,
        data={"symbol": "btcusdt", "price": 77122.96},
    )
    d = envelope.to_dict()
    assert d["version"] == "1.0"
    assert d["type"] == "snapshot"
    assert d["stream_id"] == "spot"
    assert d["seq"] == 1
    assert d["server_time"] == 1788394715000
    assert d["data"]["price"] == 77122.96


def test_rtds_spot_tick_callback():
    """Verify RTDS client dispatches parsed spot ticks to callback."""
    ticks_received = []

    def on_tick(symbol, ts_ms, price):
        ticks_received.append((symbol, ts_ms, price))

    client = RTDSStreamClient(on_spot_tick=on_tick)

    # Simulate PriceUpdatePayload
    class MockPayload:
        symbol = "btcusdt"
        timestamp = 1788394715000
        value = Decimal("77122.96")

    client._handle_price_payload(MockPayload())

    assert len(ticks_received) == 1
    sym, ts, val = ticks_received[0]
    assert sym == "btcusdt"
    assert ts == 1788394715000
    assert val == 77122.96


def test_clob_market_ws_book_snapshot():
    """Verify CLOB client updates local book on book snapshot event."""
    books_received = []

    def on_book(token_id, bids, asks):
        books_received.append((token_id, bids, asks))

    client = CLOBMarketWSClient(on_book_update=on_book)

    # Simulate book snapshot event
    raw_bids = [{"price": "0.48", "size": "100"}, {"price": "0.47", "size": "200"}]
    raw_asks = [{"price": "0.52", "size": "150"}, {"price": "0.53", "size": "250"}]

    client.apply_book_snapshot("token_123", raw_bids, raw_asks)

    assert "token_123" in client.books
    book = client.books["token_123"]
    assert book["best_bid"] == 0.48
    assert book["best_ask"] == 0.52
    assert len(books_received) == 1


def test_clob_market_ws_price_change_delta():
    """Verify CLOB client applies price changes incrementally."""
    client = CLOBMarketWSClient()
    client.apply_book_snapshot("token_123", [{"price": "0.48", "size": "100"}], [{"price": "0.52", "size": "100"}])

    # Update bid level
    client.apply_price_change("token_123", side="BUY", price=0.49, size=150)
    assert client.books["token_123"]["best_bid"] == 0.49

    # Delete bid level (size 0)
    client.apply_price_change("token_123", side="BUY", price=0.49, size=0)
    assert client.books["token_123"]["best_bid"] == 0.48


def test_user_spec_order_reducer():
    """Verify UserSpec order lifecycle status reducer."""
    orders_received = []

    def on_order(payload):
        orders_received.append(payload)

    client = UserSpecStreamClient(on_order_event=on_order)

    # Order placement live
    client.handle_order_event({
        "id": "order_1",
        "status": "LIVE",
        "side": "BUY",
        "price": 0.48,
        "size": 5,
        "timestamp": 1000,
    })
    assert client.open_orders["order_1"]["status"] == "LIVE"

    # Order filled (matched)
    client.handle_order_event({
        "id": "order_1",
        "status": "MATCHED",
        "side": "BUY",
        "price": 0.48,
        "size": 5,
        "timestamp": 1001,
    })
    assert "order_1" not in client.open_orders
    assert client.completed_orders["order_1"]["status"] == "MATCHED"


def test_user_spec_buffer_first_reconciliation():
    """Verify buffer-first replay and monotonic timestamp ordering."""
    client = UserSpecStreamClient()
    client.start_buffering()

    # Incoming WS event while REST snapshot in flight
    client.handle_order_event({
        "id": "order_2",
        "status": "MATCHED",
        "side": "BUY",
        "price": 0.48,
        "size": 5,
        "timestamp": 2000,
    })

    # REST snapshot returns older state
    rest_snapshot = [
        {"id": "order_2", "status": "LIVE", "price": 0.48, "size": 5, "timestamp": 1900}
    ]
    client.reconcile_with_rest(rest_snapshot)

    # Replayed buffer should show MATCHED, not reverted to LIVE
    assert "order_2" not in client.open_orders
    assert client.completed_orders["order_2"]["status"] == "MATCHED"


def test_unified_stream_bridge_lifecycle():
    """Verify start and stop lifecycle of UnifiedStreamBridge."""
    async def _mock_bnb(self):
        while not self._stop_event.is_set():
            await asyncio.sleep(0.05)

    with patch.object(RTDSStreamClient, "_poll_bnb_fallback", _mock_bnb):
        bridge = UnifiedStreamBridge(symbols=["btcusdt", "ethusdt"])
        assert not bridge.is_running
        bridge.start()
        assert bridge.is_running
        time.sleep(0.1)
        status = bridge.get_status()
        assert "rtds_connected" in status
        assert "symbols" in status
        bridge.stop()
        assert not bridge.is_running
