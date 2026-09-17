"""Parity test suite between backtest/engine.py and strategy/live_trader.py for fresh_start (Rule 13).

Proves that under identical tick streams:
1. Two pairs can be completed in a single window with identical fill prices and counts.
2. A stop exit can be followed by a fresh start and subsequent pair merge.
3. Markets clean inside the dead zone stay terminal (zero re-entry) across both engines.
"""
from __future__ import annotations
import pytest

from backtest.engine import BacktestParams, _simulate_window
from strategy.live_trader import LiveTraderEngine
from strategy.markets import LiveMarket


UP_TOKEN = "0xAAAA_up_token"
DN_TOKEN = "0xBBBB_dn_token"
CID = "0xCID_PARITY"
SLUG = "eth-up-or-down-15m"
SERIES = "eth-up-or-down-15m"
DUR = 900.0


def _build_tick(ts: float, mid_up: float, up_bb: float, up_ba: float, dn_bb: float, dn_ba: float,
                start_ts: float, end_ts: float) -> dict:
    """Build a tick compatible with both backtest _simulate_window and live_trader._update_market_strategy."""
    return {
        "ts": ts,
        "iso": "2026-08-29T00:00:00+00:00",
        "series": SERIES,
        "duration": DUR,
        "label": "ETH 15m",
        "cid": CID,
        "slug": SLUG,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "t_rem": max(0.0, end_ts - ts),
        "up_token": UP_TOKEN,
        "down_token": DN_TOKEN,
        "up_book": {
            "token_id": UP_TOKEN,
            "bids": {},
            "asks": {},
            "best_bid": up_bb,
            "best_ask": up_ba,
            "malformed": 0,
        },
        "down_book": {
            "token_id": DN_TOKEN,
            "bids": {},
            "asks": {},
            "best_bid": dn_bb,
            "best_ask": dn_ba,
            "malformed": 0,
        },
        "tape_delta": [],
        "mid": mid_up,
        "touch_pair": round(up_ba + dn_ba, 4),
        "resting_pair": 0.96,
        "queue_up": 0.0,
        "queue_down": 0.0,
        "err": None,
    }


def _run_backtest(ticks: list[dict], params: BacktestParams):
    return _simulate_window(ticks, params)


def _run_live(ticks: list[dict], offset: float = 0.02, exit_thresh: float = 0.05, dead_zone_val: float = 0.10):
    engine = LiveTraderEngine(selected_markets=[SLUG], dead_zone_val=dead_zone_val)
    engine.stream_bridge.start = lambda *a, **k: None
    engine._schedule_wallet_balance_fetch = lambda *a, **k: None
    engine.offset = offset
    engine.exit_thresh = exit_thresh
    engine.enable_leg_chase = False
    engine.start()

    mkt = LiveMarket(
        condition_id=CID,
        market_slug=SLUG,
        up_token=UP_TOKEN,
        down_token=DN_TOKEN,
        start_ts=ticks[0]["start_ts"],
        end_ts=ticks[0]["end_ts"],
        tick_size=0.01,
        neg_risk=False,
    )

    for tick in ticks:
        engine._update_market_strategy(SLUG, {
            "market": mkt,
            "up_book": tick["up_book"],
            "down_book": tick["down_book"],
        }, now=tick["ts"])

    return engine, engine.markets[SLUG]


def test_parity_two_pairs_in_one_window():
    """Both engines capture 2 pairs in one 15m window under oscillating ticks."""
    t0 = 1788000000.0
    start_ts = t0
    end_ts = t0 + DUR  # 900s
    ticks = []

    # Round 1:
    # Tick 0 (t+10s): Mid 0.50 -> Resting quotes at 0.48 / 0.48
    ticks.append(_build_tick(t0 + 10, 0.50, 0.49, 0.51, 0.49, 0.51, start_ts, end_ts))
    # Tick 1 (t+11s): UP best ask touches 0.479 <= 0.48 -> UP fills at 0.48
    ticks.append(_build_tick(t0 + 11, 0.48, 0.47, 0.479, 0.51, 0.52, start_ts, end_ts))
    # Tick 2 (t+12s): DN best ask touches 0.479 <= 0.48 -> DN fills at 0.48 -> Pair 1 Merges!
    ticks.append(_build_tick(t0 + 12, 0.52, 0.51, 0.52, 0.47, 0.479, start_ts, end_ts))

    # Round 2 (fresh start outside dead zone):
    # Tick 3 (t+20s): Mid 0.50 -> Fresh start! Resting quotes re-anchor at 0.48 / 0.48
    ticks.append(_build_tick(t0 + 20, 0.50, 0.49, 0.51, 0.49, 0.51, start_ts, end_ts))
    # Tick 4 (t+21s): DN best ask touches 0.479 <= 0.48 -> DN fills at 0.48
    ticks.append(_build_tick(t0 + 21, 0.52, 0.51, 0.52, 0.47, 0.479, start_ts, end_ts))
    # Tick 5 (t+22s): UP best ask touches 0.479 <= 0.48 -> UP fills at 0.48 -> Pair 2 Merges!
    ticks.append(_build_tick(t0 + 22, 0.48, 0.47, 0.479, 0.51, 0.52, start_ts, end_ts))

    params = BacktestParams(offset=0.02, exit_thresh_by_slug={"default_15m": 0.05}, dead_zone_val=0.10, enable_leg_chase=False)
    bt_res = _run_backtest(ticks, params)
    _, live_state = _run_live(ticks, offset=0.02, exit_thresh=0.05, dead_zone_val=0.10)

    # Parity verification:
    assert bt_res.pairs_count == 2
    assert live_state.pairs_count == 2

    assert bt_res.stops_count == 0
    assert live_state.stops_count == 0

    assert bt_res.pair_captured is True
    assert live_state.pair_captured is True

    # PnL parity: 2 pairs bought at 0.48 + 0.48 = 0.96 each -> profit 0.04 per pair
    # In backtest (pnl_cents): 2 * 4.0 = 8.0 cents
    # In live (5 shares by default): 2 * (5 * 1.00 - 5 * 0.96) = 2 * 0.20 = $0.40
    assert abs(bt_res.pnl_cents - 8.0) < 1e-4
    assert abs(live_state.realized_pnl_usd - 0.40) < 1e-4


def test_parity_stop_exit_then_fresh_pair_merge():
    """Both engines handle stop exit on round 1, re-enter on round 2, and complete pair merge."""
    t0 = 1788000000.0
    start_ts = t0
    end_ts = t0 + DUR
    ticks = []

    # Round 1:
    # Tick 0 (t+10s): Mid 0.50 -> Resting quotes 0.48 / 0.48
    ticks.append(_build_tick(t0 + 10, 0.50, 0.49, 0.51, 0.49, 0.51, start_ts, end_ts))
    # Tick 1 (t+11s): UP fills at 0.48
    ticks.append(_build_tick(t0 + 11, 0.48, 0.47, 0.479, 0.51, 0.52, start_ts, end_ts))
    # Tick 2 (t+12s): Mid moves adversely to 0.42 (delta 0.06 >= 0.05 exit_thresh) -> Stop Exit on UP!
    ticks.append(_build_tick(t0 + 12, 0.42, 0.41, 0.43, 0.57, 0.59, start_ts, end_ts))

    # Round 2 (fresh start outside dead zone, remaining time ~850s):
    # Tick 3 (t+50s): Mid stabilizes at 0.50 -> Fresh start! Quotes 0.48 / 0.48
    ticks.append(_build_tick(t0 + 50, 0.50, 0.49, 0.51, 0.49, 0.51, start_ts, end_ts))
    # Tick 4 (t+51s): UP fills at 0.48
    ticks.append(_build_tick(t0 + 51, 0.48, 0.47, 0.479, 0.51, 0.52, start_ts, end_ts))
    # Tick 5 (t+52s): DN fills at 0.48 -> Pair Merges!
    ticks.append(_build_tick(t0 + 52, 0.52, 0.51, 0.52, 0.47, 0.479, start_ts, end_ts))

    params = BacktestParams(offset=0.02, exit_thresh_by_slug={"default_15m": 0.05}, dead_zone_val=0.10, enable_leg_chase=False)
    bt_res = _run_backtest(ticks, params)
    _, live_state = _run_live(ticks, offset=0.02, exit_thresh=0.05, dead_zone_val=0.10)

    # Both recorded 1 stop and 1 pair
    assert bt_res.stops_count == 1
    assert live_state.stops_count == 1

    assert bt_res.pairs_count == 1
    assert live_state.pairs_count == 1

    assert bt_res.pair_captured is True
    assert live_state.pair_captured is True


def test_parity_dead_zone_blocks_fresh_entry():
    """Markets clean inside dead zone (e.g. remaining 80s < 90s dead zone) do NOT re-enter in either engine."""
    t0 = 1788000000.0
    start_ts = t0
    end_ts = t0 + DUR  # 900s, 10% dead zone = 90s -> dead zone starts at t0 + 810s
    ticks = []

    # Round 1 completes at t0 + 100s
    ticks.append(_build_tick(t0 + 10, 0.50, 0.49, 0.51, 0.49, 0.51, start_ts, end_ts))
    ticks.append(_build_tick(t0 + 11, 0.48, 0.47, 0.479, 0.51, 0.52, start_ts, end_ts))
    ticks.append(_build_tick(t0 + 12, 0.52, 0.51, 0.52, 0.47, 0.479, start_ts, end_ts))

    # Ticks inside dead zone: t0 + 820s (remaining 80s < 90s)
    ticks.append(_build_tick(t0 + 820, 0.50, 0.49, 0.51, 0.49, 0.51, start_ts, end_ts))
    # Best ask touches resting price inside dead zone:
    ticks.append(_build_tick(t0 + 821, 0.48, 0.47, 0.479, 0.51, 0.52, start_ts, end_ts))

    params = BacktestParams(offset=0.02, exit_thresh_by_slug={"default_15m": 0.05}, dead_zone_val=0.10, enable_leg_chase=False)
    bt_res = _run_backtest(ticks, params)
    _, live_state = _run_live(ticks, offset=0.02, exit_thresh=0.05, dead_zone_val=0.10)

    # Both engines must stay at exactly 1 pair, no second entry
    assert bt_res.pairs_count == 1
    assert live_state.pairs_count == 1

    assert bt_res.stops_count == 0
    assert live_state.stops_count == 0

    assert bt_res.pair_captured is True
    assert live_state.pair_captured is True
