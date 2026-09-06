"""Unit tests for strategy/streaming.py real-time streaming bridge."""
import asyncio
import json
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
    series_for_symbol,
)


def test_symbol_to_series_mapping():
    """Verify symbol to series slug mapping consistency."""
    assert SYMBOL_TO_SERIES["btcusdt"] == "btc-up-or-down-5m"
    assert SYMBOL_TO_SERIES["ethusdt"] == "eth-up-or-down-5m"
    assert SYMBOL_TO_SERIES["solusdt"] == "sol-up-or-down-5m"
    assert SYMBOL_TO_SERIES["xrpusdt"] == "xrp-up-or-down-5m"
    assert SYMBOL_TO_SERIES["btc-15m"] == "btc-up-or-down-15m"
    assert SYMBOL_TO_SERIES["eth-15m"] == "eth-up-or-down-15m"
    assert SERIES_TO_SYMBOL["btc-up-or-down-5m"] == "btcusdt"
    assert SERIES_TO_SYMBOL["bnb-up-or-down-5m"] == "bnbusdt"
    assert SERIES_TO_SYMBOL["btc-up-or-down-15m"] == "btcusdt"
    assert SERIES_TO_SYMBOL["sol-up-or-down-15m"] == "solusdt"


def test_series_for_symbol():
    """Verify series_for_symbol returns all active series for an exchange or short symbol."""
    assert series_for_symbol("btcusdt") == ["btc-up-or-down-5m", "btc-up-or-down-15m"]
    assert series_for_symbol("ethusdt") == ["eth-up-or-down-5m", "eth-up-or-down-15m"]
    assert series_for_symbol("solusdt") == ["sol-up-or-down-5m", "sol-up-or-down-15m"]
    assert series_for_symbol("xrpusdt") == ["xrp-up-or-down-5m", "xrp-up-or-down-15m"]
    assert series_for_symbol("bnbusdt") == ["bnb-up-or-down-5m", "bnb-up-or-down-15m"]
    assert series_for_symbol("btc-15m") == ["btc-up-or-down-15m"]
    assert series_for_symbol("unknown") == []


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


def test_binance_direct_ws_book_ticker_payload():
    """Verify BinanceDirectWSClient parses bookTicker payload and calculates mid price."""
    from strategy.streaming import BinanceDirectWSClient

    ticks = []

    def on_tick(sym, ts, price):
        ticks.append((sym, ts, price))

    client = BinanceDirectWSClient(on_spot_tick=on_tick)

    # Raw bookTicker payload
    raw = json.dumps({
        "u": 400900217,
        "s": "BTCUSDT",
        "b": "85000.00",
        "B": "10.0",
        "a": "85000.20",
        "A": "15.0",
    })
    client._handle_raw_message(raw)

    assert len(ticks) == 1
    sym, ts, price = ticks[0]
    assert sym == "btcusdt"
    assert pytest.approx(price, 0.001) == 85000.10
    assert client.spot_prices["btcusdt"] == 85000.10


def test_binance_direct_ws_trade_payload():
    """Verify BinanceDirectWSClient parses trade payload with execution price."""
    from strategy.streaming import BinanceDirectWSClient

    ticks = []
    client = BinanceDirectWSClient(on_spot_tick=lambda sym, ts, price: ticks.append((sym, ts, price)))

    raw = json.dumps({
        "e": "trade",
        "E": 1788394715123,
        "s": "BNBUSDT",
        "t": 123456,
        "p": "620.50",
        "q": "1.2",
        "T": 1788394715100,
    })
    client._handle_raw_message(raw)

    assert len(ticks) == 1
    sym, ts, price = ticks[0]
    assert sym == "bnbusdt"
    assert ts == 1788394715100
    assert price == 620.50
    assert client.spot_prices["bnbusdt"] == 620.50


def test_binance_direct_ws_combined_stream_envelope():
    """Verify BinanceDirectWSClient handles combined /stream?streams= wrapper."""
    from strategy.streaming import BinanceDirectWSClient

    ticks = []
    client = BinanceDirectWSClient(on_spot_tick=lambda sym, ts, price: ticks.append((sym, ts, price)))

    raw = json.dumps({
        "stream": "solusdt@bookTicker",
        "data": {
            "u": 5001,
            "s": "SOLUSDT",
            "b": "180.00",
            "B": "5.0",
            "a": "180.10",
            "A": "8.0",
        },
    })
    client._handle_raw_message(raw)

    assert len(ticks) == 1
    sym, ts, price = ticks[0]
    assert sym == "solusdt"
    assert pytest.approx(price, 0.001) == 180.05
    assert client.spot_prices["solusdt"] == 180.05


def test_binance_direct_ws_stop_event():
    """Verify BinanceDirectWSClient stop() sets stop event."""
    from strategy.streaming import BinanceDirectWSClient

    client = BinanceDirectWSClient()
    assert not client._stop_event.is_set()
    client.stop()
    assert client._stop_event.is_set()


def test_unified_stream_bridge_primary_and_fallback():
    """Verify UnifiedStreamBridge prioritizes Binance WS and falls back to RTDS."""
    from strategy.streaming import BinanceDirectWSClient, RTDSStreamClient, UnifiedStreamBridge

    ticks_received = []

    def on_tick(sym, ts, price):
        ticks_received.append((sym, ts, price))

    bridge = UnifiedStreamBridge(symbols=["btcusdt"], on_spot_tick=on_tick)

    # 1. When Binance WS is connected, Binance ticks are processed
    bridge.binance.is_connected = True
    bridge._handle_binance_spot_tick("btcusdt", 1000, 85000.0)

    assert len(ticks_received) == 1
    assert ticks_received[-1] == ("btcusdt", 1000, 85000.0)

    # When RTDS tick arrives while Binance WS is connected, RTDS tick is secondary/skipped
    bridge._handle_rtds_spot_tick("btcusdt", 1050, 85001.0)
    assert len(ticks_received) == 1  # Not forwarded since Binance is connected

    # 2. When Binance WS disconnects, RTDS ticks fall back to being forwarded
    bridge.binance.is_connected = False
    bridge._handle_rtds_spot_tick("btcusdt", 1100, 85002.0)

    assert len(ticks_received) == 2
    assert ticks_received[-1] == ("btcusdt", 1100, 85002.0)

    status = bridge.get_status()
    assert "binance_ws_connected" in status
    assert status["binance_ws_connected"] is False
    assert status["active_spot_source"] == "RTDS"

    bridge.binance.is_connected = True
    status = bridge.get_status()
    assert status["binance_ws_connected"] is True
    assert status["active_spot_source"] == "BINANCE_WS"


def test_binance_direct_ws_edge_cases_and_shielding():
    """Verify BinanceDirectWSClient shields against malformed frames and callback errors."""
    from strategy.streaming import BinanceDirectWSClient

    def buggy_callback(sym, ts, price):
        raise RuntimeError("Callback crash")

    client = BinanceDirectWSClient(on_spot_tick=buggy_callback)

    # 1. Subscription control frame
    assert client._parse_message('{"result": null, "id": 1}') is None

    # 2. Non-dict frames
    assert client._parse_message('"plain string"') is None
    assert client._parse_message('[1, 2, 3]') is None

    # 3. Malformed JSON
    assert client._parse_message('{"s": "BTCUSDT", "b":') is None

    # 4. Non-numeric price strings
    assert client._parse_message('{"s": "BTCUSDT", "b": "bad", "a": "bad"}') is None

    # 5. Non-positive prices
    assert client._parse_message('{"s": "BTCUSDT", "b": "0", "a": "0"}') is None

    # 6. Callback exception is caught and shielded inside _handle_raw_message
    valid_raw = json.dumps({"s": "BTCUSDT", "b": "85000", "a": "85010"})
    client._handle_raw_message(valid_raw)
    # Execution continues cleanly, prices are updated despite callback exception
    assert client.spot_prices["btcusdt"] == 85005.0


def test_unified_stream_bridge_restart_rearms_stop_events():
    """Verify stopping and restarting UnifiedStreamBridge clears client stop events."""
    bridge = UnifiedStreamBridge()
    # Simulate stopping the bridge and clients
    bridge.is_running = True
    bridge.stop()
    assert bridge.is_running is False
    assert bridge.binance._stop_event.is_set() is True
    assert bridge.rtds._stop_event.is_set() is True
    assert bridge.clob._stop_event.is_set() is True
    assert bridge.user._stop_event.is_set() is True

    # When start() is called, stop events must be cleared/re-armed
    with patch.object(bridge, "_worker_main"):
        bridge.start()
        assert bridge.is_running is True
        assert bridge.binance._stop_event.is_set() is False
        assert bridge.rtds._stop_event.is_set() is False
        assert bridge.clob._stop_event.is_set() is False
        assert bridge.user._stop_event.is_set() is False
        bridge.is_running = False


def test_unified_stream_bridge_concurrent_feeds_and_delta():
    """Verify UnifiedStreamBridge tracks both feeds concurrently, calculates price diff, and broadcasts telemetry."""
    from strategy.streaming import UnifiedStreamBridge

    rtds_ticks = []
    spot_ticks = []
    envelopes = []

    def on_rtds(sym, ts, price):
        rtds_ticks.append((sym, ts, price))

    def on_spot(sym, ts, price):
        spot_ticks.append((sym, ts, price))

    bridge = UnifiedStreamBridge(
        symbols=["btcusdt"],
        on_spot_tick=on_spot,
        on_rtds_tick=on_rtds,
    )

    orig_broadcast = bridge._broadcast

    def record_broadcast(stream_id, data, event_type="delta"):
        envelopes.append((stream_id, data))
        orig_broadcast(stream_id, data, event_type)

    bridge._broadcast = record_broadcast

    # 1. Binance WS connects and receives tick
    bridge.binance.is_connected = True
    bridge.binance.spot_prices["btcusdt"] = 80010.0
    bridge._handle_binance_spot_tick("btcusdt", 1000, 80010.0)

    assert len(spot_ticks) == 1
    assert spot_ticks[-1] == ("btcusdt", 1000, 80010.0)
    assert envelopes[-1][0] == "spot"
    assert envelopes[-1][1]["actual_price"] == 80010.0
    assert envelopes[-1][1]["rtds_price"] is None

    # 2. RTDS receives tick while Binance is connected
    bridge.rtds.spot_prices["btcusdt"] = 80000.0
    bridge._handle_rtds_spot_tick("btcusdt", 1050, 80000.0)

    # on_rtds_tick is called, but on_spot_tick is NOT called (Binance WS is primary)
    assert len(rtds_ticks) == 1
    assert rtds_ticks[-1] == ("btcusdt", 1050, 80000.0)
    assert len(spot_ticks) == 1

    # RTDS envelope is broadcast with price diff
    rtds_env = envelopes[-1][1]
    assert rtds_env["symbol"] == "btcusdt"
    assert rtds_env["actual_price"] == 80010.0
    assert rtds_env["rtds_price"] == 80000.0
    assert rtds_env["price_diff"] == 10.0
    assert round(rtds_env["price_diff_pct"], 4) == round((10.0 / 80000.0) * 100.0, 4)

    # 3. New Binance tick calculates diff against stored RTDS price
    bridge.binance.spot_prices["btcusdt"] = 80015.0
    bridge._handle_binance_spot_tick("btcusdt", 1100, 80015.0)
    bin_env = envelopes[-1][1]
    assert bin_env["actual_price"] == 80015.0
    assert bin_env["rtds_price"] == 80000.0
    assert bin_env["price_diff"] == 15.0

    # 4. Status reflects both prices and calculated diffs
    status = bridge.get_status()
    assert status["binance_prices"]["btcusdt"] == 80015.0
    assert status["rtds_prices"]["btcusdt"] == 80000.0
    assert status["price_diffs"]["btcusdt"] == 15.0
    assert "price_diff_pcts" in status
