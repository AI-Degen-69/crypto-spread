"""Parity tests for Issue #229: Dead zone governs the end of the window.

Proves identical decision behavior between backtest (backtest/engine.py)
and live trader (strategy/live_trader.py):
1. Both skip entering a window whose first tick arrives in the dead zone.
2. Both cancel resting unfilled entry quotes upon entering the dead zone.
3. Both exit an unpaired leg at executable book bid under naked_leg_at_expiry="close".
4. Both hold an unpaired leg to window settlement under naked_leg_at_expiry="hold".
5. Both produce identical dead zone cutoffs whether specified as pct or sec.
"""

import time
from typing import Any
from unittest.mock import MagicMock
import pytest

from backtest import BacktestParams
from backtest.engine import _simulate_window
from strategy.live_trader import LiveTraderEngine
from strategy.markets import LiveMarket
from strategy.book_math import dead_zone_cutoff_seconds, is_in_dead_zone


UP_TOKEN = "0xTOKEN_UP"
DN_TOKEN = "0xTOKEN_DN"
CID = "0xCID_PARITY_229"
SLUG = "btc-updown-5m-parity"
SERIES = "btc-up-or-down-5m"
DUR = 300.0


def _make_snapshot(ts: float, start_ts: float, up_bid: float = 0.48, up_ask: float = 0.49,
                   dn_bid: float = 0.50, dn_ask: float = 0.51, tape: list | None = None) -> dict:
    return {
        "ts": ts,
        "iso": "2026-09-03T12:00:00+00:00",
        "series": SERIES,
        "duration": int(DUR),
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
            "bids": {str(up_bid): 100.0},
            "asks": {str(up_ask): 100.0},
            "best_bid": up_bid,
            "best_ask": up_ask,
        },
        "down_book": {
            "token_id": DN_TOKEN,
            "bids": {str(dn_bid): 100.0},
            "asks": {str(dn_ask): 100.0},
            "best_bid": dn_bid,
            "best_ask": dn_ask,
        },
        "tape_delta": tape or [],
    }


def test_parity_unit_equivalence():
    """dead_zone_cutoff_seconds is identical for pct=0.10 on 300s window vs sec=30.0."""
    assert dead_zone_cutoff_seconds(300.0, 0.10, "pct") == 30.0
    assert dead_zone_cutoff_seconds(300.0, 30.0, "sec") == 30.0
    assert dead_zone_cutoff_seconds(900.0, 0.10, "pct") == 90.0
    assert dead_zone_cutoff_seconds(900.0, 90.0, "sec") == 90.0


def test_parity_first_tick_in_dead_zone_skips_entry():
    """Both engines skip entering if the first tick is already in the dead zone."""
    start_ts = 1000.0
    # Window length 300s, dead zone 10% (30s) -> dead zone is t >= 1270s (remaining <= 30s).
    # First tick arrives at t = 1275s (remaining 25s <= 30s).
    snaps = [
        _make_snapshot(1275.0, start_ts=start_ts),
        _make_snapshot(1280.0, start_ts=start_ts),
    ]

    # 1. Backtest engine
    params = BacktestParams(dead_zone_val=0.10, dead_zone_unit="pct")
    res_bt = _simulate_window(snaps, params)
    assert res_bt.entered is False
    assert res_bt.filled_up is False
    assert res_bt.filled_down is False

    # 2. LiveTrader engine
    engine = LiveTraderEngine(load_persisted=False, dead_zone_val=0.10, dead_zone_unit="pct")
    engine.is_running = True
    poll_data = {
        "market": {
            "conditionId": CID,
            "slug": SLUG,
            "up_token": UP_TOKEN,
            "down_token": DN_TOKEN,
            "start_ts": start_ts,
            "end_ts": start_ts + DUR,
        },
        "up_book": {"best_bid": 0.48, "best_ask": 0.49},
        "down_book": {"best_bid": 0.50, "best_ask": 0.51},
    }
    engine._update_market_strategy(SERIES, poll_data, now=1275.0)
    mstate = engine.markets[SERIES]
    assert mstate.late_start_skip is True
    assert mstate.order_id_up is None
    assert mstate.order_id_down is None


def test_parity_unfilled_quotes_cancelled_in_dead_zone():
    """Both engines cancel resting unfilled quotes upon entering the dead zone."""
    start_ts = 1000.0
    # Tick 1 at 1005s (remaining 295s): active quoting, no fills (asks above resting bid 0.485).
    # Tick 2 at 1275s (remaining 25s <= 30s cutoff): entered dead zone without fill.
    snaps = [
        _make_snapshot(1005.0, start_ts=start_ts, up_ask=0.52, dn_ask=0.52),
        _make_snapshot(1275.0, start_ts=start_ts, up_ask=0.52, dn_ask=0.52),
    ]

    # 1. Backtest engine
    params = BacktestParams(dead_zone_val=0.10, dead_zone_unit="pct")
    res_bt = _simulate_window(snaps, params)
    assert res_bt.filled_up is False
    assert res_bt.filled_down is False
    assert res_bt.pair_captured is False

    # 2. LiveTrader engine
    engine = LiveTraderEngine(load_persisted=False, dead_zone_val=0.10, dead_zone_unit="pct")
    engine.mode = "live"
    engine.is_running = True
    cancelled = []
    engine.place_live_quote = MagicMock(side_effect=lambda tok, px, sz, side: {"order_id": f"ord_{tok}", "status": "RESTING"})
    engine.cancel_live_order = MagicMock(side_effect=lambda oid: (cancelled.append(oid), True)[1])

    poll_1 = {
        "market": {
            "conditionId": CID,
            "slug": SLUG,
            "up_token": UP_TOKEN,
            "down_token": DN_TOKEN,
            "start_ts": start_ts,
            "end_ts": start_ts + DUR,
        },
        "up_book": {"best_bid": 0.48, "best_ask": 0.52},
        "down_book": {"best_bid": 0.50, "best_ask": 0.52},
    }
    engine._update_market_strategy(SERIES, poll_1, now=1005.0)
    mstate = engine.markets[SERIES]
    assert mstate.order_status_up == "RESTING"
    assert mstate.order_status_down == "RESTING"

    # Tick 2 reaches dead zone:
    engine._update_market_strategy(SERIES, poll_1, now=1275.0)
    assert mstate.order_status_up == "CANCELLED"
    assert mstate.order_status_down == "CANCELLED"
    assert mstate.status == "DEAD_ZONE_NO_FILL"
    assert len(cancelled) == 2


def _open_50_50_quotes(engine: LiveTraderEngine, slug: str, market: Any, now: float) -> None:
    """Helper to open initial round 0 quotes on a 50/50 centered book (resting bids 0.48/0.48)."""
    engine._update_market_strategy(slug, {
        "market": market,
        "up_book": {"best_bid": 0.49, "best_ask": 0.51},
        "down_book": {"best_bid": 0.49, "best_ask": 0.51},
    }, now)


def test_parity_unpaired_leg_close_in_dead_zone():
    """Under naked_leg_at_expiry='close', an unpaired leg exits at book bid in the dead zone."""
    start_ts = 1000.0
    # Tick 1: balanced 0.50 book, resting_up=0.48, resting_dn=0.48. Tape print fills UP @ 0.48.
    # Tick 2 at 1275s: in dead zone. UP book bid is 0.46. Both engines should exit UP at 0.46.
    tape_tick1 = [{"price": 0.48, "size": 10.0, "side": "SELL", "asset": UP_TOKEN}]
    snaps = [
        _make_snapshot(1005.0, start_ts=start_ts, up_bid=0.495, up_ask=0.505, dn_bid=0.495, dn_ask=0.505, tape=tape_tick1),
        _make_snapshot(1275.0, start_ts=start_ts, up_bid=0.46, up_ask=0.48, dn_bid=0.45, dn_ask=0.55),
    ]

    # 1. Backtest engine
    params = BacktestParams(dead_zone_val=0.10, dead_zone_unit="pct", naked_leg_at_expiry="close")
    res_bt = _simulate_window(snaps, params)
    assert res_bt.exit_taken is True
    assert res_bt.exit_side == "up"
    assert res_bt.exit_price == 0.46

    # 2. LiveTrader engine
    engine = LiveTraderEngine(load_persisted=False, dead_zone_val=0.10, dead_zone_unit="pct", naked_leg_at_expiry="close")
    engine.mode = "paper"
    engine.start()
    mkt = LiveMarket(
        condition_id=CID,
        market_slug=SLUG,
        up_token=UP_TOKEN,
        down_token=DN_TOKEN,
        start_ts=start_ts,
        end_ts=start_ts + DUR,
        tick_size=0.01,
        neg_risk=False,
    )
    poll_1 = {
        "market": mkt,
        "up_book": {"best_bid": 0.47, "best_ask": 0.479},  # fills resting UP @ 0.48
        "down_book": {"best_bid": 0.45, "best_ask": 0.55},
    }
    # Open 50/50 quotes
    _open_50_50_quotes(engine, SERIES, mkt, 1004.0)
    engine._update_market_strategy(SERIES, poll_1, now=1005.0)
    mstate = engine.markets[SERIES]
    assert mstate.filled_up is True
    assert mstate.filled_down is False

    # Tick 2 at 1275s in dead zone: UP book bid is 0.46
    poll_2 = {
        "market": mkt,
        "up_book": {"best_bid": 0.46, "best_ask": 0.48},
        "down_book": {"best_bid": 0.45, "best_ask": 0.55},
    }
    engine._update_market_strategy(SERIES, poll_2, now=1275.0)
    assert mstate.exit_taken is True
    assert mstate.status == "STOP_EXIT"
    assert mstate.exit_side == "UP"
    assert engine.trades[-1].exit_price == 0.46
    assert "Dead-zone expiry exit" in engine.trades[-1].notes


def test_parity_unpaired_leg_hold_in_dead_zone():
    """Under naked_leg_at_expiry='hold', both engines hold the unpaired leg past the dead zone to settlement."""
    start_ts = 1000.0
    tape_tick1 = [{"price": 0.48, "size": 10.0, "side": "SELL", "asset": UP_TOKEN}]
    snaps = [
        _make_snapshot(1005.0, start_ts=start_ts, up_bid=0.495, up_ask=0.505, dn_bid=0.495, dn_ask=0.505, tape=tape_tick1),
        _make_snapshot(1275.0, start_ts=start_ts, up_bid=0.46, up_ask=0.48, dn_bid=0.45, dn_ask=0.55),
    ]

    # 1. Backtest engine
    params = BacktestParams(dead_zone_val=0.10, dead_zone_unit="pct", naked_leg_at_expiry="hold")
    res_bt = _simulate_window(snaps, params)
    assert res_bt.exit_taken is False
    assert res_bt.filled_up is True
    assert res_bt.filled_down is False

    # 2. LiveTrader engine
    engine = LiveTraderEngine(load_persisted=False, dead_zone_val=0.10, dead_zone_unit="pct", naked_leg_at_expiry="hold")
    engine.mode = "paper"
    engine.start()
    mkt = LiveMarket(
        condition_id=CID,
        market_slug=SLUG,
        up_token=UP_TOKEN,
        down_token=DN_TOKEN,
        start_ts=start_ts,
        end_ts=start_ts + DUR,
        tick_size=0.01,
        neg_risk=False,
    )
    poll_1 = {
        "market": mkt,
        "up_book": {"best_bid": 0.47, "best_ask": 0.479},  # fills resting UP @ 0.48
        "down_book": {"best_bid": 0.45, "best_ask": 0.55},
    }
    _open_50_50_quotes(engine, SERIES, mkt, 1004.0)
    engine._update_market_strategy(SERIES, poll_1, now=1005.0)
    mstate = engine.markets[SERIES]
    assert mstate.filled_up is True
    assert mstate.filled_down is False

    # Tick 2 at 1275s in dead zone: UP book bid is 0.46. With hold, no exit fires.
    poll_2 = {
        "market": mkt,
        "up_book": {"best_bid": 0.46, "best_ask": 0.48},
        "down_book": {"best_bid": 0.45, "best_ask": 0.55},
    }
    engine._update_market_strategy(SERIES, poll_2, now=1275.0)
    assert mstate.exit_taken is False
    assert mstate.status == "FILLED_UP"
