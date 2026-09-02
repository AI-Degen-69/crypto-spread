"""Live Trading Cockpit Engine for 5-minute Polymarket Crypto Binary Markets.

Manages real-time quoting, paper/live execution, pair merges, stop-loss exits,
live wallet balance tracking, and timeline charting across the 5m universe:
BTC 5m, ETH 5m, BNB 5m, SOL 5m, XRP 5m.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import math
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any
import requests

from strategy.series import by_duration

GAMMA_HOST = "https://gamma-api.polymarket.com"
CLOB_HOST = "https://clob.polymarket.com"

_local = threading.local()


def _get_thread_session() -> requests.Session:
    """Get or initialize thread-local requests.Session with proper headers."""
    if not hasattr(_local, "session"):
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0"})
        _local.session = s
    return _local.session


def _iso_to_unix(s: str) -> float:
    """Convert ISO timestamp string to Unix epoch seconds."""
    if not s:
        return 0.0
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.datetime.fromisoformat(s).timestamp()


def fetch_live_series_market(series_slug: str, session: Optional[requests.Session] = None) -> Optional[Dict[str, Any]]:
    """Fetch active live market metadata for a series slug from Gamma API."""
    sess = session or _get_thread_session()
    try:
        r = sess.get(
            f"{GAMMA_HOST}/events",
            params={"series_slug": series_slug, "closed": "false", "limit": 500},
            timeout=(3.05, 5.0),
        )
        r.raise_for_status()
        events = r.json()
    except Exception as e:
        log.debug("Gamma API error for %s: %s", series_slug, e)
        return None

    now = time.time()
    candidates = []
    for ev in events:
        for m in ev.get("markets") or []:
            try:
                raw = m.get("clobTokenIds")
                tids = json.loads(raw) if isinstance(raw, str) else raw
                if not tids or len(tids) != 2:
                    continue
                st = _iso_to_unix(m.get("eventStartTime") or "")
                et = _iso_to_unix(m.get("endDate") or m.get("endDateIso") or "")
                if st <= now < et:
                    candidates.append((st, et, m, tids))
            except Exception as e:
                log.debug("Error parsing candidate market in %s: %s", series_slug, e)
                continue

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    st, et, m, tids = candidates[0]
    return {
        "conditionId": m.get("conditionId") or "",
        "slug": m.get("slug") or "",
        "start_ts": st,
        "end_ts": et,
        "up_token": str(tids[0]),
        "down_token": str(tids[1]),
        "series": series_slug,
    }


log = logging.getLogger("live_trader")

SERIES_5M = by_duration(300)

SERIES_COLORS = {
    "btc-up-or-down-5m": "#f7931a",  # Bitcoin Orange
    "eth-up-or-down-5m": "#627eea",  # Ethereum Blue/Cyan
    "bnb-up-or-down-5m": "#f3ba2f",  # BNB Gold
    "sol-up-or-down-5m": "#14f195",  # Solana Green/Teal
    "xrp-up-or-down-5m": "#00aae4",  # XRP Sky Blue
}


@dataclass
class MarketLiveState:
    """Real-time trading state for a single 5m series."""
    slug: str
    label: str
    color: str
    condition_id: str = ""
    market_slug: str = ""
    up_token: str = ""
    down_token: str = ""
    start_ts: float = 0.0
    end_ts: float = 0.0
    time_remaining_sec: float = 0.0
    
    # Book prices
    mid: Optional[float] = None
    up_bid: Optional[float] = None
    up_ask: Optional[float] = None
    down_bid: Optional[float] = None
    down_ask: Optional[float] = None
    spread: Optional[float] = None
    
    # Strategy orders
    resting_up: float = 0.48
    resting_down: float = 0.48
    order_shares: int = 5
    
    # Execution status
    status: str = "IDLE"  # IDLE, QUOTING, FILLED_UP, FILLED_DOWN, PAIR_MERGED, STOP_EXIT, SETTLED
    filled_up: bool = False
    filled_down: bool = False
    fill_price_up: Optional[float] = None
    fill_price_down: Optional[float] = None
    pair_captured: bool = False
    exit_taken: bool = False
    exit_side: Optional[str] = None
    
    # Adverse drift tracking
    max_up_drift: float = 0.0
    max_down_drift: float = 0.0
    reversal_seen_up: bool = False
    reversal_seen_down: bool = False
    
    # Performance metrics
    realized_pnl_usd: float = 0.0
    unrealized_pnl_usd: float = 0.0
    total_pnl_usd: float = 0.0
    trades_count: int = 0
    pairs_count: int = 0
    stops_count: int = 0
    last_action: str = "Ready"
    last_update_ts: float = field(default_factory=time.time)


@dataclass
class TradeEvent:
    """Historical trade log entry."""
    id: str
    timestamp: str
    slug: str
    label: str
    action: str  # PAIR_MERGE, STOP_EXIT_UP, STOP_EXIT_DOWN, WINDOW_SETTLE
    shares: int
    entry_price_up: Optional[float]
    entry_price_down: Optional[float]
    exit_price: Optional[float]
    pnl_usd: float
    pnl_pct: float
    notes: str


class LiveTraderEngine:
    """Singleton background engine for live quoting and paper/live trading."""

    def __init__(self):
        """Initialize the live trading engine with default 5m parameters and markets."""
        self.is_running: bool = False
        self.mode: str = "paper"  # "paper" or "live"
        self.wallet_address: str = ""
        self.starting_balance: float = 1000.0
        self.current_portfolio_value: float = 1000.0
        
        # Strategy Parameters
        self.offset: float = 0.02
        self.exit_thresh: float = 0.05
        self.exit_reversal: float = 0.015
        self.shares: int = 5
        self.taker_fee_rate: float = 0.0
        
        # State tracking
        self.markets: Dict[str, MarketLiveState] = {}
        for slug, _dur, label in SERIES_5M:
            self.markets[slug] = MarketLiveState(
                slug=slug,
                label=label,
                color=SERIES_COLORS.get(slug, "#33c9b5"),
                order_shares=self.shares,
                resting_up=round(0.50 - self.offset, 3),
                resting_down=round(0.50 - self.offset, 3),
            )
            
        self.trades: List[TradeEvent] = []
        self.timeline: List[Dict[str, Any]] = []
        self.total_realized_pnl: float = 0.0
        self.total_unrealized_pnl: float = 0.0
        self.total_pnl: float = 0.0
        self.total_pairs_merged: int = 0
        self.total_stops_triggered: int = 0
        self.session_start_ts: float = time.time()
        
        self._bg_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "Mozilla/5.0"})

    def get_state(self) -> Dict[str, Any]:
        """Return snapshot of entire trading engine state for the UI."""
        now = time.time()
        
        # Calculate totals
        realized = sum(m.realized_pnl_usd for m in self.markets.values())
        unrealized = sum(m.unrealized_pnl_usd for m in self.markets.values())
        total_pnl = realized + unrealized
        portfolio_val = self.starting_balance + total_pnl
        
        win_trades = sum(1 for t in self.trades if t.pnl_usd > 0)
        total_trades = len(self.trades)
        win_rate = (win_trades / total_trades * 100.0) if total_trades > 0 else 0.0
        
        # Convert markets to dict
        mkts_dict = {slug: asdict(state) for slug, state in self.markets.items()}
        
        # Format timeline for chart
        recent_timeline = self.timeline[-300:] if len(self.timeline) > 300 else self.timeline
        
        # Recent trades
        recent_trades = [asdict(t) for t in reversed(self.trades[-50:])]
        
        return {
            "is_running": self.is_running,
            "mode": self.mode,
            "wallet_address": self.wallet_address,
            "starting_balance": round(self.starting_balance, 2),
            "portfolio_value": round(portfolio_val, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round((total_pnl / max(1.0, self.starting_balance)) * 100.0, 2),
            "realized_pnl": round(realized, 2),
            "unrealized_pnl": round(unrealized, 2),
            "win_rate": round(win_rate, 1),
            "total_trades": total_trades,
            "pairs_merged": sum(m.pairs_count for m in self.markets.values()),
            "stops_triggered": sum(m.stops_count for m in self.markets.values()),
            "active_exposure": round(sum(
                (m.order_shares * (m.resting_up if m.filled_up else 0) +
                 m.order_shares * (m.resting_down if m.filled_down else 0))
                for m in self.markets.values() if not m.pair_captured
            ), 2),
            "params": {
                "offset": self.offset,
                "exit_thresh": self.exit_thresh,
                "exit_reversal": self.exit_reversal,
                "shares": self.shares,
            },
            "markets": mkts_dict,
            "timeline": recent_timeline,
            "trades": recent_trades,
            "server_time": datetime.datetime.now().strftime("%H:%M:%S"),
        }

    def update_config(self, offset: Optional[float] = None,
                      exit_thresh: Optional[float] = None,
                      shares: Optional[int] = None,
                      mode: Optional[str] = None,
                      wallet_address: Optional[str] = None,
                      starting_balance: Optional[float] = None) -> Dict[str, Any]:
        """Update strategy configuration parameters."""
        if offset is not None:
            # Constrain offset to keep resting prices strictly positive (0.001 to 0.490)
            self.offset = max(0.001, min(0.490, float(offset)))
        if exit_thresh is not None and exit_thresh > 0:
            self.exit_thresh = float(exit_thresh)
        if shares is not None and shares > 0:
            self.shares = int(shares)
        if mode in ("paper", "live"):
            self.mode = mode
        if starting_balance is not None and starting_balance > 0:
            self.starting_balance = float(starting_balance)
        if wallet_address is not None:
            self.wallet_address = wallet_address.strip()
            self._schedule_wallet_balance_fetch()

        # Update per-market resting prices
        for m in self.markets.values():
            m.resting_up = round(0.50 - self.offset, 3)
            m.resting_down = round(0.50 - self.offset, 3)
            m.order_shares = self.shares

        return self.get_state()

    def _schedule_wallet_balance_fetch(self):
        """Schedule non-blocking wallet balance fetch in executor if loop is running."""
        try:
            loop = asyncio.get_running_loop()
            loop.run_in_executor(None, self._try_fetch_wallet_balance)
        except RuntimeError:
            self._try_fetch_wallet_balance()

    def _try_fetch_wallet_balance(self):
        """Fetch live portfolio balance from Polymarket if wallet address is supplied."""
        if not self.wallet_address or not self.wallet_address.startswith("0x"):
            return
        try:
            url = f"https://data-api.polymarket.com/value?user={self.wallet_address}"
            sess = _get_thread_session()
            res = sess.get(url, timeout=(3.0, 4.0))
            if res.ok:
                data = res.json()
                val = float(data.get("value") or data.get("portfolioValue") or 0.0)
                if val > 0:
                    self.starting_balance = val
                    log.info("Fetched live Polymarket portfolio balance: $%.2f", val)
        except Exception as e:
            log.warning("Could not fetch wallet balance: %s", e)

    def start(self):
        """Start the background live trading ticker."""
        if self.is_running:
            return
        self.is_running = True
        self._schedule_wallet_balance_fetch()
        try:
            loop = asyncio.get_running_loop()
            if self._bg_task is None or self._bg_task.done():
                self._bg_task = loop.create_task(self._run_loop())
        except RuntimeError:
            # No running event loop in current thread (e.g. sync unit test)
            pass
        log.info("LiveTraderEngine started in %s mode", self.mode)

    def stop(self):
        """Stop trading engine and cancel active quoting."""
        self.is_running = False
        for m in self.markets.values():
            if m.status in ("QUOTING", "LIVE_MONITOR"):
                m.status = "IDLE"
                m.last_action = "Stopped"
        log.info("LiveTraderEngine stopped")

    def restart(self):
        """Restart engine and reload markets."""
        self.stop()
        self.start()

    def reset_pnl(self):
        """Reset session PnL and trade history."""
        self.trades.clear()
        self.timeline.clear()
        self.session_start_ts = time.time()
        for m in self.markets.values():
            m.realized_pnl_usd = 0.0
            m.unrealized_pnl_usd = 0.0
            m.total_pnl_usd = 0.0
            m.trades_count = 0
            m.pairs_count = 0
            m.stops_count = 0
            m.filled_up = False
            m.filled_down = False
            m.fill_price_up = None
            m.fill_price_down = None
            m.pair_captured = False
            m.exit_taken = False
            m.exit_side = ""
            m.max_up_drift = 0.0
            m.max_down_drift = 0.0
            m.reversal_seen_up = False
            m.reversal_seen_down = False
            m.status = "QUOTING" if self.is_running else "IDLE"
            m.last_action = "PnL Reset"
        # Seed initial timeline point
        self._record_timeline_point(time.time())

    async def _run_loop(self):
        """Main async ticker loop (1s resolution)."""
        log.info("LiveTraderEngine background loop running")
        while self.is_running:
            try:
                await self._tick_all_markets()
            except Exception as e:
                log.error("Error in LiveTraderEngine tick: %s", e, exc_info=True)
            await asyncio.sleep(1.0)

    async def _tick_all_markets(self):
        """Process one tick cycle across the 5 markets."""
        now = time.time()
        loop = asyncio.get_running_loop()

        # Run I/O fetching concurrently in threadpool
        tasks = [
            loop.run_in_executor(None, self._poll_single_market, slug)
            for slug in self.markets.keys()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        async with self._lock:
            for slug, res in zip(self.markets.keys(), results):
                if isinstance(res, Exception):
                    log.warning("Poll exception for %s: %s", slug, res)
                    continue
                if res:
                    self._update_market_strategy(slug, res, now)

            # Record timeline snapshot for charts
            self._record_timeline_point(now)

    def _poll_single_market(self, slug: str) -> Optional[Dict[str, Any]]:
        """Fetch market definition and orderbooks synchronously."""
        try:
            from strategy.markets import full_book
            sess = _get_thread_session()
            market_info = fetch_live_series_market(slug, session=sess)
            if not market_info:
                return None

            ubook = full_book(CLOB_HOST, market_info["up_token"])
            dbook = full_book(CLOB_HOST, market_info["down_token"])

            return {
                "market": market_info,
                "up_book": ubook,
                "down_book": dbook,
            }
        except Exception as e:
            log.debug("Failed polling market %s: %s", slug, e)
            return None

    def _update_market_strategy(self, slug: str, poll_data: Dict[str, Any], now: float):
        """Update trading state machine, execute fills, stop-loss exits, and pair merges."""
        mstate = self.markets[slug]
        minfo = poll_data["market"]
        ubook = poll_data["up_book"]
        dbook = poll_data["down_book"]

        if isinstance(minfo, dict):
            cid = str(minfo.get("conditionId") or minfo.get("condition_id") or "")
            mslug = str(minfo.get("slug") or minfo.get("market_slug") or "")
            up_tok = str(minfo.get("up_token") or "")
            dn_tok = str(minfo.get("down_token") or "")
            st = float(minfo.get("start_ts", 0.0))
            et = float(minfo.get("end_ts", 0.0))
        else:
            cid = str(getattr(minfo, "condition_id", ""))
            mslug = str(getattr(minfo, "market_slug", ""))
            up_tok = str(getattr(minfo, "up_token", ""))
            dn_tok = str(getattr(minfo, "down_token", ""))
            st = float(getattr(minfo, "start_ts", 0.0))
            et = float(getattr(minfo, "end_ts", 0.0))

        # Check for window rollover (condition_id changed or market ended)
        if mstate.condition_id and mstate.condition_id != cid:
            self._handle_window_rollover(mstate, now)

        # Update market metadata
        mstate.condition_id = cid
        mstate.market_slug = mslug
        mstate.up_token = up_tok
        mstate.down_token = dn_tok
        mstate.start_ts = st
        mstate.end_ts = et
        mstate.time_remaining_sec = max(0.0, et - now)
        mstate.last_update_ts = now

        # Extract book bests
        mstate.up_bid = ubook.get("best_bid")
        mstate.up_ask = ubook.get("best_ask")
        mstate.down_bid = dbook.get("best_bid")
        mstate.down_ask = dbook.get("best_ask")

        # Compute synthetic mid
        if mstate.up_bid is not None and mstate.up_ask is not None:
            up_mid = (mstate.up_bid + mstate.up_ask) / 2.0
        else:
            up_mid = mstate.up_bid or mstate.up_ask or 0.50

        if mstate.down_bid is not None and mstate.down_ask is not None:
            down_mid = (mstate.down_bid + mstate.down_ask) / 2.0
        else:
            down_mid = mstate.down_bid or mstate.down_ask or 0.50

        mstate.mid = round((up_mid + (1.0 - down_mid)) / 2.0, 4)
        if mstate.up_ask is not None and mstate.down_ask is not None:
            mstate.spread = round(mstate.up_ask + mstate.down_ask, 4)

        # If not active or window is expired, stay idle
        if not self.is_running or mstate.time_remaining_sec <= 0:
            if mstate.status in ("QUOTING", "LIVE_MONITOR"):
                mstate.status = "IDLE"
            return

        # Target resting prices
        resting_up = round(0.50 - self.offset, 3)
        resting_down = round(0.50 - self.offset, 3)
        mstate.resting_up = resting_up
        mstate.resting_down = resting_down
        mstate.order_shares = self.shares

        # In live mode without signed key execution, monitor live order book only
        if self.mode == "live":
            mstate.status = "LIVE_MONITOR"
            mstate.last_action = "Live mode: monitoring live order book (signing unconfigured)"
            return

        # --- DRIFT TRACKING (vs 0.50 base) ---
        mid = mstate.mid
        if mid > 0.50:
            mstate.max_up_drift = max(mstate.max_up_drift, mid - 0.50)
        elif mid < 0.50:
            mstate.max_down_drift = max(mstate.max_down_drift, 0.50 - mid)

        # Reversal detection: mid retraced back towards 0.50
        if mstate.max_down_drift >= self.exit_thresh and (0.50 - mid) < self.exit_reversal:
            mstate.reversal_seen_down = True
        if mstate.max_up_drift >= self.exit_thresh and (mid - 0.50) < self.exit_reversal:
            mstate.reversal_seen_up = True

        # --- FILL DETECTION (Paper Simulation) ---
        if mstate.status == "IDLE":
            mstate.status = "QUOTING"
            mstate.last_action = f"Quoting bids @ {resting_up:.2f} / {resting_down:.2f}"

        if not mstate.pair_captured and not mstate.exit_taken:
            # UP Leg Fill Check
            if not mstate.filled_up:
                if mstate.up_ask is not None and mstate.up_ask <= resting_up:
                    mstate.filled_up = True
                    mstate.fill_price_up = resting_up
                    mstate.status = "FILLED_UP"
                    mstate.last_action = f"Filled UP {self.shares} shares @ {resting_up:.2f}"
                    log.info("[%s] Filled UP @ %.2f", slug, resting_up)

            # DOWN Leg Fill Check
            if not mstate.filled_down:
                if mstate.down_ask is not None and mstate.down_ask <= resting_down:
                    mstate.filled_down = True
                    mstate.fill_price_down = resting_down
                    mstate.status = "FILLED_DOWN" if not mstate.filled_up else "PAIR_MERGED"
                    mstate.last_action = f"Filled DOWN {self.shares} shares @ {resting_down:.2f}"
                    log.info("[%s] Filled DOWN @ %.2f", slug, resting_down)

            # --- PAIR COMPLETION & MERGE ---
            if mstate.filled_up and mstate.filled_down:
                mstate.pair_captured = True
                mstate.status = "PAIR_MERGED"
                # Both sides bought at 0.48, merged to 1.00 -> Profit = (1.00 - 0.96) * shares = $0.04 * shares
                pair_profit_usd = (1.00 - (resting_up + resting_down)) * self.shares
                mstate.realized_pnl_usd += pair_profit_usd
                mstate.unrealized_pnl_usd = 0.0
                mstate.total_pnl_usd = mstate.realized_pnl_usd
                mstate.pairs_count += 1
                mstate.trades_count += 1
                mstate.last_action = f"Pair Merged! +${pair_profit_usd:.2f}"
                log.info("[%s] PAIR MERGED! Profit: +$%.2f", slug, pair_profit_usd)
                
                denom = max(0.01, 2 * resting_up * max(1, self.shares))
                # Log trade event
                self.trades.append(TradeEvent(
                    id=f"{slug}_{int(now)}",
                    timestamp=datetime.datetime.fromtimestamp(now).strftime("%H:%M:%S"),
                    slug=slug,
                    label=mstate.label,
                    action="PAIR_MERGE",
                    shares=self.shares,
                    entry_price_up=resting_up,
                    entry_price_down=resting_down,
                    exit_price=1.00,
                    pnl_usd=round(pair_profit_usd, 3),
                    pnl_pct=round(((pair_profit_usd) / denom) * 100.0, 1),
                    notes=f"Complete spread capture @ {resting_up:.2f} + {resting_down:.2f}",
                ))
                return

            # --- STOP LOSS EXIT TRIGGER ---
            # Holding UP alone and mid dropped adversely (max_down >= exit_thresh)
            if (mstate.filled_up and not mstate.filled_down and mstate.max_down_drift >= self.exit_thresh
                    and not mstate.reversal_seen_down):
                sell_bid = mstate.up_bid
                if sell_bid is not None:
                    mstate.exit_taken = True
                    mstate.exit_side = "UP"
                    mstate.status = "STOP_EXIT"
                    exit_pnl_usd = (sell_bid - resting_up) * self.shares
                    mstate.realized_pnl_usd += exit_pnl_usd
                    mstate.unrealized_pnl_usd = 0.0
                    mstate.total_pnl_usd = mstate.realized_pnl_usd
                    mstate.stops_count += 1
                    mstate.trades_count += 1
                    mstate.last_action = f"Stop Loss UP @ {sell_bid:.2f} ({exit_pnl_usd:+.2f}$)"
                    log.info("[%s] STOP LOSS EXIT (UP) @ %.2f, PnL: $%.2f", slug, sell_bid, exit_pnl_usd)

                    denom = max(0.01, resting_up * max(1, self.shares))
                    self.trades.append(TradeEvent(
                        id=f"{slug}_{int(now)}",
                        timestamp=datetime.datetime.fromtimestamp(now).strftime("%H:%M:%S"),
                        slug=slug,
                        label=mstate.label,
                        action="STOP_EXIT_UP",
                        shares=self.shares,
                        entry_price_up=resting_up,
                        entry_price_down=None,
                        exit_price=sell_bid,
                        pnl_usd=round(exit_pnl_usd, 3),
                        pnl_pct=round(((exit_pnl_usd) / denom) * 100.0, 1),
                        notes=f"Adverse drift {mstate.max_down_drift:.3f} >= {self.exit_thresh:.2f}",
                    ))
                    return

            # Holding DOWN alone and mid rallied adversely (max_up >= exit_thresh)
            if (mstate.filled_down and not mstate.filled_up and mstate.max_up_drift >= self.exit_thresh
                    and not mstate.reversal_seen_up):
                sell_bid = mstate.down_bid
                if sell_bid is not None:
                    mstate.exit_taken = True
                    mstate.exit_side = "DOWN"
                    mstate.status = "STOP_EXIT"
                    exit_pnl_usd = (sell_bid - resting_down) * self.shares
                    mstate.realized_pnl_usd += exit_pnl_usd
                    mstate.unrealized_pnl_usd = 0.0
                    mstate.total_pnl_usd = mstate.realized_pnl_usd
                    mstate.stops_count += 1
                    mstate.trades_count += 1
                    mstate.last_action = f"Stop Loss DOWN @ {sell_bid:.2f} ({exit_pnl_usd:+.2f}$)"
                    log.info("[%s] STOP LOSS EXIT (DOWN) @ %.2f, PnL: $%.2f", slug, sell_bid, exit_pnl_usd)

                    denom = max(0.01, resting_down * max(1, self.shares))
                    self.trades.append(TradeEvent(
                        id=f"{slug}_{int(now)}",
                        timestamp=datetime.datetime.fromtimestamp(now).strftime("%H:%M:%S"),
                        slug=slug,
                        label=mstate.label,
                        action="STOP_EXIT_DOWN",
                        shares=self.shares,
                        entry_price_up=None,
                        entry_price_down=resting_down,
                        exit_price=sell_bid,
                        pnl_usd=round(exit_pnl_usd, 3),
                        pnl_pct=round(((exit_pnl_usd) / denom) * 100.0, 1),
                        notes=f"Adverse drift {mstate.max_up_drift:.3f} >= {self.exit_thresh:.2f}",
                    ))
                    return

        # --- UNREALIZED PnL CALCULATION ---
        if mstate.pair_captured or mstate.exit_taken:
            mstate.unrealized_pnl_usd = 0.0
        else:
            unrealized = 0.0
            if mstate.filled_up and mstate.up_bid is not None:
                unrealized += (mstate.up_bid - resting_up) * self.shares
            if mstate.filled_down and mstate.down_bid is not None:
                unrealized += (mstate.down_bid - resting_down) * self.shares
            mstate.unrealized_pnl_usd = round(unrealized, 3)

        mstate.total_pnl_usd = round(mstate.realized_pnl_usd + mstate.unrealized_pnl_usd, 3)

    def _handle_window_rollover(self, mstate: MarketLiveState, now: float):
        """Cleanly settle unresolved positions when window expires and roll to next."""
        if (mstate.filled_up or mstate.filled_down) and not mstate.pair_captured and not mstate.exit_taken:
            # Settle unmerged leg at market bid
            resting_up = mstate.resting_up
            resting_down = mstate.resting_down
            settle_pnl = 0.0
            if mstate.filled_up:
                bid = mstate.up_bid or 0.50
                settle_pnl += (bid - resting_up) * self.shares
            if mstate.filled_down:
                bid = mstate.down_bid or 0.50
                settle_pnl += (bid - resting_down) * self.shares

            mstate.realized_pnl_usd += settle_pnl
            mstate.unrealized_pnl_usd = 0.0
            mstate.total_pnl_usd = mstate.realized_pnl_usd
            mstate.trades_count += 1
            log.info("[%s] Window Rollover Settled PnL: $%.2f", mstate.slug, settle_pnl)

            self.trades.append(TradeEvent(
                id=f"{mstate.slug}_{int(now)}",
                timestamp=datetime.datetime.fromtimestamp(now).strftime("%H:%M:%S"),
                slug=mstate.slug,
                label=mstate.label,
                action="WINDOW_SETTLE",
                shares=self.shares,
                entry_price_up=resting_up if mstate.filled_up else None,
                entry_price_down=resting_down if mstate.filled_down else None,
                exit_price=mstate.mid,
                pnl_usd=round(settle_pnl, 3),
                pnl_pct=round((settle_pnl / (resting_up * self.shares)) * 100.0, 1),
                notes="Window expired, position auto-settled",
            ))

        # Reset window execution state for the new 5m period
        mstate.filled_up = False
        mstate.filled_down = False
        mstate.fill_price_up = None
        mstate.fill_price_down = None
        mstate.pair_captured = False
        mstate.exit_taken = False
        mstate.exit_side = None
        mstate.max_up_drift = 0.0
        mstate.max_down_drift = 0.0
        mstate.reversal_seen_up = False
        mstate.reversal_seen_down = False
        mstate.status = "QUOTING" if self.is_running else "IDLE"
        mstate.last_action = "New Window Quoting"

    def _record_timeline_point(self, now: float):
        """Append real-time equity & per-market PnL data point for chart logging."""
        realized = sum(m.realized_pnl_usd for m in self.markets.values())
        unrealized = sum(m.unrealized_pnl_usd for m in self.markets.values())
        tot_pnl = realized + unrealized
        portfolio_val = self.starting_balance + tot_pnl

        pnl_by_mkt_usd = {slug: round(m.total_pnl_usd, 3) for slug, m in self.markets.items()}
        pnl_by_mkt_pct = {
            slug: round((m.total_pnl_usd / max(0.01, self.shares * m.resting_up * 2)) * 100.0, 2)
            for slug, m in self.markets.items()
        }

        point = {
            "timestamp": int(now),
            "time_str": datetime.datetime.fromtimestamp(now).strftime("%H:%M:%S"),
            "portfolio_value": round(portfolio_val, 2),
            "total_pnl": round(tot_pnl, 2),
            "total_pnl_pct": round((tot_pnl / max(1.0, self.starting_balance)) * 100.0, 2),
            "pnl_usd": pnl_by_mkt_usd,
            "pnl_pct": pnl_by_mkt_pct,
        }

        # Keep last 1,800 points (~30 mins of 1s ticks)
        self.timeline.append(point)
        if len(self.timeline) > 1800:
            self.timeline.pop(0)


# Global singleton engine
_ENGINE: Optional[LiveTraderEngine] = None


def get_live_trader_engine() -> LiveTraderEngine:
    """Access global LiveTraderEngine singleton."""
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = LiveTraderEngine()
    return _ENGINE
