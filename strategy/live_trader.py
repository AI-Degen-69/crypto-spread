"""Live Trading Cockpit Engine for 5-minute Polymarket Crypto Binary Markets.

Manages real-time quoting with advance pre-quoting on upcoming 5m windows,
paper/live execution, pair merges, stop-loss exits, live wallet balance tracking,
and timeline charting across the 5m universe:
BTC 5m, ETH 5m, BNB 5m, SOL 5m, XRP 5m.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import math
import os
from pathlib import Path
import re
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any
import requests

from strategy.series import by_duration
from strategy.streaming import UnifiedStreamBridge, SYMBOL_TO_SERIES, SERIES_TO_SYMBOL

GAMMA_HOST = "https://gamma-api.polymarket.com"
CLOB_HOST = "https://clob.polymarket.com"

_local = threading.local()
log = logging.getLogger("live_trader")

_ENV_LOADED = False
_EVM_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def _load_env_file() -> None:
    """Load key-value pairs from .env if present into os.environ."""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("\"'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except Exception as e:
        log.debug("Failed loading .env: %s", e)


def fetch_polymarket_account_value(
    wallet_address: Optional[str] = None,
    session: Optional[requests.Session] = None,
) -> Dict[str, Any]:
    """Fetch real Polymarket net account value (USDC collateral cash + open positions).
    
    Priority:
    1. CLOB balance-allowance API via py_clob_client using .env credentials.
    2. Polymarket Data API positions market value.
    3. Polymarket Data API portfolio value fallback.
    """
    _load_env_file()
    sess = session or _get_thread_session()
    errors: List[str] = []

    raw_funder = (wallet_address or "").strip() or os.getenv("POLY_FUNDER") or os.getenv("RELAYER_API_KEY_ADDRESS") or ""
    if raw_funder and not _EVM_ADDR_RE.match(raw_funder):
        errors.append(f"Invalid EVM wallet address: {raw_funder}")
        return {
            "success": False,
            "wallet_address": raw_funder,
            "net_value": 0.0,
            "cash_balance": 0.0,
            "positions_value": 0.0,
            "open_positions": 0,
            "errors": errors,
        }
    funder = raw_funder.lower() if raw_funder else ""
    private_key = os.getenv("POLY_PRIVATE_KEY", "")
    api_key = os.getenv("POLY_API_KEY", "")
    api_secret = os.getenv("POLY_API_SECRET", "")
    api_pass = os.getenv("POLY_API_PASSPHRASE", "")
    sig_type_str = os.getenv("POLY_SIG_TYPE", "3")
    try:
        sig_type = int(sig_type_str)
    except Exception:
        sig_type = 3

    cash_balance: Optional[float] = None
    positions_value: float = 0.0
    open_positions_count: int = 0

    # 1. CLOB Collateral Cash Balance (via py_clob_client if credentials present)
    if funder and private_key and api_key and api_secret and api_pass:
        try:
            try:
                from py_clob_client_v2.client import ClobClient
                from py_clob_client_v2.clob_types import ApiCreds, BalanceAllowanceParams, AssetType
            except ImportError:
                from py_clob_client.client import ClobClient
                from py_clob_client.clob_types import ApiCreds, BalanceAllowanceParams, AssetType

            client = ClobClient(
                host=CLOB_HOST,
                key=private_key,
                chain_id=137,
                signature_type=sig_type,
                funder=funder,
            )
            client.set_api_creds(ApiCreds(api_key=api_key, api_secret=api_secret, api_passphrase=api_pass))
            bal_res = client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=sig_type)
            )
            if isinstance(bal_res, dict) and "balance" in bal_res:
                cash_balance = float(bal_res["balance"]) / 1e6
                log.debug("CLOB collateral cash balance for %s: $%.2f", funder, cash_balance)
        except Exception as e:
            errors.append(f"CLOB cash error: {e}")
            log.debug("CLOB balance fetch failed: %s", e)

    # 2. Polymarket Data API Open Positions Market Value
    if funder and funder.startswith("0x"):
        try:
            r = sess.get(f"https://data-api.polymarket.com/positions?user={funder}", timeout=(3.0, 5.0))
            if r.ok:
                data = r.json()
                if isinstance(data, list):
                    positions_value = sum(float(p.get("currentValue", 0.0) or 0.0) for p in data)
                    open_positions_count = len(data)
                    log.debug("Polymarket positions value for %s: $%.2f across %d positions", funder, positions_value, open_positions_count)
        except Exception as e:
            errors.append(f"Positions error: {e}")
            log.debug("Data API positions fetch failed: %s", e)

    # 3. Data API /value fallback if cash is still None
    fallback_val: Optional[float] = None
    if cash_balance is None and funder and funder.startswith("0x"):
        try:
            r_val = sess.get(f"https://data-api.polymarket.com/value?user={funder}", timeout=(3.0, 5.0))
            if r_val.ok:
                vdata = r_val.json()
                if isinstance(vdata, list) and len(vdata) > 0 and isinstance(vdata[0], dict):
                    fallback_val = float(vdata[0].get("value", 0.0) or 0.0)
                elif isinstance(vdata, dict):
                    fallback_val = float(vdata.get("value", 0.0) or 0.0)
        except Exception as e:
            errors.append(f"Data API value error: {e}")
            log.debug("Data API value fetch failed: %s", e)

    has_balance = cash_balance is not None or fallback_val is not None
    if cash_balance is not None:
        net_value = cash_balance + positions_value
    elif fallback_val is not None:
        # /value from Data API is total portfolio value; cash is remainder
        net_value = fallback_val
        cash_balance = max(0.0, net_value - positions_value)
    else:
        net_value = positions_value
        cash_balance = 0.0

    success = bool(funder) and (has_balance or positions_value > 0)

    return {
        "success": success,
        "wallet_address": funder,
        "net_value": round(net_value, 2),
        "cash_balance": round(cash_balance or 0.0, 2),
        "positions_value": round(positions_value, 2),
        "open_positions": open_positions_count,
        "errors": errors,
    }


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
    res = fetch_live_and_upcoming_markets(series_slug, session=session)
    return res.get("current")


def fetch_live_and_upcoming_markets(series_slug: str, session: Optional[requests.Session] = None) -> Dict[str, Optional[Dict[str, Any]]]:
    """Fetch both current active market and next upcoming market for advance pre-quoting."""
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
        return {"current": None, "next": None}

    now = time.time()
    active_candidates = []
    upcoming_candidates = []

    for ev in events:
        for m in ev.get("markets") or []:
            try:
                raw = m.get("clobTokenIds")
                tids = json.loads(raw) if isinstance(raw, str) else raw
                if not tids or len(tids) != 2:
                    continue
                st = _iso_to_unix(m.get("eventStartTime") or "")
                et = _iso_to_unix(m.get("endDate") or m.get("endDateIso") or "")
                
                mdict = {
                    "conditionId": m.get("conditionId") or "",
                    "slug": m.get("slug") or "",
                    "start_ts": st,
                    "end_ts": et,
                    "up_token": str(tids[0]),
                    "down_token": str(tids[1]),
                    "series": series_slug,
                }
                
                if st <= now < et:
                    active_candidates.append((st, et, mdict))
                elif st > now:
                    upcoming_candidates.append((st, et, mdict))
            except Exception as e:
                log.debug("Error parsing candidate market in %s: %s", series_slug, e)
                continue

    current_mkt = None
    if active_candidates:
        active_candidates.sort(key=lambda x: x[0], reverse=True)
        current_mkt = active_candidates[0][2]

    next_mkt = None
    if upcoming_candidates:
        # Sort upcoming by start_ts ascending (the very next one)
        upcoming_candidates.sort(key=lambda x: x[0])
        next_mkt = upcoming_candidates[0][2]

    return {
        "current": current_mkt,
        "next": next_mkt,
    }


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
    
    # Live Order Tracking (Current Window)
    order_id_up: Optional[str] = None
    order_id_down: Optional[str] = None
    order_status_up: str = "NONE"  # NONE, RESTING, FILLED, CANCELLED
    order_status_down: str = "NONE"

    # Advance Pre-Quoting (Upcoming Window T+1)
    next_condition_id: str = ""
    next_market_slug: str = ""
    next_up_token: str = ""
    next_down_token: str = ""
    next_start_ts: float = 0.0
    next_end_ts: float = 0.0
    next_order_id_up: Optional[str] = None
    next_order_id_down: Optional[str] = None
    next_quoted: bool = False
    
    # Live Exit Order Tracking
    order_id_exit_up: Optional[str] = None
    order_id_exit_down: Optional[str] = None
    order_status_exit_up: str = "NONE"
    order_status_exit_down: str = "NONE"
    exit_price_up: Optional[float] = None
    exit_price_down: Optional[float] = None
    
    # Execution status
    status: str = "IDLE"  # IDLE, QUOTING, PRE_QUOTING, LIVE_MONITOR, FILLED_UP, FILLED_DOWN, PAIR_MERGED, STOP_EXIT_PENDING, STOP_EXIT, SETTLED
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
    
    # Real-time RTDS spot price & streaming telemetry
    spot_price: Optional[float] = None
    spot_open_price: Optional[float] = None
    spot_updated_ts: Optional[float] = None
    spot_drift: float = 0.0
    streaming_active: bool = False

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
        _load_env_file()
        self.is_running: bool = False
        self.mode: str = "paper"  # "paper" or "live"
        self.wallet_address: str = os.getenv("POLY_FUNDER") or os.getenv("RELAYER_API_KEY_ADDRESS") or ""
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
        self._clob_client: Optional[Any] = None
        self._orders_cache: List[Dict[str, Any]] = []
        self._orders_cache_ts: float = 0.0
        self.quoting_halted: bool = False
        
        # Real-time WebSocket streaming bridge
        self.stream_bridge = UnifiedStreamBridge(
            on_spot_tick=self.on_spot_tick,
            on_book_update=self.on_book_update,
            on_order_event=self.on_user_order_event,
        )

    def get_clob_client(self) -> Optional[Any]:
        """Get or lazily initialize authenticated ClobClient."""
        if self._clob_client is not None:
            return self._clob_client

        _load_env_file()
        funder = self.wallet_address or os.getenv("POLY_FUNDER") or os.getenv("RELAYER_API_KEY_ADDRESS") or ""
        private_key = os.getenv("POLY_PRIVATE_KEY", "")
        api_key = os.getenv("POLY_API_KEY", "")
        api_secret = os.getenv("POLY_API_SECRET", "")
        api_pass = os.getenv("POLY_API_PASSPHRASE", "")
        sig_type_str = os.getenv("POLY_SIG_TYPE", "3")
        try:
            sig_type = int(sig_type_str)
        except Exception:
            sig_type = 3

        if not (funder and private_key and api_key and api_secret and api_pass):
            return None

        try:
            try:
                from py_clob_client_v2.client import ClobClient
                from py_clob_client_v2.clob_types import ApiCreds
            except ImportError:
                from py_clob_client.client import ClobClient
                from py_clob_client.clob_types import ApiCreds

            client = ClobClient(
                host=CLOB_HOST,
                key=private_key,
                chain_id=137,
                signature_type=sig_type,
                funder=funder,
            )
            client.set_api_creds(ApiCreds(api_key=api_key, api_secret=api_secret, api_passphrase=api_pass))
            self._clob_client = client
            log.info("Initialized CLOB client for %s", funder)
            return self._clob_client
        except Exception as e:
            log.error("Failed creating ClobClient: %s", e)
            return None

    def place_live_quote(self, token_id: str, price: float, size: float, side: str = "BUY") -> Optional[Dict[str, Any]]:
        """Place live limit order on Polymarket CLOB."""
        client = self.get_clob_client()
        if not client:
            log.warning("place_live_quote skipped: CLOB client not configured")
            return None

        try:
            try:
                from py_clob_client_v2.clob_types import OrderArgs
            except ImportError:
                from py_clob_client.clob_types import OrderArgs

            norm_price = round(float(price), 2)
            order_args = OrderArgs(
                token_id=token_id,
                price=norm_price,
                size=float(size),
                side=side.upper(),
            )
            res = client.create_and_post_order(order_args)
            log.info("Live quote placed: %s side=%s price=%.2f size=%.1f -> %s", token_id, side, norm_price, size, res)
            
            order_id = ""
            status = "RESTING"
            if isinstance(res, dict):
                order_id = res.get("orderID") or res.get("id") or ""
                st = (res.get("status") or "").lower()
                if st in ("delayed", "unmatched"):
                    status = "RESTING"
                elif st in ("matched", "filled"):
                    status = "FILLED"
            elif isinstance(res, str):
                order_id = res

            return {
                "order_id": order_id,
                "status": status,
                "token_id": token_id,
                "price": norm_price,
                "size": size,
                "side": side,
                "raw": res,
            }
        except Exception as e:
            log.error("Failed placing live quote for %s: %s", token_id, e)
            return {"error": str(e), "order_id": None}

    @staticmethod
    def _cancel_succeeded(res: Any) -> bool:
        """Derive boolean result from venue cancellation response."""
        if res is False:
            return False
        if isinstance(res, dict):
            if res.get("success") is False:
                return False
            if res.get("not_canceled"):
                return False
            if res.get("error"):
                return False
        return True

    def cancel_live_order(self, order_id: str) -> bool:
        """Cancel a single active order on Polymarket CLOB."""
        if not order_id:
            return False
        client = self.get_clob_client()
        if not client:
            return False
        try:
            if hasattr(client, "cancel"):
                try:
                    res = client.cancel(order_id)
                    log.info("Cancelled order %s -> %s", order_id, res)
                    return self._cancel_succeeded(res)
                except (AttributeError, TypeError):
                    pass
            if hasattr(client, "cancel_orders"):
                res = client.cancel_orders([order_id])
                log.info("Cancelled order %s -> %s", order_id, res)
                return self._cancel_succeeded(res)
            log.error("No single-order cancel API available; refusing cancel_all for %s", order_id)
            return False
        except Exception as e:
            log.error("Failed cancelling order %s: %s", order_id, e)
            return False

    def cancel_all_orders(self) -> Dict[str, Any]:
        """Emergency panic button: Cancel all open orders on CLOB and clear active handles."""
        self.quoting_halted = True
        cancelled_remote = False
        client = self.get_clob_client()
        if client:
            try:
                client.cancel_all()
                cancelled_remote = True
                log.info("Emergency cancel_all invoked on Polymarket CLOB")
            except Exception as e:
                log.error("Error in remote cancel_all: %s", e)

        # Clear local order handles across all markets
        cleared_count = 0
        for m in self.markets.values():
            if m.order_id_up or m.order_id_down or m.next_order_id_up or m.next_order_id_down or m.order_id_exit_up or m.order_id_exit_down:
                cleared_count += 1
            m.order_id_up = None
            m.order_id_down = None
            m.order_status_up = "CANCELLED"
            m.order_status_down = "CANCELLED"
            m.order_id_exit_up = None
            m.order_id_exit_down = None
            m.order_status_exit_up = "CANCELLED"
            m.order_status_exit_down = "CANCELLED"
            m.next_order_id_up = None
            m.next_order_id_down = None
            m.next_quoted = False
            if m.status in ("QUOTING", "PRE_QUOTING", "LIVE_MONITOR", "STOP_EXIT_PENDING"):
                m.status = "IDLE"
            m.last_action = "All orders cancelled"

        return {
            "ok": True,
            "remote_cancel_called": cancelled_remote,
            "markets_cleared": cleared_count,
            "timestamp": time.time(),
        }

    def on_spot_tick(self, symbol: str, ts_ms: int, price: float) -> None:
        """Handle real-time spot tick from RTDS or fallback."""
        slug = SYMBOL_TO_SERIES.get(symbol.lower())
        if not slug or slug not in self.markets:
            return
        m = self.markets[slug]
        m.spot_price = price
        m.spot_updated_ts = ts_ms / 1000.0
        m.streaming_active = True

        if m.spot_open_price is None or m.spot_open_price <= 0:
            m.spot_open_price = price

        if m.spot_open_price and m.spot_open_price > 0:
            m.spot_drift = (price - m.spot_open_price) / m.spot_open_price

        if not self.is_running or self.quoting_halted:
            return

        now = time.time()
        # Fast stop loss execution on adverse leading spot drift
        if m.filled_up and not m.filled_down and not m.exit_taken:
            if m.spot_drift <= -self.exit_thresh:
                m.max_down_drift = max(m.max_down_drift, abs(m.spot_drift))
                log.info("[%s] RTDS leading tick triggered fast stop exit for UP leg: spot=%.2f drift=%.3f",
                         slug, price, m.spot_drift)
                self._trigger_fast_stop_exit(slug, m, "UP", now)
        elif m.filled_down and not m.filled_up and not m.exit_taken:
            if m.spot_drift >= self.exit_thresh:
                m.max_up_drift = max(m.max_up_drift, m.spot_drift)
                log.info("[%s] RTDS leading tick triggered fast stop exit for DOWN leg: spot=%.2f drift=%.3f",
                         slug, price, m.spot_drift)
                self._trigger_fast_stop_exit(slug, m, "DOWN", now)

    def _trigger_fast_stop_exit(self, slug: str, mstate: MarketLiveState, side: str, now: float) -> None:
        """Trigger fast stop-loss exit market order and cancel unhedged side."""
        if side == "UP":
            sell_bid = mstate.up_bid if mstate.up_bid is not None else 0.40
            if self.mode == "live":
                if mstate.order_id_down:
                    self.cancel_live_order(mstate.order_id_down)
                    mstate.order_status_down = "CANCELLED"
                if not mstate.order_id_exit_up and mstate.up_token:
                    res = self.place_live_quote(mstate.up_token, sell_bid, self.shares, "SELL")
                    if res and res.get("order_id"):
                        mstate.order_id_exit_up = res["order_id"]
                        mstate.order_status_exit_up = res.get("status") or "RESTING"
                        mstate.exit_price_up = sell_bid

                is_filled = (mstate.order_status_exit_up == "FILLED")
                if not is_filled and mstate.order_id_exit_up:
                    client = self.get_clob_client()
                    if client:
                        try:
                            ord_info = client.get_order(mstate.order_id_exit_up)
                            st = (ord_info.get("status") or "").upper()
                            sz = float(ord_info.get("size_matched", 0.0) or 0.0)
                            if st in ("MATCHED", "FILLED") or sz >= self.shares:
                                is_filled = True
                                mstate.order_status_exit_up = "FILLED"
                        except Exception as e:
                            log.debug("[%s] Error checking UP exit order %s: %s", slug, mstate.order_id_exit_up, e)

                if not is_filled:
                    mstate.status = "STOP_EXIT_PENDING"
                    mstate.last_action = f"Stop Loss UP resting @ {sell_bid:.2f}"
                    return

            mstate.exit_taken = True
            mstate.exit_side = "UP"
            mstate.status = "STOP_EXIT"
            exit_pnl_usd = (sell_bid - (mstate.fill_price_up or mstate.resting_up)) * self.shares
            mstate.realized_pnl_usd += exit_pnl_usd
            mstate.unrealized_pnl_usd = 0.0
            mstate.total_pnl_usd = mstate.realized_pnl_usd
            mstate.stops_count += 1
            mstate.trades_count += 1
            mstate.last_action = f"Fast Stop UP @ {sell_bid:.2f} ({exit_pnl_usd:+.2f}$)"
            log.info("[%s] FAST STOP LOSS EXIT (UP) @ %.2f, PnL: $%.2f", slug, sell_bid, exit_pnl_usd)
            self.trades.append(TradeEvent(
                id=f"{slug}_{int(now)}",
                timestamp=datetime.datetime.fromtimestamp(now).strftime("%H:%M:%S"),
                slug=slug,
                label=mstate.label,
                action="STOP_EXIT_UP",
                shares=self.shares,
                entry_price_up=mstate.fill_price_up or mstate.resting_up,
                entry_price_down=None,
                exit_price=sell_bid,
                pnl_usd=round(exit_pnl_usd, 3),
                pnl_pct=round((exit_pnl_usd / max(0.01, mstate.resting_up * self.shares)) * 100.0, 1),
                notes=f"RTDS Fast stop: drift {mstate.spot_drift:.3f} <= -{self.exit_thresh:.2f}",
            ))
        else:
            sell_bid = mstate.down_bid if mstate.down_bid is not None else 0.40
            if self.mode == "live":
                if mstate.order_id_up:
                    self.cancel_live_order(mstate.order_id_up)
                    mstate.order_status_up = "CANCELLED"
                if not mstate.order_id_exit_down and mstate.down_token:
                    res = self.place_live_quote(mstate.down_token, sell_bid, self.shares, "SELL")
                    if res and res.get("order_id"):
                        mstate.order_id_exit_down = res["order_id"]
                        mstate.order_status_exit_down = res.get("status") or "RESTING"
                        mstate.exit_price_down = sell_bid

                is_filled = (mstate.order_status_exit_down == "FILLED")
                if not is_filled and mstate.order_id_exit_down:
                    client = self.get_clob_client()
                    if client:
                        try:
                            ord_info = client.get_order(mstate.order_id_exit_down)
                            st = (ord_info.get("status") or "").upper()
                            sz = float(ord_info.get("size_matched", 0.0) or 0.0)
                            if st in ("MATCHED", "FILLED") or sz >= self.shares:
                                is_filled = True
                                mstate.order_status_exit_down = "FILLED"
                        except Exception as e:
                            log.debug("[%s] Error checking DOWN exit order %s: %s", slug, mstate.order_id_exit_down, e)

                if not is_filled:
                    mstate.status = "STOP_EXIT_PENDING"
                    mstate.last_action = f"Stop Loss DOWN resting @ {sell_bid:.2f}"
                    return

            mstate.exit_taken = True
            mstate.exit_side = "DOWN"
            mstate.status = "STOP_EXIT"
            exit_pnl_usd = (sell_bid - (mstate.fill_price_down or mstate.resting_down)) * self.shares
            mstate.realized_pnl_usd += exit_pnl_usd
            mstate.unrealized_pnl_usd = 0.0
            mstate.total_pnl_usd = mstate.realized_pnl_usd
            mstate.stops_count += 1
            mstate.trades_count += 1
            mstate.last_action = f"Fast Stop DOWN @ {sell_bid:.2f} ({exit_pnl_usd:+.2f}$)"
            log.info("[%s] FAST STOP LOSS EXIT (DOWN) @ %.2f, PnL: $%.2f", slug, sell_bid, exit_pnl_usd)
            self.trades.append(TradeEvent(
                id=f"{slug}_{int(now)}",
                timestamp=datetime.datetime.fromtimestamp(now).strftime("%H:%M:%S"),
                slug=slug,
                label=mstate.label,
                action="STOP_EXIT_DOWN",
                shares=self.shares,
                entry_price_up=None,
                entry_price_down=mstate.fill_price_down or mstate.resting_down,
                exit_price=sell_bid,
                pnl_usd=round(exit_pnl_usd, 3),
                pnl_pct=round((exit_pnl_usd / max(0.01, mstate.resting_down * self.shares)) * 100.0, 1),
                notes=f"RTDS Fast stop: drift {mstate.spot_drift:.3f} >= {self.exit_thresh:.2f}",
            ))

    def on_book_update(self, token_id: str, bids: Dict[float, float], asks: Dict[float, float]) -> None:
        """Handle real-time book updates from CLOB Market WebSocket."""
        best_b = max(bids.keys()) if bids else None
        best_a = min(asks.keys()) if asks else None

        for m in self.markets.values():
            if m.up_token == token_id:
                m.up_bid = best_b
                m.up_ask = best_a
            elif m.down_token == token_id:
                m.down_bid = best_b
                m.down_ask = best_a
            else:
                continue

            # Recalculate mid and spread
            up_mid = (m.up_bid + m.up_ask) / 2.0 if (m.up_bid and m.up_ask) else (m.up_bid or m.up_ask or 0.50)
            down_mid = (m.down_bid + m.down_ask) / 2.0 if (m.down_bid and m.down_ask) else (m.down_bid or m.down_ask or 0.50)
            m.mid = round((up_mid + (1.0 - down_mid)) / 2.0, 4)
            if m.up_ask is not None and m.down_ask is not None:
                m.spread = round(m.up_ask + m.down_ask, 4)

    def on_user_order_event(self, payload: Dict[str, Any]) -> None:
        """Handle real-time authenticated order events from UserSpec stream."""
        order_id = str(payload.get("id") or payload.get("order_id") or "")
        status = str(payload.get("status") or "").upper()
        for m in self.markets.values():
            if m.order_id_up == order_id:
                m.order_status_up = status
                if status in ("MATCHED", "FILLED"):
                    m.filled_up = True
                    m.fill_price_up = float(payload.get("price") or m.resting_up)
                    m.status = "FILLED_UP" if not m.filled_down else "PAIR_MERGED"
            elif m.order_id_down == order_id:
                m.order_status_down = status
                if status in ("MATCHED", "FILLED"):
                    m.filled_down = True
                    m.fill_price_down = float(payload.get("price") or m.resting_down)
                    m.status = "FILLED_DOWN" if not m.filled_up else "PAIR_MERGED"
            elif m.order_id_exit_up == order_id:
                m.order_status_exit_up = status
            elif m.order_id_exit_down == order_id:
                m.order_status_exit_down = status

    def get_open_orders_list(self) -> List[Dict[str, Any]]:
        """List active open orders from CLOB and current engine state."""
        orders: List[Dict[str, Any]] = []
        client = self.get_clob_client()

        if client:
            try:
                try:
                    from py_clob_client_v2.clob_types import OpenOrderParams
                except ImportError:
                    from py_clob_client.clob_types import OpenOrderParams
                res = client.get_orders(OpenOrderParams())
                if isinstance(res, list):
                    for o in res:
                        orders.append({
                            "order_id": o.get("id") or o.get("order_id", ""),
                            "token_id": o.get("asset_id", ""),
                            "side": o.get("side", ""),
                            "price": float(o.get("price", 0.0)),
                            "size": float(o.get("original_size", 0.0)),
                            "status": o.get("status", "OPEN"),
                            "source": "CLOB_API",
                        })
            except Exception as e:
                log.debug("CLOB get_orders error: %s", e)

        # Merge in tracked market orders if not already listed
        existing_ids = {o["order_id"] for o in orders if o.get("order_id")}
        for m in self.markets.values():
            if m.order_id_up and m.order_id_up not in existing_ids:
                orders.append({
                    "order_id": m.order_id_up,
                    "market": m.label,
                    "token_id": m.up_token,
                    "side": "BUY (UP)",
                    "price": m.resting_up,
                    "size": m.order_shares,
                    "status": m.order_status_up,
                    "source": "ENGINE_ACTIVE",
                })
            if m.order_id_down and m.order_id_down not in existing_ids:
                orders.append({
                    "order_id": m.order_id_down,
                    "market": m.label,
                    "token_id": m.down_token,
                    "side": "BUY (DOWN)",
                    "price": m.resting_down,
                    "size": m.order_shares,
                    "status": m.order_status_down,
                    "source": "ENGINE_ACTIVE",
                })
            if m.next_order_id_up and m.next_order_id_up not in existing_ids:
                orders.append({
                    "order_id": m.next_order_id_up,
                    "market": f"{m.label} (Next Window)",
                    "token_id": m.next_up_token,
                    "side": "BUY (UP)",
                    "price": m.resting_up,
                    "size": m.order_shares,
                    "status": "ADVANCE_PRE_QUOTE",
                    "source": "ENGINE_ADVANCE",
                })
            if m.next_order_id_down and m.next_order_id_down not in existing_ids:
                orders.append({
                    "order_id": m.next_order_id_down,
                    "market": f"{m.label} (Next Window)",
                    "token_id": m.next_down_token,
                    "side": "BUY (DOWN)",
                    "price": m.resting_down,
                    "size": m.order_shares,
                    "status": "ADVANCE_PRE_QUOTE",
                    "source": "ENGINE_ADVANCE",
                })

        return orders

    def merge_positions(self, condition_id: str, amount: float = 0.0) -> Dict[str, Any]:
        """Merge outcome tokens back to USDC gaslessly via Relayer / CTF."""
        log.info("Executing live pair merge for condition %s", condition_id)
        if self.mode == "live":
            try:
                from polymarket import SecureClient, RelayerApiKey
                pkey = os.getenv("POLY_PRIVATE_KEY") or os.getenv("POLYMARKET_PRIVATE_KEY")
                wallet = self.wallet_address or os.getenv("POLY_FUNDER")
                r_key = os.getenv("RELAYER_API_KEY")
                r_addr = os.getenv("RELAYER_API_KEY_ADDRESS")
                if pkey and wallet and r_key and r_addr:
                    relayer_creds = RelayerApiKey(key=r_key, address=r_addr)
                    sec_client = SecureClient.create(private_key=pkey, wallet=wallet, api_key=relayer_creds)
                    tx = sec_client.merge_positions(condition_id=condition_id, amount="max")
                    outcome = tx.wait()
                    tx_hash = getattr(outcome, "transaction_hash", "")
                    log.info("Gasless merge successful! TxHash: %s", tx_hash)
                    return {
                        "ok": True,
                        "condition_id": condition_id,
                        "merged": True,
                        "transaction_hash": tx_hash,
                        "timestamp": time.time(),
                    }
            except Exception as e:
                log.error("Live merge failed for %s: %s", condition_id, e)
                return {"ok": False, "condition_id": condition_id, "error": str(e)}

            log.error("Live merge skipped for %s: relayer credentials incomplete", condition_id)
            return {
                "ok": False,
                "condition_id": condition_id,
                "merged": False,
                "error": "relayer credentials incomplete",
            }

        return {
            "ok": True,
            "condition_id": condition_id,
            "merged": True,
            "timestamp": time.time(),
        }

    def get_state(self) -> Dict[str, Any]:
        """Return snapshot of entire trading engine state for the UI."""
        now = time.time()
        env_funder = os.getenv("POLY_FUNDER") or os.getenv("RELAYER_API_KEY_ADDRESS") or ""
        
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
        
        # Open orders (cached with 5s TTL to avoid blocking requests)
        if now - self._orders_cache_ts > 5.0:
            self._orders_cache = self.get_open_orders_list()
            self._orders_cache_ts = now
        open_orders = self._orders_cache

        return {
            "is_running": self.is_running,
            "mode": self.mode,
            "wallet_address": self.wallet_address or env_funder,
            "env_wallet_address": env_funder,
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
            "open_orders": open_orders,
            "open_orders_count": len(open_orders),
            "stream_bridge": self.stream_bridge.get_status(),
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
            self.offset = max(0.001, min(0.490, float(offset)))
        if exit_thresh is not None and exit_thresh > 0:
            self.exit_thresh = float(exit_thresh)
        if shares is not None and shares > 0:
            self.shares = int(shares)
        if mode in ("paper", "live"):
            self.mode = mode
        if wallet_address is not None:
            self.wallet_address = wallet_address.strip()
            self._clob_client = None  # Reset client if wallet changed

        if self.mode == "live":
            addr = self.wallet_address or os.getenv("POLY_FUNDER") or ""
            val_info = fetch_polymarket_account_value(addr)
            if val_info.get("success"):
                self.starting_balance = float(val_info["net_value"])
                if not self.wallet_address and val_info.get("wallet_address"):
                    self.wallet_address = val_info["wallet_address"]
                log.info("Live mode: locked starting balance to Polymarket net account value: $%.2f", self.starting_balance)
            else:
                self._schedule_wallet_balance_fetch()
        else:
            if starting_balance is not None and starting_balance >= 0:
                self.starting_balance = float(starting_balance)

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
        """Fetch live portfolio balance from Polymarket and update starting_balance if in live mode."""
        try:
            addr = self.wallet_address or os.getenv("POLY_FUNDER") or ""
            info = fetch_polymarket_account_value(addr)
            if info.get("success"):
                net_val = float(info.get("net_value", 0.0))
                if self.mode == "live":
                    self.starting_balance = net_val
                    log.info("Live mode: fetched Polymarket portfolio balance: $%.2f", net_val)
                if not self.wallet_address and info.get("wallet_address"):
                    self.wallet_address = info["wallet_address"]
        except Exception as e:
            log.warning("Could not fetch wallet balance: %s", e)

    def start(self):
        """Start the background live trading ticker."""
        self.quoting_halted = False
        if self.is_running:
            return
        self.is_running = True
        self.stream_bridge.start()
        self._schedule_wallet_balance_fetch()
        try:
            loop = asyncio.get_running_loop()
            if self._bg_task is None or self._bg_task.done():
                self._bg_task = loop.create_task(self._run_loop())
        except RuntimeError:
            pass
        log.info("LiveTraderEngine started in %s mode", self.mode)

    def stop(self):
        """Stop trading engine and cancel active quoting."""
        self.is_running = False
        self.stream_bridge.stop()
        if self.mode == "live":
            self.cancel_all_orders()
        for m in self.markets.values():
            if m.status in ("QUOTING", "PRE_QUOTING", "LIVE_MONITOR", "STOP_EXIT_PENDING"):
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
        self._record_timeline_point(time.time())

    def seed_demo_data(self):
        """Populate realistic demo simulation trades, timeline curve, and market states."""
        if self.is_running:
            self.stop()
        now = time.time()
        self.trades.clear()
        self.timeline.clear()
        self.session_start_ts = now - 300.0

        demo_trades = [
            TradeEvent(
                id=f"btc-up-or-down-5m_{int(now - 280)}",
                timestamp=datetime.datetime.fromtimestamp(now - 280).strftime("%H:%M:%S"),
                slug="btc-up-or-down-5m",
                label="BTC 5m",
                action="PAIR_MERGE",
                shares=5,
                entry_price_up=0.48,
                entry_price_down=0.48,
                exit_price=1.00,
                pnl_usd=0.20,
                pnl_pct=4.2,
                notes="Complete spread capture @ 0.48 + 0.48",
            ),
            TradeEvent(
                id=f"eth-up-or-down-5m_{int(now - 240)}",
                timestamp=datetime.datetime.fromtimestamp(now - 240).strftime("%H:%M:%S"),
                slug="eth-up-or-down-5m",
                label="ETH 5m",
                action="PAIR_MERGE",
                shares=5,
                entry_price_up=0.48,
                entry_price_down=0.48,
                exit_price=1.00,
                pnl_usd=0.20,
                pnl_pct=4.2,
                notes="Complete spread capture @ 0.48 + 0.48",
            ),
            TradeEvent(
                id=f"sol-up-or-down-5m_{int(now - 200)}",
                timestamp=datetime.datetime.fromtimestamp(now - 200).strftime("%H:%M:%S"),
                slug="sol-up-or-down-5m",
                label="SOL 5m",
                action="STOP_EXIT_UP",
                shares=5,
                entry_price_up=0.48,
                entry_price_down=None,
                exit_price=0.43,
                pnl_usd=-0.25,
                pnl_pct=-10.4,
                notes="Adverse drift 0.055 >= 0.05",
            ),
            TradeEvent(
                id=f"bnb-up-or-down-5m_{int(now - 160)}",
                timestamp=datetime.datetime.fromtimestamp(now - 160).strftime("%H:%M:%S"),
                slug="bnb-up-or-down-5m",
                label="BNB 5m",
                action="PAIR_MERGE",
                shares=5,
                entry_price_up=0.48,
                entry_price_down=0.48,
                exit_price=1.00,
                pnl_usd=0.20,
                pnl_pct=4.2,
                notes="Complete spread capture @ 0.48 + 0.48",
            ),
            TradeEvent(
                id=f"btc-up-or-down-5m_{int(now - 120)}",
                timestamp=datetime.datetime.fromtimestamp(now - 120).strftime("%H:%M:%S"),
                slug="btc-up-or-down-5m",
                label="BTC 5m",
                action="PAIR_MERGE",
                shares=5,
                entry_price_up=0.48,
                entry_price_down=0.48,
                exit_price=1.00,
                pnl_usd=0.20,
                pnl_pct=4.2,
                notes="Complete spread capture @ 0.48 + 0.48",
            ),
            TradeEvent(
                id=f"xrp-up-or-down-5m_{int(now - 80)}",
                timestamp=datetime.datetime.fromtimestamp(now - 80).strftime("%H:%M:%S"),
                slug="xrp-up-or-down-5m",
                label="XRP 5m",
                action="PAIR_MERGE",
                shares=5,
                entry_price_up=0.48,
                entry_price_down=0.48,
                exit_price=1.00,
                pnl_usd=0.20,
                pnl_pct=4.2,
                notes="Complete spread capture @ 0.48 + 0.48",
            ),
            TradeEvent(
                id=f"eth-up-or-down-5m_{int(now - 40)}",
                timestamp=datetime.datetime.fromtimestamp(now - 40).strftime("%H:%M:%S"),
                slug="eth-up-or-down-5m",
                label="ETH 5m",
                action="PAIR_MERGE",
                shares=5,
                entry_price_up=0.48,
                entry_price_down=0.48,
                exit_price=1.00,
                pnl_usd=0.20,
                pnl_pct=4.2,
                notes="Complete spread capture @ 0.48 + 0.48",
            ),
        ]
        self.trades = demo_trades

        m_btc = self.markets.get("btc-up-or-down-5m")
        if m_btc:
            m_btc.realized_pnl_usd = 0.40
            m_btc.total_pnl_usd = 0.40
            m_btc.pairs_count = 2
            m_btc.trades_count = 2
            m_btc.status = "QUOTING"
            m_btc.last_action = "Quoting bids @ 0.48 / 0.48"

        m_eth = self.markets.get("eth-up-or-down-5m")
        if m_eth:
            m_eth.realized_pnl_usd = 0.40
            m_eth.total_pnl_usd = 0.40
            m_eth.pairs_count = 2
            m_eth.trades_count = 2
            m_eth.status = "FILLED_UP"
            m_eth.filled_up = True
            m_eth.resting_up = 0.48
            m_eth.last_action = "Filled UP 5 shares @ 0.48"

        m_sol = self.markets.get("sol-up-or-down-5m")
        if m_sol:
            m_sol.realized_pnl_usd = -0.25
            m_sol.total_pnl_usd = -0.25
            m_sol.stops_count = 1
            m_sol.trades_count = 1
            m_sol.status = "STOP_EXIT"
            m_sol.exit_taken = True
            m_sol.last_action = "Stop Loss UP @ 0.43 (-$0.25)"

        m_bnb = self.markets.get("bnb-up-or-down-5m")
        if m_bnb:
            m_bnb.realized_pnl_usd = 0.20
            m_bnb.total_pnl_usd = 0.20
            m_bnb.pairs_count = 1
            m_bnb.trades_count = 1
            m_bnb.status = "PAIR_MERGED"
            m_bnb.pair_captured = True
            m_bnb.last_action = "Pair Merged! +$0.20"

        m_xrp = self.markets.get("xrp-up-or-down-5m")
        if m_xrp:
            m_xrp.realized_pnl_usd = 0.20
            m_xrp.total_pnl_usd = 0.20
            m_xrp.pairs_count = 1
            m_xrp.trades_count = 1
            m_xrp.status = "QUOTING"
            m_xrp.last_action = "Quoting bids @ 0.48 / 0.48"

        t_start = int(now - 290)
        pnl_btc, pnl_eth, pnl_sol, pnl_bnb, pnl_xrp = 0.0, 0.0, 0.0, 0.0, 0.0
        for step in range(120):
            t_cur = t_start + (step * 2.5)
            if step >= 10:
                pnl_btc = 0.20
            if step >= 25:
                pnl_eth = 0.20
            if step >= 45:
                pnl_sol = -0.25
            if step >= 60:
                pnl_bnb = 0.20
            if step >= 75:
                pnl_btc = 0.40
            if step >= 90:
                pnl_xrp = 0.20
            if step >= 105:
                pnl_eth = 0.40

            tot = pnl_btc + pnl_eth + pnl_sol + pnl_bnb + pnl_xrp
            p_val = self.starting_balance + tot

            mkt_usd = {
                "btc-up-or-down-5m": round(pnl_btc, 2),
                "eth-up-or-down-5m": round(pnl_eth, 2),
                "bnb-up-or-down-5m": round(pnl_bnb, 2),
                "sol-up-or-down-5m": round(pnl_sol, 2),
                "xrp-up-or-down-5m": round(pnl_xrp, 2),
            }
            denom = max(0.01, self.shares * 0.48 * 2)
            mkt_pct = {k: round((v / denom) * 100.0, 1) for k, v in mkt_usd.items()}

            self.timeline.append({
                "timestamp": int(t_cur),
                "time_str": datetime.datetime.fromtimestamp(t_cur).strftime("%H:%M:%S"),
                "portfolio_value": round(p_val, 2),
                "total_pnl": round(tot, 2),
                "total_pnl_pct": round((tot / max(1.0, self.starting_balance)) * 100.0, 2),
                "pnl_usd": mkt_usd,
                "pnl_pct": mkt_pct,
            })

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

            active_tokens = []
            for m in self.markets.values():
                for t in (m.up_token, m.down_token, m.next_up_token, m.next_down_token):
                    if t:
                        active_tokens.append(t)
            if active_tokens:
                self.stream_bridge.update_market_tokens(active_tokens)

            self._record_timeline_point(now)

    def _poll_single_market(self, slug: str) -> Optional[Dict[str, Any]]:
        """Fetch current and next market definition and orderbooks synchronously."""
        try:
            from strategy.markets import full_book
            sess = _get_thread_session()
            markets_pair = fetch_live_and_upcoming_markets(slug, session=sess)
            market_info = markets_pair.get("current")
            next_market = markets_pair.get("next")

            if not market_info and not next_market:
                return None

            ubook = full_book(CLOB_HOST, market_info["up_token"]) if market_info else {}
            dbook = full_book(CLOB_HOST, market_info["down_token"]) if market_info else {}

            return {
                "market": market_info,
                "next_market": next_market,
                "up_book": ubook,
                "down_book": dbook,
            }
        except Exception as e:
            log.debug("Failed polling market %s: %s", slug, e)
            return None

    def _update_market_strategy(self, slug: str, poll_data: Dict[str, Any], now: float):
        """Update trading state machine, advance pre-quoting, fills, stop-loss exits, and pair merges."""
        mstate = self.markets[slug]
        minfo = poll_data.get("market")
        next_minfo = poll_data.get("next_market")
        ubook = poll_data.get("up_book") or {}
        dbook = poll_data.get("down_book") or {}

        # 1. Update upcoming market metadata for advance pre-quoting
        if next_minfo:
            next_cid = str(next_minfo.get("conditionId") or next_minfo.get("condition_id") or "")
            if next_cid != mstate.next_condition_id:
                mstate.next_condition_id = next_cid
                mstate.next_market_slug = str(next_minfo.get("slug") or next_minfo.get("market_slug") or "")
                mstate.next_up_token = str(next_minfo.get("up_token") or "")
                mstate.next_down_token = str(next_minfo.get("down_token") or "")
                mstate.next_start_ts = float(next_minfo.get("start_ts", 0.0))
                mstate.next_end_ts = float(next_minfo.get("end_ts", 0.0))
                mstate.next_order_id_up = None
                mstate.next_order_id_down = None
                mstate.next_quoted = False

        # 2. Advance Pre-Quoting on Next Window (T+1) if live mode is active
        if self.is_running and self.mode == "live" and not self.quoting_halted and mstate.next_condition_id and not mstate.next_quoted:
            resting_up = round(0.50 - self.offset, 3)
            resting_down = round(0.50 - self.offset, 3)
            if mstate.next_up_token and not mstate.next_order_id_up:
                res_up = self.place_live_quote(mstate.next_up_token, resting_up, self.shares, "BUY")
                if res_up and res_up.get("order_id"):
                    mstate.next_order_id_up = res_up["order_id"]
            if mstate.next_down_token and not mstate.next_order_id_down:
                res_dn = self.place_live_quote(mstate.next_down_token, resting_down, self.shares, "BUY")
                if res_dn and res_dn.get("order_id"):
                    mstate.next_order_id_down = res_dn["order_id"]
            if mstate.next_order_id_up and mstate.next_order_id_down:
                mstate.next_quoted = True
                log.info("[%s] ADVANCE PRE-QUOTING active on %s (UP: %s, DN: %s)", slug, mstate.next_market_slug, mstate.next_order_id_up, mstate.next_order_id_down)

        if not minfo:
            return

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
            self._handle_window_rollover(mstate, now, cid)

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
            if mstate.status in ("QUOTING", "PRE_QUOTING", "LIVE_MONITOR", "STOP_EXIT_PENDING"):
                mstate.status = "IDLE"
            return

        # Target resting prices
        resting_up = round(0.50 - self.offset, 3)
        resting_down = round(0.50 - self.offset, 3)
        mstate.resting_up = resting_up
        mstate.resting_down = resting_down
        mstate.order_shares = self.shares

        # --- DRIFT TRACKING (vs 0.50 base) ---
        mid = mstate.mid or 0.50
        if mid > 0.50:
            mstate.max_up_drift = max(mstate.max_up_drift, mid - 0.50)
        elif mid < 0.50:
            mstate.max_down_drift = max(mstate.max_down_drift, 0.50 - mid)

        # Reversal detection: mid retraced back towards 0.50
        if mstate.max_down_drift >= self.exit_thresh and (0.50 - mid) < self.exit_reversal:
            mstate.reversal_seen_down = True
        if mstate.max_up_drift >= self.exit_thresh and (mid - 0.50) < self.exit_reversal:
            mstate.reversal_seen_up = True

        # --- LIVE ORDER PLACEMENT (if in live mode and orders not placed yet) ---
        if self.mode == "live" and not self.quoting_halted and not mstate.pair_captured and not mstate.exit_taken:
            if not mstate.order_id_up and mstate.up_token:
                res_up = self.place_live_quote(mstate.up_token, resting_up, self.shares, "BUY")
                if res_up and res_up.get("order_id"):
                    mstate.order_id_up = res_up["order_id"]
                    mstate.order_status_up = "RESTING"
            if not mstate.order_id_down and mstate.down_token:
                res_dn = self.place_live_quote(mstate.down_token, resting_down, self.shares, "BUY")
                if res_dn and res_dn.get("order_id"):
                    mstate.order_id_down = res_dn["order_id"]
                    mstate.order_status_down = "RESTING"

        # --- FILL DETECTION ---
        if mstate.status in ("IDLE", "PRE_QUOTING"):
            mstate.status = "QUOTING"
            mstate.last_action = f"Quoting bids @ {resting_up:.2f} / {resting_down:.2f}"

        if not mstate.pair_captured and not mstate.exit_taken:
            # In LIVE mode, verify true fill status directly from Polymarket CLOB
            if self.mode == "live":
                client = self.get_clob_client()
                if client:
                    if not mstate.filled_up and mstate.order_id_up:
                        try:
                            ord_up = client.get_order(mstate.order_id_up)
                            st_up = (ord_up.get("status") or "").upper()
                            sz_up = float(ord_up.get("size_matched", 0.0) or 0.0)
                            if st_up in ("MATCHED", "FILLED") or sz_up >= mstate.order_shares:
                                mstate.filled_up = True
                                mstate.fill_price_up = resting_up
                                mstate.order_status_up = "FILLED"
                                mstate.status = "FILLED_UP"
                                mstate.last_action = f"Filled UP {self.shares} shares @ {resting_up:.2f}"
                                log.info("[%s] UP leg FILLED on CLOB (status=%s, matched=%.1f)", slug, st_up, sz_up)
                        except Exception as e:
                            log.debug("[%s] Error checking UP order: %s", slug, e)

                    if not mstate.filled_down and mstate.order_id_down:
                        try:
                            ord_dn = client.get_order(mstate.order_id_down)
                            st_dn = (ord_dn.get("status") or "").upper()
                            sz_dn = float(ord_dn.get("size_matched", 0.0) or 0.0)
                            if st_dn in ("MATCHED", "FILLED") or sz_dn >= mstate.order_shares:
                                mstate.filled_down = True
                                mstate.fill_price_down = resting_down
                                mstate.order_status_down = "FILLED"
                                mstate.status = "FILLED_DOWN" if not mstate.filled_up else "PAIR_MERGED"
                                mstate.last_action = f"Filled DOWN {self.shares} shares @ {resting_down:.2f}"
                                log.info("[%s] DOWN leg FILLED on CLOB (status=%s, matched=%.1f)", slug, st_dn, sz_dn)
                        except Exception as e:
                            log.debug("[%s] Error checking DOWN order: %s", slug, e)
            else:
                # Paper / Backtest simulation fallback using order book asks
                if not mstate.filled_up:
                    if mstate.up_ask is not None and mstate.up_ask <= resting_up:
                        mstate.filled_up = True
                        mstate.fill_price_up = resting_up
                        mstate.order_status_up = "FILLED"
                        mstate.status = "FILLED_UP"
                        mstate.last_action = f"Filled UP {self.shares} shares @ {resting_up:.2f}"
                        log.info("[%s] Filled UP @ %.2f", slug, resting_up)

                if not mstate.filled_down:
                    if mstate.down_ask is not None and mstate.down_ask <= resting_down:
                        mstate.filled_down = True
                        mstate.fill_price_down = resting_down
                        mstate.order_status_down = "FILLED"
                        mstate.status = "FILLED_DOWN" if not mstate.filled_up else "PAIR_MERGED"
                        mstate.last_action = f"Filled DOWN {self.shares} shares @ {resting_down:.2f}"
                        log.info("[%s] Filled DOWN @ %.2f", slug, resting_down)

            # --- PAIR COMPLETION & MERGE ---
            if mstate.filled_up and mstate.filled_down:
                mstate.pair_captured = True
                mstate.status = "PAIR_MERGED"
                pair_profit_usd = (1.00 - (resting_up + resting_down)) * self.shares
                mstate.realized_pnl_usd += pair_profit_usd
                mstate.unrealized_pnl_usd = 0.0
                mstate.total_pnl_usd = mstate.realized_pnl_usd
                mstate.pairs_count += 1
                mstate.trades_count += 1
                mstate.last_action = f"Pair Merged! +${pair_profit_usd:.2f}"
                log.info("[%s] PAIR MERGED! Profit: +$%.2f", slug, pair_profit_usd)
                
                if self.mode == "live":
                    self.merge_positions(mstate.condition_id)

                denom = max(0.01, 2 * resting_up * max(1, self.shares))
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
                    if self.mode == "live":
                        # 1. Cancel the unhedged DOWN resting order immediately
                        if mstate.order_id_down:
                            self.cancel_live_order(mstate.order_id_down)
                            mstate.order_status_down = "CANCELLED"
                        # 2. Market sell the filled UP leg if not yet submitted
                        if not mstate.order_id_exit_up and mstate.up_token:
                            res_up = self.place_live_quote(mstate.up_token, sell_bid, self.shares, "SELL")
                            if res_up and res_up.get("order_id"):
                                mstate.order_id_exit_up = res_up["order_id"]
                                mstate.order_status_exit_up = res_up.get("status") or "RESTING"
                                mstate.exit_price_up = sell_bid

                        # Account at the price actually submitted, not the current tick bid
                        if mstate.exit_price_up is not None:
                            sell_bid = mstate.exit_price_up

                        # Defer setting exit_taken and realized_pnl until filled
                        is_filled = (mstate.order_status_exit_up == "FILLED")
                        if not is_filled and mstate.order_id_exit_up:
                            client = self.get_clob_client()
                            if client:
                                try:
                                    ord_info = client.get_order(mstate.order_id_exit_up)
                                    st = (ord_info.get("status") or "").upper()
                                    sz = float(ord_info.get("size_matched", 0.0) or 0.0)
                                    if st in ("MATCHED", "FILLED") or sz >= self.shares:
                                        is_filled = True
                                        mstate.order_status_exit_up = "FILLED"
                                except Exception as e:
                                    log.debug("[%s] Error checking UP exit order %s: %s", slug, mstate.order_id_exit_up, e)

                        if not is_filled:
                            mstate.status = "STOP_EXIT_PENDING"
                            mstate.last_action = f"Stop Loss UP resting @ {sell_bid:.2f}"
                            return

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
                    if self.mode == "live":
                        # 1. Cancel the unhedged UP resting order immediately
                        if mstate.order_id_up:
                            self.cancel_live_order(mstate.order_id_up)
                            mstate.order_status_up = "CANCELLED"
                        # 2. Market sell the filled DOWN leg if not yet submitted
                        if not mstate.order_id_exit_down and mstate.down_token:
                            res_dn = self.place_live_quote(mstate.down_token, sell_bid, self.shares, "SELL")
                            if res_dn and res_dn.get("order_id"):
                                mstate.order_id_exit_down = res_dn["order_id"]
                                mstate.order_status_exit_down = res_dn.get("status") or "RESTING"
                                mstate.exit_price_down = sell_bid

                        # Account at the price actually submitted, not the current tick bid
                        if mstate.exit_price_down is not None:
                            sell_bid = mstate.exit_price_down

                        # Defer setting exit_taken and realized_pnl until filled
                        is_filled = (mstate.order_status_exit_down == "FILLED")
                        if not is_filled and mstate.order_id_exit_down:
                            client = self.get_clob_client()
                            if client:
                                try:
                                    ord_info = client.get_order(mstate.order_id_exit_down)
                                    st = (ord_info.get("status") or "").upper()
                                    sz = float(ord_info.get("size_matched", 0.0) or 0.0)
                                    if st in ("MATCHED", "FILLED") or sz >= self.shares:
                                        is_filled = True
                                        mstate.order_status_exit_down = "FILLED"
                                except Exception as e:
                                    log.debug("[%s] Error checking DOWN exit order %s: %s", slug, mstate.order_id_exit_down, e)

                        if not is_filled:
                            mstate.status = "STOP_EXIT_PENDING"
                            mstate.last_action = f"Stop Loss DOWN resting @ {sell_bid:.2f}"
                            return

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

    def _handle_window_rollover(self, mstate: MarketLiveState, now: float, new_cid: str = ""):
        """Cleanly settle unresolved positions when window expires and roll to next."""
        # Cancel any unfilled orders from expiring window
        if self.mode == "live":
            if mstate.order_id_up and not mstate.filled_up:
                self.cancel_live_order(mstate.order_id_up)
            if mstate.order_id_down and not mstate.filled_down:
                self.cancel_live_order(mstate.order_id_down)

        if (mstate.filled_up or mstate.filled_down) and not mstate.pair_captured and not mstate.exit_taken:
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

        # Promote advance pre-quoted orders from next window if available and matching new_cid
        if mstate.next_quoted and mstate.next_condition_id and (not new_cid or mstate.next_condition_id == new_cid):
            mstate.order_id_up = mstate.next_order_id_up
            mstate.order_id_down = mstate.next_order_id_down
            mstate.order_status_up = "RESTING"
            mstate.order_status_down = "RESTING"
            mstate.next_order_id_up = None
            mstate.next_order_id_down = None
            mstate.next_quoted = False
            log.info("[%s] PROMOTED advance pre-quotes to active live window (UP: %s, DN: %s)", mstate.slug, mstate.order_id_up, mstate.order_id_down)
        else:
            if self.mode == "live":
                if mstate.next_order_id_up:
                    self.cancel_live_order(mstate.next_order_id_up)
                if mstate.next_order_id_down:
                    self.cancel_live_order(mstate.next_order_id_down)
            mstate.next_order_id_up = None
            mstate.next_order_id_down = None
            mstate.next_quoted = False
            mstate.order_id_up = None
            mstate.order_id_down = None
            mstate.order_status_up = "NONE"
            mstate.order_status_down = "NONE"

        # Reset window execution state for the new 5m period
        mstate.filled_up = False
        mstate.filled_down = False
        mstate.fill_price_up = None
        mstate.fill_price_down = None
        mstate.pair_captured = False
        mstate.exit_taken = False
        mstate.exit_side = None
        mstate.spot_open_price = None
        mstate.spot_drift = 0.0
        if self.mode == "live":
            if mstate.order_id_exit_up:
                if self.cancel_live_order(mstate.order_id_exit_up):
                    mstate.order_id_exit_up = None
                    mstate.order_status_exit_up = "NONE"
            else:
                mstate.order_id_exit_up = None
                mstate.order_status_exit_up = "NONE"

            if mstate.order_id_exit_down:
                if self.cancel_live_order(mstate.order_id_exit_down):
                    mstate.order_id_exit_down = None
                    mstate.order_status_exit_down = "NONE"
            else:
                mstate.order_id_exit_down = None
                mstate.order_status_exit_down = "NONE"
        else:
            mstate.order_id_exit_up = None
            mstate.order_id_exit_down = None
            mstate.order_status_exit_up = "NONE"
            mstate.order_status_exit_down = "NONE"
        mstate.exit_price_up = None
        mstate.exit_price_down = None
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
