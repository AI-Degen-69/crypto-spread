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
import collections
import datetime
import json
import logging
import os
import signal
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

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
    actual_price: Optional[float] = None
    rtds_price: Optional[float] = None
    price_diff: Optional[float] = None
    price_diff_pct: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert snapshot to dictionary for JSON output."""
        return {
            "timestamp": self.timestamp,
            "time_str": self.time_str,
            "symbol": self.symbol,
            "series": self.series_slug,
            "spot_price": round(self.spot_price, 2),
            "spot_drift_pct": round(self.spot_drift_pct, 4),
            "actual_price": round(self.actual_price, 2) if self.actual_price is not None else None,
            "rtds_price": round(self.rtds_price, 2) if self.rtds_price is not None else None,
            "price_diff": round(self.price_diff, 4) if self.price_diff is not None else None,
            "price_diff_pct": round(self.price_diff_pct, 4) if self.price_diff_pct is not None else None,
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

        diff_str = ""
        if self.price_diff is not None and self.price_diff_pct is not None:
            sign = "+" if self.price_diff > 0 else ""
            diff_str = f" | Δ: {sign}${self.price_diff:.2f} ({self.price_diff_pct:+.3f}%)"

        return (
            f"[{self.time_str}] | "
            f"Spot: ${self.spot_price:9.2f} ({self.spot_drift_pct * 100:+.2f}%){diff_str} | "
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

        self.actual_price: Optional[float] = None
        self.rtds_price: Optional[float] = None
        self.price_diff: Optional[float] = None
        self.price_diff_pct: Optional[float] = None

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

        src_upper = (source or "").upper()
        if "BINANCE" in src_upper or src_upper == "ACTUAL":
            self.actual_price = price
        elif "RTDS" in src_upper:
            self.rtds_price = price
        else:
            if self.actual_price is None:
                self.actual_price = price

        self._recalc_price_diff()

    def update_actual_spot(self, price: float, ts_ms: Optional[int] = None) -> None:
        """Record direct exchange (Binance) actual spot price."""
        if price <= 0:
            return
        self.actual_price = price
        if ts_ms:
            self.spot_ts = ts_ms / 1000.0
        self._recalc_price_diff()

    def update_rtds_spot(self, price: float, ts_ms: Optional[int] = None) -> None:
        """Record Polymarket RTDS spot price."""
        if price <= 0:
            return
        self.rtds_price = price
        if ts_ms:
            self.spot_ts = ts_ms / 1000.0
        self._recalc_price_diff()

    def _recalc_price_diff(self) -> None:
        """Recalculate instantaneous price difference and percent."""
        if self.actual_price is not None and self.rtds_price is not None and self.rtds_price > 0:
            self.price_diff = round(self.actual_price - self.rtds_price, 4)
            self.price_diff_pct = round(((self.actual_price - self.rtds_price) / self.rtds_price) * 100.0, 4)
        elif self.actual_price is not None and self.rtds_price is not None:
            self.price_diff = round(self.actual_price - self.rtds_price, 4)
            self.price_diff_pct = 0.0
        else:
            self.price_diff = None
            self.price_diff_pct = None

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
            actual_price=self.actual_price,
            rtds_price=self.rtds_price,
            price_diff=self.price_diff,
            price_diff_pct=self.price_diff_pct,
        )


class LatencyAuditor:
    """Detects spot price impulse moves and measures elapsed time until CLOB book adjustments."""

    def __init__(self, drift_threshold: float = 0.0010, response_window_sec: float = 10.0):
        """Initialize latency auditor with drift threshold and response window."""
        self.drift_threshold = drift_threshold
        self.response_window_sec = response_window_sec
        self.events: List[Dict[str, Any]] = []
        self._pending_shock: Optional[Dict[str, Any]] = None
        self._in_shock: bool = False
        self._recent_prices: collections.deque[Tuple[float, float]] = collections.deque()

    def record_tick(self, snapshot: StreamTickSnapshot) -> None:
        """Process tick snapshot and track shock events and reactions."""
        now = snapshot.timestamp
        resolved_shock = False

        # Maintain rolling 3-second window of spot prices: (timestamp, price)
        self._recent_prices.append((now, snapshot.spot_price))
        while self._recent_prices and (now - self._recent_prices[0][0]) > 3.0:
            self._recent_prices.popleft()

        # Check existing pending shock
        if self._pending_shock:
            dt = now - self._pending_shock["shock_ts"]
            base_mid = self._pending_shock.get("baseline_mid")
            base_bid = self._pending_shock.get("baseline_bid")
            base_ask = self._pending_shock.get("baseline_ask")
            base_dn_b = self._pending_shock.get("baseline_down_bid")
            base_dn_a = self._pending_shock.get("baseline_down_ask")
            if dt <= self.response_window_sec:
                reacted = False
                if base_mid is not None and snapshot.clob_mid is not None:
                    if abs(snapshot.clob_mid - base_mid) >= 0.01:
                        reacted = True
                if base_bid is not None and snapshot.up_bid is not None:
                    if abs(snapshot.up_bid - base_bid) >= 0.01:
                        reacted = True
                if base_ask is not None and snapshot.up_ask is not None:
                    if abs(snapshot.up_ask - base_ask) >= 0.01:
                        reacted = True
                if base_dn_b is not None and snapshot.down_bid is not None:
                    if abs(snapshot.down_bid - base_dn_b) >= 0.01:
                        reacted = True
                if base_dn_a is not None and snapshot.down_ask is not None:
                    if abs(snapshot.down_ask - base_dn_a) >= 0.01:
                        reacted = True

                if reacted:
                    self._pending_shock["reaction_time_sec"] = dt
                    self._pending_shock["reaction_time_ms"] = dt * 1000.0
                    self._pending_shock["clob_reacted"] = True
                    self.events.append(self._pending_shock)
                    self._pending_shock = None
                    resolved_shock = True
            else:
                self._pending_shock["clob_reacted"] = False
                self.events.append(self._pending_shock)
                self._pending_shock = None
                resolved_shock = True

        if resolved_shock:
            return

        # Check for acute price movement within preceding 3-second window
        if len(self._recent_prices) >= 2:
            oldest_price = self._recent_prices[0][1]
            short_window_drift = (snapshot.spot_price - oldest_price) / oldest_price if oldest_price > 0 else 0.0
            drift_active = abs(short_window_drift) >= self.drift_threshold
            trigger_drift = short_window_drift
        else:
            short_window_drift = 0.0
            # On first tick of a session or isolated test, allow cumulative spot_drift_pct to seed shock
            if not self.events and not self._pending_shock:
                drift_active = abs(snapshot.spot_drift_pct) >= self.drift_threshold
                trigger_drift = snapshot.spot_drift_pct
            else:
                drift_active = False
                trigger_drift = 0.0

        if not drift_active:
            self._in_shock = False

        # Require an observed CLOB baseline to register a shock
        if drift_active and not self._in_shock and not self._pending_shock and snapshot.clob_mid is not None:
            self._in_shock = True
            self._pending_shock = {
                "shock_ts": now,
                "spot_price": snapshot.spot_price,
                "drift_pct": trigger_drift,
                "baseline_mid": snapshot.clob_mid,
                "baseline_bid": snapshot.up_bid,
                "baseline_ask": snapshot.up_ask,
                "baseline_down_bid": snapshot.down_bid,
                "baseline_down_ask": snapshot.down_ask,
                "reaction_time_sec": None,
                "reaction_time_ms": None,
                "clob_reacted": False,
            }

    def get_summary(self) -> Dict[str, Any]:
        """Calculate statistical summary of empirical latency measurements."""
        total = len(self.events)
        reacted = [e for e in self.events if e.get("clob_reacted")]
        n_reacted = len(reacted)
        rate = (n_reacted / total * 100.0) if total > 0 else 0.0

        latencies = sorted([e["reaction_time_ms"] for e in reacted if e.get("reaction_time_ms") is not None])
        min_lat = min(latencies) if latencies else 0.0
        median_lat = 0.0
        mean_lat = 0.0
        p95_lat = 0.0
        if latencies:
            mean_lat = sum(latencies) / len(latencies)
            mid_idx = len(latencies) // 2
            if len(latencies) % 2 == 1:
                median_lat = latencies[mid_idx]
            else:
                median_lat = (latencies[mid_idx - 1] + latencies[mid_idx]) / 2.0
            idx_95 = min(len(latencies) - 1, int(len(latencies) * 0.95))
            p95_lat = latencies[idx_95]

        drifts = sorted([abs(e["drift_pct"]) for e in self.events if e.get("drift_pct") is not None])
        min_drift = min(drifts) if drifts else 0.0
        median_drift = 0.0
        mean_drift = 0.0
        p95_drift = 0.0
        if drifts:
            mean_drift = sum(drifts) / len(drifts)
            mid_idx = len(drifts) // 2
            if len(drifts) % 2 == 1:
                median_drift = drifts[mid_idx]
            else:
                median_drift = (drifts[mid_idx - 1] + drifts[mid_idx]) / 2.0
            idx_95 = min(len(drifts) - 1, int(len(drifts) * 0.95))
            p95_drift = drifts[idx_95]

        return {
            "total_shocks": total,
            "reaction_count": n_reacted,
            "reaction_rate_pct": round(rate, 2),
            "min_latency_ms": round(min_lat, 1),
            "median_latency_ms": round(median_lat, 1),
            "mean_latency_ms": round(mean_lat, 1),
            "p95_latency_ms": round(p95_lat, 1),
            "min_drift_pct": round(min_drift, 6),
            "median_drift_pct": round(median_drift, 6),
            "mean_drift_pct": round(mean_drift, 6),
            "p95_drift_pct": round(p95_drift, 6),
        }

    def format_summary(self) -> str:
        """Format empirical audit summary as readable text block."""
        s = self.get_summary()
        min_lat_str = f"{s['min_latency_ms']:.1f} ms" if s['reaction_count'] > 0 else "N/A"
        med_lat_str = f"{s['median_latency_ms']:.1f} ms" if s['reaction_count'] > 0 else "N/A"
        mean_lat_str = f"{s['mean_latency_ms']:.1f} ms" if s['reaction_count'] > 0 else "N/A"
        p95_lat_str = f"{s['p95_latency_ms']:.1f} ms" if s['reaction_count'] > 0 else "N/A"
        lines = [
            "=" * 80,
            "EMPIRICAL LATENCY AUDIT SUMMARY (RTDS Spot -> CLOB Book Response)",
            "=" * 80,
            f"Total Spot Price Shocks:   {s['total_shocks']}",
            f"CLOB Reactions Detected:   {s['reaction_count']}",
            f"Reaction Rate:             {s['reaction_rate_pct']:.1f}%",
            f"Min Lead Reaction Time:    {min_lat_str}",
            f"Median Reaction Time:      {med_lat_str}",
            f"Mean Reaction Time:        {mean_lat_str}",
            f"P95 Reaction Time:         {p95_lat_str}",
            f"Min Spot Drift:            {s['min_drift_pct'] * 100:.2f}%",
            f"Median Spot Drift:         {s['median_drift_pct'] * 100:.2f}%",
            f"Mean Spot Drift:           {s['mean_drift_pct'] * 100:.2f}%",
            f"P95 Spot Drift:            {s['p95_drift_pct'] * 100:.2f}%",
            "=" * 80,
        ]
        return "\n".join(lines)


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


_LIVE_MARKET_CACHE: dict[str, Any] = {}


def fetch_clob_books(
    series_slug: str, session: Optional[requests.Session] = None
) -> tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    """Fetch UP and DOWN top-of-book prices from Polymarket CLOB for active live market."""
    sess = session or requests.Session()
    try:
        now = time.time()
        live_mkt = _LIVE_MARKET_CACHE.get(series_slug)
        if not live_mkt or now >= (live_mkt.end_ts - 2.0):
            live_mkt = fetch_live_market("https://gamma-api.polymarket.com", series_slug)
            if live_mkt:
                _LIVE_MARKET_CACHE[series_slug] = live_mkt

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
        help="Drift threshold for latency tracking (default: 0.001 = 0.10%%)",
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
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress per-tick console printing",
    )
    return parser.parse_args(args)


def run_monitor(
    args: argparse.Namespace,
    stop_event: Optional[Any] = None,
    sleep_interval: float = 1.0,
    quiet: bool = False,
) -> int | LatencyAuditor:
    """Execute streaming observation loop and print synchronized ticks."""
    sync = StreamSynchronizer(series_slug=args.series)
    sess = requests.Session()
    sess.headers.update({"User-Agent": "Mozilla/5.0"})

    is_quiet = getattr(args, "quiet", False) or quiet

    if not is_quiet and not args.json:
        print("=" * 110)
        print(f"CROSS-VENUE STREAM MONITOR: RTDS Spot vs. CLOB Books | Series: {args.series}")
        print("=" * 110)

    start_time = time.time()
    ticks_emitted = 0
    auditor: Optional[LatencyAuditor] = None
    if args.audit:
        auditor = LatencyAuditor(drift_threshold=args.threshold)

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
            sync.update_spot(spot_val, now_ms, source="REST")

        # Ingest CLOB books
        up_b, up_a, dn_b, dn_a = fetch_clob_books(args.series, session=sess)
        b_now = time.time()
        sync.update_up_book(up_b, up_a, updated_ts=b_now, source="REST")
        sync.update_down_book(dn_b, dn_a, updated_ts=b_now, source="REST")

        snap = sync.create_snapshot(now_ts=b_now)
        if snap:
            ticks_emitted += 1
            if auditor:
                auditor.record_tick(snap)
            if not is_quiet:
                if args.json:
                    print(json.dumps(snap.to_dict()), flush=True)
                else:
                    print(snap.format_row(), flush=True)

        if (args.ticks > 0 and ticks_emitted >= args.ticks) or (args.duration > 0 and (time.time() - start_time) >= args.duration):
            break

        time.sleep(sleep_interval)

    if auditor:
        if not is_quiet:
            if args.json:
                print(json.dumps({"audit_summary": auditor.get_summary()}), flush=True)
            else:
                print(auditor.format_summary(), flush=True)
        return auditor

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

