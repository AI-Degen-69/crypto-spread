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
import threading
import time
from dataclasses import dataclass, asdict, field
from decimal import Decimal
from typing import Callable, Dict, List, Optional, Any, Set
import requests

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


class CLOBMarketWSClient:
    """Connects to Polymarket CLOB Market WebSocket and maintains order books."""

    def __init__(
        self,
        token_ids: Optional[List[str]] = None,
        on_book_update: Optional[Callable[[str, Dict[float, float], Dict[float, float]], None]] = None,
    ):
        """Initialize CLOB Market WebSocket client."""
        self.token_ids: List[str] = token_ids or []
        self._tokens_version: int = 0
        self.on_book_update = on_book_update
        self.books: Dict[str, Dict[str, Any]] = {}
        self.is_connected: bool = False
        self._stop_event = asyncio.Event()

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

    def update_tokens(self, new_tokens: List[str]) -> None:
        """Update subscribed token IDs."""
        if set(new_tokens) != set(self.token_ids):
            self.token_ids = sorted(list(set(new_tokens)))
            self._tokens_version += 1

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
    ):
        """Initialize unified stream bridge with callbacks."""
        self.symbols = symbols or RTDS_SYMBOLS
        self.on_spot_tick_ext = on_spot_tick
        self.on_book_update_ext = on_book_update
        self.on_order_event_ext = on_order_event

        self.rtds = RTDSStreamClient(symbols=self.symbols, on_spot_tick=self._handle_spot_tick)
        self.clob = CLOBMarketWSClient(on_book_update=self._handle_book_update)
        self.user = UserSpecStreamClient(on_order_event=self._handle_order_event)

        self.is_running: bool = False
        self._rtds_task: Optional[asyncio.Task] = None
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_ready = threading.Event()
        self._seq: int = 0
        self._lock = threading.Lock()
        self._subscribers: List[asyncio.Queue] = []

    def _handle_spot_tick(self, symbol: str, ts: int, price: float) -> None:
        """Handle incoming spot tick and broadcast envelope."""
        if self.on_spot_tick_ext:
            self.on_spot_tick_ext(symbol, ts, price)
        slug = SYMBOL_TO_SERIES.get(symbol.lower())
        slugs = series_for_symbol(symbol)
        self._broadcast(stream_id="spot", data={"symbol": symbol, "timestamp": ts, "price": price, "slug": slug, "slugs": slugs})

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
                    self._loop.call_soon_threadsafe(_offer, q, msg)
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
        if self.is_running:
            return
        self.is_running = True
        self._loop_ready.clear()
        self._thread = threading.Thread(target=self._worker_main, daemon=True, name="UnifiedStreamBridge")
        self._thread.start()
        self._loop_ready.wait(timeout=5.0)

    def _worker_main(self) -> None:
        """Worker thread entry point."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop_ready.set()
        try:
            self._rtds_task = self._loop.create_task(self.rtds.run())
            clob_task = self._loop.create_task(self.clob.run())
            user_task = self._loop.create_task(self.user.run())
            self._tasks = [self._rtds_task, clob_task, user_task]
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
        if not self.is_running:
            return
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
        return {
            "is_running": self.is_running,
            "rtds_connected": self.rtds.is_connected,
            "clob_ws_connected": self.clob.is_connected,
            "user_ws_connected": self.user.is_connected,
            "symbols": self.rtds.spot_prices,
            "token_count": len(self.clob.token_ids),
            "open_orders_count": len(self.user.open_orders),
            "seq": self._seq,
        }
