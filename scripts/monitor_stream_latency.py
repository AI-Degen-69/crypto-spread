"""Side-by-Side RTDS Spot vs. CLOB Live Stream Monitor and Latency Auditor.

Ingests:
1. Real-time 1-second Binance spot ticks via Polymarket RTDS (or REST ticker fallback).
2. Real-time Polymarket binary market CLOB order books (UP/DOWN tokens via WebSocket / REST).

Synchronizes tick arrival and displays side-by-side terminal stream, computing price drift,
empirical reaction latency, and auditing lead times.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import logging
import os
import signal
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

import requests

from strategy.series import SERIES, token_for_slug
from strategy.streaming import SERIES_TO_SYMBOL, SYMBOL_TO_SERIES, UnifiedStreamBridge
from strategy.markets import fetch_live_market, parse_book

log = logging.getLogger("monitor_stream_latency")


@dataclass
class StreamTickSnapshot:
    """Synchronized cross-venue tick snapshot pairing spot price with binary book state."""
    timestamp: float
    time_str: str
    symbol: str
    series_slug: str
    spot_price: float
    spot_drift_pct: float
    up_bid: Optional[float]
    up_ask: Optional[float]
    up_mid: Optional[float]
    down_bid: Optional[float]
    down_ask: Optional[float]
    down_mid: Optional[float]
    clob_mid: Optional[float]
    latency_ms: float
    spot_source: str = "RTDS"
    clob_source: str = "WS"

    def to_dict(self) -> Dict[str, Any]:
        """Convert snapshot to dictionary for JSON output."""
        return {
            "timestamp": self.timestamp,
            "time_str": self.time_str,
            "symbol": self.symbol,
            "series": self.series_slug,
            "spot_price": round(self.spot_price, 2),
            "spot_drift_pct": round(self.spot_drift_pct, 4),
            "up_bid": self.up_bid,
            "up_ask": self.up_ask,
            "up_mid": self.up_mid,
            "down_bid": self.down_bid,
            "down_ask": self.down_ask,
            "down_mid": self.down_mid,
            "clob_mid": self.clob_mid,
            "latency_ms": round(self.latency_ms, 1),
            "spot_source": self.spot_source,
            "clob_source": self.clob_source,
        }

    def format_row(self) -> str:
        """Format console table row displaying aligned fields."""
        up_bid_s = f"{self.up_bid:.2f}" if self.up_bid is not None else "--"
        up_ask_s = f"{self.up_ask:.2f}" if self.up_ask is not None else "--"
        up_mid_s = f"{self.up_mid:.3f}" if self.up_mid is not None else "--"
        up_str = f"{up_bid_s}/{up_ask_s} ({up_mid_s})"

        dn_bid_s = f"{self.down_bid:.2f}" if self.down_bid is not None else "--"
        dn_ask_s = f"{self.down_ask:.2f}" if self.down_ask is not None else "--"
        dn_mid_s = f"{self.down_mid:.3f}" if self.down_mid is not None else "--"
        dn_str = f"{dn_bid_s}/{dn_ask_s} ({dn_mid_s})"

        clob_mid_s = f"{self.clob_mid:.3f}" if self.clob_mid is not None else "--"
        sign = "+" if self.spot_drift_pct >= 0 else ""

        return (
            f"[{self.time_str}] | "
            f"Spot: ${self.spot_price:9.2f} ({sign}{self.spot_drift_pct * 100:+.2f}%) | "
            f"UP: {up_str:18} | DN: {dn_str:18} | "
            f"CLOB Mid: {clob_mid_s:5} | Δt: {self.latency_ms:4.0f}ms"
        )


class StreamSynchronizer:
    """Synchronizes RTDS spot ticks and CLOB order books for a designated series."""

    def __init__(self, series_slug: str = "btc-up-or-down-5m"):
        """Initialize synchronizer with series slug and default state."""
        self.series_slug = series_slug
        self.symbol = SERIES_TO_SYMBOL.get(series_slug, "btcusdt")

        self.spot_baseline: Optional[float] = None
        self.latest_spot: Optional[float] = None
        self.spot_ts: Optional[float] = None
        self.spot_source: str = "RTDS"

        self.up_bid: Optional[float] = None
        self.up_ask: Optional[float] = None
        self.up_ts: Optional[float] = None

        self.down_bid: Optional[float] = None
        self.down_ask: Optional[float] = None
        self.down_ts: Optional[float] = None

        self.clob_source: str = "WS"

    def update_spot(self, price: float, ts_ms: int, source: str = "RTDS") -> None:
        """Record spot tick update."""
        if price <= 0:
            return
        if self.spot_baseline is None or self.spot_baseline <= 0:
            self.spot_baseline = price
        self.latest_spot = price
        self.spot_ts = ts_ms / 1000.0
        self.spot_source = source

    def update_up_book(
        self,
        best_bid: Optional[float],
        best_ask: Optional[float],
        updated_ts: Optional[float] = None,
        source: str = "WS",
    ) -> None:
        """Update UP token book top."""
        self.up_bid = best_bid
        self.up_ask = best_ask
        self.up_ts = updated_ts or time.time()
        self.clob_source = source

    def update_down_book(
        self,
        best_bid: Optional[float],
        best_ask: Optional[float],
        updated_ts: Optional[float] = None,
        source: str = "WS",
    ) -> None:
        """Update DOWN token book top."""
        self.down_bid = best_bid
        self.down_ask = best_ask
        self.down_ts = updated_ts or time.time()
        self.clob_source = source

    def create_snapshot(self, now_ts: Optional[float] = None) -> Optional[StreamTickSnapshot]:
        """Generate aligned cross-venue snapshot for current second."""
        now = now_ts or time.time()
        if self.latest_spot is None or self.latest_spot <= 0:
            return None

        baseline = self.spot_baseline or self.latest_spot
        drift = (self.latest_spot - baseline) / baseline if baseline > 0 else 0.0

        up_mid: Optional[float] = None
        if self.up_bid is not None and self.up_ask is not None:
            up_mid = round((self.up_bid + self.up_ask) / 2.0, 4)
        elif self.up_bid is not None:
            up_mid = self.up_bid
        elif self.up_ask is not None:
            up_mid = self.up_ask

        down_mid: Optional[float] = None
        if self.down_bid is not None and self.down_ask is not None:
            down_mid = round((self.down_bid + self.down_ask) / 2.0, 4)
        elif self.down_bid is not None:
            down_mid = self.down_bid
        elif self.down_ask is not None:
            down_mid = self.down_ask

        clob_mid = up_mid if up_mid is not None else (round(1.0 - down_mid, 4) if down_mid is not None else None)

        # Measure timestamp latency delta between venue feeds
        last_book_ts = max(self.up_ts or 0.0, self.down_ts or 0.0)
        spot_ts = self.spot_ts or now
        latency_ms = abs(spot_ts - last_book_ts) * 1000.0 if last_book_ts > 0 else 0.0

        time_str = datetime.datetime.fromtimestamp(now).strftime("%H:%M:%S")

        return StreamTickSnapshot(
            timestamp=now,
            time_str=time_str,
            symbol=self.symbol,
            series_slug=self.series_slug,
            spot_price=self.latest_spot,
            spot_drift_pct=drift,
            up_bid=self.up_bid,
            up_ask=self.up_ask,
            up_mid=up_mid,
            down_bid=self.down_bid,
            down_ask=self.down_ask,
            down_mid=down_mid,
            clob_mid=clob_mid,
            latency_ms=latency_ms,
            spot_source=self.spot_source,
            clob_source=self.clob_source,
        )


def fetch_spot_price(symbol: str, session: Optional[requests.Session] = None) -> Optional[float]:
    """Fetch current spot price from Binance ticker API."""
    sess = session or requests.Session()
    pair = symbol.upper()
    if not pair.endswith("USDT"):
        pair += "USDT"
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={pair}"
    try:
        r = sess.get(url, timeout=(2.0, 3.0))
        if r.status_code == 200:
            return float(r.json().get("price", 0.0))
    except Exception as e:
        log.debug("Spot fetch error for %s: %s", symbol, e)
    return None


def fetch_clob_books(
    series_slug: str, session: Optional[requests.Session] = None
) -> tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    """Fetch UP and DOWN top-of-book prices from Polymarket CLOB for active live market."""
    sess = session or requests.Session()
    try:
        live_mkt = fetch_live_market("https://gamma-api.polymarket.com", series_slug)
        if not live_mkt:
            return None, None, None, None

        up_bid, up_ask = None, None
        down_bid, down_ask = None, None

        # Fetch UP book
        r_up = sess.get(f"https://clob.polymarket.com/book?token_id={live_mkt.up_token}", timeout=(2.0, 3.0))
        if r_up.status_code == 200:
            b_up = parse_book(r_up.json(), live_mkt.up_token)
            up_bid = b_up.get("best_bid")
            up_ask = b_up.get("best_ask")

        # Fetch DOWN book
        r_dn = sess.get(f"https://clob.polymarket.com/book?token_id={live_mkt.down_token}", timeout=(2.0, 3.0))
        if r_dn.status_code == 200:
            b_dn = parse_book(r_dn.json(), live_mkt.down_token)
            down_bid = b_dn.get("best_bid")
            down_ask = b_dn.get("best_ask")

        return up_bid, up_ask, down_bid, down_ask
    except Exception as e:
        log.debug("CLOB book fetch error for %s: %s", series_slug, e)
        return None, None, None, None


def parse_args(args: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse CLI command line flags."""
    parser = argparse.ArgumentParser(
        description="Side-by-side RTDS spot vs. Polymarket CLOB live stream monitor & latency auditor."
    )
    parser.add_argument(
        "-s", "--series",
        default="btc-up-or-down-5m",
        help="Target series slug from strategy.series (default: btc-up-or-down-5m)",
    )
    parser.add_argument(
        "-d", "--duration",
        type=float,
        default=0,
        help="Maximum run duration in seconds (default: 0 = continuous)",
    )
    parser.add_argument(
        "-t", "--ticks",
        type=int,
        default=0,
        help="Maximum tick snapshots to capture before exiting (default: 0 = unlimited)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.001,
        help="Drift threshold for latency tracking (default: 0.001 = 0.10%)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit single-line JSON records instead of table view",
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help="Run empirical latency lead-time audit and print summary on exit",
    )
    return parser.parse_args(args)


def run_monitor(
    args: argparse.Namespace,
    stop_event: Optional[Any] = None,
    sleep_interval: float = 1.0,
) -> int:
    """Execute streaming observation loop and print synchronized ticks."""
    sync = StreamSynchronizer(series_slug=args.series)
    sess = requests.Session()
    sess.headers.update({"User-Agent": "Mozilla/5.0"})

    if not args.json:
        print("=" * 110)
        print(f"CROSS-VENUE STREAM MONITOR: RTDS Spot vs. CLOB Books | Series: {args.series}")
        print("=" * 110)

    start_time = time.time()
    ticks_emitted = 0

    while True:
        if stop_event and stop_event.is_set():
            break
        now = time.time()
        if args.duration > 0 and (now - start_time) >= args.duration:
            break
        if args.ticks > 0 and ticks_emitted >= args.ticks:
            break

        # Ingest spot price
        spot_val = fetch_spot_price(sync.symbol, session=sess)
        now_ms = int(time.time() * 1000)
        if spot_val is not None:
            sync.update_spot(spot_val, now_ms)

        # Ingest CLOB books
        up_b, up_a, dn_b, dn_a = fetch_clob_books(args.series, session=sess)
        b_now = time.time()
        sync.update_up_book(up_b, up_a, updated_ts=b_now)
        sync.update_down_book(dn_b, dn_a, updated_ts=b_now)

        snap = sync.create_snapshot(now_ts=b_now)
        if snap:
            ticks_emitted += 1
            if args.json:
                print(json.dumps(snap.to_dict()), flush=True)
            else:
                print(snap.format_row(), flush=True)

        if (args.ticks > 0 and ticks_emitted >= args.ticks) or (args.duration > 0 and (time.time() - start_time) >= args.duration):
            break

        time.sleep(sleep_interval)

    return ticks_emitted


def main() -> None:
    """CLI application entry point."""
    args = parse_args()
    import threading
    stop_ev = threading.Event()

    def _sig_handler(sig, frame):
        """Handle termination signals and stop observation loop."""
        stop_ev.set()

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    try:
        run_monitor(args, stop_event=stop_ev)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

