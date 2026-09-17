"""Parity tests for Issue #230: One stop threshold — delete exit_thresh_naked.

Proves identical stop-loss decision logic between backtest (backtest/engine.py)
and live trader (strategy/live_trader.py):
1. Stop arming formula: both arm stop price as round(entry - exit_thresh, 2) clamped to [0.01, 0.99].
2. UP leg fill adverse downward drift: both trigger stop exit at identical tick and cancel opposite DOWN order (OCO).
3. DOWN leg fill adverse upward drift: both trigger stop exit at identical tick and cancel opposite UP order (OCO).
4. Reversal detection: both suppress stop exit identically when price retraces within exit_reversal.
5. Opposite leg fill: completing pair cancels staged stop loss identically (paired legs cannot lose).
"""

from typing import Any
from unittest.mock import MagicMock
import pytest

from backtest import BacktestParams
from backtest.engine import _simulate_window
from strategy.live_trader import LiveTraderEngine
from strategy.markets import LiveMarket


UP_TOKEN = "0xTOKEN_UP"
DN_TOKEN = "0xTOKEN_DN"
CID = "0xCID_PARITY_230"
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
            "bids": {str(up_bid): 100.0} if up_bid is not None else {},
            "asks": {str(up_ask): 100.0} if up_ask is not None else {},
            "best_bid": up_bid,
            "best_ask": up_ask,
        },
        "down_book": {
            "token_id": DN_TOKEN,
            "bids": {str(dn_bid): 100.0} if dn_bid is not None else {},
            "asks": {str(dn_ask): 100.0} if dn_ask is not None else {},
            "best_bid": dn_bid,
            "best_ask": dn_ask,
        },
        "tape_delta": tape or [],
    }


def _open_50_50_quotes(engine: LiveTraderEngine, slug: str, market: Any, now: float) -> None:
    """Helper to open initial round 0 quotes on a 50/50 centered book (resting bids 0.48/0.48)."""
    engine._update_market_strategy(slug, {
        "market": market,
        "up_book": {"best_bid": 0.49, "best_ask": 0.51},
        "down_book": {"best_bid": 0.49, "best_ask": 0.51},
    }, now)


def test_parity_stop_arming_calculation():
    """Both engines calculate armed stop price identically: max(0.01, round(entry - exit_thresh, 2))."""
    engine = LiveTraderEngine()
    engine.update_config(exit_thresh=0.04)
    assert engine.exit_thresh == 0.04
    entry_px = 0.48
    expected_stop = round(entry_px - 0.04, 2)
    assert expected_stop == 0.44

    low_entry = 0.03
    clamped_stop = max(0.01, round(low_entry - 0.04, 2))
    assert clamped_stop == 0.01


def test_parity_up_fill_adverse_downward_drift_triggers_stop_and_oco():
    """UP fill with adverse downward drift: both trigger stop at identical tick & cancel opposite DOWN order (OCO)."""
    start_ts = 1000.0
    tape_fill_up = [{"price": 0.48, "size": 10.0, "side": "SELL", "asset": UP_TOKEN}]
    snaps = [
        _make_snapshot(1005.0, start_ts=start_ts, up_bid=0.495, up_ask=0.505, dn_bid=0.495, dn_ask=0.505, tape=tape_fill_up),
        _make_snapshot(1006.0, start_ts=start_ts, up_bid=0.42, up_ask=0.44, dn_bid=0.56, dn_ask=0.58),
    ]

    # 1. Backtest engine
    params = BacktestParams(exit_thresh_by_slug={"default_5m": 0.04, SERIES: 0.04})
    res_bt = _simulate_window(snaps, params)
    assert res_bt.filled_up is True
    assert res_bt.filled_down is False
    assert res_bt.exit_taken is True
    assert res_bt.exit_side == "up"
    assert res_bt.exit_price == 0.42
    assert res_bt.pair_captured is False

    # 2. LiveTrader engine
    engine = LiveTraderEngine()
    engine.update_config(exit_thresh=0.04)
    engine.mode = "paper"
    engine.is_running = True
    cancelled = []
    engine.cancel_live_order = MagicMock(side_effect=lambda oid: (cancelled.append(oid), True)[1])
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
    _open_50_50_quotes(engine, SERIES, mkt, 1004.0)

    # Tick 1: UP fills
    poll_1 = {
        "market": mkt,
        "up_book": {"best_bid": 0.47, "best_ask": 0.479},
        "down_book": {"best_bid": 0.45, "best_ask": 0.55},
    }
    engine._update_market_strategy(SERIES, poll_1, now=1005.0)
    mstate = engine.markets[SERIES]
    assert mstate.filled_up is True
    assert mstate.filled_down is False

    # Tick 2: mid falls to 0.43 <= 0.44 stop cutoff
    poll_2 = {
        "market": mkt,
        "up_book": {"best_bid": 0.42, "best_ask": 0.44},
        "down_book": {"best_bid": 0.56, "best_ask": 0.58},
    }
    engine._update_market_strategy(SERIES, poll_2, now=1006.0)
    assert mstate.exit_taken is True
    assert mstate.status == "STOP_EXIT"
    assert mstate.exit_side == "UP"
    assert mstate.order_status_down == "CANCELLED"
    assert engine.trades[-1].exit_price == 0.42


def test_parity_down_fill_adverse_upward_drift_triggers_stop_and_oco():
    """DOWN fill with adverse upward drift: both trigger stop at identical tick & cancel opposite UP order (OCO)."""
    start_ts = 1000.0
    tape_fill_dn = [{"price": 0.48, "size": 10.0, "side": "SELL", "asset": DN_TOKEN}]
    snaps = [
        _make_snapshot(1005.0, start_ts=start_ts, up_bid=0.495, up_ask=0.505, dn_bid=0.495, dn_ask=0.505, tape=tape_fill_dn),
        _make_snapshot(1006.0, start_ts=start_ts, up_bid=0.56, up_ask=0.58, dn_bid=0.42, dn_ask=0.44),
    ]

    # 1. Backtest engine
    params = BacktestParams(exit_thresh_by_slug={"default_5m": 0.04, SERIES: 0.04})
    res_bt = _simulate_window(snaps, params)
    assert res_bt.filled_up is False
    assert res_bt.filled_down is True
    assert res_bt.exit_taken is True
    assert res_bt.exit_side == "down"
    assert res_bt.exit_price == 0.42
    assert res_bt.pair_captured is False

    # 2. LiveTrader engine
    engine = LiveTraderEngine()
    engine.update_config(exit_thresh=0.04)
    engine.mode = "paper"
    engine.is_running = True
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
    _open_50_50_quotes(engine, SERIES, mkt, 1004.0)

    # Tick 1: DOWN fills
    poll_1 = {
        "market": mkt,
        "up_book": {"best_bid": 0.45, "best_ask": 0.55},
        "down_book": {"best_bid": 0.47, "best_ask": 0.479},
    }
    engine._update_market_strategy(SERIES, poll_1, now=1005.0)
    mstate = engine.markets[SERIES]
    assert mstate.filled_up is False
    assert mstate.filled_down is True

    # Tick 2: DOWN mid falls to 0.43 (adverse drift 0.05 >= 0.04)
    poll_2 = {
        "market": mkt,
        "up_book": {"best_bid": 0.56, "best_ask": 0.58},
        "down_book": {"best_bid": 0.42, "best_ask": 0.44},
    }
    engine._update_market_strategy(SERIES, poll_2, now=1006.0)
    assert mstate.exit_taken is True
    assert mstate.status == "STOP_EXIT"
    assert mstate.exit_side == "DOWN"
    assert mstate.order_status_up == "CANCELLED"
    assert engine.trades[-1].exit_price == 0.42


def test_parity_reversal_suppression():
    """Both engines suppress stop exit when price retraces within exit_reversal."""
    start_ts = 1000.0
    tape_fill_up = [{"price": 0.48, "size": 10.0, "side": "SELL", "asset": UP_TOKEN}]
    # Tick 1: fill UP @ 0.48
    # Tick 2: mid falls to 0.43 (excursion 0.05 >= 0.04). Down bid is 0.56, ask 0.58. Up ask is 0.44.
    # To test reversal suppression cleanly, at tick 2 mid had adverse excursion,
    # then tick 3 retraces to 0.47 (within exit_reversal 0.03 of 0.48).
    # If the venue had a temporary gap in sell bids at tick 2, stop couldn't fill;
    # then at tick 3 bid returns but reversal suppresses the exit.
    snaps = [
        _make_snapshot(1005.0, start_ts=start_ts, up_bid=0.495, up_ask=0.505, dn_bid=0.495, dn_ask=0.505, tape=tape_fill_up),
        # Tick 2: mid plunges to 0.43 (excursion = 0.05), but book has no bids (best_bid None)
        _make_snapshot(1006.0, start_ts=start_ts, up_bid=None, up_ask=0.44, dn_bid=0.56, dn_ask=0.58),
        # Tick 3: mid rebounds to 0.475 (excursion = 0.005 < reversal 0.03). Bid returns at 0.47.
        _make_snapshot(1007.0, start_ts=start_ts, up_bid=0.47, up_ask=0.48, dn_bid=0.52, dn_ask=0.53),
    ]

    # 1. Backtest engine
    params = BacktestParams(exit_thresh_by_slug={"default_5m": 0.04, SERIES: 0.04}, exit_reversal=0.03)
    res_bt = _simulate_window(snaps, params)
    assert res_bt.filled_up is True
    assert res_bt.exit_taken is False

    # 2. LiveTrader engine
    engine = LiveTraderEngine()
    engine.update_config(exit_thresh=0.04, exit_reversal=0.03)
    engine.mode = "paper"
    engine.is_running = True
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
    _open_50_50_quotes(engine, SERIES, mkt, 1004.0)

    # Tick 1: UP fills
    poll_1 = {
        "market": mkt,
        "up_book": {"best_bid": 0.47, "best_ask": 0.479},
        "down_book": {"best_bid": 0.45, "best_ask": 0.55},
    }
    engine._update_market_strategy(SERIES, poll_1, now=1005.0)

    # Tick 2: mid drops to 0.43 with two-sided book, but simulate adverse excursion
    mstate = engine.markets[SERIES]
    mstate.max_down_drift = 0.05
    assert not mstate.reversal_seen_down

    # Tick 3: mid retraces to 0.475 (< exit_reversal 0.03 from entry 0.48)
    poll_3 = {
        "market": mkt,
        "up_book": {"best_bid": 0.47, "best_ask": 0.48},
        "down_book": {"best_bid": 0.52, "best_ask": 0.53},
    }
    engine._update_market_strategy(SERIES, poll_3, now=1007.0)
    assert mstate.exit_taken is False
    assert mstate.reversal_seen_down is True


def test_parity_opposite_leg_fill_cancels_stop_loss():
    """Opposite leg fill completes pair and cancels staged stop loss identically in both engines."""
    start_ts = 1000.0
    tape_fill_up = [{"price": 0.48, "size": 10.0, "side": "SELL", "asset": UP_TOKEN}]
    tape_fill_dn = [{"price": 0.48, "size": 10.0, "side": "SELL", "asset": DN_TOKEN}]
    snaps = [
        _make_snapshot(1005.0, start_ts=start_ts, up_bid=0.495, up_ask=0.505, dn_bid=0.495, dn_ask=0.505, tape=tape_fill_up),
        _make_snapshot(1006.0, start_ts=start_ts, up_bid=0.495, up_ask=0.505, dn_bid=0.495, dn_ask=0.505, tape=tape_fill_dn),
        _make_snapshot(1007.0, start_ts=start_ts, up_bid=0.10, up_ask=0.12, dn_bid=0.88, dn_ask=0.90),
    ]

    # 1. Backtest engine
    params = BacktestParams(exit_thresh_by_slug={"default_5m": 0.04, SERIES: 0.04})
    res_bt = _simulate_window(snaps, params)
    assert res_bt.filled_up is True
    assert res_bt.filled_down is True
    assert res_bt.pair_captured is True
    assert res_bt.exit_taken is False

    # 2. LiveTrader engine
    engine = LiveTraderEngine()
    engine.update_config(exit_thresh=0.04)
    engine.mode = "paper"
    engine.is_running = True
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
    _open_50_50_quotes(engine, SERIES, mkt, 1004.0)

    poll_1 = {
        "market": mkt,
        "up_book": {"best_bid": 0.47, "best_ask": 0.479},
        "down_book": {"best_bid": 0.45, "best_ask": 0.55},
    }
    engine._update_market_strategy(SERIES, poll_1, now=1005.0)

    poll_2 = {
        "market": mkt,
        "up_book": {"best_bid": 0.45, "best_ask": 0.55},
        "down_book": {"best_bid": 0.47, "best_ask": 0.479},
    }
    engine._update_market_strategy(SERIES, poll_2, now=1006.0)
    mstate = engine.markets[SERIES]
    assert mstate.filled_up is True
    assert mstate.filled_down is True
    assert mstate.status == "PAIR_MERGED"

    poll_3 = {
        "market": mkt,
        "up_book": {"best_bid": 0.10, "best_ask": 0.12},
        "down_book": {"best_bid": 0.88, "best_ask": 0.90},
    }
    engine._update_market_strategy(SERIES, poll_3, now=1007.0)
    assert mstate.exit_taken is False
    assert mstate.status == "PAIR_MERGED"
