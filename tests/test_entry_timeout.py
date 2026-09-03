"""Unit tests for Issue #48: Cancel unfilled entry orders once 10% of window elapsed.

Tests coverage:
1. LiveTrader cancels resting entry orders after 10% of window elapsed if 0 legs filled.
2. LiveTrader skips placing orders if a window is detected late (> 10% elapsed).
3. LiveTrader keeps the opposite leg open if 1 leg filled before the 10% timeout.
4. LiveTrader rollover resets entry_cancelled_timeout for the new window.
5. Backtest replay engine cancels unfilled orders after 10% elapsed.
6. Backtest replay engine skips fills when start_delay_sec >= 10% duration.
7. Backtest replay engine completes pair merge if leg 1 filled before 10% and leg 2 filled after 10%.
"""

import time
from unittest.mock import MagicMock
import pytest

from strategy.live_trader import LiveTraderEngine
from backtest import BacktestParams
from backtest.engine import _simulate_window


UP_TOKEN = "0xAAAA_up_token"
DN_TOKEN = "0xBBBB_dn_token"
CID = "0xCID_TIMEOUT"
SLUG = "btc-updown-5m-1788500000"
SERIES = "btc-up-or-down-5m"
DUR = 300


def _make_snap(ts: float, mid: float = 0.50, up_ask: float = 0.49, down_ask: float = 0.49,
               start_ts: float = 1000.0, tape: list | None = None) -> dict:
    half = 0.005
    bb_up = round(mid - half, 4)
    ba_up = round(mid + half, 4)
    if up_ask is not None:
        ba_up = up_ask
    return {
        "ts": ts,
        "iso": "2026-09-03T12:00:00+00:00",
        "series": SERIES,
        "duration": DUR,
        "label": "BTC 5m",
        "cid": CID,
        "slug": SLUG,
        "start_ts": start_ts,
        "end_ts": start_ts + DUR,
        "t_rem": max(0.0, (start_ts + DUR) - ts),
        "up_token": UP_TOKEN,
        "down_token": DN_TOKEN,
        "up_book": {
            "token_id": UP_TOKEN,
            "bids": {},
            "asks": {},
            "best_bid": bb_up,
            "best_ask": ba_up,
            "malformed": 0,
        },
        "down_book": {
            "token_id": DN_TOKEN,
            "bids": {},
            "asks": {},
            "best_bid": round(down_ask - 0.005, 4),
            "best_ask": down_ask,
            "malformed": 0,
        },
        "tape_delta": tape or [],
        "mid": mid,
        "touch_pair": (up_ask or 0.5) + (down_ask or 0.5),
        "resting_pair": 0.96,
        "queue_up": 0.0,
        "queue_down": 0.0,
        "err": None,
    }


# ============================================================================
# LIVE TRADER TESTS
# ============================================================================

def test_live_trader_cancels_unfilled_entry_at_10_percent_elapsed():
    """If 0 legs filled and 10% of window elapsed (30s on 5m), cancel both resting orders."""
    engine = LiveTraderEngine()
    engine.mode = "live"
    engine.is_running = True
    slug = "btc-up-or-down-5m"
    start_time = 1000.0

    placed_orders = []
    def mock_place(token_id, price, size, side):
        oid = f"ord_{token_id}_{len(placed_orders)}"
        placed_orders.append(oid)
        return {"order_id": oid, "status": "RESTING"}

    cancelled_orders = []
    def mock_cancel(order_id):
        cancelled_orders.append(order_id)
        return True

    engine.place_live_quote = MagicMock(side_effect=mock_place)
    engine.cancel_live_order = MagicMock(side_effect=mock_cancel)

    mkt = {
        "conditionId": "0xwin1",
        "slug": "btc-up-down-01",
        "up_token": "tok_up",
        "down_token": "tok_dn",
        "start_ts": start_time,
        "end_ts": start_time + 300.0,
    }
    poll_data = {
        "market": mkt,
        "up_book": {"best_bid": 0.49, "best_ask": 0.51},
        "down_book": {"best_bid": 0.49, "best_ask": 0.51},
    }

    # Tick 1: at t=1005s (elapsed 5s < 30s cutoff) -> orders placed
    engine._update_market_strategy(slug, poll_data, now=1005.0)
    mstate = engine.markets[slug]
    assert mstate.order_id_up is not None
    assert mstate.order_id_down is not None
    assert mstate.order_status_up == "RESTING"
    assert mstate.order_status_down == "RESTING"
    assert mstate.entry_cancelled_timeout is False

    # Tick 2: at t=1031s (elapsed 31s >= 30s cutoff) -> cancel both orders
    engine._update_market_strategy(slug, poll_data, now=1031.0)
    assert mstate.entry_cancelled_timeout is True
    assert mstate.order_status_up == "CANCELLED"
    assert mstate.order_status_down == "CANCELLED"
    assert mstate.status == "TIMEOUT_NO_FILL"
    assert "10% window timeout" in mstate.last_action
    assert len(cancelled_orders) == 2


def test_live_trader_skips_orders_on_late_start_window():
    """If bot attaches to a window with > 10% elapsed, skip opening quotes entirely."""
    engine = LiveTraderEngine()
    engine.mode = "live"
    engine.is_running = True
    slug = "btc-up-or-down-5m"
    start_time = 1000.0

    engine.place_live_quote = MagicMock()

    mkt = {
        "conditionId": "0xwin_late",
        "slug": "btc-up-down-late",
        "up_token": "tok_up",
        "down_token": "tok_dn",
        "start_ts": start_time,
        "end_ts": start_time + 300.0,
    }
    poll_data = {
        "market": mkt,
        "up_book": {"best_bid": 0.49, "best_ask": 0.51},
        "down_book": {"best_bid": 0.49, "best_ask": 0.51},
    }

    # First update at t=1035s (elapsed 35s > 30s)
    engine._update_market_strategy(slug, poll_data, now=1035.0)
    mstate = engine.markets[slug]
    assert mstate.entry_cancelled_timeout is True
    assert mstate.order_id_up is None
    assert mstate.order_id_down is None
    assert engine.place_live_quote.call_count == 0
    assert mstate.status == "TIMEOUT_NO_FILL"


def test_live_trader_keeps_opposite_leg_open_if_one_filled_before_timeout():
    """If 1 leg filled before timeout, the other leg stays open hoping to complete pair merge."""
    engine = LiveTraderEngine()
    engine.mode = "paper"
    engine.is_running = True
    slug = "btc-up-or-down-5m"
    start_time = 1000.0

    mkt = {
        "conditionId": "0xwin_leg1",
        "slug": "btc-up-down-leg1",
        "up_token": "tok_up",
        "down_token": "tok_dn",
        "start_ts": start_time,
        "end_ts": start_time + 300.0,
    }

    # Tick 1: at t=1010s, UP ask drops to 0.48 -> UP fills!
    poll_1 = {
        "market": mkt,
        "up_book": {"best_bid": 0.47, "best_ask": 0.48},
        "down_book": {"best_bid": 0.49, "best_ask": 0.52},
    }
    engine._update_market_strategy(slug, poll_1, now=1010.0)
    mstate = engine.markets[slug]
    assert mstate.filled_up is True
    assert mstate.filled_down is False
    assert mstate.entry_cancelled_timeout is False

    # Tick 2: at t=1035s (elapsed 35s >= 30s cutoff)
    # Opposite leg (DOWN) must NOT be cancelled because 1 leg already filled
    poll_2 = {
        "market": mkt,
        "up_book": {"best_bid": 0.47, "best_ask": 0.49},
        "down_book": {"best_bid": 0.48, "best_ask": 0.51},
    }
    engine._update_market_strategy(slug, poll_2, now=1035.0)
    assert mstate.entry_cancelled_timeout is False
    assert mstate.order_status_down != "CANCELLED"

    # Tick 3: at t=1050s, DOWN ask drops to 0.48 -> DOWN fills and completes pair!
    poll_3 = {
        "market": mkt,
        "up_book": {"best_bid": 0.47, "best_ask": 0.49},
        "down_book": {"best_bid": 0.47, "best_ask": 0.48},
    }
    engine._update_market_strategy(slug, poll_3, now=1050.0)
    assert mstate.filled_down is True
    assert mstate.pair_captured is True
    assert mstate.status == "PAIR_MERGED"


def test_live_trader_rollover_resets_timeout_state():
    """After window rollover, entry_cancelled_timeout resets to False for the next window."""
    engine = LiveTraderEngine()
    engine.mode = "live"
    engine.is_running = True
    slug = "btc-up-or-down-5m"
    start_1 = 1000.0

    engine.place_live_quote = MagicMock(return_value={"order_id": "ord_1", "status": "RESTING"})
    engine.cancel_live_order = MagicMock(return_value=True)

    mkt_1 = {
        "conditionId": "0xwin1",
        "slug": "btc-01",
        "up_token": "tok_up_1",
        "down_token": "tok_dn_1",
        "start_ts": start_1,
        "end_ts": start_1 + 300.0,
    }
    # Times out in window 1
    engine._update_market_strategy(slug, {"market": mkt_1, "up_book": {}, "down_book": {}}, now=1035.0)
    mstate = engine.markets[slug]
    assert mstate.entry_cancelled_timeout is True

    # Rollover to window 2 at t=1305s (5s into new window)
    start_2 = 1300.0
    mkt_2 = {
        "conditionId": "0xwin2",
        "slug": "btc-02",
        "up_token": "tok_up_2",
        "down_token": "tok_dn_2",
        "start_ts": start_2,
        "end_ts": start_2 + 300.0,
    }
    engine._update_market_strategy(slug, {"market": mkt_2, "up_book": {}, "down_book": {}}, now=1305.0)
    assert mstate.entry_cancelled_timeout is False
    assert mstate.condition_id == "0xwin2"


# ============================================================================
# BACKTEST REPLAY ENGINE TESTS
# ============================================================================

def test_backtest_cancels_unfilled_at_10_percent_elapsed():
    """Ticks that only cross resting price after 10% window elapsed are not filled."""
    # 5m window starts at 1000.0; 10% cutoff is at 1030.0 (30s)
    snaps = [
        _make_snap(1005.0, mid=0.50, up_ask=0.51, down_ask=0.51),
        _make_snap(1015.0, mid=0.50, up_ask=0.51, down_ask=0.51),
        _make_snap(1025.0, mid=0.50, up_ask=0.51, down_ask=0.51),
        # At 1040s (> 1030s), price crosses to 0.48
        _make_snap(1040.0, mid=0.48, up_ask=0.48, down_ask=0.54,
                   tape=[{"asset": UP_TOKEN, "price": 0.48}]),
    ]

    # With default 10% entry timeout: cancelled, 0 fills
    p_timeout = BacktestParams(offset=0.02, entry_timeout_pct=0.10)
    res_timeout = _simulate_window(snaps, p_timeout)
    assert res_timeout.filled_up is False
    assert res_timeout.filled_down is False
    assert res_timeout.pair_captured is False

    # With entry timeout disabled (0.0): fill is captured
    p_no_timeout = BacktestParams(offset=0.02, entry_timeout_pct=0.0)
    res_no_timeout = _simulate_window(snaps, p_no_timeout)
    assert res_no_timeout.filled_up is True


def test_backtest_skips_window_with_late_start_delay():
    """If first tick delay >= 10% duration (e.g. 35s in 5m), quotes are cancelled immediately."""
    snaps = [
        # First tick is at 1035s (start_ts = 1000.0, delay = 35s > 30s)
        _make_snap(1035.0, mid=0.48, up_ask=0.48, down_ask=0.52,
                   tape=[{"asset": UP_TOKEN, "price": 0.48}]),
    ]
    p = BacktestParams(offset=0.02, entry_timeout_pct=0.10)
    res = _simulate_window(snaps, p)
    assert res.filled_up is False
    assert res.filled_down is False


def test_backtest_allows_second_leg_fill_after_timeout_if_first_filled_early():
    """If leg 1 fills before 10% cutoff, leg 2 is allowed to fill after 10% cutoff."""
    snaps = [
        # Leg 1 (UP) fills at t=1010s (< 1030s cutoff)
        _make_snap(1010.0, mid=0.50, up_ask=0.48, down_ask=0.52,
                   tape=[{"asset": UP_TOKEN, "price": 0.48}]),
        # Leg 2 (DOWN) fills at t=1045s (> 1030s cutoff)
        _make_snap(1045.0, mid=0.50, up_ask=0.52, down_ask=0.48,
                   tape=[{"asset": DN_TOKEN, "price": 0.48}]),
    ]
    p = BacktestParams(offset=0.02, entry_timeout_pct=0.10)
    res = _simulate_window(snaps, p)
    assert res.filled_up is True
    assert res.filled_down is True
    assert res.pair_captured is True
