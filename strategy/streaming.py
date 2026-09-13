"""Unified Real-Time Streaming Bridge for Polymarket RTDS Spot Feeds and CLOB WebSockets.

Implements:
1. 1-second cadence spot prices via Polymarket RTDS (prices.crypto.binance) with BNB fallback.
2. CLOB Market WebSockets (MarketSpec) with 10s PING keepalive and book state reducer.
3. UserSpec authenticated stream with buffer-first REST reconciliation.
4. Versioned DashboardEnvelope schema for SSE/WebSocket delivery.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import random
import threading
import time
from dataclasses import dataclass, asdict, field
from decimal import Decimal
from typing import Callable, Dict, List, Optional, Any, Set
import requests

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    websockets = None

try:
    from polymarket import AsyncPublicClient, AsyncSecureClient
    from polymarket.streams import CryptoPricesSpec, MarketSpec, UserSpec
    POLYMARKET_AVAILABLE = True
except ImportError:
    POLYMARKET_AVAILABLE = False
    AsyncPublicClient = None
    AsyncSecureClient = None
    CryptoPricesSpec = None
    MarketSpec = None
    UserSpec = None

log = logging.getLogger("streaming")

SYMBOL_TO_SERIES = {
    "btcusdt": "btc-up-or-down-5m",
    "ethusdt": "eth-up-or-down-5m",
    "solusdt": "sol-up-or-down-5m",
    "xrpusdt": "xrp-up-or-down-5m",
    "bnbusdt": "bnb-up-or-down-5m",
    "btc-5m": "btc-up-or-down-5m",
    "eth-5m": "eth-up-or-down-5m",
    "sol-5m": "sol-up-or-down-5m",
    "xrp-5m": "xrp-up-or-down-5m",
    "bnb-5m": "bnb-up-or-down-5m",
    "btc-15m": "btc-up-or-down-15m",
    "eth-15m": "eth-up-or-down-15m",
    "sol-15m": "sol-up-or-down-15m",
    "xrp-15m": "xrp-up-or-down-15m",
    "bnb-15m": "bnb-up-or-down-15m",
}

SERIES_TO_SYMBOL = {
    "btc-up-or-down-5m": "btcusdt",
    "eth-up-or-down-5m": "ethusdt",
    "sol-up-or-down-5m": "solusdt",
    "xrp-up-or-down-5m": "xrpusdt",
    "bnb-up-or-down-5m": "bnbusdt",
    "btc-5m": "btcusdt",
    "eth-5m": "ethusdt",
    "sol-5m": "solusdt",
    "xrp-5m": "xrpusdt",
    "bnb-5m": "bnbusdt",
    "btc-up-or-down-15m": "btcusdt",
    "eth-up-or-down-15m": "ethusdt",
    "sol-up-or-down-15m": "solusdt",
    "xrp-up-or-down-15m": "xrpusdt",
    "bnb-up-or-down-15m": "bnbusdt",
    "btc-15m": "btcusdt",
    "eth-15m": "ethusdt",
    "sol-15m": "solusdt",
    "xrp-15m": "xrpusdt",
    "bnb-15m": "bnbusdt",
}

RTDS_SYMBOLS = ["btcusdt", "ethusdt", "solusdt", "xrpusdt"]
BINANCE_SYMBOLS = ["btcusdt", "ethusdt", "solusdt", "xrpusdt", "bnbusdt"]

# CLOB market channel (issue #165). The REST tape at 1 poll/s drops every
# intra-second print; this socket is the only source that sees all of them.
CLOB_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
CLOB_WS_PING_INTERVAL = 10.0      # venue keepalive: text "PING" -> "PONG"
CLOB_WS_BACKOFF_BASE = 1.0
CLOB_WS_BACKOFF_FACTOR = 2.0
CLOB_WS_BACKOFF_MAX = 30.0
CLOB_WS_RECV_TIMEOUT = 1.0        # recv poll slice, so stop()/rotation land fast
CLOB_WS_JITTER_PCT = 0.25         # de-synchronizes reconnect storms



def series_for_symbol(symbol: str) -> list[str]:
    """Resolve an exchange symbol or alias to all matching canonical series slugs.

    For base exchange symbols (e.g. 'btcusdt'), returns both 5m and 15m canonical slugs.
    For specific duration aliases (e.g. 'btc-15m' or 'btc-up-or-down-15m'), returns
    the single corresponding canonical slug. Returns an empty list if unknown.
    """
    sym = symbol.lower().strip()
    if not sym:
        return []

    from strategy.series import SERIES, token_for_slug
    canonical_slugs = {s[0] for s in SERIES}
    if sym in canonical_slugs:
        return [sym]

    if sym in SYMBOL_TO_SERIES and ("-5m" in sym or "-15m" in sym):
        return [SYMBOL_TO_SERIES[sym]]

    token = sym.removesuffix("usdt")
    return [s[0] for s in SERIES if token_for_slug(s[0]).lower() == token]



@dataclass
class DashboardEnvelope:
    """Versioned streaming envelope schema for UI SSE/WebSocket."""
    version: str = "1.0"
    type: str = "delta"  # "snapshot" | "delta"
    stream_id: str = "spot"  # "spot" | "books" | "orders" | "positions"
    seq: int = 0
    server_time: int = field(default_factory=lambda: int(time.time() * 1000))
    data: Any = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert envelope to dictionary format."""
        return {
            "version": self.version,
            "type": self.type,
            "stream_id": self.stream_id,
            "seq": self.seq,
            "server_time": self.server_time,
            "data": self.data,
        }

    def to_json(self) -> str:
        """Serialize envelope to JSON string."""
        return json.dumps(self.to_dict())


class RTDSStreamClient:
    """Ingests 1-second cadence spot prices from Polymarket RTDS."""

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        on_spot_tick: Optional[Callable[[str, int, float], None]] = None,
    ):
        """Initialize RTDS stream client with symbols and tick callback."""
        self.symbols = symbols or RTDS_SYMBOLS
        self.on_spot_tick = on_spot_tick
        self.spot_prices: Dict[str, float] = {}
        self.last_tick_ts: Dict[str, int] = {}
        self.is_connected: bool = False
        self._stop_event = asyncio.Event()

    def _handle_price_payload(self, payload: Any) -> None:
        """Process incoming PriceUpdatePayload."""
        try:
            sym = str(getattr(payload, "symbol", "")).lower()
            raw_val = getattr(payload, "value", 0.0)
            val = float(raw_val)
            ts = int(getattr(payload, "timestamp", int(time.time() * 1000)))
            self.spot_prices[sym] = val
            self.last_tick_ts[sym] = ts
            if self.on_spot_tick:
                self.on_spot_tick(sym, ts, val)
        except Exception as e:
            log.debug("Error handling price payload: %s", e)

    async def _poll_bnb_fallback(self) -> None:
        """1-second REST ticker fallback for BNB which lacks RTDS Binance feeds."""
        sess = requests.Session()
        sess.headers.update({"User-Agent": "Mozilla/5.0"})
        bnb_symbol = "bnbusdt"
        binance_url = "https://api.binance.com/api/v3/ticker/price?symbol=BNBUSDT"

        while not self._stop_event.is_set():
            try:
                loop = asyncio.get_running_loop()
                r = await loop.run_in_executor(None, lambda: sess.get(binance_url, timeout=2.0))
                if r.status_code == 200:
                    data = r.json()
                    val = float(data.get("price", 0.0))
                    ts = int(time.time() * 1000)
                    self.spot_prices[bnb_symbol] = val
                    self.last_tick_ts[bnb_symbol] = ts
                    if self.on_spot_tick:
                        self.on_spot_tick(bnb_symbol, ts, val)
            except Exception as e:
                log.debug("BNB REST ticker fallback failed: %s", e)
            await asyncio.sleep(1.0)

    async def run(self) -> None:
        """Main async task connecting to RTDS stream and running fallback."""
        if not POLYMARKET_AVAILABLE or AsyncPublicClient is None:
            log.warning("polymarket SDK not installed; RTDS streaming running in fallback mode")
            await self._poll_bnb_fallback()
            return

        bnb_task = asyncio.create_task(self._poll_bnb_fallback())
        try:
            while not self._stop_event.is_set():
                try:
                    async with AsyncPublicClient() as client:
                        async with await client.subscribe(
                            CryptoPricesSpec(
                                topic="prices.crypto.binance",
                                symbols=self.symbols,
                            )
                        ) as stream:
                            self.is_connected = True
                            log.info("Connected to RTDS prices.crypto.binance: %s", self.symbols)
                            async for event in stream:
                                if self._stop_event.is_set():
                                    break
                                payload = getattr(event, "payload", None)
                                if payload:
                                    self._handle_price_payload(payload)
                except Exception as e:
                    self.is_connected = False
                    log.debug("RTDS stream exception (reconnecting in 2s): %s", e)
                    await asyncio.sleep(2.0)
        finally:
            bnb_task.cancel()
            self.is_connected = False

    def stop(self) -> None:
        """Signal client to stop ingestion loop."""
        self._stop_event.set()


class BinanceDirectWSClient:
    """Direct WebSocket client for Binance sub-second spot ticks and book tickers."""

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        on_spot_tick: Optional[Callable[[str, int, float], None]] = None,
        ws_base_url: str = "wss://stream.binance.com:9443",
    ):
        """Initialize Binance direct WebSocket client."""
        self.symbols = [s.lower() for s in (symbols or BINANCE_SYMBOLS)]
        self.on_spot_tick = on_spot_tick
        self.ws_base_url = ws_base_url
        self.spot_prices: Dict[str, float] = {}
        self.last_tick_ts: Dict[str, int] = {}
        self.is_connected: bool = False
        self._stop_event = asyncio.Event()

    def _parse_message(self, raw_msg: str | bytes) -> Optional[tuple[str, int, float]]:
        """Parse incoming WebSocket message from Binance stream."""
        try:
            msg = json.loads(raw_msg)

            # Support combined stream format ({"stream": "...", "data": {...}}) or raw payload
            data = msg.get("data") if isinstance(msg, dict) and "data" in msg else msg
            if not isinstance(data, dict):
                return None

            raw_sym = data.get("s")
            if not raw_sym:
                stream = msg.get("stream", "") if isinstance(msg, dict) else ""
                if "@" in stream:
                    raw_sym = stream.split("@")[0]

            if not raw_sym:
                return None

            symbol = str(raw_sym).lower()

            # 1. bookTicker: "b" (best bid) and "a" (best ask)
            price: Optional[float] = None
            if "b" in data and "a" in data:
                try:
                    bid = float(data["b"])
                    ask = float(data["a"])
                    if bid > 0 and ask > 0:
                        price = (bid + ask) / 2.0
                    elif bid > 0:
                        price = bid
                    elif ask > 0:
                        price = ask
                except (ValueError, TypeError):
                    price = None

            # 2. trade: "p" (price)
            if price is None and "p" in data:
                try:
                    price = float(data["p"])
                except (ValueError, TypeError):
                    price = None

            # 3. 24hrTicker / miniTicker: "c" (close / last price)
            if price is None and "c" in data:
                try:
                    price = float(data["c"])
                except (ValueError, TypeError):
                    price = None

            if price is None or price <= 0:
                return None

            # bookTicker streams omit T/E event timestamps; fallback to local millisecond clock
            raw_ts = data.get("T") or data.get("E")
            ts = int(raw_ts) if raw_ts is not None else int(time.time() * 1000)
            return symbol, ts, price
        except Exception as e:
            log.debug("Error parsing Binance WS message: %s", e)
            return None

    def _handle_raw_message(self, raw_msg: str | bytes) -> None:
        """Process and dispatch incoming message from stream."""
        try:
            parsed = self._parse_message(raw_msg)
            if not parsed:
                return
            symbol, ts, price = parsed
            self.spot_prices[symbol] = price
            self.last_tick_ts[symbol] = ts
            if self.on_spot_tick:
                self.on_spot_tick(symbol, ts, price)
        except Exception as e:
            log.debug("Error handling Binance WS message: %s", e)

    async def run(self) -> None:
        """Connect to Binance WebSocket stream with auto-reconnect loop."""
        if not WEBSOCKETS_AVAILABLE or websockets is None:
            log.warning("websockets package not available; BinanceDirectWSClient disabled")
            return

        stream_names = [f"{s}@bookTicker" for s in self.symbols]
        combined_url = f"{self.ws_base_url}/stream?streams={'/'.join(stream_names)}"

        backoff = 1.0
        max_backoff = 30.0

        while not self._stop_event.is_set():
            try:
                log.info("Connecting to Binance direct WebSocket: %s", combined_url)
                async with websockets.connect(
                    combined_url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self.is_connected = True
                    backoff = 1.0
                    log.info("Connected to Binance direct WebSocket stream (%d symbols)", len(self.symbols))

                    while not self._stop_event.is_set():
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=10.0)
                            self._handle_raw_message(msg)
                        except asyncio.TimeoutError:
                            continue
            except Exception as e:
                self.is_connected = False
                if self._stop_event.is_set():
                    break
                log.debug("Binance WebSocket stream disconnect (%s); retrying in %.1fs", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.5, max_backoff)
            finally:
                self.is_connected = False

    def stop(self) -> None:
        """Signal client to stop ingestion loop."""
        self._stop_event.set()


class CLOBMarketWSClient:
    """Connects to Polymarket CLOB Market WebSocket and maintains order books.

    Two transports share the same reducers:
    - `run()` subscribes through the polymarket SDK (`MarketSpec`).
    - `run_direct()` opens `CLOB_WS_URL` with `websockets` and drives the raw
      frame protocol itself — subscription frame, 10s `"PING"` keepalive,
      exponential-backoff reconnects. This is the transport the tick collector
      uses, because it also captures `last_trade_price` prints into a
      thread-safe buffer that `drain_trades()` hands to the collector thread.
    """

    def __init__(
        self,
        token_ids: Optional[List[str]] = None,
        on_book_update: Optional[Callable[[str, Dict[float, float], Dict[float, float]], None]] = None,
        on_trade: Optional[Callable[[Dict[str, Any]], None]] = None,
        ws_url: str = CLOB_WS_URL,
        ping_interval: float = CLOB_WS_PING_INTERVAL,
        backoff_base: float = CLOB_WS_BACKOFF_BASE,
        backoff_max: float = CLOB_WS_BACKOFF_MAX,
        connect_factory: Optional[Callable[..., Any]] = None,
    ):
        """Initialize CLOB Market WebSocket client."""
        self.token_ids: List[str] = sorted(set(token_ids)) if token_ids else []
        self._tokens_version: int = 0
        self.on_book_update = on_book_update
        self.on_trade = on_trade
        self.ws_url = ws_url
        self.ping_interval = float(ping_interval)
        self.backoff_base = float(backoff_base)
        self.backoff_max = float(backoff_max)
        self._connect_factory = connect_factory
        self.books: Dict[str, Dict[str, Any]] = {}
        self.top_of_book: Dict[str, Dict[str, Optional[float]]] = {}
        self.tick_sizes: Dict[str, float] = {}
        self.is_connected: bool = False
        self.reconnect_count: int = 0
        self.trades_captured: int = 0
        self.last_ping_ts: float = 0.0
        self.last_pong_ts: float = 0.0
        self._trade_buffer: Dict[str, List[Dict[str, Any]]] = {}
        self._buffer_lock = threading.Lock()
        self._stop_event = asyncio.Event()

    @property
    def tokens_version(self) -> int:
        """Monotonic counter bumped whenever the subscribed token set changes."""
        return self._tokens_version

    @property
    def drain_ready(self) -> bool:
        """True while at least one streamed print is waiting to be drained."""
        with self._buffer_lock:
            return any(self._trade_buffer.values())

    def apply_book_snapshot(self, token_id: str, raw_bids: List[Any], raw_asks: List[Any]) -> None:
        """Full replacement of local book state from snapshot."""
        bids: Dict[float, float] = {}
        asks: Dict[float, float] = {}

        for b in raw_bids:
            try:
                p = float(b["price"] if isinstance(b, dict) else getattr(b, "price", 0))
                s = float(b["size"] if isinstance(b, dict) else getattr(b, "size", 0))
                if s > 0:
                    bids[p] = s
            except Exception:
                continue

        for a in raw_asks:
            try:
                p = float(a["price"] if isinstance(a, dict) else getattr(a, "price", 0))
                s = float(a["size"] if isinstance(a, dict) else getattr(a, "size", 0))
                if s > 0:
                    asks[p] = s
            except Exception:
                continue

        best_bid = max(bids.keys()) if bids else None
        best_ask = min(asks.keys()) if asks else None

        self.books[token_id] = {
            "bids": bids,
            "asks": asks,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "last_updated": time.time(),
        }

        if self.on_book_update:
            self.on_book_update(token_id, bids, asks)

    def apply_price_change(self, token_id: str, side: str, price: float, size: float) -> None:
        """Incremental level mutation."""
        if token_id not in self.books:
            self.books[token_id] = {
                "bids": {},
                "asks": {},
                "best_bid": None,
                "best_ask": None,
                "last_updated": time.time(),
            }

        book = self.books[token_id]
        side_dict = book["bids"] if side.upper() in ("BUY", "BID") else book["asks"]

        if size <= 0:
            side_dict.pop(price, None)
        else:
            side_dict[price] = size

        book["best_bid"] = max(book["bids"].keys()) if book["bids"] else None
        book["best_ask"] = min(book["asks"].keys()) if book["asks"] else None
        book["last_updated"] = time.time()

        if self.on_book_update:
            self.on_book_update(token_id, book["bids"], book["asks"])

    def apply_best_bid_ask(self, token_id: str, best_bid: Optional[float], best_ask: Optional[float]) -> None:
        """Record the venue's own top-of-book quote for a token."""
        self.top_of_book[token_id] = {"best_bid": best_bid, "best_ask": best_ask}

    def record_trade(self, token_id: str, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Parse one `last_trade_price` event into the per-token trade buffer.

        A row that does not parse is dropped, never raised: one malformed print
        must not take the socket (and with it the whole tape) down.
        """
        try:
            price = float(event.get("price"))
            size = float(event.get("size") or 0.0)
        except (TypeError, ValueError):
            log.debug("skipping unparseable trade print: %s", event)
            return None

        raw_ts = event.get("timestamp", event.get("ts"))
        try:
            ts = int(float(raw_ts))
        except (TypeError, ValueError):
            ts = int(time.time() * 1000)

        trade = {
            "asset": token_id,
            "price": price,
            "size": size,
            "side": str(event.get("side") or "").upper(),
            "ts": ts,
            "hash": str(event.get("transaction_hash") or event.get("hash") or ""),
        }
        with self._buffer_lock:
            self._trade_buffer.setdefault(token_id, []).append(trade)
            self.trades_captured += 1
        if self.on_trade:
            try:
                self.on_trade(trade)
            except Exception as e:
                log.debug("on_trade callback failed: %s", e)
        return trade

    def drain_trades(self, token_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Pop buffered prints for one token, or for every token when None."""
        with self._buffer_lock:
            if token_id is not None:
                return self._trade_buffer.pop(token_id, [])
            drained: List[Dict[str, Any]] = []
            for tid in list(self._trade_buffer):
                drained.extend(self._trade_buffer.pop(tid))
            return drained

    def handle_raw_message(self, raw: str | bytes | None) -> None:
        """Reduce one raw frame from the market channel into local state."""
        if raw is None:
            return
        if isinstance(raw, (bytes, bytearray)):
            raw = bytes(raw).decode("utf-8", "replace")
        text = str(raw).strip()
        if not text:
            return
        if text.upper() == "PONG":
            self.last_pong_ts = time.time()
            return
        if text.upper() == "PING":
            return
        try:
            msg = json.loads(text)
        except Exception:
            log.debug("non-JSON CLOB frame dropped: %.80s", text)
            return
        events = msg if isinstance(msg, list) else [msg]
        for ev in events:
            if isinstance(ev, dict):
                self._handle_event(ev)

    def _handle_event(self, ev: Dict[str, Any]) -> None:
        """Dispatch a single decoded market-channel event."""
        ev_type = str(ev.get("event_type") or ev.get("type") or "").lower()
        token_id = str(ev.get("asset_id") or ev.get("token_id") or "").strip()
        if not ev_type or not token_id:
            return

        if ev_type == "book":
            self.apply_book_snapshot(token_id, ev.get("bids") or [], ev.get("asks") or [])
        elif ev_type == "price_change":
            for change in (ev.get("changes") or ev.get("price_changes") or []):
                if not isinstance(change, dict):
                    continue
                try:
                    price = float(change.get("price"))
                    size = float(change.get("size") or 0.0)
                except (TypeError, ValueError):
                    continue
                self.apply_price_change(token_id, str(change.get("side") or "BUY"), price, size)
        elif ev_type == "last_trade_price":
            self.record_trade(token_id, ev)
        elif ev_type == "best_bid_ask":
            def _opt_float(key: str) -> Optional[float]:
                """Parse an optional decimal-string quote field."""
                try:
                    return float(ev.get(key))
                except (TypeError, ValueError):
                    return None
            self.apply_best_bid_ask(token_id, _opt_float("best_bid"), _opt_float("best_ask"))
        elif ev_type == "tick_size_change":
            try:
                self.tick_sizes[token_id] = float(ev.get("new_tick_size") or ev.get("tick_size"))
            except (TypeError, ValueError):
                pass

    def update_tokens(self, new_tokens: List[str]) -> None:
        """Update subscribed token IDs."""
        if set(new_tokens) != set(self.token_ids):
            self.token_ids = sorted(list(set(new_tokens)))
            self._tokens_version += 1

    def subscription_payload(self) -> str:
        """Build the market-channel subscription frame for the current tokens."""
        return json.dumps({"assets_ids": list(self.token_ids), "type": "market"})

    def next_backoff(self, current: float) -> float:
        """Advance the reconnect backoff one step, capped at `backoff_max`."""
        return min(current * CLOB_WS_BACKOFF_FACTOR, self.backoff_max)

    def jittered(self, delay: float) -> float:
        """Spread a backoff delay by up to `CLOB_WS_JITTER_PCT` upward."""
        return delay * (1.0 + random.random() * CLOB_WS_JITTER_PCT)

    async def _wait_stop(self, timeout: float) -> bool:
        """Sleep up to `timeout`, returning True if stop was signalled first."""
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def _ping_loop(self, ws: Any) -> None:
        """Send the venue's text `"PING"` keepalive every `ping_interval`."""
        while not self._stop_event.is_set():
            if await self._wait_stop(self.ping_interval):
                return
            try:
                await ws.send("PING")
                self.last_ping_ts = time.time()
            except Exception as e:
                log.debug("CLOB PING failed: %s", e)
                return

    async def _session(self, ws: Any) -> bool:
        """Run one connected session. True = ended cleanly (stop or rotation)."""
        await ws.send(self.subscription_payload())
        subscribed_version = self._tokens_version
        ping_task = asyncio.create_task(self._ping_loop(ws))
        try:
            while not self._stop_event.is_set():
                if self._tokens_version != subscribed_version:
                    return True
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=CLOB_WS_RECV_TIMEOUT)
                except asyncio.TimeoutError:
                    continue
                self.handle_raw_message(msg)
            return True
        finally:
            ping_task.cancel()
            try:
                await ping_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                log.debug("CLOB ping task ended: %s", e)

    async def run_direct(self) -> None:
        """Drive the raw market-channel socket with reconnects until stopped."""
        connect = self._connect_factory
        if connect is None:
            if not WEBSOCKETS_AVAILABLE or websockets is None:
                log.warning("websockets package not available; CLOB direct WS disabled")
                return
            connect = websockets.connect

        backoff = self.backoff_base
        while not self._stop_event.is_set():
            if not self.token_ids:
                if await self._wait_stop(0.5):
                    break
                continue

            clean = False
            try:
                async with connect(self.ws_url, ping_interval=None, close_timeout=5) as ws:
                    self.is_connected = True
                    backoff = self.backoff_base
                    log.info("CLOB market WS connected: %d tokens", len(self.token_ids))
                    clean = await self._session(ws)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.debug("CLOB market WS session ended: %s", e)
            finally:
                self.is_connected = False

            if self._stop_event.is_set():
                break
            if clean:
                continue  # token rotation: resubscribe immediately, no penalty
            self.reconnect_count += 1
            if await self._wait_stop(self.jittered(backoff)):
                break
            backoff = self.next_backoff(backoff)

    def get_status(self) -> Dict[str, Any]:
        """Return socket health and capture counters."""
        return {
            "ws_connected": self.is_connected,
            "token_count": len(self.token_ids),
            "reconnects": self.reconnect_count,
            "trades_captured": self.trades_captured,
            "last_ping_ts": self.last_ping_ts,
            "last_pong_ts": self.last_pong_ts,
            "books_tracked": len(self.books),
        }

    async def run(self) -> None:
        """Connect to CLOB Market WebSocket and process messages."""
        if not POLYMARKET_AVAILABLE or AsyncPublicClient is None:
            log.warning("polymarket SDK not installed; CLOB WS in idle mode")
            return

        while not self._stop_event.is_set():
            if not self.token_ids:
                await asyncio.sleep(1.0)
                continue
            subscribed_version = self._tokens_version
            try:
                async with AsyncPublicClient() as client:
                    async with await client.subscribe(
                        MarketSpec(
                            token_ids=self.token_ids,
                            custom_feature_enabled=True,
                        )
                    ) as stream:
                        self.is_connected = True
                        log.info("Connected to CLOB Market WS with %d tokens", len(self.token_ids))
                        async for event in stream:
                            if self._stop_event.is_set() or self._tokens_version != subscribed_version:
                                break
                            ev_type = getattr(event, "type", "")
                            payload = getattr(event, "payload", None)
                            if not payload:
                                continue

                            tid = getattr(payload, "token_id", None) or getattr(payload, "asset_id", "")
                            if not tid:
                                continue

                            if ev_type == "book" or hasattr(payload, "bids"):
                                raw_bids = getattr(payload, "bids", [])
                                raw_asks = getattr(payload, "asks", [])
                                self.apply_book_snapshot(str(tid), raw_bids, raw_asks)
                            elif ev_type == "price_change" or hasattr(payload, "price_changes"):
                                for pc in getattr(payload, "price_changes", []):
                                    side = getattr(pc, "side", "BUY")
                                    p = float(getattr(pc, "price", 0))
                                    s = float(getattr(pc, "size", 0))
                                    self.apply_price_change(str(tid), side, p, s)
            except Exception as e:
                self.is_connected = False
                log.debug("CLOB Market WS error (reconnecting in 2s): %s", e)
                await asyncio.sleep(2.0)
            finally:
                self.is_connected = False

    def stop(self) -> None:
        """Signal market WebSocket client to stop."""
        self._stop_event.set()


class CLOBStreamCollectorBridge:
    """Runs `CLOBMarketWSClient.run_direct()` on a daemon thread for sync callers.

    The tick collector is a synchronous 1-second loop; the market socket is
    async and must stay connected between polls. This bridge owns the event
    loop and exposes only thread-safe calls, so `poll_once` can drain prints
    that arrived between two ticks without touching asyncio at all.
    """

    def __init__(
        self,
        token_ids: Optional[List[str]] = None,
        ws_url: str = CLOB_WS_URL,
        ping_interval: float = CLOB_WS_PING_INTERVAL,
        connect_factory: Optional[Callable[..., Any]] = None,
        on_trade: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        """Initialize the background CLOB stream collector bridge."""
        self.client = CLOBMarketWSClient(
            token_ids=token_ids,
            on_trade=on_trade,
            ws_url=ws_url,
            ping_interval=ping_interval,
            connect_factory=connect_factory,
        )
        self.is_running: bool = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_ready = threading.Event()
        self._lock = threading.Lock()

    @property
    def is_connected(self) -> bool:
        """True while the market socket is connected."""
        return bool(self.client.is_connected)

    def start(self) -> None:
        """Start the daemon thread running the market socket."""
        with self._lock:
            if self.is_running:
                return
            self.is_running = True
            self._loop_ready.clear()
            self._thread = threading.Thread(
                target=self._worker_main, daemon=True, name="CLOBStreamCollectorBridge")
            self._thread.start()
            self._loop_ready.wait(timeout=5.0)

    def _worker_main(self) -> None:
        """Worker thread entry point owning the socket's event loop."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        # Re-arm on this loop: the ctor builds the event with no loop running.
        self.client._stop_event = asyncio.Event()
        self._loop_ready.set()
        try:
            self._loop.run_until_complete(self.client.run_direct())
        except Exception as e:
            log.debug("CLOB stream bridge loop ended: %s", e)
        finally:
            try:
                pending = [t for t in asyncio.all_tasks(self._loop) if not t.done()]
                for t in pending:
                    t.cancel()
                if pending:
                    self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            self._loop.close()
            self.client.is_connected = False
            self.is_running = False

    def update_subscribed_tokens(self, token_ids: List[str]) -> None:
        """Sync the subscribed token set (triggers resubscribe when changed)."""
        self.client.update_tokens(list(token_ids))

    def drain_trades_for_token(self, token_id: str) -> List[Dict[str, Any]]:
        """Pop every print buffered for one token since the last drain."""
        return self.client.drain_trades(token_id)

    def drain_all_trades(self) -> List[Dict[str, Any]]:
        """Pop every buffered print across all subscribed tokens."""
        return self.client.drain_trades()

    def get_book_for_token(self, token_id: str) -> Optional[Dict[str, Any]]:
        """Return a copy of the streamed book for a token, or None."""
        book = self.client.books.get(token_id)
        return dict(book) if book else None

    def get_status(self) -> Dict[str, Any]:
        """Return socket health, capture counters and runner state."""
        status = self.client.get_status()
        status["is_running"] = self.is_running
        return status

    def stop(self) -> None:
        """Stop the socket and join the worker thread."""
        with self._lock:
            if not self.is_running:
                return
            loop = self._loop
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(self.client.stop)
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=5.0)
            self.is_running = False
            self.client.is_connected = False


class UserSpecStreamClient:
    """Authenticated user order and trade execution stream with buffer-first reconciliation."""

    def __init__(
        self,
        on_order_event: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        """Initialize authenticated user stream client."""
        self.on_order_event = on_order_event
        self.open_orders: Dict[str, Dict[str, Any]] = {}
        self.completed_orders: Dict[str, Dict[str, Any]] = {}
        self.is_connected: bool = False
        self._buffering: bool = False
        self._buffer: List[Dict[str, Any]] = []
        self._stop_event = asyncio.Event()

    def start_buffering(self) -> None:
        """Start buffering incoming WebSocket events during boot."""
        self._buffering = True
        self._buffer = []

    def handle_order_event(self, payload: Dict[str, Any]) -> None:
        """Reduce order event into local state store."""
        if self._buffering:
            self._buffer.append(payload)
            return

        order_id = str(payload.get("id") or payload.get("order_id") or "")
        if not order_id:
            return

        status = str(payload.get("status") or "").upper()
        if status in ("MATCHED", "CANCELED", "CANCELLED", "FILLED"):
            self.open_orders.pop(order_id, None)
            self.completed_orders[order_id] = payload
        elif status in ("LIVE", "DELAYED", "UNMATCHED"):
            self.open_orders[order_id] = payload

        if self.on_order_event:
            self.on_order_event(payload)

    def reconcile_with_rest(self, rest_orders: List[Dict[str, Any]]) -> None:
        """Seed base state with REST snapshot and replay buffered WS events."""
        self._buffering = False
        for o in rest_orders:
            oid = str(o.get("id") or "")
            if not oid:
                continue
            status = str(o.get("status") or "").upper()
            if status in ("LIVE", "DELAYED", "UNMATCHED"):
                self.open_orders[oid] = o
            else:
                self.completed_orders[oid] = o

        # Sort buffered events monotonically by timestamp and replay
        sorted_buffer = sorted(self._buffer, key=lambda x: x.get("timestamp", 0))
        for evt in sorted_buffer:
            self.handle_order_event(evt)
        self._buffer.clear()

    async def run(self) -> None:
        """Connect to authenticated UserSpec stream if credentials present."""
        private_key = os.getenv("POLY_PRIVATE_KEY", "")
        if not private_key or not POLYMARKET_AVAILABLE or AsyncSecureClient is None:
            log.info("UserSpec stream idling: POLY_PRIVATE_KEY not set or SDK unavailable")
            return

        while not self._stop_event.is_set():
            try:
                async with await AsyncSecureClient.create(private_key=private_key) as client:
                    async with await client.subscribe(UserSpec()) as stream:
                        self.is_connected = True
                        log.info("Connected to authenticated UserSpec stream")
                        async for event in stream:
                            if self._stop_event.is_set():
                                break
                            payload = getattr(event, "payload", None)
                            if not payload:
                                continue
                            pdict = asdict(payload) if hasattr(payload, "__dataclass_fields__") else dict(payload)
                            self.handle_order_event(pdict)
            except Exception as e:
                self.is_connected = False
                log.debug("UserSpec stream error (reconnecting in 5s): %s", e)
                await asyncio.sleep(5.0)
            finally:
                self.is_connected = False

    def stop(self) -> None:
        """Signal user order stream client to stop."""
        self._stop_event.set()


class UnifiedStreamBridge:
    """Orchestrates RTDS, CLOB WebSocket, and UserSpec in a background thread."""

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        on_spot_tick: Optional[Callable[[str, int, float], None]] = None,
        on_book_update: Optional[Callable[[str, Dict[float, float], Dict[float, float]], None]] = None,
        on_order_event: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_rtds_tick: Optional[Callable[[str, int, float], None]] = None,
    ):
        """Initialize unified stream bridge with callbacks."""
        self.symbols = symbols or RTDS_SYMBOLS
        self.binance_symbols = BINANCE_SYMBOLS
        self.on_spot_tick_ext = on_spot_tick
        self.on_book_update_ext = on_book_update
        self.on_order_event_ext = on_order_event
        self.on_rtds_tick = on_rtds_tick

        self.binance = BinanceDirectWSClient(symbols=self.binance_symbols, on_spot_tick=self._handle_binance_spot_tick)
        self.rtds = RTDSStreamClient(symbols=self.symbols, on_spot_tick=self._handle_rtds_spot_tick)
        self.clob = CLOBMarketWSClient(on_book_update=self._handle_book_update)
        self.user = UserSpecStreamClient(on_order_event=self._handle_order_event)

        self.is_running: bool = False
        self._binance_task: Optional[asyncio.Task] = None
        self._rtds_task: Optional[asyncio.Task] = None
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_ready = threading.Event()
        self._seq: int = 0
        self._lock = threading.Lock()
        self._subscribers: List[asyncio.Queue] = []

    @property
    def binance_spot_prices(self) -> Dict[str, float]:
        """Direct Binance WebSocket spot prices."""
        return self.binance.spot_prices

    @property
    def rtds_spot_prices(self) -> Dict[str, float]:
        """Polymarket RTDS spot prices."""
        return self.rtds.spot_prices

    def _handle_binance_spot_tick(self, symbol: str, ts: int, price: float) -> None:
        """Handle incoming sub-second spot tick from Binance Direct WS."""
        if self.on_spot_tick_ext:
            try:
                self.on_spot_tick_ext(symbol, ts, price, source="BINANCE")
            except TypeError:
                self.on_spot_tick_ext(symbol, ts, price)
        slug = SYMBOL_TO_SERIES.get(symbol.lower())
        slugs = series_for_symbol(symbol)
        rtds_price = self.rtds.spot_prices.get(symbol.lower())
        price_diff = round(price - rtds_price, 4) if rtds_price is not None else None
        price_diff_pct = round(((price - rtds_price) / rtds_price) * 100.0, 4) if (rtds_price is not None and rtds_price > 0) else None
        self._broadcast(
            stream_id="spot",
            data={
                "symbol": symbol,
                "timestamp": ts,
                "price": price,
                "actual_price": price,
                "rtds_price": rtds_price,
                "price_diff": price_diff,
                "price_diff_pct": price_diff_pct,
                "slug": slug,
                "slugs": slugs,
                "source": "BINANCE_WS",
            },
        )

    def _handle_rtds_spot_tick(self, symbol: str, ts: int, price: float) -> None:
        """Handle incoming spot tick from RTDS or REST fallback."""
        if self.on_rtds_tick:
            self.on_rtds_tick(symbol, ts, price)

        # Fallback: if Binance WS is disconnected, forward RTDS tick as primary spot tick
        if not self.binance.is_connected and self.on_spot_tick_ext:
            try:
                self.on_spot_tick_ext(symbol, ts, price, source="RTDS")
            except TypeError:
                self.on_spot_tick_ext(symbol, ts, price)

        slug = SYMBOL_TO_SERIES.get(symbol.lower())
        slugs = series_for_symbol(symbol)
        binance_price = self.binance.spot_prices.get(symbol.lower()) if self.binance.is_connected else None
        price_diff = round(binance_price - price, 4) if binance_price is not None else None
        price_diff_pct = round(((binance_price - price) / price) * 100.0, 4) if (binance_price is not None and price > 0) else None
        self._broadcast(
            stream_id="spot",
            data={
                "symbol": symbol,
                "timestamp": ts,
                "price": binance_price if (self.binance.is_connected and binance_price is not None) else price,
                "actual_price": binance_price,
                "rtds_price": price,
                "price_diff": price_diff,
                "price_diff_pct": price_diff_pct,
                "slug": slug,
                "slugs": slugs,
                "source": "RTDS",
            },
        )

    def _handle_spot_tick(self, symbol: str, ts: int, price: float) -> None:
        """Backward-compatible alias for spot tick handling."""
        self._handle_binance_spot_tick(symbol, ts, price)

    def _handle_book_update(self, token_id: str, bids: Dict[float, float], asks: Dict[float, float]) -> None:
        """Handle incoming book snapshot/delta and broadcast envelope."""
        if self.on_book_update_ext:
            self.on_book_update_ext(token_id, bids, asks)
        best_b = max(bids.keys()) if bids else None
        best_a = min(asks.keys()) if asks else None
        self._broadcast(stream_id="books", data={"token_id": token_id, "best_bid": best_b, "best_ask": best_a})

    def _handle_order_event(self, payload: Dict[str, Any]) -> None:
        """Handle incoming user order event and broadcast envelope."""
        if self.on_order_event_ext:
            self.on_order_event_ext(payload)
        self._broadcast(stream_id="orders", data=payload)

    def _broadcast(self, stream_id: str, data: Any, event_type: str = "delta") -> None:
        """Broadcast an event envelope to all registered SSE subscriber queues."""
        with self._lock:
            self._seq += 1
            envelope = DashboardEnvelope(
                type=event_type,
                stream_id=stream_id,
                seq=self._seq,
                server_time=int(time.time() * 1000),
                data=data,
            )
        # Notify any registered async queues
        if self._loop and self._loop.is_running():
            msg = envelope.to_json()

            def _offer(queue: asyncio.Queue, payload: str) -> None:
                """Safely put payload into queue, dropping if full."""
                try:
                    queue.put_nowait(payload)
                except asyncio.QueueFull:
                    log.debug("Subscriber queue full; dropping envelope")

            for q in list(self._subscribers):
                try:
                    target_loop = getattr(q, "_loop", None) or self._loop
                    if target_loop and target_loop.is_running():
                        target_loop.call_soon_threadsafe(_offer, q, msg)
                except Exception:
                    pass

    def register_queue(self, q: asyncio.Queue) -> None:
        """Register an SSE subscriber queue for real-time broadcasts."""
        with self._lock:
            if q not in self._subscribers:
                self._subscribers.append(q)

    def unregister_queue(self, q: asyncio.Queue) -> None:
        """Unregister an SSE subscriber queue."""
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def start(self) -> None:
        """Start the background streaming worker thread."""
        with self._lock:
            if self.is_running:
                return
            self.binance._stop_event.clear()
            self.rtds._stop_event.clear()
            self.clob._stop_event.clear()
            self.user._stop_event.clear()
            self.is_running = True
            self._loop_ready.clear()
            self._thread = threading.Thread(target=self._worker_main, daemon=True, name="UnifiedStreamBridge")
            self._thread.start()
            self._loop_ready.wait(timeout=5.0)

    def _worker_main(self) -> None:
        """Worker thread entry point."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self.binance._stop_event = asyncio.Event()
        self.rtds._stop_event = asyncio.Event()
        self.clob._stop_event = asyncio.Event()
        self.user._stop_event = asyncio.Event()
        self._loop_ready.set()
        try:
            self._binance_task = self._loop.create_task(self.binance.run())
            self._rtds_task = self._loop.create_task(self.rtds.run())
            clob_task = self._loop.create_task(self.clob.run())
            user_task = self._loop.create_task(self.user.run())
            self._tasks = [self._binance_task, self._rtds_task, clob_task, user_task]
            self._loop.run_until_complete(asyncio.gather(*self._tasks, return_exceptions=True))
        except Exception as e:
            log.debug("Stream worker loop ended: %s", e)
        finally:
            try:
                pending = [t for t in asyncio.all_tasks(self._loop) if not t.done()]
                for t in pending:
                    t.cancel()
                if pending:
                    self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            self._loop.close()
            self.is_running = False

    @property
    def is_binance_running(self) -> bool:
        """Check whether the Binance direct WS stream task is currently running."""
        if not self.is_running:
            return False
        if self._binance_task is not None:
            return not self._binance_task.done()
        return True

    @property
    def is_rtds_running(self) -> bool:
        """Check whether the RTDS stream task is currently running."""
        if not self.is_running:
            return False
        if self._rtds_task is not None:
            return not self._rtds_task.done()
        return True

    def update_market_tokens(self, tokens: List[str]) -> None:
        """Update active CLOB market tokens."""
        self.clob.update_tokens(tokens)

    def stop(self) -> None:
        """Stop background worker thread gracefully."""
        with self._lock:
            if not self.is_running:
                return
            self.binance.stop()
            self.rtds.stop()
            self.clob.stop()
            self.user.stop()
            if self._loop and self._loop.is_running():
                def _cancel_and_stop():
                    """Cancel all running tasks on the worker loop."""
                    for t in getattr(self, "_tasks", []):
                        t.cancel()
                self._loop.call_soon_threadsafe(_cancel_and_stop)
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=3.0)
            self.is_running = False

    def get_status(self) -> Dict[str, Any]:
        """Return streaming health and telemetry."""
        for _ in range(3):
            try:
                binance_prices = self.binance.spot_prices.copy()
                rtds_prices = self.rtds.spot_prices.copy()
                break
            except RuntimeError:
                continue
        else:
            binance_prices = dict(list(self.binance.spot_prices.items()))
            rtds_prices = dict(list(self.rtds.spot_prices.items()))
        price_diffs: Dict[str, float] = {}
        price_diff_pcts: Dict[str, float] = {}

        common_syms = set(binance_prices.keys()).intersection(rtds_prices.keys())
        for sym in common_syms:
            bp = binance_prices[sym]
            rp = rtds_prices[sym]
            d = round(bp - rp, 4)
            price_diffs[sym] = d
            if rp > 0:
                price_diff_pcts[sym] = round((d / rp) * 100.0, 4)

        if self.binance.is_connected:
            active_source = "BINANCE_WS"
            active_prices = {**rtds_prices, **binance_prices}
        else:
            active_source = "RTDS"
            active_prices = {**binance_prices, **rtds_prices}
        return {
            "is_running": self.is_running,
            "binance_ws_connected": self.binance.is_connected,
            "rtds_connected": self.rtds.is_connected,
            "clob_ws_connected": self.clob.is_connected,
            "user_ws_connected": self.user.is_connected,
            "active_spot_source": active_source,
            "symbols": active_prices,
            "binance_prices": binance_prices,
            "rtds_prices": rtds_prices,
            "price_diffs": price_diffs,
            "price_diff_pcts": price_diff_pcts,
            "token_count": len(self.clob.token_ids),
            "open_orders_count": len(self.user.open_orders),
            "seq": self._seq,
        }

