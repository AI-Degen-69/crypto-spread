"""Tests for pre-placed resting stop-loss orders (issue #87)."""
import time

from strategy.live_trader import LiveTraderEngine
from strategy.markets import LiveMarket

SLUG = "btc-up-or-down-5m"


def _fake_market(now: float) -> LiveMarket:
    return LiveMarket(
        condition_id="0xabc123",
        market_slug="btc-up-down-5m",
        up_token="tok_up",
        down_token="tok_dn",
        start_ts=now - 10,
        end_ts=now + 290,
        tick_size=0.01,
        neg_risk=False,
    )


def _poll(market, up_bid, up_ask, dn_bid, dn_ask) -> dict:
    return {
        "market": market,
        "up_book": {"best_bid": up_bid, "best_ask": up_ask},
        "down_book": {"best_bid": dn_bid, "best_ask": dn_ask},
    }


def test_place_stop_order_resting_paper():
    """place_stop_order stages an idempotent resting stop at fill - exit_thresh."""
    engine = LiveTraderEngine()
    mstate = engine.markets[SLUG]
    mstate.fill_price_up = 0.48
    mstate.up_token = "tok_up"

    engine.place_stop_order(mstate, "UP")

    assert mstate.stop_order_id == f"paper_stop_{SLUG}"
    assert mstate.stop_order_status == "RESTING"
    assert mstate.stop_price == 0.43  # 0.48 - 0.05
    assert mstate.stop_side == "UP"
    assert mstate.stop_order_time != "-"


def test_place_stop_order_idempotent():
    """A second placement call must not duplicate or overwrite the staged stop."""
    engine = LiveTraderEngine()
    mstate = engine.markets[SLUG]
    mstate.fill_price_up = 0.48
    mstate.up_token = "tok_up"

    engine.place_stop_order(mstate, "UP")
    first_id = mstate.stop_order_id
    first_time = mstate.stop_order_time
    engine.place_stop_order(mstate, "UP")

    assert mstate.stop_order_id == first_id
    assert mstate.stop_order_time == first_time


def test_single_leg_fill_places_stop_paper():
    """A single UP leg fill automatically stages the stop-loss protection order."""
    engine = LiveTraderEngine()
    engine.start()
    now = time.time()
    market = _fake_market(now)

    engine._update_market_strategy(SLUG, _poll(market, 0.47, 0.48, 0.51, 0.52), now)
    mstate = engine.markets[SLUG]

    assert mstate.filled_up is True
    assert mstate.filled_down is False
    assert mstate.stop_order_id == f"paper_stop_{SLUG}"
    assert mstate.stop_order_status == "RESTING"
    assert mstate.stop_side == "UP"
    assert mstate.stop_price == 0.43
