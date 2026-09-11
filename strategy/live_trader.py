"""Live Trading Cockpit Engine for 5-minute Polymarket Crypto Binary Markets.

Manages real-time quoting with advance pre-quoting on upcoming 5m windows,
paper/live execution, pair merges, stop-loss exits, live wallet balance tracking,
and timeline charting across the 5m universe:
BTC 5m, ETH 5m, BNB 5m, SOL 5m, XRP 5m.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
from collections import defaultdict
import datetime
import json
import logging
import math
import os
import sys
from pathlib import Path
import re
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any, Tuple, Iterable, Sequence
import requests

from strategy.series import by_duration, SERIES, filter_series, token_for_slug
from strategy.streaming import UnifiedStreamBridge, SYMBOL_TO_SERIES, SERIES_TO_SYMBOL, series_for_symbol

GAMMA_HOST = "https://gamma-api.polymarket.com"
CLOB_HOST = "https://clob.polymarket.com"

_local = threading.local()
log = logging.getLogger("live_trader")
_SERVER_CLOCK_OFFSET = 0.0
_LAST_OFFSET_SYNC = 0.0


def get_real_utc_time() -> float:
    """Return epoch timestamp calibrated against Polymarket server time if local clock drifts."""
    global _SERVER_CLOCK_OFFSET, _LAST_OFFSET_SYNC
    now = time.time()
    if "pytest" in sys.modules or os.getenv("PYTEST_CURRENT_TEST"):
        return now
    if now - _LAST_OFFSET_SYNC > 60.0:  # check offset every 60s
        try:
            r = requests.get(f"{GAMMA_HOST}/events", params={"limit": 1}, timeout=(2.0, 3.0))
            if r.ok and "Date" in r.headers:
                from email.utils import parsedate_to_datetime
                server_ts = parsedate_to_datetime(r.headers["Date"]).timestamp()
                diff = server_ts - now
                if 2.0 < abs(diff) < 86400.0:  # ignore frozen test timestamps > 24h away
                    _SERVER_CLOCK_OFFSET = diff
                    log.warning("Detected system clock drift of %+.1f seconds vs Polymarket server. Applying auto-offset calibration.", diff)
                else:
                    _SERVER_CLOCK_OFFSET = 0.0
                _LAST_OFFSET_SYNC = now
        except Exception:
            pass
    return now + _SERVER_CLOCK_OFFSET

_EVM_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
RUN_DIR = Path(__file__).resolve().parent.parent / "run"
TRADES_FILE = RUN_DIR / "live_trades.jsonl"
META_FILE = RUN_DIR / "live_trades_meta.json"
# One line per drift-skip re-entry (issue #95 observability): what was quoted,
# whether each leg reached the book and filled, and how the window ended. Written
# at window rollover, which is the first moment the outcome is known.
REENTRY_FILE = RUN_DIR / "reentry_events.jsonl"


def _empty_reentry_stats() -> Dict[str, int]:
    """A zeroed re-entry tally, one counter per outcome plus the reached-book count."""
    return {"reentries": 0, "reached_book": 0, "paired": 0, "single_leg": 0,
            "exited": 0, "both_no_merge": 0, "no_fill": 0,
            "chased_fills": 0, "passive_fills": 0}


def _empty_band_skip_stats() -> Dict[str, int]:
    """A zeroed entry-band skip tally (issue #137 observability)."""
    return {"band_skips": 0}


def _queue_ahead(bids: Optional[Dict[float, float]], price: float) -> Optional[float]:
    """Shares resting at or above `price` — our queue position at rest.

    Issue #138: mirrors run/sweeps/sim2.py:_queue_ahead. An empty or missing
    book yields None (unknown), never zero, so a degenerate book cannot
    masquerade as front-of-queue.
    """
    if not bids:
        return None
    try:
        return float(sum(s for p, s in bids.items() if float(p) >= price))
    except (TypeError, ValueError):
        return None


# Per-fill queue-position telemetry (issue #138): one JSONL line per entry
# fill answering the tape-vs-tapeq question with real fills.
FILL_TELEMETRY_FILE = RUN_DIR / "live_fill_telemetry.jsonl"
FILL_RATIO_FLAG_THRESHOLD = 10.0
FILL_PRICE_TICK_TOL = 0.001
TAPE_FETCH_TIMEOUT = (3.05, 5.0)


def _parse_print_ts(raw: Any) -> Optional[float]:
    """Normalize a data-api trade timestamp to epoch seconds, or None."""
    try:
        if isinstance(raw, bool):
            return None
        if isinstance(raw, (int, float)):
            v = float(raw)
            if v > 1e15:  # micros
                v /= 1e6
            elif v > 1e12:  # millis
                v /= 1000.0
            return v if v > 0 else None
        if isinstance(raw, str):
            s = raw.strip()
            if not s:
                return None
            try:
                return _parse_print_ts(float(s))
            except (TypeError, ValueError):
                pass
            iso = s[:-1] + "+00:00" if s[-1:] in ("Z", "z") else s
            try:
                dt = datetime.datetime.fromisoformat(iso)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=datetime.timezone.utc)
                return dt.timestamp()
            except (ValueError, OverflowError, OSError):
                return None
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    return None


def _sum_prints_at_price(rows: Any, token: str, price: float,
                         since_ts: float) -> float:
    """Sum printed size at ~= `price` for `token` with ts >= `since_ts`.

    Issue #138: the queue-burn numerator. Tolerates the venue tick
    (`FILL_PRICE_TICK_TOL`); malformed rows are skipped, never raised.
    """
    total = 0.0
    if not isinstance(rows, list):
        return total
    for t in rows:
        if not isinstance(t, dict):
            continue
        if str(t.get("asset")) != token:
            continue
        try:
            p = float(t.get("price"))
        except (TypeError, ValueError):
            continue
        if abs(p - price) > FILL_PRICE_TICK_TOL:
            continue
        ts = _parse_print_ts(t.get("timestamp"))
        if ts is None or ts < since_ts:
            continue
        try:
            total += float(t.get("size") or 0)
        except (TypeError, ValueError):
            continue
    return total


def _fetch_price_prints(condition_id: str, limit: int = 500) -> Optional[list[dict[str, Any]]]:
    """Timestamped tape rows for one market, or None on any failure.

    Issue #138: `markets.recent_trades` aggregates volume without timestamps,
    so the fill join fetches rows directly. Best-effort by contract.
    """
    if not condition_id:
        return None
    try:
        limit = max(1, min(int(limit), 2000))
    except (TypeError, ValueError):
        limit = 500
    try:
        r = requests.get("https://data-api.polymarket.com/trades",
                         params={"market": condition_id, "limit": limit},
                         timeout=TAPE_FETCH_TIMEOUT)
        r.raise_for_status()
        rows = r.json()
        return rows if isinstance(rows, list) else None
    except Exception as e:
        log.debug("fill-telemetry tape fetch failed: %s", e)
        return None


def _build_fill_record(*, ts: float, slug: str, market_slug: str,
                       condition_id: str, leg: str, chased: bool,
                       resting_price: Optional[float], fill_price: Optional[float],
                       queue_ahead: Optional[float], printed_size: Optional[float],
                       filled_size: float, window_elapsed_sec: float,
                       mid_at_fill: Optional[float],
                       resting_pair_cost: Optional[float]) -> Dict[str, Any]:
    """Pure record math for one entry fill (issue #138)."""
    ratio: Optional[float] = None
    if queue_ahead is not None and printed_size is not None:
        ratio = printed_size / max(queue_ahead, 1.0)
    return {
        "ts": ts,
        "slug": slug,
        "market_slug": market_slug,
        "condition_id": condition_id,
        "leg": leg,
        "chased": bool(chased),
        "resting_price": resting_price,
        "fill_price": fill_price,
        "queue_ahead_at_rest": queue_ahead,
        "printed_size_at_price_since_rest": printed_size,
        "filled_size": filled_size,
        "fill_ratio": ratio,
        "ratio_flagged": bool(ratio is not None and ratio > FILL_RATIO_FLAG_THRESHOLD),
        "window_elapsed_sec": window_elapsed_sec,
        "mid_at_fill": mid_at_fill,
        "resting_pair_cost": resting_pair_cost,
    }


def _append_fill_telemetry(record: Dict[str, Any], path: Any = None) -> bool:
    """Append one telemetry line; failure warns and returns False, never raises."""
    try:
        target = Path(path) if path is not None else FILL_TELEMETRY_FILE
        if target.parent and str(target.parent) not in ("", "."):
            target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        return True
    except Exception as e:
        log.warning("fill-telemetry write failed: %s", e)
        return False


def _reentry_outcome(m: "MarketLiveState") -> str:
    """Name the end state of a re-entered window, for the observability record."""
    if m.pair_captured:
        return "paired"
    if m.exit_taken:
        return "exited"
    if m.filled_up != m.filled_down:
        return "single_leg"
    if m.filled_up and m.filled_down:
        return "both_no_merge"
    return "no_fill"


_ENV_LOADED = False


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

    positions_list: List[Dict[str, Any]] = []

    # 2. Polymarket Data API Open Positions Market Value
    if funder and funder.startswith("0x"):
        try:
            r = sess.get(f"https://data-api.polymarket.com/positions?user={funder}", timeout=(3.0, 5.0))
            if r.ok:
                data = r.json()
                if isinstance(data, list):
                    positions_value = sum(float(p.get("currentValue", 0.0) or 0.0) for p in data)
                    open_positions_count = len(data)
                    for p in data:
                        if isinstance(p, dict):
                            positions_list.append({
                                "asset": str(p.get("asset") or ""),
                                "conditionId": str(p.get("conditionId") or ""),
                                "size": float(p.get("size", 0.0) or 0.0),
                                "avgPrice": float(p.get("avgPrice", 0.0) or 0.0),
                                "curPrice": float(p.get("curPrice", 0.0) or 0.0),
                                "initialValue": float(p.get("initialValue", 0.0) or 0.0),
                                "currentValue": float(p.get("currentValue", 0.0) or 0.0),
                                "cashPnl": float(p.get("cashPnl", 0.0) or 0.0),
                                "title": str(p.get("title") or ""),
                                "outcome": str(p.get("outcome") or ""),
                                "time": str(p.get("time") or p.get("createdAt") or p.get("timestamp") or "")[:19].replace("T", " "),
                            })
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
        "positions": positions_list,
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

    now = get_real_utc_time()
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
    "btc-up-or-down-15m": "#f7931a",
    "eth-up-or-down-15m": "#627eea",
    "bnb-up-or-down-15m": "#f3ba2f",
    "sol-up-or-down-15m": "#14f195",
    "xrp-up-or-down-15m": "#00aae4",
}


def _resolve_series_selection(
    selected_markets: Optional[Iterable[str]] = None,
    tokens: Optional[Iterable[str]] = None,
    durations: Optional[Iterable[int]] = None,
) -> tuple[tuple[str, int, str], ...]:
    """Resolve market selection into canonical SERIES subset.

    If selected_markets is provided, returns series matching those slugs.
    Validates that every requested slug exists in SERIES.
    If tokens or durations are provided, delegates to filter_series().
    Otherwise, defaults to all 5m series.
    """
    if selected_markets is not None:
        sel_list = list(selected_markets)
        if not sel_list:
            raise ValueError("selected_markets cannot be empty")
        valid_slugs = {s[0] for s in SERIES}
        for slug in sel_list:
            if slug not in valid_slugs:
                raise ValueError(f"Unknown series slug '{slug}'. Must be one of {sorted(valid_slugs)}")
        sel_set = set(sel_list)
        resolved = tuple(s for s in SERIES if s[0] in sel_set)
        if not resolved:
            raise ValueError("No valid series matched selected_markets")
        return resolved
    if tokens is not None or durations is not None:
        resolved = filter_series(tokens=tokens, durations=durations)
        if not resolved:
            raise ValueError("No valid series matched tokens and duration filter")
        return resolved
    return by_duration(300)



# Fraction of a window that may already have elapsed when the engine observes its
# first tick for that window and still count as "started at the open" (issue #96).
# 30s of a 5m window, 90s of a 15m window.
DEFAULT_MAX_START_ELAPSED_PCT = 0.10
# Minimum window time remaining that still justifies opening a fresh quoting
# round after a pair merge (issue #89). 300s keeps 5m windows single-round
# while letting 15m windows recycle; 0 disables re-quoting entirely.
DEFAULT_MIN_REQUOTE_REMAINING_SEC = 300.0

# Named live-trading presets (issue #137). `patient_band_maker` encodes the
# EV-research-winning configuration: delay entry 60s, only enter undecided
# markets (|mid - 0.50| <= 0.04), quote at mid - 0.03, hold naked legs to
# settlement (no stop-loss), chase the second leg capped at pair cost 0.98,
# on the recommended xrp-15m / bnb-15m / eth-5m universe.
PATIENT_BAND_MAKER = "patient_band_maker"
LIVE_PRESETS: Dict[str, Dict[str, Any]] = {
    PATIENT_BAND_MAKER: {
        "offset": 0.03,
        "entry_band": 0.04,
        "entry_delay_sec": 60.0,
        "stop_loss_enabled": False,
        "max_pair_cost": 0.98,
        "selected_markets": ("xrp-up-or-down-15m", "bnb-up-or-down-15m", "eth-up-or-down-5m"),
    },
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
    order_time_up: str = "-"
    order_time_down: str = "-"
    order_status_up: str = "NONE"  # NONE, RESTING, FILLED, CANCELLED
    order_status_down: str = "NONE"
    entry_cancelled_timeout: bool = False

    # Adverse-open drift gate snapshot (issue #92). Captured once per window
    # from the first tick with a two-sided book on both legs, never re-evaluated
    # against the live mid, and cleared on window rollover.
    open_mid: Optional[float] = None
    open_drift: float = 0.0
    adverse_open: bool = False
    open_gate_evaluated: bool = False

    # Entry-band gate (issue #137). Evaluated once per window after
    # `entry_delay_sec` expires, against the then-current mid — never
    # re-evaluated, never applied to re-entry, and cleared on rollover.
    band_gate_evaluated: bool = False
    band_skip: bool = False

    # Fill-telemetry rest context (issue #138). Snapshot per leg the first
    # tick its entry order is active; cleared on rollover. `last_bids_*`
    # stash the most recent full bid books so the stream fill path (which
    # carries no book) can still estimate queue-ahead.
    rest_up_price: Optional[float] = None
    rest_up_queue: Optional[float] = None
    rest_up_ts: Optional[float] = None
    rest_dn_price: Optional[float] = None
    rest_dn_queue: Optional[float] = None
    rest_dn_ts: Optional[float] = None
    last_bids_up: Dict[float, float] = field(default_factory=dict)
    last_bids_down: Dict[float, float] = field(default_factory=dict)
    fill_telemetry_done_up: bool = False
    fill_telemetry_done_down: bool = False

    # Late-start guard (issue #96). `first_seen_start_ts` records which window the
    # latch belongs to, `first_tick_elapsed_sec` how far into that window the
    # engine's first observed tick landed, and `late_start_skip` whether that is
    # past `max_start_elapsed_pct` -- in which case the engine never witnessed the
    # open, so it neither quotes the window nor snapshots its mid.
    first_seen_start_ts: Optional[float] = None
    first_tick_elapsed_sec: Optional[float] = None
    late_start_skip: bool = False

    # Drift-skip re-entry (issue #95). A window skipped by the adverse-open gate may
    # be re-entered later in the same window once the live mid has reverted to within
    # `reentry_drift_band` of 0.50. `reentry_count` caps that per window; `reentry_mid`
    # and `reentry_drift` record the book the re-entry was taken on, leaving the
    # original `open_mid` / `open_drift` snapshot intact for telemetry.
    reentry_count: int = 0
    reentry_mid: Optional[float] = None
    reentry_drift: Optional[float] = None
    # Observability record for the re-entry currently in flight. Seeded when
    # re-entry is granted, completed once both legs reach the book, stamped with
    # the window outcome and flushed to `REENTRY_FILE` at rollover.
    reentry_telemetry: Optional[Dict[str, Any]] = None

    # Advance Pre-Quoting (Upcoming Window T+1)
    next_condition_id: str = ""
    next_market_slug: str = ""
    next_up_token: str = ""
    next_down_token: str = ""
    next_start_ts: float = 0.0
    next_end_ts: float = 0.0
    next_order_id_up: Optional[str] = None
    next_order_id_down: Optional[str] = None
    next_order_time_up: str = "-"
    next_order_time_down: str = "-"
    next_quoted: bool = False
    
    # Pre-placed resting stop-loss protection (issue #87)
    stop_order_id: Optional[str] = None
    # Issue #124: wall-clock time the leg went naked (exactly one side filled).
    # The naked timeout measures from the fill, not the window open, so a leg that
    # fills late still gets its full timeout horizon and a pair whose second leg
    # fills just after is not killed on the same tick it paired.
    naked_since_ts: Optional[float] = None
    stop_order_status: str = "NONE"
    stop_price: Optional[float] = None
    stop_side: Optional[str] = None
    stop_order_time: str = "-"

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
    # Issue #123: leg chase tracking
    chased_leg: Optional[str] = None
    chased_fill: bool = False

    # Multi-merge re-quoting (issue #89): completed merges in the current
    # window. Round 0 is the initial static-anchor quote; each re-quote opens
    # the next round anchored to the live mid minus offset. Reset on rollover.
    requote_round: int = 0
    last_requote_telemetry: Optional[Dict[str, Any]] = None
    
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
    actual_price: Optional[float] = None
    rtds_price: Optional[float] = None
    price_diff: Optional[float] = None
    price_diff_pct: Optional[float] = None

    # Retained cancelled orders for active window
    cancelled_orders: List[Dict[str, Any]] = field(default_factory=list)

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
    market_slug: str = ""


# Display rank for Open Orders rows (issue #97): current-window live orders
# first, resting stop-loss second, next-window pre-quotes third, cancelled
# rows last regardless of source.
def _open_order_sort_key(order: Dict[str, Any], series_index: Dict[str, int]) -> tuple:
    """Sort key making get_open_orders_list output deterministic for every consumer."""
    status = str(order.get("status") or "").upper()
    source = str(order.get("source") or "").upper()
    if status in ("CANCELLED", "CANCELED"):
        rank = 3
    elif source == "ENGINE_ADVANCE" or status == "ADVANCE_PRE_QUOTE":
        rank = 2
    elif source == "ENGINE_STOP":
        rank = 1
    else:
        # Current-window live rows: ENGINE_ACTIVE, CLOB_API, PAPER_SIMULATION.
        rank = 0
    series_idx = series_index.get(str(order.get("series_slug") or ""), len(series_index))
    side = str(order.get("side") or "").upper()
    leg = 0 if "UP" in side else (1 if "DOWN" in side else 2)
    return (rank, series_idx, leg, str(order.get("order_id") or ""))


class LiveTraderEngine:
    """Singleton background engine for live quoting and paper/live trading."""

    def __init__(
        self,
        load_persisted: bool = True,
        selected_markets: Optional[Sequence[str]] = None,
        tokens: Optional[Sequence[str]] = None,
        durations: Optional[Sequence[int]] = None,
        entry_timeout_pct: Optional[float] = None,
        max_start_elapsed_pct: Optional[float] = None,
        min_requote_remaining_sec: Optional[float] = None,
    ):
        """Initialize the live trading engine with default parameters and selected markets."""
        _load_env_file()
        self.is_running: bool = False
        self.mode: str = "paper"  # "paper" or "live"
        self.wallet_address: str = os.getenv("POLY_FUNDER") or os.getenv("RELAYER_API_KEY_ADDRESS") or ""
        self.starting_balance: float = 1000.0
        self.current_portfolio_value: float = 1000.0
        
        # Strategy Parameters
        self.offset: float = 0.02
        self.exit_thresh: float = 0.05
        # Issue #124: naked legs (one side filled, other cancelled or never filled)
        # bleed far more per stop than paired positions earn, so they get their own
        # tighter stop. A leg is "naked" whenever exactly one side is filled; a
        # paired position (both filled) keeps the standard `exit_thresh`.
        self.exit_thresh_naked: float = 0.05
        # Issue #124: force-exit a leg still unpaired after this fraction of the
        # window has elapsed, regardless of drift. 0 disables the timeout. This
        # bounds the worst case where the mid hovers just inside the stop so the
        # naked leg rides all the way to the wall.
        self.naked_leg_timeout_pct: float = 0.70
        # Issue #124: re-entry must not open a position that can only fill one
        # leg. When True, drift-skip re-entry additionally requires both books to
        # quote two sides at re-entry time (book_two_sided already gates it) AND
        # requires at least naked_timeout-equivalent time remaining, so a fresh
        # entry can pair before the naked timeout would fire. When False, re-entry
        # behaves as before (issue #95 semantics).
        self.reentry_require_pairable: bool = True
        # Issue #123: actively chase second leg after a one-sided fill by stepping
        # up the opposite leg quote toward the ask, capped so pair cost <= max_pair_cost.
        self.enable_leg_chase: bool = True
        self.max_pair_cost: float = 0.98
        # Issue #137: patient undecided-band maker knobs. `entry_delay_sec`
        # holds all quoting until that many seconds into the window (0 = off);
        # `entry_band` only admits windows whose mid is still near 0.50 at
        # entry time (0 = off); `stop_loss_enabled=False` holds a filled naked
        # leg to settlement/rollover instead of staging a stop. Defaults
        # preserve the current behavior exactly.
        self.entry_delay_sec: float = 0.0
        self.entry_band: float = 0.0
        self.stop_loss_enabled: bool = True
        # Name of the active named preset, or None for a manual/custom
        # configuration. Set by update_config(preset=...), cleared as soon as
        # a manual change to a preset-table knob diverges from the table.
        self.active_preset: Optional[str] = None
        self.spot_exit_drift: float = 0.003
        self.exit_reversal: float = 0.02  # unified with BacktestParams (issue #111)
        self.shares: int = 5
        self.taker_fee_rate: float = 0.0
        self.entry_timeout_pct: float = float(entry_timeout_pct) if entry_timeout_pct is not None else 1.0
        # Late-start guard (issue #96), independent of `entry_timeout_pct`: the
        # fraction of a window that may already have elapsed when the engine sees
        # its first tick for that window. Past it the window is left alone, so a
        # restart mid-window neither rests unhedgeable legs nor latches an
        # adverse-drift snapshot from a mid-window mid. 0 or >= 1.0 disables.
        self.max_start_elapsed_pct: float = (
            float(max_start_elapsed_pct) if max_start_elapsed_pct is not None
            else DEFAULT_MAX_START_ELAPSED_PCT
        )
        # Re-quote time gate (issue #89): a fresh round after a pair merge only
        # opens when at least this much window time remains. 0 disables it.
        # Issue #95 shares this knob for drift-skip re-entry: both answer the same
        # question -- "is there enough window left to open a fresh two-leg position
        # and have it pair?" -- and the answer does not depend on why the market is
        # currently flat. At the 300s default a 5m window can never re-enter; lower
        # it to re-enter 5m markets.
        self.min_requote_remaining_sec: float = (
            max(0.0, float(min_requote_remaining_sec)) if min_requote_remaining_sec is not None
            else DEFAULT_MIN_REQUOTE_REMAINING_SEC
        )
        # Drift-skip re-entry (issue #95). A window the adverse-open gate skipped is
        # re-entered once the live mid comes back within `reentry_drift_band` of 0.50
        # and `min_requote_remaining_sec` of the window is left. The band is
        # deliberately far tighter than `exit_thresh`: issue #89's mid-anchored
        # quoting only applies to post-merge re-quote rounds, so the *entry* (and
        # therefore the re-entry) still rests at a static `0.50 - offset`. A wide band
        # would re-quote 0.48/0.48 into a market trading well away from 0.50 and fill
        # only the adverse leg. Defaults are mirrored in `BacktestParams`.
        self.reentry_drift_band: float = 0.015
        self.max_reentries_per_window: int = 1
        # Session tally of drift-skip re-entries and how they ended (issue #95
        # observability). Counted as each re-entered window closes, so it answers
        # "did re-entry actually fill anything today?" without reading the file.
        self.reentry_stats: Dict[str, int] = _empty_reentry_stats()
        # Entry-band skip tally (issue #137 observability). Counts windows the
        # post-delay band filter rejected, so the pilot can tell band skips
        # apart from adverse-open skips without reading the log.
        self.band_skip_stats: Dict[str, int] = _empty_band_skip_stats()
        # Issue #138: fill-telemetry worker mode. True (default) joins tape
        # and appends off the hot path in a daemon thread; False runs inline
        # (deterministic, for tests and debugging).
        self.fill_telemetry_async: bool = True
        # Re-entry time gate, as a fraction of the window (issue #95). The shared
        # `min_requote_remaining_sec` is an absolute 300s, which is a whole 5m
        # window -- an absolute floor cannot mean the same thing on a 5m and a 15m
        # market, and at 300s it made re-entry impossible on exactly the 5m markets
        # the issue's evidence table shows reverting. The effective gate is the
        # tighter of the two, so this can only ever add restriction to #89's knob
        # and never loosens the post-merge re-quoting that knob also governs.
        self.reentry_min_remaining_pct: float = 0.30
        
        # State tracking
        self.selected_series: tuple[tuple[str, int, str], ...] = _resolve_series_selection(
            selected_markets=selected_markets,
            tokens=tokens,
            durations=durations,
        )
        self.markets: Dict[str, MarketLiveState] = {}
        for slug, _dur, label in self.selected_series:
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
        self.historical_realized_pnl: float = 0.0
        self.total_unrealized_pnl: float = 0.0
        self.total_pnl: float = 0.0
        self.total_pairs_merged: int = 0
        self.total_stops_triggered: int = 0
        self.session_start_ts: float = time.time()
        self.open_positions: List[Dict[str, Any]] = []
        
        self._bg_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._engine_lock = threading.RLock()
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="LiveTraderExec")
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "Mozilla/5.0"})
        self._clob_client: Optional[Any] = None
        self._orders_cache: List[Dict[str, Any]] = []
        self._orders_cache_ts: float = 0.0
        self.quoting_halted: bool = False
        
        # Real-time WebSocket streaming bridge
        self.stream_bridge = UnifiedStreamBridge(
            on_spot_tick=self.on_spot_tick,
            on_rtds_tick=self.on_rtds_tick,
            on_book_update=self.on_book_update,
            on_order_event=self.on_user_order_event,
        )
        if load_persisted and not os.getenv("PYTEST_CURRENT_TEST"):
            self._load_persisted_trades()

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
                try:
                    from py_clob_client.clob_types import OrderArgs
                except ImportError:
                    class OrderArgs:  # type: ignore[no-redef]
                        """Fallback OrderArgs stub when py_clob_client is not installed."""

                        def __init__(self, token_id: str = "", price: float = 0.0, size: float = 0.0, side: str = "BUY"):
                            """Initialize OrderArgs fallback instance."""
                            self.token_id = token_id
                            self.price = price
                            self.size = size
                            self.side = side

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

    def _cancel_stop_order(self, mstate: MarketLiveState, reason: str) -> bool:
        """Cancel and clear the staged stop-loss order (OCO reciprocal leg).

        Returns True when no stop remains outstanding. A venue-side cancel
        failure keeps the handle (status CANCEL_FAILED) so callers can block
        dependent transitions — e.g. pair merge — and retry on the next tick.
        """
        with self._engine_lock:
            stop_id = mstate.stop_order_id
            stop_status = mstate.stop_order_status
        if not stop_id:
            return True
        if self.mode == "live" and stop_status in ("RESTING", "CANCEL_FAILED"):
            # Only an order actually resting on the book needs a venue cancel;
            # STAGED buffers exist in memory only and clear locally. A previous
            # cancel failure is retried here before the handle is cleared.
            if not self.cancel_live_order(stop_id):
                log.warning(
                    "[%s] Failed to cancel resting stop-loss %s (%s); retaining handle as CANCEL_FAILED",
                    mstate.slug,
                    stop_id,
                    reason,
                )
                with self._engine_lock:
                    mstate.stop_order_status = "CANCEL_FAILED"
                return False
        with self._engine_lock:
            mstate.stop_order_id = None
            mstate.stop_order_status = "NONE"
            mstate.stop_price = None
            mstate.stop_side = None
            mstate.stop_order_time = "-"
        log.info("[%s] Stop-loss order cancelled and cleared (%s)", mstate.slug, reason)
        return True

    def _record_fill_telemetry(self, mstate: MarketLiveState, side: str,
                                 fill_price: Optional[float],
                                 filled_size: Optional[float],
                                 now: float) -> None:
        """Claim one queue-position telemetry line for an entry fill.

        Issue #138, observation only. The claim (exactly-once flag) is taken
        synchronously under the engine lock; the tape join and file append run
        in a daemon worker (or inline when `fill_telemetry_async` is False),
        so a slow venue response never stalls the tick/stream hot path. Any
        failure degrades to a lost line, never into the fill path.
        """
        try:
            leg = side.upper()
            if leg not in ("UP", "DOWN"):
                log.warning("[%s] fill-telemetry skipped: unknown side %r", mstate.slug, side)
                return
            done_attr = "fill_telemetry_done_up" if leg == "UP" else "fill_telemetry_done_down"
            with self._engine_lock:
                if getattr(mstate, done_attr, False):
                    return
                setattr(mstate, done_attr, True)
            if isinstance(filled_size, bool) or not isinstance(filled_size, (int, float)) or filled_size <= 0:
                size = float(self.shares)
            else:
                size = float(filled_size)
            is_up = (leg == "UP")
            rest_price = mstate.rest_up_price if is_up else mstate.rest_dn_price
            rest_queue = mstate.rest_up_queue if is_up else mstate.rest_dn_queue
            rest_ts = mstate.rest_up_ts if is_up else mstate.rest_dn_ts
            if rest_price is None:
                # Stream fills may predate any placement tick: fall back to
                # the latched resting price and the stashed books (no rest
                # timestamp, so no tape join — printed stays null).
                rest_price = mstate.resting_up if is_up else mstate.resting_down
                stash = mstate.last_bids_up if is_up else mstate.last_bids_down
                rest_queue = _queue_ahead(stash, rest_price) if rest_price is not None else None
            snapshot = {
                "slug": mstate.slug,
                "market_slug": mstate.market_slug or "",
                "condition_id": mstate.condition_id or "",
                "leg": leg,
                "chased": (mstate.chased_leg == leg),
                "resting_price": rest_price,
                "rest_queue": rest_queue,
                "rest_ts": rest_ts,
                "token": mstate.up_token if is_up else mstate.down_token,
                "fill_price": fill_price,
                "filled_size": size,
                "window_elapsed_sec": max(0.0, now - mstate.start_ts) if mstate.start_ts > 0 else 0.0,
                "mid_at_fill": mstate.mid,
                "resting_pair_cost": round(mstate.resting_up + mstate.resting_down, 4),
                "ts": now,
            }
            if self.fill_telemetry_async:
                thread = threading.Thread(
                    target=self._fill_telemetry_worker,
                    kwargs=snapshot, daemon=True,
                    name=f"fill-telemetry-{mstate.slug}-{leg}",
                )
                thread.start()
            else:
                self._fill_telemetry_worker(**snapshot)
        except Exception as e:
            log.warning("[%s] fill-telemetry record failed: %s", mstate.slug, e)

    def _fill_telemetry_worker(self, *, slug: str, market_slug: str,
                               condition_id: str, leg: str, chased: bool,
                               resting_price: Optional[float],
                               fill_price: Optional[float],
                               rest_queue: Optional[float],
                               rest_ts: Optional[float], token: str,
                               filled_size: float, window_elapsed_sec: float,
                               mid_at_fill: Optional[float],
                               resting_pair_cost: Optional[float],
                               ts: float) -> None:
        """Fetch tape, build the record, append it. Exceptions never propagate."""
        try:
            printed: Optional[float] = None
            if (resting_price is not None and rest_ts is not None
                    and token and condition_id):
                rows = _fetch_price_prints(condition_id)
                if rows is not None:
                    printed = _sum_prints_at_price(rows, token, resting_price, rest_ts)
            record = _build_fill_record(
                ts=ts,
                slug=slug,
                market_slug=market_slug,
                condition_id=condition_id,
                leg=leg,
                chased=chased,
                resting_price=resting_price,
                fill_price=fill_price,
                queue_ahead=rest_queue,
                printed_size=printed,
                filled_size=filled_size,
                window_elapsed_sec=window_elapsed_sec,
                mid_at_fill=mid_at_fill,
                resting_pair_cost=resting_pair_cost,
            )
            if not _append_fill_telemetry(record):
                log.warning("[%s] fill-telemetry append failed (line lost)", slug)
        except Exception as e:
            log.warning("[%s] fill-telemetry worker failed: %s", slug, e)

    def place_stop_order(self, mstate: MarketLiveState, side: str) -> None:
        """Stage stop-loss protection for a single-leg filled position.

        Idempotent: a no-op when a stop is already staged for this window.
        Venue evaluation (issue #87): the binary CLOB accepts standard limit
        orders only, and a SELL priced at the stop threshold would cross the
        bid immediately — instantly exiting the freshly filled leg. So the
        stop is maintained as a pre-signed zero-latency buffer in memory
        (status STAGED) and is submitted bounded at
        min(best_bid, stop_price) the moment the trigger fires, via the
        existing _execute_stop_exit path. Paper mode stages a simulated
        order that fills when the bid touches the stop price.
        """
        # Issue #137: with the stop-loss disabled the preset holds naked legs
        # to settlement/rollover instead, so no protection is ever staged.
        # Staging choke point covering the live-buffered, paper-simulated, and
        # polling fill paths; the drift-stop triggers are gated separately
        # below (naked-timeout and rollover stay authoritative).
        if not self.stop_loss_enabled:
            return
        is_up = (side.upper() == "UP")
        with self._engine_lock:
            if mstate.stop_order_id:
                return
            fill_price = mstate.fill_price_up if is_up else mstate.fill_price_down
            token = mstate.up_token if is_up else mstate.down_token
            if fill_price is None or not token:
                return
            stop_price = round(min(0.99, max(0.01, fill_price - self._naked_exit_thresh())), 2)

        if self.mode == "live":
            stop_order_id = f"buffer_stop_{mstate.slug}"
            stop_status = "STAGED"
        else:
            stop_order_id = f"paper_stop_{mstate.slug}"
            stop_status = "RESTING"

        with self._engine_lock:
            mstate.stop_order_id = stop_order_id
            mstate.stop_order_status = stop_status
            mstate.stop_price = stop_price
            mstate.stop_side = side.upper()
            mstate.stop_order_time = time.strftime("%H:%M:%S")
            log.info(
                "[%s] Stop-loss %s buffered: SELL %s shares @ %.2f on trigger (%s)",
                mstate.slug,
                mstate.stop_side,
                self.shares,
                stop_price,
                stop_order_id,
            )

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
        success = False
        try:
            if hasattr(client, "cancel"):
                try:
                    res = client.cancel(order_id)
                    log.info("Cancelled order %s -> %s", order_id, res)
                    success = self._cancel_succeeded(res)
                except (AttributeError, TypeError):
                    pass
            if not success and hasattr(client, "cancel_orders"):
                res = client.cancel_orders([order_id])
                log.info("Cancelled order %s -> %s", order_id, res)
                success = self._cancel_succeeded(res)
            if not success and not hasattr(client, "cancel") and not hasattr(client, "cancel_orders"):
                log.error("No single-order cancel API available; refusing cancel_all for %s", order_id)
                return False
        except Exception as e:
            log.error("Failed cancelling order %s: %s", order_id, e)
            return False

        if success:
            with self._engine_lock:
                self._clear_order_handles(order_id)
        return success

    @staticmethod
    def _record_cancelled_order(m: MarketLiveState, order_dict: Dict[str, Any]) -> None:
        """Append cancelled order to market's retained list if not already present."""
        oid = order_dict.get("order_id")
        side = order_dict.get("side")
        if not any(o.get("order_id") == oid and o.get("side") == side for o in m.cancelled_orders):
            m.cancelled_orders.append(order_dict.copy())

    def _clear_order_handles(self, order_id: str) -> None:
        """Clear cached order handles and status markers for a cancelled order ID.

        Caller must hold _engine_lock.
        """
        now_time_str = time.strftime("%H:%M:%S")
        for m in self.markets.values():
            if m.order_id_up == order_id:
                self._record_cancelled_order(m, {
                    "order_id": order_id,
                    "market": m.label,
                    "market_slug": m.market_slug or "",
                    "series_slug": m.slug,
                    "token_id": m.up_token,
                    "side": "BUY (UP)",
                    "price": m.resting_up,
                    "size": m.order_shares,
                    "status": "CANCELLED",
                    "source": "CLOB_API" if self.mode == "live" else "PAPER_SIMULATION",
                    "time": m.order_time_up if m.order_time_up != "-" else now_time_str,
                })
                m.order_id_up = None
                m.order_status_up = "CANCELLED"
                m.order_time_up = "-"
            if m.order_id_down == order_id:
                self._record_cancelled_order(m, {
                    "order_id": order_id,
                    "market": m.label,
                    "market_slug": m.market_slug or "",
                    "series_slug": m.slug,
                    "token_id": m.down_token,
                    "side": "BUY (DOWN)",
                    "price": m.resting_down,
                    "size": m.order_shares,
                    "status": "CANCELLED",
                    "source": "CLOB_API" if self.mode == "live" else "PAPER_SIMULATION",
                    "time": m.order_time_down if m.order_time_down != "-" else now_time_str,
                })
                m.order_id_down = None
                m.order_status_down = "CANCELLED"
                m.order_time_down = "-"
            if m.next_order_id_up == order_id:
                m.next_order_id_up = None
                m.next_order_time_up = "-"
            if m.next_order_id_down == order_id:
                m.next_order_id_down = None
                m.next_order_time_down = "-"
            if m.order_id_exit_up == order_id:
                m.order_id_exit_up = None
            if m.order_id_exit_down == order_id:
                m.order_id_exit_down = None


    def cancel_all_orders(self) -> Dict[str, Any]:
        """Emergency panic button: Cancel all open orders on CLOB and clear active handles."""
        self.quoting_halted = True
        self.is_running = False
        cancelled_remote = False
        if self.mode == "live" or self._clob_client is not None:
            client = self.get_clob_client()
            if not client:
                log.error("Live emergency cancel_all failed: no CLOB client available")
                return {
                    "ok": False,
                    "error": "No CLOB client available",
                    "remote_cancel_called": False,
                    "markets_cleared": 0,
                    "timestamp": time.time(),
                }
            try:
                client.cancel_all()
                cancelled_remote = True
                log.info("Emergency cancel_all invoked on Polymarket CLOB")
            except Exception as e:
                log.error("Error in remote cancel_all: %s", e)
                return {
                    "ok": False,
                    "error": str(e),
                    "remote_cancel_called": False,
                    "markets_cleared": 0,
                    "timestamp": time.time(),
                }

        # Clear local order handles across all markets
        cleared_count = 0
        now_time_str = datetime.datetime.now().strftime("%H:%M:%S")
        with self._engine_lock:
            for m in self.markets.values():
                if m.order_id_up or m.order_id_down or m.next_order_id_up or m.next_order_id_down or m.order_id_exit_up or m.order_id_exit_down:
                    cleared_count += 1
                if m.order_id_up:
                    self._record_cancelled_order(m, {
                        "order_id": m.order_id_up,
                        "market": m.label,
                        "market_slug": m.market_slug or "",
                        "series_slug": m.slug,
                        "token_id": m.up_token or "",
                        "side": "BUY (UP)",
                        "price": m.resting_up,
                        "size": m.order_shares,
                        "status": "CANCELLED",
                        "source": "CLOB_API" if self.mode == "live" else "PAPER_SIMULATION",
                        "time": m.order_time_up if m.order_time_up != "-" else now_time_str,
                    })
                if m.order_id_down:
                    self._record_cancelled_order(m, {
                        "order_id": m.order_id_down,
                        "market": m.label,
                        "market_slug": m.market_slug or "",
                        "series_slug": m.slug,
                        "token_id": m.down_token or "",
                        "side": "BUY (DOWN)",
                        "price": m.resting_down,
                        "size": m.order_shares,
                        "status": "CANCELLED",
                        "source": "CLOB_API" if self.mode == "live" else "PAPER_SIMULATION",
                        "time": m.order_time_down if m.order_time_down != "-" else now_time_str,
                    })
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

    def on_rtds_tick(self, symbol: str, ts_ms: int, price: float) -> None:
        """Handle real-time RTDS tick across all matching active series."""
        slugs = series_for_symbol(symbol)
        if not slugs:
            single = SYMBOL_TO_SERIES.get(symbol.lower())
            slugs = [single] if single else []

        for slug in slugs:
            if not slug:
                continue
            with self._engine_lock:
                if slug not in self.markets:
                    continue
                m = self.markets[slug]
                m.rtds_price = price
                if m.actual_price is not None:
                    m.price_diff = round(m.actual_price - price, 4)
                    if price > 0:
                        m.price_diff_pct = round(((m.actual_price - price) / price) * 100.0, 4)
                    else:
                        m.price_diff_pct = None
                else:
                    m.price_diff = None
                    m.price_diff_pct = None

    def on_spot_tick(self, symbol: str, ts_ms: int, price: float, source: str = "BINANCE") -> None:
        """Handle real-time spot tick from RTDS or fallback across all matching active series."""
        slugs = series_for_symbol(symbol)
        if not slugs:
            single = SYMBOL_TO_SERIES.get(symbol.lower())
            slugs = [single] if single else []

        for slug in slugs:
            if not slug:
                continue

            trigger_side: Optional[str] = None
            note: str = ""
            now = time.time()

            with self._engine_lock:
                if slug not in self.markets:
                    continue
                m = self.markets[slug]
                m.spot_price = price
                if (source or "").upper().startswith("BINANCE"):
                    m.actual_price = price
                else:
                    m.actual_price = None
                m.spot_updated_ts = ts_ms / 1000.0
                m.streaming_active = True

                if m.actual_price is not None and m.rtds_price is not None:
                    m.price_diff = round(m.actual_price - m.rtds_price, 4)
                    if m.rtds_price > 0:
                        m.price_diff_pct = round(((m.actual_price - m.rtds_price) / m.rtds_price) * 100.0, 4)
                    else:
                        m.price_diff_pct = None
                elif m.actual_price is None or m.rtds_price is None:
                    m.price_diff = None
                    m.price_diff_pct = None

                if m.spot_open_price is None or m.spot_open_price <= 0:
                    m.spot_open_price = price

                if m.spot_open_price and m.spot_open_price > 0:
                    m.spot_drift = (price - m.spot_open_price) / m.spot_open_price

                # Issue #137: the streaming fast-stop path honors
                # stop_loss_enabled like every other stop trigger — a disabled
                # stop holds naked legs through spot drift too.
                if self.is_running and not self.quoting_halted and self.stop_loss_enabled:
                    # Fast stop loss execution on adverse leading spot drift
                    if m.filled_up and not m.filled_down and not m.exit_taken and m.status != "STOP_EXIT_PENDING":
                        if m.spot_drift <= -self.spot_exit_drift:
                            m.max_down_drift = max(m.max_down_drift, abs(m.spot_drift))
                            m.status = "STOP_EXIT_PENDING"
                            trigger_side = "UP"
                    elif m.filled_down and not m.filled_up and not m.exit_taken and m.status != "STOP_EXIT_PENDING":
                        if m.spot_drift >= self.spot_exit_drift:
                            m.max_up_drift = max(m.max_up_drift, m.spot_drift)
                            m.status = "STOP_EXIT_PENDING"
                            trigger_side = "DOWN"

            if trigger_side:
                log.info("[%s] Spot leading tick triggered fast stop exit for %s leg: spot=%.2f drift=%.3f",
                         slug, trigger_side, price, m.spot_drift)
                note = f"Spot Fast stop: drift {m.spot_drift:.3f} {'<=' if trigger_side == 'UP' else '>='} {'-' if trigger_side == 'UP' else ''}{self.spot_exit_drift:.3f}"
                if self.mode == "live":
                    fut = self._executor.submit(self._execute_stop_exit, slug, m, trigger_side, None, note, now)
                    fut.add_done_callback(
                        lambda f, s=slug: log.error("[%s] Fast stop execution failed: %s", s, f.exception())
                        if f.exception() else None
                    )
                else:
                    self._execute_stop_exit(slug, m, trigger_side, None, note, now)

    def _execute_stop_exit(
        self,
        slug: str,
        mstate: MarketLiveState,
        side: str,
        exit_price: Optional[float],
        trigger_note: str,
        now: float,
    ) -> None:
        """Execute stop loss order cancellation, market sell quote, fill check, and accounting."""
        is_up = (side.upper() == "UP")
        with self._engine_lock:
            opp_order_id = mstate.order_id_down if is_up else mstate.order_id_up
            exit_order_id = mstate.order_id_exit_up if is_up else mstate.order_id_exit_down
            exit_token = mstate.up_token if is_up else mstate.down_token
            book_bid = mstate.up_bid if is_up else mstate.down_bid
            sell_bid = exit_price if exit_price is not None else (book_bid if book_bid is not None else 0.40)

        now_time_str = datetime.datetime.fromtimestamp(now).strftime("%H:%M:%S")
        if self.mode == "live":
            # 1. Cancel unhedged opposite resting order
            if opp_order_id:
                cancel_ok = self.cancel_live_order(opp_order_id)
                if not cancel_ok:
                    log.warning(
                        "[%s] Failed to cancel opposite leg %s during stop exit, retaining handle and marking STOP_EXIT_PENDING",
                        mstate.slug,
                        opp_order_id,
                    )
                    with self._engine_lock:
                        mstate.status = "STOP_EXIT_PENDING"
                    return
                with self._engine_lock:
                    if is_up:
                        mstate.order_status_down = "CANCELLED"
                        mstate.order_id_down = None
                        self._record_cancelled_order(mstate, {
                            "order_id": opp_order_id,
                            "market": mstate.label,
                            "market_slug": mstate.market_slug or "",
                            "series_slug": mstate.slug,
                            "token_id": mstate.down_token,
                            "side": "BUY (DOWN)",
                            "price": mstate.resting_down,
                            "size": mstate.order_shares,
                            "status": "CANCELLED",
                            "source": "CLOB_API",
                            "time": now_time_str,
                        })
                    else:
                        mstate.order_status_up = "CANCELLED"
                        mstate.order_id_up = None
                        self._record_cancelled_order(mstate, {
                            "order_id": opp_order_id,
                            "market": mstate.label,
                            "market_slug": mstate.market_slug or "",
                            "series_slug": mstate.slug,
                            "token_id": mstate.up_token,
                            "side": "BUY (UP)",
                            "price": mstate.resting_up,
                            "size": mstate.order_shares,
                            "status": "CANCELLED",
                            "source": "CLOB_API",
                            "time": now_time_str,
                        })

            # 2. Market sell the filled leg if not yet submitted
            if not exit_order_id and exit_token:
                res = self.place_live_quote(exit_token, sell_bid, self.shares, "SELL")
                if res and res.get("order_id"):
                    with self._engine_lock:
                        if is_up:
                            mstate.order_id_exit_up = res["order_id"]
                            mstate.order_status_exit_up = res.get("status") or "RESTING"
                            mstate.exit_price_up = sell_bid
                            exit_order_id = mstate.order_id_exit_up
                        else:
                            mstate.order_id_exit_down = res["order_id"]
                            mstate.order_status_exit_down = res.get("status") or "RESTING"
                            mstate.exit_price_down = sell_bid
                            exit_order_id = mstate.order_id_exit_down

            with self._engine_lock:
                submitted_exit_price = mstate.exit_price_up if is_up else mstate.exit_price_down
                if submitted_exit_price is not None:
                    sell_bid = submitted_exit_price
                curr_status = mstate.order_status_exit_up if is_up else mstate.order_status_exit_down
                is_filled = (curr_status == "FILLED")

            # 3. Check fill status on CLOB
            if not is_filled and exit_order_id:
                client = self.get_clob_client()
                if client:
                    try:
                        ord_info = client.get_order(exit_order_id)
                        st = (ord_info.get("status") or "").upper()
                        sz = float(ord_info.get("size_matched", 0.0) or 0.0)
                        if st in ("MATCHED", "FILLED") or sz >= self.shares:
                            is_filled = True
                            with self._engine_lock:
                                if is_up:
                                    mstate.order_status_exit_up = "FILLED"
                                else:
                                    mstate.order_status_exit_down = "FILLED"
                    except Exception as e:
                        log.debug("[%s] Error checking %s exit order %s: %s", slug, side, exit_order_id, e)

            if not is_filled:
                with self._engine_lock:
                    mstate.status = "STOP_EXIT_PENDING"
                    mstate.last_action = f"Stop Loss {side} resting @ {sell_bid:.2f}"
                return
        else:
            with self._engine_lock:
                if is_up:
                    paper_oid = opp_order_id or f"paper_dn_{mstate.slug}"
                    mstate.order_status_down = "CANCELLED"
                    mstate.order_id_down = None
                    self._record_cancelled_order(mstate, {
                        "order_id": paper_oid,
                        "market": mstate.label,
                        "market_slug": mstate.market_slug or "",
                        "series_slug": mstate.slug,
                        "token_id": mstate.down_token,
                        "side": "BUY (DOWN)",
                        "price": mstate.resting_down,
                        "size": mstate.order_shares,
                        "status": "CANCELLED",
                        "source": "PAPER_SIMULATION",
                        "time": now_time_str,
                    })
                else:
                    paper_oid = opp_order_id or f"paper_up_{mstate.slug}"
                    mstate.order_status_up = "CANCELLED"
                    mstate.order_id_up = None
                    self._record_cancelled_order(mstate, {
                        "order_id": paper_oid,
                        "market": mstate.label,
                        "market_slug": mstate.market_slug or "",
                        "series_slug": mstate.slug,
                        "token_id": mstate.up_token,
                        "side": "BUY (UP)",
                        "price": mstate.resting_up,
                        "size": mstate.order_shares,
                        "status": "CANCELLED",
                        "source": "PAPER_SIMULATION",
                        "time": now_time_str,
                    })

        # 4. Finalize stop exit state & PnL accounting
        with self._engine_lock:
            entry_price = (mstate.fill_price_up or mstate.resting_up) if is_up else (mstate.fill_price_down or mstate.resting_down)
            mstate.exit_taken = True
            mstate.exit_side = side.upper()
            mstate.status = "STOP_EXIT"
            exit_pnl_usd = (sell_bid - entry_price) * self.shares
            mstate.realized_pnl_usd += exit_pnl_usd
            mstate.unrealized_pnl_usd = 0.0
            mstate.total_pnl_usd = mstate.realized_pnl_usd
            mstate.stops_count += 1
            mstate.trades_count += 1
            action_name = "STOP_EXIT_UP" if is_up else "STOP_EXIT_DOWN"
            mstate.last_action = f"Stop Loss {side} @ {sell_bid:.2f} ({exit_pnl_usd:+.2f}$)"
            log.info("[%s] STOP LOSS EXIT (%s) @ %.2f, PnL: $%.2f", slug, side, sell_bid, exit_pnl_usd)

            denom = max(0.01, entry_price * max(1, self.shares))
            self.trades.append(TradeEvent(
                id=f"{slug}_{int(now)}",
                timestamp=datetime.datetime.fromtimestamp(now).strftime("%H:%M:%S"),
                slug=slug,
                label=mstate.label,
                action=action_name,
                shares=self.shares,
                entry_price_up=entry_price if is_up else None,
                entry_price_down=None if is_up else entry_price,
                exit_price=sell_bid,
                pnl_usd=round(exit_pnl_usd, 3),
                pnl_pct=round(((exit_pnl_usd) / denom) * 100.0, 1),
                notes=trigger_note,
                market_slug=mstate.market_slug or "",
            ))
            self._save_persisted_trades()

    def _trigger_fast_stop_exit(self, slug: str, mstate: MarketLiveState, side: str, now: float) -> None:
        """Trigger fast stop-loss exit market order and cancel unhedged side."""
        note = f"RTDS Fast stop: drift {mstate.spot_drift:.3f}"
        self._execute_stop_exit(slug, mstate, side, None, note, now)

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
            up_mid = (m.up_bid + m.up_ask) / 2.0 if (m.up_bid is not None and m.up_ask is not None) else (m.up_bid or m.up_ask or 0.50)
            down_mid = (m.down_bid + m.down_ask) / 2.0 if (m.down_bid is not None and m.down_ask is not None) else (m.down_bid or m.down_ask or 0.50)
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
                    if m.chased_leg == "UP":
                        m.chased_fill = True
                    m.fill_price_up = float(payload.get("price") or m.resting_up)
                    m.status = "FILLED_UP"
                    # Issue #138: queue-position telemetry, stashed books
                    # (the stream event carries no book).
                    self._record_fill_telemetry(
                        m, "UP", m.fill_price_up, payload.get("size"), time.time())
            elif m.order_id_down == order_id:
                m.order_status_down = status
                if status in ("MATCHED", "FILLED"):
                    m.filled_down = True
                    if m.chased_leg == "DOWN":
                        m.chased_fill = True
                    m.fill_price_down = float(payload.get("price") or m.resting_down)
                    m.status = "FILLED_DOWN"
                    # Issue #138: queue-position telemetry, stashed books
                    # (the stream event carries no book).
                    self._record_fill_telemetry(
                        m, "DOWN", m.fill_price_down, payload.get("size"), time.time())
            elif m.order_id_exit_up == order_id:
                m.order_status_exit_up = status
            elif m.order_id_exit_down == order_id:
                m.order_status_exit_down = status
            elif m.stop_order_id == order_id:
                # Keep pre-placed stop-loss status in sync for dashboard display (issue #87)
                m.stop_order_status = status
            if order_id in (m.order_id_up, m.order_id_down):
                # A stream-detected entry fill must stage protection just like the
                # polling path does (place_stop_order is idempotent) (issue #87)
                if m.filled_up and not m.filled_down and not m.exit_taken:
                    if m.naked_since_ts is None:
                        m.naked_since_ts = time.time()
                    self.place_stop_order(m, "UP")
                elif m.filled_down and not m.filled_up and not m.exit_taken:
                    if m.naked_since_ts is None:
                        m.naked_since_ts = time.time()
                    self.place_stop_order(m, "DOWN")

    def get_open_orders_list(self) -> List[Dict[str, Any]]:
        """List active open orders from CLOB and current engine state."""
        orders: List[Dict[str, Any]] = []
        client = self.get_clob_client()

        if client:
            try:
                try:
                    from py_clob_client_v2.clob_types import OpenOrderParams
                except ImportError:
                    try:
                        from py_clob_client.clob_types import OpenOrderParams
                    except ImportError:
                        class OpenOrderParams:  # type: ignore[no-redef]
                            """Stub for CLOB OpenOrderParams when py_clob_client is not installed."""
                            pass
                res = client.get_orders(OpenOrderParams())
                if isinstance(res, list):
                    for o in res:
                        created_raw = o.get("created_at") or o.get("timestamp") or o.get("createdAt")
                        time_str = "-"
                        if created_raw:
                            try:
                                if isinstance(created_raw, (int, float)):
                                    ts_val = created_raw / 1000.0 if created_raw > 1e11 else float(created_raw)
                                    time_str = time.strftime("%H:%M:%S", time.localtime(ts_val))
                                elif isinstance(created_raw, str):
                                    try:
                                        from datetime import datetime
                                        iso_str = created_raw.replace("Z", "+00:00")
                                        dt = datetime.fromisoformat(iso_str)
                                        if dt.tzinfo is not None:
                                            dt = dt.astimezone()
                                        time_str = dt.strftime("%H:%M:%S")
                                    except (ValueError, OverflowError, OSError):
                                        time_str = created_raw[11:19] if "T" in created_raw else created_raw
                            except (ValueError, OverflowError, OSError):
                                time_str = "-"

                        # Resolve market label and side UP/DOWN by token_id
                        raw_asset = str(o.get("asset_id") or o.get("token_id") or "")
                        market_label = str(o.get("market") or "")
                        side_val = str(o.get("side") or "BUY").upper()
                        mkt_slug = str(o.get("market_slug") or "")
                        series_slug = str(o.get("series_slug") or "")
                        for m in self.markets.values():
                            if raw_asset:
                                if raw_asset == m.up_token:
                                    market_label = m.label
                                    mkt_slug = m.market_slug or ""
                                    series_slug = m.slug
                                    if "UP" not in side_val:
                                        side_val = f"{side_val} (UP)"
                                    break
                                elif raw_asset == m.down_token:
                                    market_label = m.label
                                    mkt_slug = m.market_slug or ""
                                    series_slug = m.slug
                                    if "DOWN" not in side_val:
                                        side_val = f"{side_val} (DOWN)"
                                    break
                                elif raw_asset == m.next_up_token:
                                    market_label = f"{m.label} (Next Window)"
                                    mkt_slug = m.next_market_slug or m.market_slug or ""
                                    series_slug = m.slug
                                    if "UP" not in side_val:
                                        side_val = f"{side_val} (UP)"
                                    break
                                elif raw_asset == m.next_down_token:
                                    market_label = f"{m.label} (Next Window)"
                                    mkt_slug = m.next_market_slug or m.market_slug or ""
                                    series_slug = m.slug
                                    if "DOWN" not in side_val:
                                        side_val = f"{side_val} (DOWN)"
                                    break
                        if not market_label:
                            market_label = raw_asset[:10] + "..." if len(raw_asset) > 14 else (raw_asset or "Unknown")

                        # Issue #90: map CLOB size_matched to the filled key the
                        # dashboard reads (o.filled). Missing/None/unparseable/
                        # non-finite -> 0.0.
                        try:
                            filled_val = float(o.get("size_matched", 0.0) or 0.0)
                        except (TypeError, ValueError, OverflowError):
                            filled_val = 0.0
                        if not math.isfinite(filled_val):
                            filled_val = 0.0

                        orders.append({
                            "order_id": o.get("id") or o.get("order_id", ""),
                            "market": market_label,
                            "market_slug": mkt_slug,
                            "series_slug": series_slug,
                            "token_id": raw_asset,
                            "side": side_val,
                            "price": float(o.get("price", 0.0)),
                            "size": float(o.get("original_size", 0.0)),
                            "filled": filled_val,
                            "status": o.get("status", "OPEN"),
                            "source": "CLOB_API",
                            "time": time_str,
                        })
            except Exception as e:
                log.debug("CLOB get_orders error: %s", e)

        # Merge in tracked market orders if not already listed
        existing_ids = {o["order_id"] for o in orders if o.get("order_id")}
        now_time_str = time.strftime("%H:%M:%S")
        for m in self.markets.values():
            # Issue #113: a filled leg is a held position, not a resting bid —
            # never emit it as an open order (the dashboard's Positions tab
            # already synthesizes it from filled_up/filled_down state).
            if m.order_id_up and not m.filled_up and m.order_id_up not in existing_ids:
                orders.append({
                    "order_id": m.order_id_up,
                    "market": m.label,
                    "market_slug": m.market_slug or "",
                    "series_slug": m.slug,
                    "token_id": m.up_token,
                    "side": "BUY (UP)",
                    "price": m.resting_up,
                    "size": m.order_shares,
                    "filled": 0.0,
                    "status": m.order_status_up,
                    "source": "ENGINE_ACTIVE",
                    "time": m.order_time_up if m.order_time_up != "-" else now_time_str,
                })
                existing_ids.add(m.order_id_up)
            if m.order_id_down and not m.filled_down and m.order_id_down not in existing_ids:
                orders.append({
                    "order_id": m.order_id_down,
                    "market": m.label,
                    "market_slug": m.market_slug or "",
                    "series_slug": m.slug,
                    "token_id": m.down_token,
                    "side": "BUY (DOWN)",
                    "price": m.resting_down,
                    "size": m.order_shares,
                    "filled": 0.0,
                    "status": m.order_status_down,
                    "source": "ENGINE_ACTIVE",
                    "time": m.order_time_down if m.order_time_down != "-" else now_time_str,
                })
                existing_ids.add(m.order_id_down)
            # Pre-placed resting stop-loss protection (issue #87)
            if m.stop_order_id and m.stop_order_status not in ("NONE", "CANCELLED", "FILLED") and m.stop_order_id not in existing_ids:
                orders.append({
                    "order_id": m.stop_order_id,
                    "market": m.label,
                    "market_slug": m.market_slug or "",
                    "series_slug": m.slug,
                    "token_id": m.up_token if m.stop_side == "UP" else m.down_token,
                    "side": f"SELL ({m.stop_side})" if m.stop_side else "SELL",
                    "price": m.stop_price,
                    "size": m.order_shares,
                    "filled": 0.0,
                    "status": m.stop_order_status,
                    "source": "ENGINE_STOP",
                    "time": m.stop_order_time if m.stop_order_time != "-" else now_time_str,
                })
                existing_ids.add(m.stop_order_id)
            if m.next_order_id_up and m.next_order_id_up not in existing_ids:
                orders.append({
                    "order_id": m.next_order_id_up,
                    "market": f"{m.label} (Next Window)",
                    "market_slug": m.next_market_slug or m.market_slug or "",
                    "series_slug": m.slug,
                    "token_id": m.next_up_token,
                    "side": "BUY (UP)",
                    "price": m.resting_up,
                    "size": m.order_shares,
                    "filled": 0.0,
                    "status": "ADVANCE_PRE_QUOTE",
                    "source": "ENGINE_ADVANCE",
                    "time": m.next_order_time_up if m.next_order_time_up != "-" else now_time_str,
                })
                existing_ids.add(m.next_order_id_up)
            if m.next_order_id_down and m.next_order_id_down not in existing_ids:
                orders.append({
                    "order_id": m.next_order_id_down,
                    "market": f"{m.label} (Next Window)",
                    "market_slug": m.next_market_slug or m.market_slug or "",
                    "series_slug": m.slug,
                    "token_id": m.next_down_token,
                    "side": "BUY (DOWN)",
                    "price": m.resting_down,
                    "size": m.order_shares,
                    "filled": 0.0,
                    "status": "ADVANCE_PRE_QUOTE",
                    "source": "ENGINE_ADVANCE",
                    "time": m.next_order_time_down if m.next_order_time_down != "-" else now_time_str,
                })
                existing_ids.add(m.next_order_id_down)

        # In paper mode, expose resting simulation orders for active quoting markets
        if self.mode == "paper" and self.is_running:
            for m in self.markets.values():
                if m.pair_captured or m.exit_taken or m.entry_cancelled_timeout:
                    continue
                # Only include active quoting markets with resolved tokens within active window
                if m.status in ("QUOTING", "PRE_QUOTING") and m.up_token and m.down_token:
                    if not m.filled_up:
                        oid_up = f"paper_up_{m.slug}"
                        if oid_up not in existing_ids:
                            orders.append({
                                "order_id": oid_up,
                                "market": m.label,
                                "market_slug": m.market_slug or "",
                                "series_slug": m.slug,
                                "token_id": m.up_token,
                                "side": "BUY (UP)",
                                "price": m.resting_up,
                                "size": m.order_shares,
                                "filled": 0.0,
                                "status": "RESTING",
                                "source": "PAPER_SIMULATION",
                                "time": m.order_time_up if m.order_time_up != "-" else now_time_str,
                            })
                            existing_ids.add(oid_up)
                    if not m.filled_down:
                        oid_dn = f"paper_dn_{m.slug}"
                        if oid_dn not in existing_ids:
                            orders.append({
                                "order_id": oid_dn,
                                "market": m.label,
                                "market_slug": m.market_slug or "",
                                "series_slug": m.slug,
                                "token_id": m.down_token,
                                "side": "BUY (DOWN)",
                                "price": m.resting_down,
                                "size": m.order_shares,
                                "filled": 0.0,
                                "status": "RESTING",
                                "source": "PAPER_SIMULATION",
                                "time": m.order_time_down if m.order_time_down != "-" else now_time_str,
                            })
                            existing_ids.add(oid_dn)

        # Append retained cancelled orders for markets whose window is still
        # live (Issue #113): once a window has settled (end_ts in the past) its
        # cancelled rows are history, not book state, and must not appear in
        # the open-orders list.
        with self._engine_lock:
            now_ts = time.time()
            for m in self.markets.values():
                if m.end_ts and m.end_ts < now_ts:
                    continue
                for c_ord in list(m.cancelled_orders):
                    c_id = c_ord.get("order_id")
                    if c_id and c_id in existing_ids:
                        continue
                    # Issue #90: guarantee the filled key on old retained rows too.
                    retained = c_ord.copy()
                    retained.setdefault("filled", 0.0)
                    orders.append(retained)
                    if c_id:
                        existing_ids.add(c_id)

        # Issue #97: deterministic display rank (live > stop > pre-quote >
        # cancelled, then series order, Up before Down) so every consumer and
        # the dashboard render see the same row order. The render groups rows
        # in first-seen order, which now matches this ranking.
        series_index = {slug: i for i, (slug, _dur, _label) in enumerate(SERIES)}
        orders.sort(key=lambda o: _open_order_sort_key(o, series_index))
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

    def get_open_positions(self) -> List[Dict[str, Any]]:
        """Return currently open positions (dynamic for paper, wallet/clob for live, static for demo)."""
        if self.mode == "live":
            enriched: List[Dict[str, Any]] = []
            for p in self.open_positions:
                p_copy = dict(p)
                if not p_copy.get("market_slug") or not p_copy.get("series_slug"):
                    cid = p_copy.get("conditionId") or p_copy.get("condition_id")
                    asset_id = p_copy.get("asset")
                    for m in self.markets.values():
                        if (cid and m.condition_id == cid) or (asset_id and asset_id in (m.up_token, m.down_token)):
                            if not p_copy.get("market_slug"):
                                p_copy["market_slug"] = m.market_slug or ""
                            if not p_copy.get("series_slug"):
                                p_copy["series_slug"] = m.slug
                            break
                enriched.append(p_copy)
            return enriched

        # Paper mode: dynamically synthesize open positions from active markets
        positions: List[Dict[str, Any]] = []
        now_str = time.strftime("%H:%M:%S")
        for m in self.markets.values():
            if m.pair_captured or m.exit_taken:
                continue
            if m.filled_up:
                entry_px = m.fill_price_up if m.fill_price_up is not None else m.resting_up
                cur_px = m.up_bid if m.up_bid is not None else (m.mid or 0.50)
                init_val = round(float(m.order_shares) * entry_px, 4)
                cur_val = round(float(m.order_shares) * cur_px, 4)
                positions.append({
                    "asset": m.up_token or f"paper_up_{m.slug}",
                    "conditionId": m.condition_id,
                    "market_slug": m.market_slug or "",
                    "series_slug": m.slug,
                    "title": f"{m.label} - {m.market_slug}" if m.market_slug else m.label,
                    "outcome": "Up",
                    "side": "Up",
                    "size": float(m.order_shares),
                    "avgPrice": round(entry_px, 4),
                    "price": round(entry_px, 4),
                    "curPrice": round(cur_px, 4),
                    "initialValue": init_val,
                    "currentValue": cur_val,
                    "cashPnl": round(cur_val - init_val, 4),
                    "time": m.order_time_up if m.order_time_up != "-" else now_str,
                })
            if m.filled_down:
                entry_px = m.fill_price_down if m.fill_price_down is not None else m.resting_down
                down_cur = m.down_bid if m.down_bid is not None else (round(1.0 - (m.mid or 0.50), 4))
                init_val = round(float(m.order_shares) * entry_px, 4)
                cur_val = round(float(m.order_shares) * down_cur, 4)
                positions.append({
                    "asset": m.down_token or f"paper_dn_{m.slug}",
                    "conditionId": m.condition_id,
                    "market_slug": m.market_slug or "",
                    "series_slug": m.slug,
                    "title": f"{m.label} - {m.market_slug}" if m.market_slug else m.label,
                    "outcome": "Down",
                    "side": "Down",
                    "size": float(m.order_shares),
                    "avgPrice": round(entry_px, 4),
                    "price": round(entry_px, 4),
                    "curPrice": round(down_cur, 4),
                    "initialValue": init_val,
                    "currentValue": cur_val,
                    "cashPnl": round(cur_val - init_val, 4),
                    "time": m.order_time_down if m.order_time_down != "-" else now_str,
                })
        if not positions and self.open_positions and not self.is_running:
            return self.open_positions
        return positions

    def get_state(self) -> Dict[str, Any]:
        """Return snapshot of entire trading engine state for the UI."""
        now = time.time()
        env_funder = os.getenv("POLY_FUNDER") or os.getenv("RELAYER_API_KEY_ADDRESS") or ""
        
        # Calculate totals
        realized = self.historical_realized_pnl + sum(m.realized_pnl_usd for m in self.markets.values())
        unrealized = sum(m.unrealized_pnl_usd for m in self.markets.values())
        total_pnl = realized + unrealized
        portfolio_val = self.starting_balance + total_pnl
        
        win_trades = sum(1 for t in self.trades if t.pnl_usd > 0)
        total_trades = len(self.trades)
        win_rate = (win_trades / total_trades * 100.0) if total_trades > 0 else 0.0
        
        # Convert markets to dict. Issue #100: expose win_duration_sec per market
        # so the dashboard can render a time-remaining seeker bar without
        # re-deriving window length client-side.
        mkts_dict = {slug: asdict(state) for slug, state in self.markets.items()}
        for slug, d in mkts_dict.items():
            dur = (d["end_ts"] - d["start_ts"]) if d["end_ts"] > d["start_ts"] else (900.0 if "15m" in slug else 300.0)
            d["win_duration_sec"] = round(dur, 3)
        # Copied under the lock its writer holds, so the dashboard can never read a
        # tally mid-update with `reentries` bumped but the outcome bucket not yet.
        with self._engine_lock:
            reentry_stats_snapshot = dict(self.reentry_stats)
            band_skip_stats_snapshot = dict(self.band_skip_stats)
        
        # Format timeline for chart
        recent_timeline = self.timeline[-300:] if len(self.timeline) > 300 else self.timeline
        
        # Recent trades
        recent_trades = [asdict(t) for t in reversed(self.trades[-50:])]
        
        # Open orders (cached with 5s TTL to avoid blocking requests)
        if now - self._orders_cache_ts > 5.0:
            self._orders_cache = self.get_open_orders_list()
            self._orders_cache_ts = now
        open_orders = self._orders_cache

        open_pos = self.get_open_positions()

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
                (m.order_shares * ((m.fill_price_up if m.fill_price_up is not None else m.resting_up) if m.filled_up else 0) +
                 m.order_shares * ((m.fill_price_down if m.fill_price_down is not None else m.resting_down) if m.filled_down else 0))
                for m in self.markets.values() if not m.pair_captured
            ), 2),
            "params": {
                "offset": self.offset,
                "exit_thresh": self.exit_thresh,
                "exit_thresh_naked": self._naked_exit_thresh(),
                "naked_leg_timeout_pct": self.naked_leg_timeout_pct,
                "reentry_require_pairable": self.reentry_require_pairable,
                "exit_reversal": self.exit_reversal,
                "shares": self.shares,
                "entry_timeout_pct": self.entry_timeout_pct,
                "max_start_elapsed_pct": self.max_start_elapsed_pct,
                "min_requote_remaining_sec": self.min_requote_remaining_sec,
                "reentry_drift_band": self.reentry_drift_band,
                "reentry_min_remaining_pct": self.reentry_min_remaining_pct,
                "max_reentries_per_window": self.max_reentries_per_window,
                "enable_leg_chase": self.enable_leg_chase,
                "max_pair_cost": self.max_pair_cost,
                "entry_delay_sec": self.entry_delay_sec,
                "entry_band": self.entry_band,
                "stop_loss_enabled": self.stop_loss_enabled,
            },
            "active_preset": self.active_preset,
            "reentry_stats": reentry_stats_snapshot,
            "band_skip_stats": band_skip_stats_snapshot,
            "markets": mkts_dict,
            "timeline": recent_timeline,
            "trades": recent_trades,
            "open_orders": open_orders,
            "open_orders_count": len(open_orders),
            "positions": open_pos,
            "open_positions": open_pos,
            "positions_count": len(open_pos),

            "selected_series": [s[0] for s in self.selected_series],
            "available_series": [
                {
                    "slug": s[0],
                    "duration": s[1],
                    "label": s[2],
                    "token": token_for_slug(s[0]),
                    "color": SERIES_COLORS.get(s[0], "#33c9b5"),
                }
                for s in SERIES
            ],
            "stream_bridge": self.stream_bridge.get_status(),
            "server_time": datetime.datetime.now().strftime("%H:%M:%S"),
        }

    def update_config(self, offset: Optional[float] = None,
                      exit_thresh: Optional[float] = None,
                      shares: Optional[int] = None,
                      mode: Optional[str] = None,
                      wallet_address: Optional[str] = None,
                      starting_balance: Optional[float] = None,
                      selected_markets: Optional[Iterable[str]] = None,
                      tokens: Optional[Iterable[str]] = None,
                      durations: Optional[Iterable[int]] = None,
                      entry_timeout_pct: Optional[float] = None,
                      exit_reversal: Optional[float] = None,
                      min_requote_remaining_sec: Optional[float] = None,
                      reentry_drift_band: Optional[float] = None,
                      reentry_min_remaining_pct: Optional[float] = None,
                      max_reentries_per_window: Optional[int] = None,
                      exit_thresh_naked: Optional[float] = None,
                      naked_leg_timeout_pct: Optional[float] = None,
                      reentry_require_pairable: Optional[bool] = None,
                      enable_leg_chase: Optional[bool] = None,
                      max_pair_cost: Optional[float] = None,
                      entry_delay_sec: Optional[float] = None,
                      entry_band: Optional[float] = None,
                      stop_loss_enabled: Optional[bool] = None,
                      preset: Optional[str] = None) -> Dict[str, Any]:
        """Update strategy configuration parameters and market selection.

        Raises:
            ValueError: If the selection is invalid, would deselect a market with an
                open position, or would change the traded market set while running.
                No configuration field is modified when this is raised.
        """
        fetch_live_balance = False
        live_addr = ""

        with self._engine_lock:
            # Issue #137: a named preset fills any knob left unspecified, so
            # the table flows through the same validation, clamping, and
            # market-selection contract as explicit knobs below. Explicit
            # arguments win over the preset table.
            preset_name: Optional[str] = None
            if preset is not None:
                if preset not in LIVE_PRESETS:
                    raise ValueError(
                        f"Unknown preset '{preset}'. Must be one of {sorted(LIVE_PRESETS)}"
                    )
                preset_name = preset
                table = LIVE_PRESETS[preset]
                if offset is None:
                    offset = table["offset"]
                if entry_band is None:
                    entry_band = table["entry_band"]
                if entry_delay_sec is None:
                    entry_delay_sec = table["entry_delay_sec"]
                if stop_loss_enabled is None:
                    stop_loss_enabled = table["stop_loss_enabled"]
                if max_pair_cost is None:
                    max_pair_cost = table["max_pair_cost"]
                if selected_markets is None and tokens is None and durations is None:
                    selected_markets = list(table["selected_markets"])
            # Market selection is resolved and checked before any scalar field is
            # assigned, so a rejected selection leaves the whole configuration untouched.
            new_series = None
            if selected_markets is not None or tokens is not None or durations is not None:
                new_series = _resolve_series_selection(selected_markets, tokens, durations)
                new_slugs = {s[0] for s in new_series}
                # Read is_running under the lock that start()/stop() also take, so a
                # concurrent start cannot slip in between the check and the mutation.
                if self.is_running and new_slugs != set(self.markets.keys()):
                    raise ValueError("Cannot change market selection while the trading bot is running. Stop the bot first.")

            # Guard against modifying scalar strategy parameters while the trading bot is running
            if self.is_running:
                # Each parameter is checked independently. An elif chain would let an
                # unchanged leading parameter mask a changed trailing one: the dashboard
                # always posts `offset`, so a changed entry_timeout_pct went undetected
                # and the "stop the bot first" guard silently failed to fire.
                param_changed = False
                if offset is not None:
                    norm_offset = max(0.001, min(0.490, float(offset)))
                    if abs(norm_offset - self.offset) > 1e-6:
                        param_changed = True
                if exit_thresh is not None and abs(float(exit_thresh) - self.exit_thresh) > 1e-6:
                    param_changed = True
                if shares is not None and int(shares) != self.shares:
                    param_changed = True
                if mode is not None and mode != self.mode:
                    param_changed = True
                if wallet_address is not None and wallet_address.strip() != (self.wallet_address or ""):
                    param_changed = True
                if starting_balance is not None and abs(float(starting_balance) - self.starting_balance) > 1e-6:
                    param_changed = True
                if entry_timeout_pct is not None and abs(float(entry_timeout_pct) - self.entry_timeout_pct) > 1e-6:
                    param_changed = True
                if exit_reversal is not None and abs(float(exit_reversal) - self.exit_reversal) > 1e-6:
                    param_changed = True
                if min_requote_remaining_sec is not None and abs(float(min_requote_remaining_sec) - self.min_requote_remaining_sec) > 1e-6:
                    param_changed = True
                if reentry_drift_band is not None and abs(float(reentry_drift_band) - self.reentry_drift_band) > 1e-6:
                    param_changed = True
                if reentry_min_remaining_pct is not None and abs(float(reentry_min_remaining_pct) - self.reentry_min_remaining_pct) > 1e-6:
                    param_changed = True
                if exit_thresh_naked is not None and abs(float(exit_thresh_naked) - self._naked_exit_thresh()) > 1e-6:
                    param_changed = True
                if naked_leg_timeout_pct is not None and abs(float(naked_leg_timeout_pct) - self.naked_leg_timeout_pct) > 1e-6:
                    param_changed = True
                if reentry_require_pairable is not None and bool(reentry_require_pairable) != self.reentry_require_pairable:
                    param_changed = True
                if max_reentries_per_window is not None and int(max_reentries_per_window) != self.max_reentries_per_window:
                    param_changed = True
                if enable_leg_chase is not None and bool(enable_leg_chase) != self.enable_leg_chase:
                    param_changed = True
                if max_pair_cost is not None and abs(float(max_pair_cost) - self.max_pair_cost) > 1e-6:
                    param_changed = True
                if entry_delay_sec is not None and abs(float(entry_delay_sec) - self.entry_delay_sec) > 1e-6:
                    param_changed = True
                if entry_band is not None and abs(float(entry_band) - self.entry_band) > 1e-6:
                    param_changed = True
                if stop_loss_enabled is not None and bool(stop_loss_enabled) != self.stop_loss_enabled:
                    param_changed = True
                if preset is not None and preset != self.active_preset:
                    param_changed = True

                if param_changed:
                    raise ValueError("Cannot change strategy parameters while the trading bot is running. Stop the bot first.")

            # Only perform parameter mutations when NOT running.
            # Idempotent calls while running leave active strategy parameters intact and unmutated.
            if not self.is_running:
                if new_series is not None:
                    to_remove = [s for s in list(self.markets.keys()) if s not in new_slugs]
                    # Guard against deselecting markets with open positions or active pending exits
                    for s in to_remove:
                        m = self.markets[s]
                        has_unhedged_position = (m.filled_up != m.filled_down) and not m.exit_taken
                        if m.status == "STOP_EXIT_PENDING" or has_unhedged_position:
                            raise ValueError(
                                f"Cannot deselect active market '{s}' with open positions or in-flight exits "
                                f"(status={m.status}, filled_up={m.filled_up}, filled_down={m.filled_down}, exit_taken={m.exit_taken}). "
                                f"Wait for window settlement or stop exit before deselecting."
                            )

                    removed_slugs = set()
                    order_attr_names = (
                        "order_id_up",
                        "order_id_down",
                        "next_order_id_up",
                        "next_order_id_down",
                        "order_id_exit_up",
                        "order_id_exit_down",
                    )
                    for s in to_remove:
                        m = self.markets[s]
                        cancel_failed = False
                        for attr in order_attr_names:
                            oid = getattr(m, attr, None)
                            if oid:
                                try:
                                    success = self.cancel_live_order(oid)
                                    if success:
                                        setattr(m, attr, None)
                                    else:
                                        log.warning("[%s] Failed to cancel order %s on removal", s, oid)
                                        cancel_failed = True
                                        break
                                except Exception as e:
                                    log.warning("[%s] Error cancelling order %s on removal: %s", s, oid, e)
                                    cancel_failed = True
                                    break

                        if cancel_failed:
                            log.error("[%s] Aborting removal of market: order cancellation failed or raised. Retaining market.", s)
                            continue

                        self.historical_realized_pnl += m.realized_pnl_usd
                        del self.markets[s]
                        removed_slugs.add(s)

                    for slug, _dur, label in new_series:
                        if slug not in self.markets:
                            self.markets[slug] = MarketLiveState(
                                slug=slug,
                                label=label,
                                color=SERIES_COLORS.get(slug, "#33c9b5"),
                                order_shares=self.shares,
                                resting_up=round(0.50 - self.offset, 3),
                                resting_down=round(0.50 - self.offset, 3),
                            )

                    # Keep any retained markets that failed cancellation in selected_series
                    retained_series = [s for s in self.selected_series if s[0] in self.markets and s[0] not in {x[0] for x in new_series}]
                    self.selected_series = list(new_series) + retained_series

                if offset is not None:
                    self.offset = max(0.001, min(0.490, float(offset)))
                if exit_thresh is not None and exit_thresh > 0:
                    self.exit_thresh = float(exit_thresh)
                if shares is not None and shares > 0:
                    self.shares = int(shares)
                if mode in ("paper", "live"):
                    if self.mode == "live" and mode == "paper":
                        has_active_live_exposure = any(
                            (m.filled_up or m.filled_down) and not m.pair_captured and not m.exit_taken
                            for m in self.markets.values()
                        ) or bool(self.open_positions)
                        if has_active_live_exposure:
                            raise ValueError("Cannot switch from live to paper mode while unresolved live positions or fills remain")
                    self.mode = mode
                    if self.mode == "paper":
                        self.open_positions = []
                if wallet_address is not None:
                    self.wallet_address = wallet_address.strip()
                    self._clob_client = None  # Reset client if wallet changed

                if self.mode == "live":
                    live_addr = self.wallet_address or os.getenv("POLY_FUNDER") or ""
                    fetch_live_balance = True
                else:
                    if starting_balance is not None and starting_balance >= 0:
                        self.starting_balance = float(starting_balance)
                if entry_timeout_pct is not None:
                    self.entry_timeout_pct = max(0.0, min(1.0, float(entry_timeout_pct)))
                if exit_reversal is not None:
                    # Mercy-rule disarm distance; unified with BacktestParams (issue #111).
                    self.exit_reversal = max(0.001, min(0.50, float(exit_reversal)))
                if min_requote_remaining_sec is not None:
                    self.min_requote_remaining_sec = max(0.0, float(min_requote_remaining_sec))
                if reentry_drift_band is not None:
                    # Clamped to the same 0..0.50 range as `exit_thresh`; 0 disables
                    # re-entry entirely by making the band unreachable for any real mid.
                    self.reentry_drift_band = max(0.0, min(0.50, float(reentry_drift_band)))
                if reentry_min_remaining_pct is not None:
                    # 0 or >= 1.0 falls back to the absolute knob alone: a gate of a
                    # whole window can never be satisfied.
                    self.reentry_min_remaining_pct = max(0.0, min(1.0, float(reentry_min_remaining_pct)))
                if max_reentries_per_window is not None:
                    # Non-negative; 0 disables re-entry entirely via the per-window cap.
                    self.max_reentries_per_window = max(0, int(max_reentries_per_window))
                if exit_thresh_naked is not None:
                    # Issue #124: tighter naked-leg stop; clamped to (0, exit_thresh).
                    # Values at/above the paired stop or <= 0 fall back to exit_thresh
                    # via _naked_exit_thresh(), which is the single read path.
                    self.exit_thresh_naked = max(0.0, min(self.exit_thresh, float(exit_thresh_naked)))
                if naked_leg_timeout_pct is not None:
                    # Fraction of the window after which an unpaired leg force-exits.
                    # 0 disables the timeout.
                    self.naked_leg_timeout_pct = max(0.0, min(1.0, float(naked_leg_timeout_pct)))
                if reentry_require_pairable is not None:
                    self.reentry_require_pairable = bool(reentry_require_pairable)
                if enable_leg_chase is not None:
                    self.enable_leg_chase = bool(enable_leg_chase)
                if max_pair_cost is not None:
                    self.max_pair_cost = max(0.50, min(1.00, float(max_pair_cost)))
                if entry_delay_sec is not None:
                    # Issue #137: seconds into the window before quoting may
                    # start. Negative clamps to 0 (off).
                    self.entry_delay_sec = max(0.0, float(entry_delay_sec))
                if entry_band is not None:
                    # Issue #137: |mid - 0.50| admission band at entry time.
                    # Clamped to the same 0..0.50 range as `exit_thresh`; 0
                    # disables the filter.
                    self.entry_band = max(0.0, min(0.50, float(entry_band)))
                if stop_loss_enabled is not None:
                    self.stop_loss_enabled = bool(stop_loss_enabled)
                # Issue #137: latch only a preset the resulting configuration
                # still matches. An explicit override — or a partially failed
                # market removal that leaves a hybrid universe — yields a
                # custom configuration instead of a misleading preset label.
                if preset_name is not None:
                    self.active_preset = (
                        preset_name if self._preset_matches(preset_name) else None
                    )
                elif self.active_preset is not None and not self._preset_matches(self.active_preset):
                    self.active_preset = None
                # Re-entry is a narrower test than the adverse-open gate, never a
                # looser one: a band at or above `exit_thresh` would let a window
                # re-enter at the very drift the gate exists to reject. Applied
                # unconditionally so lowering `exit_thresh` tightens the band with it.
                self.reentry_drift_band = min(self.reentry_drift_band, self.exit_thresh)

                # Update per-market resting prices
                for m in self.markets.values():
                    m.resting_up = round(0.50 - self.offset, 3)
                    m.resting_down = round(0.50 - self.offset, 3)
                    m.order_shares = self.shares
                    # Issue #89: a stopped config change drops any latched
                    # re-quote round, so the next tick re-anchors from the
                    # static base instead of a stale dynamic price.
                    m.requote_round = 0
                    m.last_requote_telemetry = None

        # Perform remote account fetch outside _engine_lock so network I/O never blocks stop()
        if fetch_live_balance:
            val_info = fetch_polymarket_account_value(live_addr)
            with self._engine_lock:
                current_addr = self.wallet_address or os.getenv("POLY_FUNDER") or ""
                if self.mode == "live" and current_addr == live_addr:
                    if val_info.get("success"):
                        self.starting_balance = float(val_info["net_value"])
                        self.open_positions = val_info.get("positions", [])
                        if not self.wallet_address and val_info.get("wallet_address"):
                            self.wallet_address = val_info["wallet_address"]
                        log.info("Live mode: locked starting balance to Polymarket net account value: $%.2f", self.starting_balance)
                    else:
                        self._schedule_wallet_balance_fetch()

        # get_state() executed outside _engine_lock to prevent order retrieval from blocking engine control
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
                    self.open_positions = info.get("positions", [])
                    self.starting_balance = net_val
                    log.info("Live mode: fetched Polymarket portfolio balance: $%.2f", net_val)
                if not self.wallet_address and info.get("wallet_address"):
                    self.wallet_address = info["wallet_address"]
        except Exception as e:
            log.warning("Could not fetch wallet balance: %s", e)


    def ensure_telemetry_streaming(self) -> None:
        """Ensure stream bridge is running in background observer mode for dashboard telemetry."""
        with self._engine_lock:
            if not self.stream_bridge.is_running:
                self.stream_bridge.start()
            active_tokens: List[str] = []
            for m in self.markets.values():
                for t in (m.up_token, m.down_token, m.next_up_token, m.next_down_token):
                    if t and t not in active_tokens:
                        active_tokens.append(t)
            if active_tokens:
                self.stream_bridge.update_market_tokens(active_tokens)

    def start(self) -> None:
        """Start the background live trading ticker."""
        with self._engine_lock:
            if self.is_running:
                return
            self.quoting_halted = False
            self.is_running = True
        self.ensure_telemetry_streaming()
        self._schedule_wallet_balance_fetch()
        try:
            loop = asyncio.get_running_loop()
            if self._bg_task is None or self._bg_task.done():
                self._bg_task = loop.create_task(self._run_loop())
        except RuntimeError:
            pass
        log.info("LiveTraderEngine started in %s mode", self.mode)

    def stop(self, stop_streams: bool = False) -> None:
        """Stop trading engine and cancel active quoting.

        Args:
            stop_streams: If True, also stop background WebSocket stream bridge.
                Defaults to False so cockpit telemetry continues observing live prices.
        """
        with self._engine_lock:
            self.is_running = False
            if stop_streams:
                self.stream_bridge.stop()
            if self.mode == "live":
                self.cancel_all_orders()
            for m in self.markets.values():
                if m.status in ("QUOTING", "PRE_QUOTING", "LIVE_MONITOR", "STOP_EXIT_PENDING"):
                    m.status = "IDLE"
                    m.last_action = "Stopped"
        # Deliberately NOT flushing `reentry_telemetry` here. `start()` resumes the
        # same in-flight window without resetting per-window state, and the re-entry
        # gate cannot reseed a record mid-window (`adverse_open` is already cleared),
        # so flushing on stop would write a premature "no_fill" and then swallow the
        # real outcome at rollover. The record survives a stop/start and is flushed
        # by `_handle_window_rollover()` when the window actually ends. A record is
        # only lost if the process dies mid-window, which no in-process hook can fix.
        log.info("LiveTraderEngine stopped (streams_active=%s)", self.stream_bridge.is_running)

    def restart(self) -> None:
        """Restart engine and reload markets."""
        self.stop(stop_streams=False)
        self.start()

    def _load_persisted_trades(self):
        """Load trades and metadata from disk if available and restore state."""
        if META_FILE.exists():
            try:
                meta = json.loads(META_FILE.read_text(encoding="utf-8"))
                if "starting_balance" in meta:
                    self.starting_balance = float(meta["starting_balance"])
                if "wallet_address" in meta and meta["wallet_address"]:
                    w = str(meta["wallet_address"]).strip()
                    if w.startswith("0x") and len(w) >= 10:
                        self.wallet_address = w
            except Exception as e:
                log.warning("Failed loading trade metadata: %s", e)
        if not TRADES_FILE.exists():
            return
        try:
            loaded: List[TradeEvent] = []
            for line in TRADES_FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                loaded.append(TradeEvent(**d))
            if loaded:
                self.trades = loaded
                self._recalculate_from_trades()
                log.info("Loaded %d persisted trades from %s", len(self.trades), TRADES_FILE)
        except Exception as e:
            log.warning("Failed loading persisted trades: %s", e)

    def _save_persisted_trades(self):
        """Write all current trades and metadata to disk for persistence."""
        try:
            RUN_DIR.mkdir(parents=True, exist_ok=True)
            with open(TRADES_FILE, "w", encoding="utf-8") as f:
                for t in self.trades:
                    f.write(json.dumps(asdict(t)) + "\n")
            META_FILE.write_text(json.dumps({
                "starting_balance": round(self.starting_balance, 2),
                "wallet_address": self.wallet_address,
                "saved_at": time.time(),
            }, indent=2), encoding="utf-8")
        except Exception as e:
            log.warning("Failed saving persisted trades: %s", e)

    def _recalculate_from_trades(self):
        """Recalculate market stats, totals, and timeline curve from self.trades."""
        cum_pnl = 0.0
        mkt_pnl: Dict[str, float] = defaultdict(float)
        mkt_trades: Dict[str, int] = defaultdict(int)
        mkt_pairs: Dict[str, int] = defaultdict(int)
        mkt_stops: Dict[str, int] = defaultdict(int)
        self.timeline.clear()

        for t in self.trades:
            cum_pnl += t.pnl_usd
            mkt_pnl[t.slug] += t.pnl_usd
            mkt_trades[t.slug] += 1
            if t.action == "PAIR_MERGE":
                mkt_pairs[t.slug] += 1
            elif "STOP" in t.action:
                mkt_stops[t.slug] += 1

            notional_per_market = max(0.01, self.shares * 0.48 * 2.0)
            self.timeline.append({
                "timestamp": int(time.time()),
                "time_str": t.timestamp,
                "portfolio_value": round(self.starting_balance + cum_pnl, 2),
                "total_pnl": round(cum_pnl, 2),
                "total_pnl_pct": round((cum_pnl / max(1.0, self.starting_balance)) * 100.0, 2),
                "pnl_usd": {k: round(v, 2) for k, v in mkt_pnl.items()},
                "pnl_pct": {k: round((v / notional_per_market) * 100.0, 2) for k, v in mkt_pnl.items()},
            })

        for slug, m in self.markets.items():
            m.realized_pnl_usd = round(mkt_pnl[slug], 2)
            m.total_pnl_usd = round(mkt_pnl[slug], 2)
            m.trades_count = mkt_trades[slug]
            m.pairs_count = mkt_pairs[slug]
            m.stops_count = mkt_stops[slug]
            if m.trades_count > 0:
                m.last_action = f"PnL: ${m.total_pnl_usd:+.2f} ({m.trades_count} fills)"

        unselected_pnl = sum(v for k, v in mkt_pnl.items() if k not in self.markets)
        self.historical_realized_pnl = round(unselected_pnl, 2)
        self.total_realized_pnl = round(cum_pnl, 2)
        self.total_pnl = round(cum_pnl, 2)
        self.total_pairs_merged = sum(mkt_pairs.values())
        self.total_stops_triggered = sum(mkt_stops.values())
        self.current_portfolio_value = round(self.starting_balance + cum_pnl, 2)

    def sync_wallet_trades(
        self,
        wallet_address: Optional[str] = None,
        start_marker: Optional[str] = None,
        fallback_cash: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Fetch historical activities and trades from Polymarket Data API starting from the requested run."""
        addr = (wallet_address or "").strip() or (self.wallet_address or "").strip() or (os.getenv("POLY_FUNDER") or "").strip()
        if not addr or not addr.startswith("0x") or len(addr) < 10:
            return {"success": False, "error": "No valid wallet address provided"}

        sess = _get_thread_session()
        activities = []
        try:
            r = sess.get(f"https://data-api.polymarket.com/activity?user={addr}&limit=500", timeout=(4.0, 10.0))
            if r.ok:
                activities = r.json()
        except Exception as e:
            log.warning("Could not fetch activity endpoint: %s", e)

        if not isinstance(activities, list) or not activities:
            try:
                r = sess.get(f"https://data-api.polymarket.com/trades?user={addr}&limit=200", timeout=(4.0, 10.0))
                if r.ok:
                    raw_trades = r.json()
                    if isinstance(raw_trades, list):
                        activities = []
                        for t in raw_trades:
                            norm = dict(t)
                            norm.setdefault("type", "TRADE")
                            sz = float(t.get("size", 0.0))
                            px = float(t.get("price", 0.0))
                            norm.setdefault("usdcSize", round(sz * px, 4))
                            activities.append(norm)
            except Exception as e:
                log.error("Failed fetching trades fallback: %s", e)
                return {"success": False, "error": str(e)}

        if not isinstance(activities, list) or not activities:
            return {"success": True, "trades_count": 0, "total_pnl": self.total_pnl, "message": "No trades found"}

        activities.sort(key=lambda x: x.get("timestamp", 0))

        # Look for the user's specific starting trade if marker provided (default "1788380100" if present)
        marker = start_marker if start_marker is not None else "1788380100"
        start_idx = 0
        if marker:
            found = False
            for i, a in enumerate(activities):
                slug_cand = str(a.get("slug", ""))
                if marker in slug_cand and a.get("outcome") == "Down" and a.get("side") == "BUY":
                    start_idx = i
                    found = True
                    break
            if not found:
                return {"success": False, "error": f"Start marker '{marker}' not found in wallet activities"}

        session_acts = activities[start_idx:]

        # Calculate exact cash flow to infer starting balance before this trade
        net_cash_flow = 0.0
        for a in session_acts:
            atype = a.get("type")
            side = a.get("side", "")
            usdc = float(a.get("usdcSize", 0.0))
            if atype == "TRADE":
                if side == "BUY":
                    net_cash_flow -= usdc
                elif side == "SELL":
                    net_cash_flow += usdc
            elif atype in ("MERGE", "REDEEM"):
                net_cash_flow += usdc

        # Fetch current balance
        acct_val = fetch_polymarket_account_value(addr)
        if not acct_val.get("success"):
            if fallback_cash is not None:
                current_cash = fallback_cash
            else:
                return {
                    "success": False,
                    "error": f"Failed fetching account balance for {addr}: {acct_val.get('error', 'unknown error')}",
                }
        else:
            current_cash = float(acct_val.get("cash_balance", 0.0))

        inferred_start = round(current_cash - net_cash_flow, 2)
        self.starting_balance = inferred_start

        def _to_series_slug(title: str, slug: str):
            """Map title and slug strings to canonical 5m series slug and display label."""
            t = (title + " " + slug).lower()
            if "bitcoin" in t or "btc" in t:
                return "btc-up-or-down-5m", "BTC 5m"
            if "ethereum" in t or "eth" in t:
                return "eth-up-or-down-5m", "ETH 5m"
            if "solana" in t or "sol" in t:
                return "sol-up-or-down-5m", "SOL 5m"
            if "bnb" in t:
                return "bnb-up-or-down-5m", "BNB 5m"
            if "xrp" in t:
                return "xrp-up-or-down-5m", "XRP 5m"
            return None, None

        by_window = defaultdict(list)
        for a in session_acts:
            wid = a.get("conditionId") or a.get("slug")
            by_window[wid].append(a)

        new_events: List[Tuple[int, TradeEvent]] = []
        for wid, acts in by_window.items():
            acts.sort(key=lambda x: x.get("timestamp", 0))
            first = acts[0]
            title = first.get("title", "")
            slug_name = first.get("slug", "")
            series_slug, label = _to_series_slug(title, slug_name)
            if not series_slug:
                continue

            buys_cost = 0.0
            buys_shares = 0.0
            sells_proceeds = 0.0
            sells_shares = 0.0
            merges_proceeds = 0.0
            redeems_proceeds = 0.0

            up_bought = 0.0
            down_bought = 0.0
            up_cost = 0.0
            down_cost = 0.0

            last_ts = first.get("timestamp", 0)

            for a in acts:
                atype = a.get("type")
                side = a.get("side", "")
                outcome = a.get("outcome", "")
                sz = float(a.get("size", 0.0))
                usdc = float(a.get("usdcSize", 0.0))
                last_ts = max(last_ts, a.get("timestamp", 0))

                if atype == "TRADE":
                    if side == "BUY":
                        buys_cost += usdc
                        buys_shares += sz
                        if outcome == "Up":
                            up_bought += sz
                            up_cost += usdc
                        elif outcome == "Down":
                            down_bought += sz
                            down_cost += usdc
                    elif side == "SELL":
                        sells_proceeds += usdc
                        sells_shares += sz
                elif atype == "MERGE":
                    merges_proceeds += usdc
                elif atype == "REDEEM":
                    redeems_proceeds += usdc

            total_proceeds = sells_proceeds + merges_proceeds + redeems_proceeds
            window_pnl = total_proceeds - buys_cost

            if merges_proceeds > 0:
                action = "PAIR_MERGE"
                notes = f"Merged pair for ${merges_proceeds:.2f}"
            elif sells_proceeds > 0 and redeems_proceeds == 0:
                if window_pnl >= 0:
                    action = "TAKE_PROFIT"
                    notes = f"Sold early +${window_pnl:.2f}"
                else:
                    action = "STOP_EXIT"
                    notes = f"Stop Loss exit -${abs(window_pnl):.2f}"
            elif redeems_proceeds > 0:
                action = "WINDOW_SETTLE"
                notes = f"Won & redeemed ${redeems_proceeds:.2f}"
            else:
                action = "EXPIRED"
                notes = f"Expired out of money (-${buys_cost:.2f})"

            entry_up = round(up_cost / up_bought, 3) if up_bought > 0 else None
            entry_down = round(down_cost / down_bought, 3) if down_bought > 0 else None
            time_str = datetime.datetime.fromtimestamp(last_ts).strftime("%H:%M:%S")

            m_obj = self.markets.get(series_slug)
            slug_name = str(first.get("slug") or "")
            ev_mkt_slug = slug_name or (m_obj.market_slug if (m_obj and m_obj.market_slug) else "")

            ev = TradeEvent(
                id=f"{series_slug}_{last_ts}",
                timestamp=time_str,
                slug=series_slug,
                label=label,
                action=action,
                shares=int(round(buys_shares or 5)),
                entry_price_up=entry_up,
                entry_price_down=entry_down,
                exit_price=round(total_proceeds / max(0.1, buys_shares), 3) if buys_shares > 0 else None,
                pnl_usd=round(window_pnl, 2),
                pnl_pct=round((window_pnl / max(0.01, buys_cost)) * 100.0, 1),
                notes=notes,
                market_slug=ev_mkt_slug,
            )
            new_events.append((last_ts, ev))

        new_events.sort(key=lambda x: x[0])
        with self._engine_lock:
            self.trades = [ev for _, ev in new_events]
            self._recalculate_from_trades()
            self._save_persisted_trades()

        return {
            "success": True,
            "trades_count": len(self.trades),
            "starting_balance": self.starting_balance,
            "current_portfolio_value": self.current_portfolio_value,
            "total_pnl": self.total_pnl,
            "pairs_merged": self.total_pairs_merged,
            "stops_triggered": self.total_stops_triggered,
        }

    @staticmethod
    def _market_has_orders(m: MarketLiveState) -> bool:
        """Return True if one market holds any order handle or retained rows.

        Covers entry legs, advance pre-quotes, the resting stop-loss, exit
        legs, and the retained `cancelled_orders` list (issue #93).
        Deliberately conservative: any handle counts, even one the dashboard
        would hide, so the live-running refusal errs toward safety.
        """
        return bool(
            m.order_id_up
            or m.order_id_down
            or m.next_order_id_up
            or m.next_order_id_down
            or m.stop_order_id
            or m.order_id_exit_up
            or m.order_id_exit_down
            or m.cancelled_orders
        )

    def _has_outstanding_orders(self) -> bool:
        """Return True if any market holds an order handle or retained rows.

        Caller must hold `_engine_lock`.
        """
        return any(self._market_has_orders(m) for m in self.markets.values())

    @staticmethod
    def _clear_market_order_state(m: MarketLiveState) -> None:
        """Clear every order handle on one market (issue #93).

        Caller must hold `_engine_lock`. Mirrors the local-handle clearing in
        `cancel_all_orders()` but fully empties `cancelled_orders` and resets
        statuses to `"NONE"` so no FILLED/RESTING row can survive a reset.
        """
        m.cancelled_orders = []
        m.order_id_up = None
        m.order_id_down = None
        m.order_status_up = "NONE"
        m.order_status_down = "NONE"
        m.order_time_up = "-"
        m.order_time_down = "-"
        m.entry_cancelled_timeout = False
        m.next_order_id_up = None
        m.next_order_id_down = None
        m.next_order_time_up = "-"
        m.next_order_time_down = "-"
        m.next_quoted = False
        m.stop_order_id = None
        m.stop_order_status = "NONE"
        m.stop_price = None
        m.stop_side = None
        m.stop_order_time = "-"
        m.order_id_exit_up = None
        m.order_id_exit_down = None
        m.order_status_exit_up = "NONE"
        m.order_status_exit_down = "NONE"
        # Issue #89: a reset market restarts at round 0 with no stale telemetry.
        m.requote_round = 0
        m.last_requote_telemetry = None

    def reset_pnl(self) -> Dict[str, Any]:
        """Reset session PnL, trade history, and outstanding order state.

        Cancel-and-clear (issue #93): every market's order handles are emptied
        so `get_open_orders_list()` comes back empty, and the 5s orders cache
        is invalidated. Returns a result dict; callers that ignore it keep
        working as before.

        Live-mode safety net: when `mode == "live"` and the engine is running
        with outstanding orders, the reset is REFUSED (nothing is cleared and
        no venue cancel is fired) so real-money orders are never cancelled
        behind the operator's back — Stop first, then reset. When live but
        stopped, venue-side orders are cancelled before the local handles are
        dropped; a missing CLOB client or any cancel failure also refuses
        without clearing, so no order id is ever dropped without a cancel
        attempt.
        """
        with self._engine_lock:
            hot = self.mode == "live" and self.is_running and self._has_outstanding_orders()
        if hot:
            return {
                "ok": False,
                "refused": True,
                "venue_cancelled": False,
                "markets_cleared": 0,
                "message": (
                    "Stop the engine before RESET P&L while orders are "
                    "outstanding — press Stop first (Stop cancels live orders), "
                    "then reset."
                ),
            }
        venue_cancelled = False
        with self._engine_lock:
            needs_venue_cancel = self.mode == "live" and self._has_outstanding_orders()
            if needs_venue_cancel:
                oids = [
                    oid
                    for m in self.markets.values()
                    for oid in (
                        m.order_id_up,
                        m.order_id_down,
                        m.next_order_id_up,
                        m.next_order_id_down,
                        m.stop_order_id,
                        m.order_id_exit_up,
                        m.order_id_exit_down,
                    )
                    if oid
                ]
        if needs_venue_cancel:
            client = self.get_clob_client()
            if client is None:
                log.error("reset_pnl: live reset refused — no CLOB client available")
                return {
                    "ok": False,
                    "refused": False,
                    "venue_cancelled": False,
                    "markets_cleared": 0,
                    "error": "No CLOB client available — cannot cancel live orders; reset refused.",
                }
            try:
                if hasattr(client, "cancel_all"):
                    cancel_res = client.cancel_all()
                    log.info("reset_pnl: venue cancel_all invoked on Polymarket CLOB")
                    if not self._cancel_succeeded(cancel_res):
                        log.error("reset_pnl: venue cancel_all reported failure: %s", cancel_res)
                        return {
                            "ok": False,
                            "refused": False,
                            "venue_cancelled": False,
                            "markets_cleared": 0,
                            "error": "Venue cancel reported failure; reset refused.",
                        }
                else:
                    failures = [oid for oid in oids if not self.cancel_live_order(oid)]
                    if failures:
                        log.error("reset_pnl: per-order venue cancel failed for %s", failures)
                        return {
                            "ok": False,
                            "refused": False,
                            "venue_cancelled": False,
                            "markets_cleared": 0,
                            "error": (
                                f"Venue cancel failed for {len(failures)} order(s); "
                                "reset refused."
                            ),
                        }
                venue_cancelled = True
            except Exception as e:
                log.error("reset_pnl: venue cancel failed: %s", e)
                return {
                    "ok": False,
                    "refused": False,
                    "venue_cancelled": False,
                    "markets_cleared": 0,
                    "error": str(e),
                }
        if TRADES_FILE.exists() and not os.getenv("PYTEST_CURRENT_TEST"):
            try:
                TRADES_FILE.unlink()
            except Exception as e:
                log.warning("Could not delete %s: %s", TRADES_FILE, e)
        if META_FILE.exists() and not os.getenv("PYTEST_CURRENT_TEST"):
            try:
                META_FILE.unlink()
            except Exception as e:
                log.warning("Could not delete %s: %s", META_FILE, e)
        if REENTRY_FILE.exists() and not os.getenv("PYTEST_CURRENT_TEST"):
            try:
                REENTRY_FILE.unlink()
            except Exception as e:
                log.warning("Could not delete %s: %s", REENTRY_FILE, e)
        with self._engine_lock:
            if self.mode == "live" and self.is_running and self._has_outstanding_orders():
                return {
                    "ok": False,
                    "refused": True,
                    "venue_cancelled": False,
                    "markets_cleared": 0,
                    "message": (
                        "Stop the engine before RESET P&L while orders are "
                        "outstanding — press Stop first (Stop cancels live orders), "
                        "then reset."
                    ),
                }
            self.trades.clear()
            self.timeline.clear()
            self.open_positions.clear()
            self.session_start_ts = time.time()
            self.historical_realized_pnl = 0.0
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
                m.open_mid = None
                m.open_drift = 0.0
                m.adverse_open = False
                m.open_gate_evaluated = False
                # Issue #138: fill-telemetry rest context resets with the
                # other per-window gate state (mirrors the rollover block).
                m.rest_up_price = None
                m.rest_up_queue = None
                m.rest_up_ts = None
                m.rest_dn_price = None
                m.rest_dn_queue = None
                m.rest_dn_ts = None
                m.last_bids_up = {}
                m.last_bids_down = {}
                m.fill_telemetry_done_up = False
                m.fill_telemetry_done_down = False
                # Issue #137: the entry-band gate resets with the other
                # per-window gates so the next window re-evaluates it.
                m.band_gate_evaluated = False
                m.band_skip = False
                m.first_seen_start_ts = None
                m.first_tick_elapsed_sec = None
                m.late_start_skip = False
                m.reentry_count = 0
                m.reentry_mid = None
                m.reentry_drift = None
                m.reentry_telemetry = None
                m.status = "QUOTING" if self.is_running else "IDLE"
                m.last_action = "PnL Reset"
            self.reentry_stats = _empty_reentry_stats()
            # Issue #137: band-skip telemetry resets with the re-entry tally.
            self.band_skip_stats = _empty_band_skip_stats()
            cleared_count = 0
            for m in self.markets.values():
                if self._market_has_orders(m):
                    cleared_count += 1
                self._clear_market_order_state(m)
            self._orders_cache_ts = 0.0
        self._record_timeline_point(time.time())
        return {
            "ok": True,
            "refused": False,
            "venue_cancelled": venue_cancelled,
            "markets_cleared": cleared_count,
        }

    def seed_demo_data(self):
        """Populate realistic demo simulation trades, timeline curve, and market states."""
        if self.is_running:
            self.stop()
        now = time.time()
        self.trades.clear()
        self.timeline.clear()
        self._orders_cache_ts = 0.0
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
        self.open_positions = [
            {
                "asset": "0x1234567890abcdef1",
                "conditionId": "0xabcdef1234567890",
                "market_slug": "eth-up-or-down-5m",
                "series_slug": "eth-up-or-down-5m",
                "size": 5.0,
                "avgPrice": 0.485,
                "curPrice": 0.510,
                "cashPnl": 0.125,
                "title": "ETH Up or Down 5m",
                "outcome": "Up",
            },
        ]

        m_btc = self.markets.get("btc-up-or-down-5m")
        if m_btc:
            m_btc.realized_pnl_usd = 0.40
            m_btc.total_pnl_usd = 0.40
            m_btc.pairs_count = 2
            m_btc.trades_count = 2
            m_btc.status = "QUOTING"
            m_btc.last_action = "Quoting bids @ 0.48 / 0.48"
            m_btc.order_id_up = f"demo_ord_btc_up_{int(now)}"
            m_btc.order_status_up = "LIVE"
            m_btc.order_time_up = datetime.datetime.fromtimestamp(now - 15).strftime("%H:%M:%S")
            m_btc.order_id_down = f"demo_ord_btc_dn_{int(now)}"
            m_btc.order_status_down = "LIVE"
            m_btc.order_time_down = datetime.datetime.fromtimestamp(now - 15).strftime("%H:%M:%S")

        m_eth = self.markets.get("eth-up-or-down-5m")
        if m_eth:
            m_eth.realized_pnl_usd = 0.40
            m_eth.total_pnl_usd = 0.40
            m_eth.pairs_count = 2
            m_eth.trades_count = 2
            m_eth.status = "FILLED_UP"
            m_eth.filled_up = True
            m_eth.resting_up = 0.48
            m_eth.fill_price_up = 0.485
            m_eth.last_action = "Filled UP 5 shares @ 0.485"

        m_sol = self.markets.get("sol-up-or-down-5m")
        if m_sol:
            m_sol.realized_pnl_usd = -0.25
            m_sol.total_pnl_usd = -0.25
            m_sol.stops_count = 1
            m_sol.trades_count = 1
            m_sol.status = "STOP_EXIT"
            m_sol.exit_taken = True
            m_sol.fill_price_up = 0.48
            m_sol.last_action = "Stop Loss UP @ 0.43 (-$0.25)"

        m_bnb = self.markets.get("bnb-up-or-down-5m")
        if m_bnb:
            m_bnb.realized_pnl_usd = 0.20
            m_bnb.total_pnl_usd = 0.20
            m_bnb.pairs_count = 1
            m_bnb.trades_count = 1
            m_bnb.status = "PAIR_MERGED"
            m_bnb.pair_captured = True
            m_bnb.filled_up = True
            m_bnb.filled_down = True
            m_bnb.fill_price_up = 0.48
            m_bnb.fill_price_down = 0.48
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
        """Process one tick cycle across the configured active markets."""
        now = get_real_utc_time()
        loop = asyncio.get_running_loop()

        # Single snapshot taken at top of tick for dispatch and results
        markets_snapshot = list(self.markets.items())

        tasks = [
            loop.run_in_executor(None, self._poll_single_market, slug)
            for slug, _ in markets_snapshot
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        async with self._lock:
            for (slug, _), res in zip(markets_snapshot, results):
                if slug not in self.markets:
                    continue
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

    def _preset_matches(self, name: str) -> bool:
        """True when the live knob values equal the named preset table.

        Issue #137: keeps `active_preset` honest — a manual knob change that
        diverges from the table clears the latch in update_config().
        """
        table = LIVE_PRESETS.get(name)
        if table is None:
            return False
        if abs(self.offset - float(table["offset"])) > 1e-9:
            return False
        if abs(self.entry_band - float(table["entry_band"])) > 1e-9:
            return False
        if abs(self.entry_delay_sec - float(table["entry_delay_sec"])) > 1e-9:
            return False
        if self.stop_loss_enabled != bool(table["stop_loss_enabled"]):
            return False
        if abs(self.max_pair_cost - float(table["max_pair_cost"])) > 1e-9:
            return False
        if {s[0] for s in self.selected_series} != set(table["selected_markets"]):
            return False
        return True

    def _naked_exit_thresh(self) -> float:
        """Adverse-drift stop distance for a single (naked) leg — issue #124.

        A naked leg exits at `exit_thresh_naked` (tighter than the paired
        `exit_thresh`); a config of 0 or a value above `exit_thresh` falls back
        to `exit_thresh` so the knob can never loosen risk beyond the paired
        stop.
        """
        naked = self.exit_thresh_naked
        if naked is None or naked <= 0 or naked >= self.exit_thresh:
            return self.exit_thresh
        return naked

    def _naked_timeout_elapsed(self, mstate: MarketLiveState, now: float, win_duration: float) -> bool:
        """True when a naked leg has exceeded `naked_leg_timeout_pct` of its window.

        Issue #124: bounds the worst case where the mid hovers just inside the
        stop so the leg rides to the wall. 0 disables. The clock starts when the
        leg went naked (`naked_since_ts`), not at window open, so a late fill
        still gets its full horizon and a just-completed pair is never killed.
        Returns False when both legs are filled (no naked exposure), the fill
        time is unknown, or the window geometry is unknown.
        """
        if self.naked_leg_timeout_pct is None or self.naked_leg_timeout_pct <= 0:
            return False
        if not ((mstate.filled_up and not mstate.filled_down) or (mstate.filled_down and not mstate.filled_up)):
            return False
        if mstate.exit_taken or mstate.pair_captured:
            return False
        if win_duration <= 0 or not mstate.naked_since_ts:
            return False
        naked_elapsed = max(0.0, now - mstate.naked_since_ts)
        return naked_elapsed >= self.naked_leg_timeout_pct * win_duration

    def _reentry_min_remaining_sec(self, win_duration: float) -> float:
        """Seconds of window that must remain for a drift-skipped window to re-enter.

        The tighter of issue #89's shared `min_requote_remaining_sec` and
        `reentry_min_remaining_pct` of this window's own duration: 90s on a 5m
        window, 270s on a 15m one, at stock settings.

        Issue #124: when `reentry_require_pairable` is on, a re-entry must also
        leave enough time for a fresh two-leg entry to pair before the naked
        timeout would fire -- a full quoting round needs roughly the naked
        timeout worth of window (entry may sit unfilled for that long and still
        end paired), so the gate is the tighter of the existing gates and the
        timeout horizon. This prevents re-entry from opening a position that can
        only ever fill one leg.
        """
        gate = self.min_requote_remaining_sec
        if win_duration > 0 and 0.0 < self.reentry_min_remaining_pct <= 1.0:
            gate = min(gate, self.reentry_min_remaining_pct * win_duration)
        if self.reentry_require_pairable and win_duration > 0:
            if self.naked_leg_timeout_pct and 0.0 < self.naked_leg_timeout_pct <= 1.0:
                # Horizon before the timeout fires, not the timeout itself: a
                # re-entry needs (1 - timeout_pct) of the window left so a fresh
                # entry can pair before the naked timeout would kill one leg.
                # Rounded to dodge float dust (300 * 0.30000000000000004).
                gate = max(gate, round((1.0 - self.naked_leg_timeout_pct) * win_duration, 6))
        return gate

    def _maybe_reenter_drift_skipped(
        self,
        mstate: MarketLiveState,
        slug: str,
        mid: float,
        remaining_sec: float,
        win_duration: float,
        book_two_sided: bool,
        is_late_start: bool,
        now: float,
        resting_up: float,
        resting_down: float,
    ) -> bool:
        """Re-enter a window the adverse-open gate skipped, once the mid reverts.

        Issue #95. The gate (issue #92) is a one-shot decision taken from the
        opening book, and until now it was terminal: one adverse snapshot latched
        the market out for the whole window even when the skew closed minutes later.
        This re-opens entry for the remainder of the window when the live mid has
        come back within `reentry_drift_band` of 0.50 and at least
        `_reentry_min_remaining_sec()` is left to fill and pair both legs.

        The condition is `mstate.adverse_open`, never `entry_cancelled_timeout`
        alone: that latch is shared with the entry timeout (`is_late_start`) and
        with the issue #96 late-start skip, and neither of those windows may be
        resurrected here. `open_mid` / `open_drift` / `open_gate_evaluated` are left
        untouched -- the operator keeps seeing what the window actually opened at,
        and re-entry is itself a stricter test of the live mid than re-running the
        gate would be (the band is well inside `exit_thresh`).

        Returns True when the window was re-entered on this tick.
        """
        if not mstate.adverse_open or not mstate.entry_cancelled_timeout:
            return False
        if mstate.late_start_skip or is_late_start:
            return False
        if mstate.filled_up or mstate.filled_down or mstate.pair_captured or mstate.exit_taken:
            return False
        if self.quoting_halted or not book_two_sided:
            return False
        if mstate.reentry_count >= self.max_reentries_per_window:
            return False
        min_remaining_sec = self._reentry_min_remaining_sec(win_duration)
        if remaining_sec < min_remaining_sec:
            return False
        drift = abs(mid - 0.50)
        # Re-entry may never be looser than the gate it undoes. A band at or above
        # `exit_thresh` would re-quote into exactly the skew the gate rejected, so
        # the effective band is capped here as well as clamped in `update_config()`
        # -- the attribute is also writable directly.
        band = min(self.reentry_drift_band, self.exit_thresh)
        # `reentry_drift_band == 0` is documented as "disabled". Without this guard
        # a two-sided mid of exactly 0.50 has drift 0 and would pass the
        # `drift > band` test below, placing orders despite the disabled setting.
        if band <= 0:
            return False
        # `>= exit_thresh` is what the gate rejects, so re-entry must stay strictly
        # inside it -- otherwise a band configured at exactly `exit_thresh` would
        # re-enter at the very drift that skipped the window.
        if drift > band or drift >= self.exit_thresh:
            return False

        open_mid_txt = f"{mstate.open_mid:.4f}" if mstate.open_mid is not None else "n/a"
        with self._engine_lock:
            mstate.entry_cancelled_timeout = False
            mstate.adverse_open = False
            mstate.reentry_count += 1
            mstate.reentry_mid = mid
            mstate.reentry_drift = drift
            # Stale handles from the cancelled entry would suppress placement below;
            # the `cancelled_orders` rows stay as history.
            mstate.order_id_up = None
            mstate.order_id_down = None
            mstate.order_status_up = "NONE"
            mstate.order_status_down = "NONE"
            mstate.order_time_up = "-"
            mstate.order_time_down = "-"
            mstate.status = "QUOTING"
            mstate.reentry_telemetry = {
                "reentry_index": mstate.reentry_count,
                "slug": mstate.slug,
                "series_label": mstate.label,
                "window_start_ts": mstate.start_ts,
                "window_duration_sec": round(win_duration, 1),
                "open_mid": mstate.open_mid,
                "open_drift": round(mstate.open_drift, 4),
                "reentry_mid": round(mid, 4),
                "reentry_drift": round(drift, 4),
                "effective_band": round(band, 4),
                "remaining_sec": round(remaining_sec, 1),
                "min_remaining_sec": round(min_remaining_sec, 1),
                "quoted": {"up": resting_up, "down": resting_down},
                "decided_at": datetime.datetime.fromtimestamp(now).strftime("%H:%M:%S"),
                "perf_start": time.perf_counter(),
                "mid_at_resting": None,
                "latency_ms": None,
                "resting_drift": None,
                "filled_up": None,
                "filled_down": None,
                "pair_captured": None,
                "exit_taken": None,
                "outcome": None,
            }
            mstate.last_action = (
                f"Re-entered after drift reverted (open {open_mid_txt}, drift "
                f"{mstate.open_drift:.3f} -> mid {mid:.4f}, drift {drift:.3f} <= "
                f"{self.reentry_drift_band:.3f}, {remaining_sec:.0f}s left) "
                f"— re-entry {mstate.reentry_count}/{self.max_reentries_per_window}"
            )
        log.info(
            "[%s] Re-entering drift-skipped window (open_mid=%s, open_drift=%.3f, "
            "mid=%.4f, drift=%.3f <= %.3f, remaining=%.0fs, re-entry %d/%d)",
            slug, open_mid_txt, mstate.open_drift, mid, drift,
            self.reentry_drift_band, remaining_sec,
            mstate.reentry_count, self.max_reentries_per_window,
        )
        return True

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

        # 2. Advance Pre-Quoting on Next Window (T+1) (live CLOB or paper simulation)
        # Issue #137: suspended while the entry controls are armed — a
        # pre-quoted T+1 window would otherwise roll over with order IDs set,
        # bypassing both the entry delay and the band evaluation.
        entry_controls_armed = self.entry_delay_sec > 0 or self.entry_band > 0
        if (self.is_running and not self.quoting_halted and not entry_controls_armed
                and mstate.next_condition_id and not mstate.next_quoted):
            resting_up = round(0.50 - self.offset, 3)
            resting_down = round(0.50 - self.offset, 3)
            if self.mode == "live":
                if mstate.next_up_token and not mstate.next_order_id_up:
                    res_up = self.place_live_quote(mstate.next_up_token, resting_up, self.shares, "BUY")
                    if res_up and res_up.get("order_id"):
                        mstate.next_order_id_up = res_up["order_id"]
                        mstate.next_order_time_up = time.strftime("%H:%M:%S")
                if mstate.next_down_token and not mstate.next_order_id_down:
                    res_dn = self.place_live_quote(mstate.next_down_token, resting_down, self.shares, "BUY")
                    if res_dn and res_dn.get("order_id"):
                        mstate.next_order_id_down = res_dn["order_id"]
                        mstate.next_order_time_down = time.strftime("%H:%M:%S")
                if mstate.next_order_id_up and mstate.next_order_id_down:
                    mstate.next_quoted = True
                    log.info("[%s] ADVANCE PRE-QUOTING (live) active on %s (UP: %s, DN: %s)", slug, mstate.next_market_slug, mstate.next_order_id_up, mstate.next_order_id_down)
            else:
                mstate.next_order_id_up = f"paper_up_{mstate.next_market_slug or slug}"
                mstate.next_order_id_down = f"paper_dn_{mstate.next_market_slug or slug}"
                mstate.next_order_time_up = time.strftime("%H:%M:%S")
                mstate.next_order_time_down = time.strftime("%H:%M:%S")
                mstate.next_quoted = True
                log.info("[%s] ADVANCE PRE-QUOTING (paper) active on %s", slug, mstate.next_market_slug)

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

        # Target resting prices. Anchor initial quotes dynamically and symmetrically to
        # each leg's live mid minus offset (up_mid - offset, down_mid - offset).
        # Once orders are placed or a window has merged and re-quoted, prices latch.
        if mstate.requote_round <= 0:
            if not mstate.order_id_up and not mstate.order_id_down and not mstate.filled_up and not mstate.filled_down:
                u_m = up_mid if 'up_mid' in locals() and up_mid is not None else 0.50
                d_m = down_mid if 'down_mid' in locals() and down_mid is not None else 0.50
                resting_up = round(min(0.99, max(0.01, u_m - self.offset)), 3)
                resting_down = round(min(0.99, max(0.01, d_m - self.offset)), 3)
                mstate.resting_up = resting_up
                mstate.resting_down = resting_down
            else:
                resting_up = mstate.resting_up
                resting_down = mstate.resting_down
        else:
            resting_up = mstate.resting_up
            resting_down = mstate.resting_down
        mstate.order_shares = self.shares

        # --- LEG CHASE AFTER ONE-SIDED FILL (Issue #123) ---
        # When one leg fills, step up the opposite leg's quote towards the ask,
        # strictly capped so pair cost stays <= max_pair_cost (default 0.98).
        if self.enable_leg_chase and not mstate.pair_captured and not mstate.exit_taken:
            if mstate.filled_up and not mstate.filled_down:
                entry_up = mstate.fill_price_up if mstate.fill_price_up is not None else resting_up
                # Floor strictly to cent precision so entry + opposite never exceeds max_pair_cost
                max_down_bid = round(math.floor((self.max_pair_cost - entry_up + 1e-9) * 100) / 100.0, 2)
                if mstate.down_ask is not None:
                    target_down = min(mstate.down_ask, max_down_bid)
                    if target_down > resting_down:
                        resting_down = target_down
                        mstate.resting_down = resting_down
                        mstate.chased_leg = "DOWN"
                    elif target_down == max_down_bid and target_down >= resting_down:
                        if max_down_bid > resting_down:
                            resting_down = max_down_bid
                            mstate.resting_down = resting_down
                            mstate.chased_leg = "DOWN"
            elif mstate.filled_down and not mstate.filled_up:
                entry_dn = mstate.fill_price_down if mstate.fill_price_down is not None else resting_down
                # Floor strictly to cent precision so entry + opposite never exceeds max_pair_cost
                max_up_bid = round(math.floor((self.max_pair_cost - entry_dn + 1e-9) * 100) / 100.0, 2)
                if mstate.up_ask is not None:
                    target_up = min(mstate.up_ask, max_up_bid)
                    if target_up > resting_up:
                        resting_up = target_up
                        mstate.resting_up = resting_up
                        mstate.chased_leg = "UP"
                    elif target_up == max_up_bid and target_up >= resting_up:
                        if max_up_bid > resting_up:
                            resting_up = max_up_bid
                            mstate.resting_up = resting_up
                            mstate.chased_leg = "UP"
            else:
                mstate.chased_leg = None
        else:
            mstate.chased_leg = None

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

        # Determine window duration & elapsed time (Issue #48)
        win_duration = (mstate.end_ts - mstate.start_ts) if (mstate.end_ts > mstate.start_ts) else (900.0 if "15m" in slug else 300.0)
        elapsed_sec = max(0.0, now - mstate.start_ts) if mstate.start_ts > 0 else (win_duration - mstate.time_remaining_sec)
        if self.entry_timeout_pct is not None and 0.0 < self.entry_timeout_pct < 1.0:
            entry_timeout_sec = self.entry_timeout_pct * win_duration
            is_late_start = (elapsed_sec >= entry_timeout_sec)
        else:
            entry_timeout_sec = win_duration
            is_late_start = False

        # --- ENTRY DELAY (issue #137) ---
        # While the window is younger than `entry_delay_sec`, no quotes are
        # placed at all. Transient by design: no skip flag is latched, so the
        # adverse-open, band, and timeout gates below still evaluate normally
        # after expiry. Stateless (a pure function of `elapsed_sec`), so there
        # is nothing to reset on rollover. Windows with a fill already are
        # unaffected.
        entry_delay_pending = (
            self.entry_delay_sec > 0
            and elapsed_sec < self.entry_delay_sec
            and not mstate.filled_up
            and not mstate.filled_down
        )

        # Late-start latch (issue #96). Evaluated once per window from the first
        # tick the engine observes for it, keyed on `start_ts` so it re-arms on every
        # rollover. Gating on the latched value rather than on the live `elapsed_sec`
        # is what keeps a normally-started window quoting for its whole duration at
        # `entry_timeout_pct = 1.0`.
        late_start_cutoff_sec = (
            self.max_start_elapsed_pct * win_duration
            if (self.max_start_elapsed_pct is not None and 0.0 < self.max_start_elapsed_pct < 1.0)
            else None
        )
        if mstate.first_seen_start_ts != mstate.start_ts:
            mstate.first_seen_start_ts = mstate.start_ts
            # Never re-arm a window the engine is already trading. Not every market
            # loader reports a stable `start_ts` (`strategy/markets.py:170` derives one
            # from `time.time()`), and a shifting value must not turn a live, quoted
            # window into a late start half way through it.
            window_engaged = bool(
                mstate.order_id_up or mstate.order_id_down
                or mstate.filled_up or mstate.filled_down
                or mstate.order_status_up == "RESTING" or mstate.order_status_down == "RESTING"
            )
            if not window_engaged:
                mstate.first_tick_elapsed_sec = elapsed_sec
                mstate.late_start_skip = (
                    late_start_cutoff_sec is not None and elapsed_sec >= late_start_cutoff_sec
                )
        first_tick_elapsed = (
            mstate.first_tick_elapsed_sec if mstate.first_tick_elapsed_sec is not None else elapsed_sec
        )
        late_cutoff_txt = f"{late_start_cutoff_sec:.0f}s" if late_start_cutoff_sec is not None else "n/a"
        late_start_action = (
            f"Engine started {first_tick_elapsed:.0f}s into window (>= {late_cutoff_txt}) "
            f"— waiting for next window"
        )

        # Pre-entry drift check (issue #92): if the mid was already drifted >= exit_thresh
        # vs 0.50 *when the window opened*, the market is already strongly monotonic /
        # skewed. Never enter or quote into an immediate stop. The gate is evaluated once
        # per window from the opening snapshot -- re-running it against the live mid on
        # every 1s tick cancelled healthy resting bids seconds into a window.
        # A one-sided book collapses `mid` onto whichever side exists, which reports a
        # synthetic drift that is a book artifact rather than a real skew, so the snapshot
        # is only taken once both legs quote two sides.
        book_two_sided = (
            mstate.up_bid is not None and mstate.up_ask is not None
            and mstate.down_bid is not None and mstate.down_ask is not None
        )
        if not mstate.open_gate_evaluated and book_two_sided and not mstate.late_start_skip:
            mstate.open_mid = mid
            mstate.open_drift = abs(mid - 0.50)
            mstate.adverse_open = (mstate.open_drift >= self.exit_thresh)
            mstate.open_gate_evaluated = True
        initial_drift = mstate.open_drift
        is_adverse_open = mstate.adverse_open

        # --- POST-DELAY ENTRY BAND (issue #137) ---
        # Admit only undecided markets: once the entry delay has expired, the
        # first tick with a two-sided book checks |mid - 0.50| against
        # `entry_band` (0 = off). A failure latches the window skipped through
        # the same `entry_cancelled_timeout` family the other pre-entry gates
        # use, plus a distinct `band_skip` flag and session counter. The
        # adverse gate owns windows it already claimed, and re-entry (#95)
        # keeps its own `reentry_drift_band` — neither path reaches this check.
        delay_expired = self.entry_delay_sec <= 0 or elapsed_sec >= self.entry_delay_sec
        if (self.entry_band > 0 and not mstate.band_gate_evaluated
                and not mstate.entry_cancelled_timeout and not is_adverse_open
                and not mstate.filled_up and not mstate.filled_down
                and not mstate.order_id_up and not mstate.order_id_down
                and book_two_sided and not mstate.late_start_skip
                and delay_expired):
            mstate.band_gate_evaluated = True
            band_drift = abs(mid - 0.50)
            if band_drift > self.entry_band:
                with self._engine_lock:
                    mstate.entry_cancelled_timeout = True
                    mstate.band_skip = True
                    self.band_skip_stats["band_skips"] += 1
                mstate.status = "BAND_SKIPPED"
                mstate.last_action = (
                    f"Entry band skip (mid {mid:.4f}, drift {band_drift:.3f} > {self.entry_band:.2f})"
                    " — entry skipped"
                )
                log.info("[%s] Entry skipped by entry band (mid=%.4f, drift=%.3f > %.2f)",
                         slug, mid, band_drift, self.entry_band)

        # While the band filter is armed but has not seen a two-sided book
        # yet, hold placement too: "entry waits for the first two-sided tick"
        # covers the quotes as well as the decision. Once evaluated (pass or
        # fail) this clears by itself; re-entry bypasses the band entirely
        # (marked evaluated when granted below).
        band_hold = (
            self.entry_band > 0
            and not mstate.band_gate_evaluated
            and not mstate.entry_cancelled_timeout
            and not is_adverse_open
            and not mstate.late_start_skip
            and not mstate.filled_up
            and not mstate.filled_down
            and not mstate.order_id_up
            and not mstate.order_id_down
            and delay_expired
        )

        # --- LATE-START SKIP (issue #96) ---
        # The engine attached to this window after `max_start_elapsed_pct` elapsed,
        # so there is nothing to cancel: no entry was ever placed for it. Mark the
        # window skipped and wait for the next rollover, which takes a genuine
        # opening snapshot.
        if (mstate.late_start_skip and not mstate.entry_cancelled_timeout
                and not mstate.filled_up and not mstate.filled_down
                and not mstate.order_id_up and not mstate.order_id_down):
            with self._engine_lock:
                mstate.entry_cancelled_timeout = True
                mstate.status = "LATE_START_SKIPPED"
                mstate.last_action = late_start_action
            log.info("[%s] Window skipped: engine started %.1fs in (cutoff=%s)",
                     slug, first_tick_elapsed, late_cutoff_txt)

        # --- PRE-ENTRY DRIFT & ENTRY TIMEOUT CANCELLATION ---
        if (is_late_start or is_adverse_open or mstate.late_start_skip) and not mstate.entry_cancelled_timeout:
            if not mstate.filled_up and not mstate.filled_down:
                now_str = datetime.datetime.now().strftime("%H:%M:%S")
                if self.mode == "live":
                    cancel_ok_up = True
                    cancel_ok_down = True
                    if mstate.order_id_up and mstate.order_status_up == "RESTING":
                        oid = mstate.order_id_up
                        if self.cancel_live_order(oid):
                            with self._engine_lock:
                                mstate.order_status_up = "CANCELLED"
                                self._record_cancelled_order(mstate, {
                                    "order_id": oid,
                                    "market": mstate.label,
                                    "market_slug": mstate.market_slug or "",
                                    "series_slug": mstate.slug,
                                    "token_id": mstate.up_token or "",
                                    "side": "BUY (UP)",
                                    "price": resting_up,
                                    "size": mstate.order_shares,
                                    "status": "CANCELLED",
                                    "source": "CLOB_API",
                                    "time": now_str,
                                    })
                        else:
                            cancel_ok_up = False
                    if mstate.order_id_down and mstate.order_status_down == "RESTING":
                        oid = mstate.order_id_down
                        if self.cancel_live_order(oid):
                            with self._engine_lock:
                                mstate.order_status_down = "CANCELLED"
                                self._record_cancelled_order(mstate, {
                                    "order_id": oid,
                                    "market": mstate.label,
                                    "market_slug": mstate.market_slug or "",
                                    "series_slug": mstate.slug,
                                    "token_id": mstate.down_token or "",
                                    "side": "BUY (DOWN)",
                                    "price": resting_down,
                                    "size": mstate.order_shares,
                                    "status": "CANCELLED",
                                    "source": "CLOB_API",
                                    "time": now_str,
                                })
                        else:
                            cancel_ok_down = False

                    if cancel_ok_up and cancel_ok_down:
                        with self._engine_lock:
                            mstate.entry_cancelled_timeout = True
                else:
                    with self._engine_lock:
                        if mstate.order_status_up in ("RESTING", "NONE", "OPEN"):
                            mstate.order_status_up = "CANCELLED"
                            self._record_cancelled_order(mstate, {
                                "order_id": mstate.order_id_up or f"paper_up_{slug}",
                                "market": mstate.label,
                                "market_slug": mstate.market_slug or "",
                                "series_slug": mstate.slug,
                                "token_id": mstate.up_token or "",
                                "side": "BUY (UP)",
                                "price": resting_up,
                                "size": mstate.order_shares,
                                "status": "CANCELLED",
                                "source": "PAPER_SIMULATION",
                                "time": now_str,
                            })
                        if mstate.order_status_down in ("RESTING", "NONE", "OPEN"):
                            mstate.order_status_down = "CANCELLED"
                            self._record_cancelled_order(mstate, {
                                "order_id": mstate.order_id_down or f"paper_dn_{slug}",
                                "market": mstate.label,
                                "market_slug": mstate.market_slug or "",
                                "series_slug": mstate.slug,
                                "token_id": mstate.down_token or "",
                                "side": "BUY (DOWN)",
                                "price": resting_down,
                                "size": mstate.order_shares,
                                "status": "CANCELLED",
                                "source": "PAPER_SIMULATION",
                                "time": now_str,
                            })
                        mstate.entry_cancelled_timeout = True

                if mstate.entry_cancelled_timeout:
                    if mstate.late_start_skip:
                        mstate.status = "LATE_START_SKIPPED"
                        mstate.last_action = late_start_action
                        log.info("[%s] Window skipped: engine started %.1fs in (cutoff=%s)",
                                 slug, first_tick_elapsed, late_cutoff_txt)
                    elif is_adverse_open:
                        mstate.status = "DRIFT_SKIPPED"
                        open_mid_txt = f"{mstate.open_mid:.4f}" if mstate.open_mid is not None else "n/a"
                        mstate.last_action = f"Adverse drift at open (mid {open_mid_txt}, drift {initial_drift:.3f} >= {self.exit_thresh:.2f}) — entry skipped"
                        log.info("[%s] Entry skipped due to adverse open drift (open_mid=%s, drift=%.3f >= %.2f)", slug, open_mid_txt, initial_drift, self.exit_thresh)
                    elif mstate.band_skip:
                        # Issue #137: the latch above already recorded the
                        # mid/drift numbers in last_action; keep them.
                        mstate.status = "BAND_SKIPPED"
                    else:
                        mstate.status = "TIMEOUT_NO_FILL"
                        pct_val = int(round(self.entry_timeout_pct * 100)) if self.entry_timeout_pct is not None else 10
                        mstate.last_action = f"{pct_val}% window timeout ({elapsed_sec:.0f}s >= {entry_timeout_sec:.0f}s) — entry cancelled"
                        log.info("[%s] Entry orders cancelled due to %d%% elapsed timeout (elapsed=%.1fs, cutoff=%.1fs)", slug, pct_val, elapsed_sec, entry_timeout_sec)

        # --- DRIFT-SKIP RE-ENTRY (issue #95) ---
        # Evaluated after the cancellation block so a window skipped on an earlier
        # tick can be re-opened on this one, and before `can_place_entry` so the
        # cleared flags are visible to placement on the same tick.
        remaining_sec = (
            max(0.0, mstate.end_ts - now) if mstate.end_ts > 0
            else max(0.0, win_duration - elapsed_sec)
        )
        if self._maybe_reenter_drift_skipped(
                mstate, slug, mid, remaining_sec, win_duration,
                book_two_sided, is_late_start, now, resting_up, resting_down):
            is_adverse_open = mstate.adverse_open
            # Issue #137: the entry band never gates re-entry; mark it
            # evaluated so the hold below cannot block the re-opened window.
            mstate.band_gate_evaluated = True

        # --- ORDER PLACEMENT (Live CLOB or Paper Simulation) ---
        can_place_entry = (
            not self.quoting_halted
            and not mstate.pair_captured
            and not mstate.exit_taken
            and not mstate.entry_cancelled_timeout
            and not is_late_start
            and not is_adverse_open
            and not mstate.late_start_skip
            and not entry_delay_pending
            and not band_hold
        )
        if entry_delay_pending and not mstate.entry_cancelled_timeout:
            mstate.last_action = (
                f"Entry delayed ({elapsed_sec:.0f}s/{self.entry_delay_sec:.0f}s into window)"
                " — quoting after delay"
            )
        elif band_hold:
            mstate.last_action = (
                "Entry band check waiting for two-sided book — quoting held"
            )
        if can_place_entry:
            if self.mode == "live":
                # In live mode, if opposite leg is being chased, cancel existing resting quote so replacement is submitted at chase price
                if mstate.chased_leg == "DOWN" and mstate.order_id_down and mstate.order_status_down == "RESTING":
                    if self.cancel_live_order(mstate.order_id_down):
                        mstate.order_id_down = None
                elif mstate.chased_leg == "UP" and mstate.order_id_up and mstate.order_status_up == "RESTING":
                    if self.cancel_live_order(mstate.order_id_up):
                        mstate.order_id_up = None

                if not mstate.order_id_up and mstate.up_token:
                    res_up = self.place_live_quote(mstate.up_token, resting_up, self.shares, "BUY")
                    if res_up and res_up.get("order_id"):
                        mstate.order_id_up = res_up["order_id"]
                        mstate.order_time_up = time.strftime("%H:%M:%S")
                        mstate.order_status_up = "RESTING"
                if not mstate.order_id_down and mstate.down_token:
                    res_dn = self.place_live_quote(mstate.down_token, resting_down, self.shares, "BUY")
                    if res_dn and res_dn.get("order_id"):
                        mstate.order_id_down = res_dn["order_id"]
                        mstate.order_time_down = time.strftime("%H:%M:%S")
                        mstate.order_status_down = "RESTING"
                if mstate.requote_round > 0:
                    self._finalize_requote_telemetry(mstate, slug, mid)
                self._finalize_reentry_telemetry(mstate, slug, mid)
            else:
                if not mstate.filled_up and mstate.order_status_up != "RESTING":
                    mstate.order_id_up = mstate.order_id_up or f"paper_up_{slug}"
                    mstate.order_status_up = "RESTING"
                    mstate.order_time_up = mstate.order_time_up if mstate.order_time_up != "-" else time.strftime("%H:%M:%S")
                if not mstate.filled_down and mstate.order_status_down != "RESTING":
                    mstate.order_id_down = mstate.order_id_down or f"paper_dn_{slug}"
                    mstate.order_status_down = "RESTING"
                    mstate.order_time_down = mstate.order_time_down if mstate.order_time_down != "-" else time.strftime("%H:%M:%S")
                if mstate.requote_round > 0:
                    self._finalize_requote_telemetry(mstate, slug, mid)
                self._finalize_reentry_telemetry(mstate, slug, mid)

        # --- FILL-TELEMETRY REST SNAPSHOT (issue #138) ---
        # Observation only: stash the full bid books for the stream fill path
        # and snapshot per-leg rest context the first tick a leg is active.
        # Snapshot-once semantics mirror sim2 (queue at quotable time); the
        # context persists until rollover so fills on later ticks join it.
        # Stash copies are skipped once both legs are recorded: nothing left
        # to join.
        up_active = bool(mstate.order_id_up) or mstate.order_status_up == "RESTING"
        dn_active = bool(mstate.order_id_down) or mstate.order_status_down == "RESTING"
        need_stash = ((up_active and (mstate.rest_up_price is None or not mstate.fill_telemetry_done_up))
                      or (dn_active and (mstate.rest_dn_price is None or not mstate.fill_telemetry_done_down)))
        if need_stash:
            if isinstance(ubook.get("bids"), dict) and ubook["bids"]:
                mstate.last_bids_up = dict(ubook["bids"])
            if isinstance(dbook.get("bids"), dict) and dbook["bids"]:
                mstate.last_bids_down = dict(dbook["bids"])
        if up_active and mstate.rest_up_price is None:
            mstate.rest_up_price = resting_up
            mstate.rest_up_queue = _queue_ahead(mstate.last_bids_up, resting_up)
            mstate.rest_up_ts = now
        if dn_active and mstate.rest_dn_price is None:
            mstate.rest_dn_price = resting_down
            mstate.rest_dn_queue = _queue_ahead(mstate.last_bids_down, resting_down)
            mstate.rest_dn_ts = now

        # --- FILL DETECTION ---
        if mstate.status in ("IDLE", "PRE_QUOTING") and can_place_entry:
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
                                if mstate.chased_leg == "UP":
                                    mstate.chased_fill = True
                                actual_px_up = None
                                trades_up = ord_up.get("associate_trades") or ord_up.get("trades")
                                if isinstance(trades_up, list) and trades_up:
                                    tot_vol = sum(float(t.get("size", 0.0) or 0.0) for t in trades_up)
                                    if tot_vol > 0:
                                        actual_px_up = sum(float(t.get("price", 0.0) or 0.0) * float(t.get("size", 0.0) or 0.0) for t in trades_up) / tot_vol
                                if actual_px_up is None and ord_up.get("price") is not None:
                                    try:
                                        actual_px_up = float(ord_up["price"])
                                    except (ValueError, TypeError):
                                        pass
                                mstate.fill_price_up = round(actual_px_up if actual_px_up is not None else resting_up, 4)
                                mstate.order_status_up = "FILLED"
                                mstate.status = "FILLED_UP"
                                mstate.last_action = f"Filled UP {self.shares} shares @ {mstate.fill_price_up:.2f}"
                                log.info("[%s] UP leg FILLED on CLOB (status=%s, matched=%.1f, price=%.4f)", slug, st_up, sz_up, mstate.fill_price_up)
                                if not mstate.filled_down and mstate.naked_since_ts is None:
                                    mstate.naked_since_ts = time.time()
                                # Pre-place resting stop-loss protection for the filled leg (issue #87)
                                self.place_stop_order(mstate, "UP")
                                # Issue #138: queue-position telemetry (observation only).
                                self._record_fill_telemetry(
                                    mstate, "UP", mstate.fill_price_up, sz_up, now)
                        except Exception as e:
                            log.debug("[%s] Error checking UP order: %s", slug, e)

                    if not mstate.filled_down and mstate.order_id_down:
                        try:
                            ord_dn = client.get_order(mstate.order_id_down)
                            st_dn = (ord_dn.get("status") or "").upper()
                            sz_dn = float(ord_dn.get("size_matched", 0.0) or 0.0)
                            if st_dn in ("MATCHED", "FILLED") or sz_dn >= mstate.order_shares:
                                mstate.filled_down = True
                                if mstate.chased_leg == "DOWN":
                                    mstate.chased_fill = True
                                actual_px_dn = None
                                trades_dn = ord_dn.get("associate_trades") or ord_dn.get("trades")
                                if isinstance(trades_dn, list) and trades_dn:
                                    tot_vol = sum(float(t.get("size", 0.0) or 0.0) for t in trades_dn)
                                    if tot_vol > 0:
                                        actual_px_dn = sum(float(t.get("price", 0.0) or 0.0) * float(t.get("size", 0.0) or 0.0) for t in trades_dn) / tot_vol
                                if actual_px_dn is None and ord_dn.get("price") is not None:
                                    try:
                                        actual_px_dn = float(ord_dn["price"])
                                    except (ValueError, TypeError):
                                        pass
                                mstate.fill_price_down = round(actual_px_dn if actual_px_dn is not None else resting_down, 4)
                                mstate.order_status_down = "FILLED"
                                mstate.status = "FILLED_DOWN" if not mstate.filled_up else "PAIR_MERGED"
                                mstate.last_action = f"Filled DOWN {self.shares} shares @ {mstate.fill_price_down:.2f}"
                                log.info("[%s] DOWN leg FILLED on CLOB (status=%s, matched=%.1f, price=%.4f)", slug, st_dn, sz_dn, mstate.fill_price_down)
                                if not mstate.filled_up and mstate.naked_since_ts is None:
                                    mstate.naked_since_ts = time.time()
                                # Issue #138: queue-position telemetry (observation only,
                                # every fill incl. paired ones).
                                self._record_fill_telemetry(
                                    mstate, "DOWN", mstate.fill_price_down, sz_dn, now)
                                if not mstate.filled_up:
                                    # Pre-place resting stop-loss protection for the filled leg (issue #87)
                                    self.place_stop_order(mstate, "DOWN")
                        except Exception as e:
                            log.debug("[%s] Error checking DOWN order: %s", slug, e)
            else:
                # Paper / Backtest simulation fallback using order book asks
                can_sim_up = (not mstate.filled_up) and (mstate.filled_down or can_place_entry)
                can_sim_dn = (not mstate.filled_down) and (mstate.filled_up or can_place_entry)

                if can_sim_up:
                    if mstate.up_ask is not None and mstate.up_ask <= resting_up:
                        mstate.filled_up = True
                        mstate.fill_price_up = resting_up
                        mstate.order_status_up = "FILLED"
                        mstate.order_time_up = time.strftime("%H:%M:%S")
                        mstate.status = "FILLED_UP"
                        if mstate.chased_leg == "UP":
                            mstate.chased_fill = True
                        mstate.last_action = f"Filled UP {self.shares} shares @ {resting_up:.2f}"
                        log.info("[%s] Filled UP @ %.2f", slug, resting_up)
                        if mstate.naked_since_ts is None and not mstate.filled_down:
                            mstate.naked_since_ts = now
                        # Pre-place resting stop-loss protection for the filled leg (issue #87)
                        self.place_stop_order(mstate, "UP")
                        # Issue #138: queue-position telemetry (observation only).
                        self._record_fill_telemetry(
                            mstate, "UP", mstate.fill_price_up, self.shares, now)
                        # If DOWN is not yet filled, step up DOWN quote towards ask within cap
                        if not mstate.filled_down and self.enable_leg_chase:
                            entry_up = mstate.fill_price_up
                            # Floor strictly to cent precision so entry + opposite never exceeds max_pair_cost
                            max_down_bid = round(math.floor((self.max_pair_cost - entry_up + 1e-9) * 100) / 100.0, 2)
                            if mstate.down_ask is not None:
                                target_down = min(mstate.down_ask, max_down_bid)
                                if target_down > resting_down:
                                    resting_down = target_down
                                    mstate.resting_down = resting_down
                                    mstate.chased_leg = "DOWN"
                                elif target_down == max_down_bid and target_down >= resting_down and max_down_bid > resting_down:
                                    resting_down = max_down_bid
                                    mstate.resting_down = resting_down
                                    mstate.chased_leg = "DOWN"

                if can_sim_dn:
                    if mstate.down_ask is not None and mstate.down_ask <= resting_down:
                        mstate.filled_down = True
                        mstate.fill_price_down = resting_down
                        mstate.order_status_down = "FILLED"
                        mstate.order_time_down = time.strftime("%H:%M:%S")
                        mstate.status = "FILLED_DOWN" if not mstate.filled_up else "PAIR_MERGED"
                        if mstate.chased_leg == "DOWN":
                            mstate.chased_fill = True
                        mstate.last_action = f"Filled DOWN {self.shares} shares @ {resting_down:.2f}"
                        log.info("[%s] Filled DOWN @ %.2f", slug, resting_down)
                        if not mstate.filled_up:
                            if mstate.naked_since_ts is None:
                                mstate.naked_since_ts = now
                        # Issue #138: queue-position telemetry (observation only,
                        # every fill incl. paired ones).
                        self._record_fill_telemetry(
                            mstate, "DOWN", mstate.fill_price_down, self.shares, now)
                        if not mstate.filled_up:
                            # Pre-place resting stop-loss protection for the filled leg (issue #87)
                            self.place_stop_order(mstate, "DOWN")
                            # If UP is not yet filled, step up UP quote towards ask within cap
                            if self.enable_leg_chase:
                                entry_dn = mstate.fill_price_down
                                # Floor strictly to cent precision so entry + opposite never exceeds max_pair_cost
                                max_up_bid = round(math.floor((self.max_pair_cost - entry_dn + 1e-9) * 100) / 100.0, 2)
                                if mstate.up_ask is not None:
                                    target_up = min(mstate.up_ask, max_up_bid)
                                    if target_up > resting_up:
                                        resting_up = target_up
                                        mstate.resting_up = resting_up
                                        mstate.chased_leg = "UP"
                                    elif target_up == max_up_bid and target_up >= resting_up and max_up_bid > resting_up:
                                        resting_up = max_up_bid
                                        mstate.resting_up = resting_up
                                        mstate.chased_leg = "UP"
                                    # Immediate fill check if UP ask meets new chase quote
                                    if can_sim_up and mstate.up_ask <= resting_up:
                                        mstate.filled_up = True
                                        mstate.fill_price_up = resting_up
                                        mstate.order_status_up = "FILLED"
                                        mstate.order_time_up = time.strftime("%H:%M:%S")
                                        mstate.status = "PAIR_MERGED"
                                        mstate.chased_fill = True
                                        mstate.last_action = f"Filled UP {self.shares} shares @ {resting_up:.2f}"
                                        log.info("[%s] Filled UP @ %.2f (chased)", slug, resting_up)
                                        # Issue #138: queue-position telemetry (observation only).
                                        self._record_fill_telemetry(
                                            mstate, "UP", mstate.fill_price_up, self.shares, now)

            # --- PAIR COMPLETION & MERGE ---
            if mstate.filled_up and mstate.filled_down:
                mstate.chased_leg = None
                # OCO Case A: cancel the stop-loss before the merge — a hedged
                # pair must never keep protection working against one leg (issue #87).
                # If a venue-side cancellation fails, block the merge and retry next tick.
                if not self._cancel_stop_order(mstate, reason="pair completed"):
                    mstate.last_action = "Pair merge deferred: stop-loss cancellation failed"
                    log.warning(
                        "[%s] Pair merge deferred until stop-loss cancellation succeeds",
                        slug,
                    )
                    return
                mstate.pair_captured = True
                mstate.status = "PAIR_MERGED"
                mstate.naked_since_ts = None
                fill_up = mstate.fill_price_up if mstate.fill_price_up is not None else resting_up
                fill_dn = mstate.fill_price_down if mstate.fill_price_down is not None else resting_down
                pair_profit_usd = (1.00 - (fill_up + fill_dn)) * self.shares
                mstate.realized_pnl_usd += pair_profit_usd
                mstate.unrealized_pnl_usd = 0.0
                mstate.total_pnl_usd = mstate.realized_pnl_usd
                mstate.pairs_count += 1
                mstate.trades_count += 1
                mstate.last_action = f"Pair Merged! +${pair_profit_usd:.2f}"
                log.info("[%s] PAIR MERGED! Profit: +$%.2f (entry=%.3f+%.3f)", slug, pair_profit_usd, fill_up, fill_dn)
                
                if self.mode == "live":
                    self.merge_positions(mstate.condition_id)

                denom = max(0.01, (fill_up + fill_dn) * max(1, self.shares))
                with self._engine_lock:
                    self.trades.append(TradeEvent(
                        id=f"{slug}_{int(now)}",
                        timestamp=datetime.datetime.fromtimestamp(now).strftime("%H:%M:%S"),
                        slug=slug,
                        label=mstate.label,
                        action="PAIR_MERGE",
                        shares=self.shares,
                        entry_price_up=fill_up,
                        entry_price_down=fill_dn,
                        exit_price=1.00,
                        pnl_usd=round(pair_profit_usd, 3),
                        pnl_pct=round(((pair_profit_usd) / denom) * 100.0, 1),
                        notes=f"Complete spread capture @ {fill_up:.2f} + {fill_dn:.2f}",
                        market_slug=mstate.market_slug or "",
                    ))
                    self._save_persisted_trades()
                # Issue #89: recycle into a fresh quoting round when the window
                # has enough life left; otherwise stay terminal until rollover.
                self._maybe_requote_after_merge(mstate, slug, mid, now)
                return

        # --- RECONCILE STAGED STOP-LOSS (issue #87) ---
        if mstate.stop_order_id and not mstate.exit_taken:
            # Live: poll the venue for a stop resting on the book (engine-wide
            # UserSpec events keep stop_order_status in sync meanwhile).
            stop_fill_confirmed = False
            if self.mode == "live" and mstate.stop_order_status == "RESTING" and self.get_clob_client():
                try:
                    ord_stop = self.get_clob_client().get_order(mstate.stop_order_id)
                    st_stop = (ord_stop.get("status") or "").upper()
                    sz_stop = float(ord_stop.get("size_matched", 0.0) or 0.0)
                    if st_stop in ("MATCHED", "FILLED") or sz_stop >= self.shares:
                        stop_fill_confirmed = True
                except Exception as e:
                    log.debug("[%s] Error checking stop-loss order %s: %s", slug, mstate.stop_order_id, e)
            if stop_fill_confirmed:
                # OCO Case B from venue confirmation: cancel opposite entry, STOP_EXIT
                side_confirmed = mstate.stop_side or "UP"
                confirmed_price = mstate.stop_price
                mstate.stop_order_status = "FILLED"
                mstate.stop_order_id = None
                mstate.stop_price = None
                mstate.stop_side = None
                self._execute_stop_exit(
                    slug,
                    mstate,
                    side_confirmed,
                    confirmed_price,
                    "Confirmed stop-loss fill (venue)",
                    now,
                )
                return

        # --- RECONCILE PENDING STOP EXIT ---
        if mstate.status == "STOP_EXIT_PENDING" and not mstate.exit_taken:
            pending_side = "UP" if (mstate.order_id_exit_up or mstate.filled_up) else "DOWN"
            exit_px = mstate.exit_price_up if pending_side == "UP" else mstate.exit_price_down
            self._execute_stop_exit(
                slug,
                mstate,
                pending_side,
                exit_px,
                f"Reconciled pending stop exit ({pending_side})",
                now,
            )
            if mstate.exit_taken:
                return

        # --- NAKED-LEG TIMEOUT (issue #124) ---
        # A leg still unpaired after `naked_leg_timeout_pct` of the window force-
        # exits at the live bid with a WINDOW_SETTLE-style trade event, so a mid
        # hovering just inside the stop cannot ride the naked leg to the wall.
        if not mstate.exit_taken and not mstate.pair_captured:
            win_dur_naked = (mstate.end_ts - mstate.start_ts) if (mstate.end_ts > mstate.start_ts) else 0.0
            if self._naked_timeout_elapsed(mstate, now, win_dur_naked):
                naked_side = "UP" if mstate.filled_up else "DOWN"
                naked_bid = mstate.up_bid if naked_side == "UP" else mstate.down_bid
                if naked_bid is not None:
                    with self._engine_lock:
                        mstate.status = "STOP_EXIT_PENDING"
                    if mstate.stop_order_id:
                        mstate.stop_order_status = "FILLED"
                        mstate.stop_order_id = None
                        mstate.stop_price = None
                        mstate.stop_side = None
                    trigger_note = (
                        f"Naked-leg timeout: unpaired {naked_side} after "
                        f"{self.naked_leg_timeout_pct:.0%} of window"
                    )
                    self._execute_stop_exit(slug, mstate, naked_side, naked_bid, trigger_note, now)
                    return

        # --- STOP LOSS EXIT TRIGGER ---
        # Holding UP alone and mid dropped adversely (max_down >= exit_thresh).
        # In paper mode the staged stop also fills when the protected leg's bid
        # touches the staged stop price (issue #87).
        # Issue #137: the whole trigger is gated on `stop_loss_enabled` — with
        # the stop off, naked-timeout and rollover below stay authoritative.
        paper_stop_hit_up = (
            self.mode != "live" and mstate.stop_order_id and mstate.stop_side == "UP"
            and mstate.up_bid is not None and mstate.stop_price is not None
            and mstate.up_bid <= mstate.stop_price
        )
        if self.stop_loss_enabled and ((mstate.filled_up and not mstate.filled_down and mstate.max_down_drift >= self._naked_exit_thresh()
                or paper_stop_hit_up)
                and not mstate.reversal_seen_down and not mstate.exit_taken and mstate.status != "STOP_EXIT_PENDING"):
            sell_bid = mstate.up_bid
            if sell_bid is not None:
                with self._engine_lock:
                    mstate.status = "STOP_EXIT_PENDING"
                if mstate.stop_order_id:
                    # Buffered stop: protection was staged in memory at fill time
                    # (zero venue exposure). The monitored exit submits the SELL
                    # only now, at the live bid, via _execute_stop_exit.
                    mstate.stop_order_status = "FILLED"
                    mstate.stop_order_id = None
                    mstate.stop_price = None
                    mstate.stop_side = None
                trigger_note = f"Adverse drift {mstate.max_down_drift:.3f} >= {self._naked_exit_thresh():.2f}"
                self._execute_stop_exit(slug, mstate, "UP", sell_bid, trigger_note, now)
                return

        # Holding DOWN alone and mid rallied adversely (max_up >= exit_thresh).
        # Paper-mode staged stop also fills when the DOWN bid reaches its stop price.
        paper_stop_hit_down = (
            self.mode != "live" and mstate.stop_order_id and mstate.stop_side == "DOWN"
            and mstate.down_bid is not None and mstate.stop_price is not None
            and mstate.down_bid <= mstate.stop_price
        )
        if self.stop_loss_enabled and ((mstate.filled_down and not mstate.filled_up and mstate.max_up_drift >= self._naked_exit_thresh()
                or paper_stop_hit_down)
                and not mstate.reversal_seen_up and not mstate.exit_taken and mstate.status != "STOP_EXIT_PENDING"):
            sell_bid = mstate.down_bid
            if sell_bid is not None:
                with self._engine_lock:
                    mstate.status = "STOP_EXIT_PENDING"
                if mstate.stop_order_id:
                    # Buffered stop: protection was staged in memory at fill time
                    # (zero venue exposure). The monitored exit submits the SELL
                    # only now, at the live bid, via _execute_stop_exit.
                    mstate.stop_order_status = "FILLED"
                    mstate.stop_order_id = None
                    mstate.stop_price = None
                    mstate.stop_side = None
                trigger_note = f"Adverse drift {mstate.max_up_drift:.3f} >= {self._naked_exit_thresh():.2f}"
                self._execute_stop_exit(slug, mstate, "DOWN", sell_bid, trigger_note, now)
                return

        # --- UNREALIZED PnL CALCULATION ---
        if mstate.pair_captured or mstate.exit_taken:
            mstate.unrealized_pnl_usd = 0.0
        else:
            unrealized = 0.0
            fill_up = mstate.fill_price_up if mstate.fill_price_up is not None else resting_up
            fill_dn = mstate.fill_price_down if mstate.fill_price_down is not None else resting_down
            if mstate.filled_up and mstate.up_bid is not None:
                unrealized += (mstate.up_bid - fill_up) * self.shares
            if mstate.filled_down and mstate.down_bid is not None:
                unrealized += (mstate.down_bid - fill_dn) * self.shares
            mstate.unrealized_pnl_usd = round(unrealized, 3)

        mstate.total_pnl_usd = round(mstate.realized_pnl_usd + mstate.unrealized_pnl_usd, 3)

    def _maybe_requote_after_merge(self, mstate: MarketLiveState, slug: str, mid: float, now: float) -> bool:
        """Open a fresh quoting round after a pair merge when time allows (issue #89).

        Only PAIR_MERGED windows qualify — stop-loss exits stay terminal for the
        window. The new round anchors resting bids to the live mid minus offset
        (`target_up = mid - offset`, `target_down = (1 - mid) - offset`, so the
        pair still sums to `1 - 2 * offset`) instead of the static 0.50 base,
        resets per-round fill/order/drift state while keeping cumulative PnL,
        trade history, and pair counts, and records decision-time telemetry that
        `_finalize_requote_telemetry` completes once the round reaches the book.

        Returns True when a new round opened.
        """
        gate = self.min_requote_remaining_sec
        if gate is None or gate <= 0:
            return False
        if mstate.time_remaining_sec < gate:
            return False
        anchor_up = round(min(0.99, max(0.01, mid - self.offset)), 3)
        anchor_down = round(min(0.99, max(0.01, (1.0 - mid) - self.offset)), 3)
        # A re-entered window that pairs and then opens a fresh re-quote round would
        # otherwise be classified at rollover from the LATER round's state, recording
        # a paired re-entry as `no_fill` or `single_leg`. Freeze the re-entry's own
        # terminal state here, while it is still the current one.
        self._lock_reentry_outcome(mstate)
        with self._engine_lock:
            mstate.requote_round += 1
            mstate.filled_up = False
            mstate.filled_down = False
            mstate.fill_price_up = None
            mstate.fill_price_down = None
            mstate.order_id_up = None
            mstate.order_id_down = None
            mstate.order_time_up = "-"
            mstate.order_time_down = "-"
            mstate.order_status_up = "NONE"
            mstate.order_status_down = "NONE"
            mstate.pair_captured = False
            mstate.status = "QUOTING"
            mstate.max_up_drift = 0.0
            mstate.max_down_drift = 0.0
            mstate.reversal_seen_up = False
            mstate.reversal_seen_down = False
            mstate.resting_up = anchor_up
            mstate.resting_down = anchor_down
            mstate.last_requote_telemetry = {
                "round": mstate.requote_round,
                "mid_at_calc": round(mid, 4),
                "order_prices": {"up": anchor_up, "down": anchor_down},
                "mid_at_submit": round(mid, 4),
                "submitted_at": datetime.datetime.fromtimestamp(now).strftime("%H:%M:%S"),
                "perf_start": time.perf_counter(),
                "mid_at_resting": None,
                "latency_ms": None,
                "drift": None,
            }
            mstate.last_action = (
                f"Re-quoting round {mstate.requote_round} @ {anchor_up:.2f}/{anchor_down:.2f} "
                f"(mid {mid:.3f}, {mstate.time_remaining_sec:.0f}s left)"
            )
        log.info(
            "[%s] Re-quote round %d @ %.3f/%.3f (mid=%.4f, %.0fs left)",
            slug, mstate.requote_round, anchor_up, anchor_down, mid, mstate.time_remaining_sec,
        )
        return True

    def _finalize_reentry_telemetry(self, mstate: MarketLiveState, slug: str, mid: float) -> None:
        """Complete the re-entry record once both legs reach the book.

        No-op when no re-entry is in flight, when the record is already finalised,
        and until both legs are confirmed RESTING or FILLED. Mirrors
        `_finalize_requote_telemetry`: the point is to capture how far the mid
        travelled between the decision and the quote actually resting, which is the
        slippage a re-entry pays before it can fill.
        """
        tel = mstate.reentry_telemetry
        if not isinstance(tel, dict) or tel.get("mid_at_resting") is not None:
            return
        if mstate.order_status_up not in ("RESTING", "FILLED"):
            return
        if mstate.order_status_down not in ("RESTING", "FILLED"):
            return
        try:
            perf_start = float(tel.get("perf_start") or time.perf_counter())
        except (TypeError, ValueError):
            perf_start = time.perf_counter()
        latency_ms = round((time.perf_counter() - perf_start) * 1000.0, 2)
        try:
            mid_at_calc = float(tel.get("reentry_mid") if tel.get("reentry_mid") is not None else mid)
        except (TypeError, ValueError):
            mid_at_calc = mid
        resting_drift = round(abs(mid - mid_at_calc), 4)
        with self._engine_lock:
            tel["mid_at_resting"] = round(mid, 4)
            tel["latency_ms"] = latency_ms
            tel["resting_drift"] = resting_drift
        if resting_drift > self.offset:
            log.warning(
                "[%s] Re-entry mid moved %.4f (> offset %.3f) between decision and resting "
                "(decided %.4f, resting %.4f, %.0fms)",
                slug, resting_drift, self.offset, mid_at_calc, mid, latency_ms,
            )
        else:
            log.info(
                "[%s] Re-entry quotes resting @ %.3f/%.3f (mid %.4f, drift %.4f, %.0fms)",
                slug, mstate.resting_up, mstate.resting_down, mid, resting_drift, latency_ms,
            )

    def _lock_reentry_outcome(self, mstate: MarketLiveState) -> None:
        """Freeze the re-entry's terminal state before something resets the flags.

        `_maybe_requote_after_merge()` (issue #89) clears `filled_up`, `filled_down`
        and `pair_captured` to open a new round. The re-entry record is flushed later,
        at rollover, so without this it would report whatever the last round happened
        to leave behind. No-op when no re-entry is in flight or the state is already
        frozen -- the first freeze wins, since that is the one the re-entry produced.
        """
        tel = mstate.reentry_telemetry
        if not isinstance(tel, dict) or tel.get("outcome") is not None:
            return
        with self._engine_lock:
            tel["filled_up"] = mstate.filled_up
            tel["filled_down"] = mstate.filled_down
            tel["pair_captured"] = mstate.pair_captured
            tel["exit_taken"] = mstate.exit_taken
            tel["exit_side"] = mstate.exit_side or ""
            tel["realized_pnl_usd"] = round(mstate.realized_pnl_usd, 4)
            tel["final_status"] = mstate.status
            tel["outcome"] = _reentry_outcome(mstate)
            tel["chased_fill"] = mstate.chased_fill
            tel["outcome_locked_at_round"] = mstate.requote_round

    def _flush_reentry_event(self, mstate: MarketLiveState) -> Optional[Dict[str, Any]]:
        """Stamp the in-flight re-entry record with the window outcome and persist it.

        Called from `_handle_window_rollover()` before the per-window reset clears
        the fill flags, since the outcome is only knowable once the window is over.
        Returns the completed record, or None when the window never re-entered.
        The write is skipped under pytest so the suite never touches `run/`,
        mirroring the file handling in `reset_pnl()`.
        """
        tel = mstate.reentry_telemetry
        if not isinstance(tel, dict):
            return None
        # `_lock_reentry_outcome()` may already have frozen the terminal state, if a
        # re-quote round opened after the re-entry paired. That frozen state is the
        # re-entry's own; the live flags now describe a later round.
        self._lock_reentry_outcome(mstate)
        with self._engine_lock:
            tel.pop("perf_start", None)
            record = dict(tel)
            mstate.reentry_telemetry = None
            self.reentry_stats["reentries"] += 1
            if record.get("mid_at_resting") is not None:
                self.reentry_stats["reached_book"] += 1
            self.reentry_stats[record["outcome"]] = self.reentry_stats.get(record["outcome"], 0) + 1
            if record.get("chased_fill"):
                self.reentry_stats["chased_fills"] = self.reentry_stats.get("chased_fills", 0) + 1
            elif record.get("outcome") == "paired":
                self.reentry_stats["passive_fills"] = self.reentry_stats.get("passive_fills", 0) + 1
        if not os.getenv("PYTEST_CURRENT_TEST"):
            try:
                RUN_DIR.mkdir(parents=True, exist_ok=True)
                with open(REENTRY_FILE, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record) + "\n")
            except Exception as e:
                log.warning("Could not append to %s: %s", REENTRY_FILE, e)
        log.info(
            "[%s] Re-entry window closed: outcome=%s filled=%s/%s pnl=%.4f",
            mstate.slug, record["outcome"], record["filled_up"], record["filled_down"],
            record["realized_pnl_usd"],
        )
        return record

    def _finalize_requote_telemetry(self, mstate: MarketLiveState, slug: str, mid: float) -> None:
        """Complete the re-quote resting snapshot once the new round reaches the book.

        No-op for round 0, for already-finalised telemetry, and until both legs
        are confirmed RESTING or FILLED. Logs a warning when the mid escaped
        beyond the offset between the re-quote decision and the resting
        confirmation, so operators can see adverse slippage on entry.
        """
        tel = mstate.last_requote_telemetry
        if not isinstance(tel, dict) or tel.get("mid_at_resting") is not None:
            return
        if mstate.order_status_up not in ("RESTING", "FILLED"):
            return
        if mstate.order_status_down not in ("RESTING", "FILLED"):
            return
        try:
            perf_start = float(tel.get("perf_start") or time.perf_counter())
        except (TypeError, ValueError):
            perf_start = time.perf_counter()
        latency_ms = round((time.perf_counter() - perf_start) * 1000.0, 2)
        try:
            mid_at_calc = float(tel.get("mid_at_calc") if tel.get("mid_at_calc") is not None else mid)
        except (TypeError, ValueError):
            mid_at_calc = mid
        drift = round(abs(mid - mid_at_calc), 4)
        with self._engine_lock:
            tel["mid_at_resting"] = round(mid, 4)
            tel["latency_ms"] = latency_ms
            tel["drift"] = drift
        if drift > self.offset:
            log.warning(
                "[%s] Re-quote round %s: mid drifted %.3f between decision and resting (offset %.3f)",
                slug, tel.get("round"), drift, self.offset,
            )
        else:
            log.info(
                "[%s] Re-quote round %s resting confirmed (mid=%.4f, latency=%.1fms, drift=%.4f)",
                slug, tel.get("round"), mid, latency_ms, drift,
            )

    def _handle_window_rollover(self, mstate: MarketLiveState, now: float, new_cid: str = ""):
        """Cleanly settle unresolved positions when window expires and roll to next."""
        # Reconcile any pending stop-exit order before rollover
        if mstate.status == "STOP_EXIT_PENDING" and not mstate.exit_taken:
            pending_side = "UP" if (mstate.order_id_exit_up or mstate.filled_up) else "DOWN"
            exit_px = mstate.exit_price_up if pending_side == "UP" else mstate.exit_price_down
            self._execute_stop_exit(
                mstate.slug,
                mstate,
                pending_side,
                exit_px,
                f"Window rollover reconciled pending stop exit ({pending_side})",
                now,
            )

        # Cancel any unfilled orders from expiring window
        if self.mode == "live":
            if mstate.order_id_up and not mstate.filled_up:
                self.cancel_live_order(mstate.order_id_up)
            if mstate.order_id_down and not mstate.filled_down:
                self.cancel_live_order(mstate.order_id_down)
        # OCO Case C: cancel any stop-loss alongside entry orders (issue #87).
        # If the venue cancel fails, defer the window reset so the stale remote
        # stop can't survive into the next window with a cleared local handle.
        if not self._cancel_stop_order(mstate, reason="window rollover"):
            log.warning(
                "[%s] Window rollover deferred until stop-loss cancellation succeeds",
                mstate.slug,
            )
            return

        if (mstate.filled_up or mstate.filled_down) and not mstate.pair_captured and not mstate.exit_taken:
            resting_up = mstate.resting_up
            resting_down = mstate.resting_down
            fill_up = mstate.fill_price_up if mstate.fill_price_up is not None else resting_up
            fill_dn = mstate.fill_price_down if mstate.fill_price_down is not None else resting_down
            settle_pnl = 0.0
            if mstate.filled_up:
                bid = mstate.up_bid or 0.50
                settle_pnl += (bid - fill_up) * self.shares
            if mstate.filled_down:
                bid = mstate.down_bid or 0.50
                settle_pnl += (bid - fill_dn) * self.shares

            mstate.realized_pnl_usd += settle_pnl
            mstate.unrealized_pnl_usd = 0.0
            mstate.total_pnl_usd = mstate.realized_pnl_usd
            mstate.trades_count += 1
            log.info("[%s] Window Rollover Settled PnL: $%.2f", mstate.slug, settle_pnl)

            cost_basis = max(0.01, (fill_up if mstate.filled_up else fill_dn) * self.shares)
            with self._engine_lock:
                self.trades.append(TradeEvent(
                    id=f"{mstate.slug}_{int(now)}",
                    timestamp=datetime.datetime.fromtimestamp(now).strftime("%H:%M:%S"),
                    slug=mstate.slug,
                    label=mstate.label,
                    action="WINDOW_SETTLE",
                    shares=self.shares,
                    entry_price_up=fill_up if mstate.filled_up else None,
                    entry_price_down=fill_dn if mstate.filled_down else None,
                    exit_price=mstate.mid,
                    pnl_usd=round(settle_pnl, 3),
                    pnl_pct=round((settle_pnl / cost_basis) * 100.0, 1),
                    notes="Window expired, position auto-settled",
                    market_slug=mstate.market_slug or "",
                ))
                self._save_persisted_trades()

        # Promote advance pre-quoted orders from next window if available and matching new_cid
        if mstate.next_quoted and mstate.next_condition_id and (not new_cid or mstate.next_condition_id == new_cid):
            mstate.order_id_up = mstate.next_order_id_up
            mstate.order_id_down = mstate.next_order_id_down
            mstate.order_time_up = mstate.next_order_time_up if mstate.next_order_time_up != "-" else time.strftime("%H:%M:%S")
            mstate.order_time_down = mstate.next_order_time_down if mstate.next_order_time_down != "-" else time.strftime("%H:%M:%S")
            mstate.order_status_up = "RESTING"
            mstate.order_status_down = "RESTING"
            mstate.next_order_id_up = None
            mstate.next_order_id_down = None
            mstate.next_order_time_up = "-"
            mstate.next_order_time_down = "-"
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

        # Persist the re-entry record before the reset below clears the fill flags
        # it reports (issue #95 observability). No-op when the window never
        # re-entered.
        self._flush_reentry_event(mstate)

        # Reset window execution state for the new 5m period
        with self._engine_lock:
            mstate.cancelled_orders.clear()
            mstate.filled_up = False
            mstate.filled_down = False
            mstate.fill_price_up = None
            mstate.fill_price_down = None
            mstate.pair_captured = False
            mstate.exit_taken = False
            mstate.chased_leg = None
            mstate.chased_fill = False
            mstate.entry_cancelled_timeout = False
            mstate.requote_round = 0
            mstate.last_requote_telemetry = None
            mstate.reentry_telemetry = None
            mstate.open_mid = None
            mstate.open_drift = 0.0
            mstate.adverse_open = False
            mstate.open_gate_evaluated = False
            mstate.band_gate_evaluated = False
            mstate.band_skip = False
            mstate.rest_up_price = None
            mstate.rest_up_queue = None
            mstate.rest_up_ts = None
            mstate.rest_dn_price = None
            mstate.rest_dn_queue = None
            mstate.rest_dn_ts = None
            mstate.last_bids_up = {}
            mstate.last_bids_down = {}
            mstate.fill_telemetry_done_up = False
            mstate.fill_telemetry_done_down = False
            mstate.first_seen_start_ts = None
            mstate.first_tick_elapsed_sec = None
            mstate.late_start_skip = False
            mstate.reentry_count = 0
            mstate.reentry_mid = None
            mstate.reentry_drift = None
            mstate.naked_since_ts = None
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
        realized = self.historical_realized_pnl + sum(m.realized_pnl_usd for m in self.markets.values())
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
