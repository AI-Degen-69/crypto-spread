"""Tests for LiveTraderEngine."""
import pytest
import time
from strategy.live_trader import LiveTraderEngine, get_live_trader_engine, MarketLiveState
from strategy.markets import LiveMarket


def test_live_trader_engine_init():
    engine = LiveTraderEngine()
    assert not engine.is_running
    assert engine.mode == "paper"
    assert engine.offset == 0.02
    assert engine.exit_thresh == 0.05
    assert engine.shares == 5
    assert len(engine.markets) == 5
    assert "btc-up-or-down-5m" in engine.markets
    assert engine.markets["btc-up-or-down-5m"].resting_up == 0.48
    assert engine.markets["btc-up-or-down-5m"].resting_down == 0.48


def test_live_trader_config_update():
    engine = LiveTraderEngine()
    state = engine.update_config(offset=0.03, exit_thresh=0.08, shares=10, mode="paper", starting_balance=2500.0)
    assert engine.offset == 0.03
    assert engine.exit_thresh == 0.08
    assert engine.shares == 10
    assert engine.starting_balance == 2500.0
    assert engine.markets["btc-up-or-down-5m"].resting_up == 0.47
    assert engine.markets["btc-up-or-down-5m"].order_shares == 10
    assert state["portfolio_value"] == 2500.0


def test_live_trader_pair_merge_execution():
    engine = LiveTraderEngine()
    engine.start()
    slug = "btc-up-or-down-5m"
    now = time.time()

    fake_market = LiveMarket(
        condition_id="0xabc123",
        market_slug="btc-up-down-5m",
        up_token="tok_up",
        down_token="tok_dn",
        start_ts=now - 10,
        end_ts=now + 290,
        tick_size=0.01,
        neg_risk=False,
    )

    # First poll: resting bids 0.48 / 0.48, ask touches 0.48 on UP
    poll1 = {
        "market": fake_market,
        "up_book": {"best_bid": 0.47, "best_ask": 0.48},
        "down_book": {"best_bid": 0.51, "best_ask": 0.52},
    }
    engine._update_market_strategy(slug, poll1, now)
    mstate = engine.markets[slug]
    assert mstate.filled_up is True
    assert mstate.filled_down is False
    assert mstate.status == "FILLED_UP"

    # Second poll: ask touches 0.48 on DOWN -> PAIR MERGE!
    poll2 = {
        "market": fake_market,
        "up_book": {"best_bid": 0.51, "best_ask": 0.52},
        "down_book": {"best_bid": 0.47, "best_ask": 0.48},
    }
    engine._update_market_strategy(slug, poll2, now + 1)
    assert mstate.filled_down is True
    assert mstate.pair_captured is True
    assert mstate.status == "PAIR_MERGED"
    # Profit on 5 shares: (1.00 - (0.48 + 0.48)) * 5 = 0.04 * 5 = $0.20
    assert round(mstate.realized_pnl_usd, 2) == 0.20
    assert len(engine.trades) == 1
    assert engine.trades[0].action == "PAIR_MERGE"


def test_live_trader_stop_loss_exit():
    engine = LiveTraderEngine()
    engine.start()
    slug = "eth-up-or-down-5m"
    now = time.time()

    fake_market = LiveMarket(
        condition_id="0xeth123",
        market_slug="eth-up-down-5m",
        up_token="tok_eth_up",
        down_token="tok_eth_dn",
        start_ts=now - 10,
        end_ts=now + 290,
        tick_size=0.01,
        neg_risk=False,
    )

    # Fill UP at 0.48
    poll1 = {
        "market": fake_market,
        "up_book": {"best_bid": 0.47, "best_ask": 0.48},
        "down_book": {"best_bid": 0.51, "best_ask": 0.52},
    }
    engine._update_market_strategy(slug, poll1, now)
    mstate = engine.markets[slug]
    assert mstate.filled_up is True

    # Adverse drift: mid drops down to 0.44 (drift = 0.06 >= exit_thresh 0.05)
    poll2 = {
        "market": fake_market,
        "up_book": {"best_bid": 0.43, "best_ask": 0.45},
        "down_book": {"best_bid": 0.55, "best_ask": 0.57},
    }
    engine._update_market_strategy(slug, poll2, now + 1)
    assert mstate.exit_taken is True
    assert mstate.status == "STOP_EXIT"
    # Sold at best bid 0.43: (0.43 - 0.48) * 5 = -0.05 * 5 = -$0.25
    assert round(mstate.realized_pnl_usd, 2) == -0.25
    assert len(engine.trades) == 1
    assert engine.trades[0].action == "STOP_EXIT_UP"


def test_live_trader_reset_pnl():
    engine = LiveTraderEngine()
    engine.update_config(shares=5)
    mstate = engine.markets["btc-up-or-down-5m"]
    mstate.realized_pnl_usd = 15.0
    mstate.pairs_count = 3
    engine.reset_pnl()
    assert mstate.realized_pnl_usd == 0.0
    assert mstate.pairs_count == 0
    assert len(engine.trades) == 0
