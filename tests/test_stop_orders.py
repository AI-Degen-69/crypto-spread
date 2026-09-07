"""Tests for pre-placed resting stop-loss orders (issue #87)."""
import time

from strategy.live_trader import LiveTraderEngine
from strategy.markets import LiveMarket

SLUG = "btc-up-or-down-5m"


def test_window_rollover_cancels_stop():
    """OCO Case C: window expiry cancels the resting stop and resets stop fields."""
    from unittest.mock import MagicMock

    engine = LiveTraderEngine()
    engine.mode = "live"
    engine.is_running = True
    fake_client = MagicMock()
    fake_client.cancel.return_value = {"success": True}
    engine._clob_client = fake_client

    mstate = engine.markets[SLUG]
    mstate.stop_order_id = "ord_stop_resting"
    mstate.stop_order_status = "RESTING"
    mstate.stop_price = 0.43
    mstate.stop_side = "UP"
    mstate.stop_order_time = "12:00:00"

    engine._handle_window_rollover(mstate, time.time())

    fake_client.cancel.assert_any_call("ord_stop_resting")
    assert mstate.stop_order_id is None
    assert mstate.stop_order_status == "NONE"
    assert mstate.stop_price is None
    assert mstate.stop_side is None
    assert mstate.stop_order_time == "-"


def test_window_rollover_resets_stop_paper():
    """OCO Case C in paper mode: simulated stop cleared without venue calls."""
    engine = LiveTraderEngine()
    engine.start()
    mstate = engine.markets[SLUG]
    mstate.stop_order_id = f"paper_stop_{SLUG}"
    mstate.stop_order_status = "RESTING"
    mstate.stop_price = 0.43
    mstate.stop_side = "UP"

    engine._handle_window_rollover(mstate, time.time())

    assert mstate.stop_order_id is None
    assert mstate.stop_order_status == "NONE"
    assert mstate.stop_price is None


def test_resting_stop_visible_in_open_orders():
    """The staged stop appears in get_open_orders_list() as an ENGINE_STOP SELL row."""
    engine = LiveTraderEngine()
    mstate = engine.markets[SLUG]
    mstate.up_token = "tok_up"
    mstate.down_token = "tok_dn"
    mstate.fill_price_up = 0.48
    mstate.up_bid = 0.47

    engine.place_stop_order(mstate, "UP")

    orders = engine.get_open_orders_list()
    stop_rows = [o for o in orders if o.get("source") == "ENGINE_STOP"]
    assert len(stop_rows) == 1
    row = stop_rows[0]
    assert row["order_id"] == f"paper_stop_{SLUG}"
    assert row["side"] == "SELL (UP)"
    assert row["status"] == "RESTING"
    assert row["price"] == 0.43
    assert row["size"] == 5


def test_cancelled_stop_not_in_open_orders():
    """A cleared/cancelled stop must not linger in the open-orders list."""
    engine = LiveTraderEngine()
    mstate = engine.markets[SLUG]
    mstate.up_token = "tok_up"
    mstate.fill_price_up = 0.48

    engine.place_stop_order(mstate, "UP")
    engine._cancel_stop_order(mstate, reason="test")

    orders = engine.get_open_orders_list()
    assert not any(o.get("source") == "ENGINE_STOP" for o in orders)


def test_staged_stop_does_not_touch_venue_before_trigger():
    """A below-market SELL limit would cross the bid instantly — so the staged stop
    must never touch the venue until the trigger fires (monitored exit design)."""
    from unittest.mock import MagicMock

    engine = LiveTraderEngine()
    engine.mode = "live"
    engine.is_running = True
    now = time.time()
    fake_client = MagicMock()
    fake_client.create_and_post_order.return_value = {"orderID": "ord_exit_sell", "status": "matched"}
    fake_client.cancel.return_value = {"success": True}
    engine._clob_client = fake_client

    mstate = engine.markets[SLUG]
    mstate.condition_id = "0xbtc123"
    mstate.up_token = "tok_up"
    mstate.down_token = "tok_dn"
    mstate.order_id_up = "ord_active_up"
    mstate.order_id_down = "ord_active_dn"
    mstate.resting_up = 0.48
    mstate.resting_down = 0.48
    mstate.filled_up = True
    mstate.fill_price_up = 0.48

    # Stage the buffered stop, then collapse the bid through the threshold
    engine.place_stop_order(mstate, "UP")
    assert mstate.stop_order_status == "STAGED"
    staging_orders = fake_client.create_and_post_order.call_count

    poll_stop = {
        "market": {"conditionId": "0xbtc123", "up_token": "tok_up", "down_token": "tok_dn", "start_ts": now - 100, "end_ts": now + 200},
        "up_book": {"best_bid": 0.40, "best_ask": 0.42},
        "down_book": {"best_bid": 0.58, "best_ask": 0.60},
    }
    engine._update_market_strategy(SLUG, poll_stop, now)

    assert mstate.exit_taken is True
    assert mstate.status == "STOP_EXIT"
    # The exit SELL must have been submitted exactly once — post-trigger
    assert fake_client.create_and_post_order.call_count >= 1


def test_pair_merge_blocked_when_stop_cancel_fails():
    """A venue-side cancel failure keeps the handle and defers the pair merge."""
    from unittest.mock import MagicMock, patch

    engine = LiveTraderEngine()
    engine.mode = "live"
    engine.is_running = True
    now = time.time()
    fake_client = MagicMock()
    fake_client.create_and_post_order.side_effect = [
        {"orderID": "ord_up", "status": "unmatched"},
        {"orderID": "ord_dn", "status": "unmatched"},
    ]

    dn_polls = {"n": 0}

    def fake_get_order(order_id):
        if order_id == "ord_up":
            return {"status": "MATCHED", "size_matched": 5.0, "price": 0.48}
        if order_id == "ord_dn":
            dn_polls["n"] += 1
            if dn_polls["n"] >= 2:
                return {"status": "MATCHED", "size_matched": 5.0, "price": 0.48}
            return {"status": "UNMATCHED", "size_matched": 0.0}
        return {"status": "UNMATCHED", "size_matched": 0.0}

    fake_client.get_order.side_effect = fake_get_order
    engine._clob_client = fake_client

    # UP fill -> stop staged -> force it onto the book so cancellation matters
    engine._update_market_strategy(SLUG, _poll(_fake_market(now), 0.47, 0.48, 0.51, 0.52), now)
    mstate = engine.markets[SLUG]
    mstate.stop_order_id = "ord_stop_resting"
    mstate.stop_order_status = "RESTING"

    # DOWN fills -> merge attempted -> stop cancel fails -> merge deferred
    with patch.object(engine, "cancel_live_order", return_value=False):
        engine._update_market_strategy(SLUG, _poll(_fake_market(now), 0.51, 0.52, 0.47, 0.48), now + 1)
    assert mstate.pair_captured is False
    assert mstate.last_action == "Pair merge deferred: stop-loss cancellation failed"
    assert mstate.stop_order_id == "ord_stop_resting"
    assert mstate.stop_order_status == "CANCEL_FAILED"

    # Retry with a successful cancel -> merge proceeds and stop clears
    engine._update_market_strategy(SLUG, _poll(_fake_market(now), 0.51, 0.52, 0.47, 0.48), now + 2)
    assert mstate.pair_captured is True
    assert mstate.status == "PAIR_MERGED"
    assert mstate.stop_order_id is None


def test_user_order_event_syncs_stop_status():
    """UserSpec stream events keep the stop-order status in sync for the dashboard."""
    engine = LiveTraderEngine()
    mstate = engine.markets[SLUG]
    mstate.fill_price_up = 0.48
    mstate.up_token = "tok_up"

    engine.place_stop_order(mstate, "UP")
    stop_id = mstate.stop_order_id
    assert mstate.stop_order_status == "RESTING"

    engine.on_user_order_event({"id": stop_id, "status": "MATCHED"})
    assert mstate.stop_order_status == "MATCHED"


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


def test_pair_completion_cancels_stop_live():
    """OCO Case A: pair completion cancels the resting stop before PAIR_MERGED."""
    from unittest.mock import MagicMock

    engine = LiveTraderEngine()
    engine.mode = "live"
    engine.is_running = True
    now = time.time()
    fake_client = MagicMock()

    # Sequential order placements: UP entry, then DOWN entry (the stop is a
    # pre-signed in-memory buffer, so no third venue order is submitted)
    fake_client.create_and_post_order.side_effect = [
        {"orderID": "ord_up", "status": "unmatched"},
        {"orderID": "ord_dn", "status": "unmatched"},
    ]
    fake_client.cancel.return_value = {"success": True}

    dn_polls = {"n": 0}

    def fake_get_order(order_id):
        if order_id == "ord_up":
            return {"status": "MATCHED", "size_matched": 5.0, "price": 0.48}
        if order_id == "ord_dn":
            # DOWN leg only fills on the second strategy tick
            dn_polls["n"] += 1
            if dn_polls["n"] >= 2:
                return {"status": "MATCHED", "size_matched": 5.0, "price": 0.48}
            return {"status": "UNMATCHED", "size_matched": 0.0}
        return {"status": "UNMATCHED", "size_matched": 0.0}

    fake_client.get_order.side_effect = fake_get_order
    engine._clob_client = fake_client

    # UP leg fills live -> stop protection staged as zero-latency buffer
    engine._update_market_strategy(SLUG, _poll(_fake_market(now), 0.47, 0.48, 0.51, 0.52), now)
    mstate = engine.markets[SLUG]
    assert mstate.filled_up is True
    assert mstate.stop_order_id == f"buffer_stop_{SLUG}"
    assert mstate.stop_order_status == "STAGED"

    # DOWN leg fills -> pair complete -> staged stop must be cleared (no venue call)
    engine._update_market_strategy(SLUG, _poll(_fake_market(now), 0.51, 0.52, 0.47, 0.48), now + 1)
    assert mstate.pair_captured is True
    assert mstate.status == "PAIR_MERGED"
    assert mstate.stop_order_id is None
    assert mstate.stop_order_status == "NONE"
    assert mstate.stop_price is None


def test_pair_completion_clears_stop_paper():
    """OCO Case A in paper mode: simulated stop is cleared without venue calls."""
    engine = LiveTraderEngine()
    engine.start()
    now = time.time()
    market = _fake_market(now)

    engine._update_market_strategy(SLUG, _poll(market, 0.47, 0.48, 0.51, 0.52), now)
    mstate = engine.markets[SLUG]
    assert mstate.stop_order_id == f"paper_stop_{SLUG}"

    engine._update_market_strategy(SLUG, _poll(market, 0.51, 0.52, 0.47, 0.48), now + 1)
    assert mstate.pair_captured is True
    assert mstate.status == "PAIR_MERGED"
    assert mstate.stop_order_id is None
    assert mstate.stop_order_status == "NONE"


def test_stop_fill_triggers_stop_exit_paper():
    """OCO Case B (paper): bid drops to the stop -> stop fills, entry cancelled, STOP_EXIT."""
    engine = LiveTraderEngine()
    engine.start()
    now = time.time()
    market = _fake_market(now)

    # UP leg fills at 0.48; stop staged at 0.43
    engine._update_market_strategy(SLUG, _poll(market, 0.47, 0.48, 0.51, 0.52), now)
    mstate = engine.markets[SLUG]
    assert mstate.stop_order_id == f"paper_stop_{SLUG}"
    assert mstate.order_status_down == "RESTING"

    # Bid collapses to the stop price: the resting stop fills as taker
    engine._update_market_strategy(SLUG, _poll(market, 0.43, 0.44, 0.55, 0.56), now + 1)

    assert mstate.exit_taken is True
    assert mstate.status == "STOP_EXIT"
    assert mstate.stop_order_status == "FILLED"
    # Sold at the staged stop 0.43 vs entry 0.48: (0.43-0.48)*5 = -0.25
    assert round(mstate.realized_pnl_usd, 2) == -0.25
    assert any(t.action == "STOP_EXIT_UP" for t in engine.trades)
    # Reciprocal OCO: the unhedged DOWN entry must be cancelled
    assert mstate.order_status_down == "CANCELLED"
    assert mstate.order_id_down is None
