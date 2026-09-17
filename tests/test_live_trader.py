"""Tests for LiveTraderEngine."""
from typing import Any
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


def _open_50_50_quotes(engine: LiveTraderEngine, slug: str, market: Any, now: float) -> None:
    """Helper to open initial round 0 quotes on a 50/50 centered book (resting bids 0.48/0.48)."""
    engine._update_market_strategy(slug, {
        "market": market,
        "up_book": {"best_bid": 0.49, "best_ask": 0.51},
        "down_book": {"best_bid": 0.49, "best_ask": 0.51},
    }, now)


def test_live_trader_pair_merge_execution():
    engine = LiveTraderEngine()
    engine.enable_leg_chase = False
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

    # Initial round 0 quoting at 0.50 mid -> resting bids 0.48 / 0.48
    _open_50_50_quotes(engine, slug, fake_market, now - 1)

    # First poll: resting bids 0.48 / 0.48, ask passes through 0.48 on UP
    poll1 = {
        "market": fake_market,
        "up_book": {"best_bid": 0.47, "best_ask": 0.479},
        "down_book": {"best_bid": 0.51, "best_ask": 0.52},
    }
    engine._update_market_strategy(slug, poll1, now)
    mstate = engine.markets[slug]
    assert mstate.filled_up is True
    assert mstate.filled_down is False
    assert mstate.status == "FILLED_UP"

    # Second poll: ask passes through 0.48 on DOWN -> PAIR MERGE!
    poll2 = {
        "market": fake_market,
        "up_book": {"best_bid": 0.51, "best_ask": 0.52},
        "down_book": {"best_bid": 0.47, "best_ask": 0.479},
    }
    engine._update_market_strategy(slug, poll2, now + 1)
    assert mstate.filled_down is True
    assert mstate.pair_captured is True
    assert mstate.status == "PAIR_MERGED"
    # Profit on 5 shares: (1.00 - (0.48 + 0.48)) * 5 = 0.04 * 5 = $0.20
    assert round(mstate.realized_pnl_usd, 2) == 0.20
    assert len(engine.trades) == 1
    assert engine.trades[0].action == "PAIR_MERGE"


def test_ws_book_update_not_regressed_by_stale_rest_poll():
    """Issue #171: a REST poll must never overwrite strictly newer WS data.

    Drives `on_book_update` (the WS writer) first, establishing a fresh book
    and a `mid` computed from it. Then drives `_update_market_strategy` (the
    REST-poll writer) with an older/stale-looking snapshot while the WS book
    is still fresh (within `ws_book_max_age_sec`, socket connected). The
    resulting `mid` must still reflect the WS data -- it must never regress
    to the staler REST value racing in behind it.
    """
    engine = LiveTraderEngine()
    engine.start()
    slug = "btc-up-or-down-5m"
    mstate = engine.markets[slug]
    now = time.time()

    fake_market = LiveMarket(
        condition_id="0xdef456",
        market_slug="btc-up-down-5m",
        up_token="tok_up",
        down_token="tok_dn",
        start_ts=now - 10,
        end_ts=now + 290,
        tick_size=0.01,
        neg_risk=False,
    )
    mstate.up_token = "tok_up"
    mstate.down_token = "tok_dn"

    # Socket reports itself live -- required for is_ws_book_fresh() to trust it.
    engine.stream_bridge.clob.is_connected = True

    # 1. WS callback delivers a fresh, newer book: up mid 0.61, down mid 0.37.
    engine.on_book_update("tok_up", bids={0.60: 10.0}, asks={0.62: 10.0})
    engine.on_book_update("tok_dn", bids={0.36: 10.0}, asks={0.38: 10.0})
    ws_mid = mstate.mid
    assert ws_mid is not None
    assert ws_mid > 0.55  # sanity: WS pushed mid up, away from 0.50 default

    # 2. A REST poll lands right behind it (same tick loop cadence) carrying a
    #    stale, lower-mid snapshot -- exactly the race the issue describes.
    #    WS is still fresh (age ~0s, well under ws_book_max_age_sec), so this
    #    REST data must be rejected for both legs.
    stale_poll = {
        "market": fake_market,
        "up_book": {"best_bid": 0.44, "best_ask": 0.46},
        "down_book": {"best_bid": 0.53, "best_ask": 0.55},
    }
    engine._update_market_strategy(slug, stale_poll, now)

    # mid must not have regressed to the older/staler REST-implied value.
    assert mstate.mid == ws_mid
    assert mstate.mid >= ws_mid
    # And the WS-authoritative best bid/ask must have survived the REST poll.
    assert mstate.up_bid == 0.60
    assert mstate.up_ask == 0.62
    assert mstate.down_bid == 0.36
    assert mstate.down_ask == 0.38


def test_rest_mid_recompute_cannot_tear_against_concurrent_ws_update(monkeypatch):
    """Issue #171: mid must stay consistent with the bests it was derived from.

    The REST path reads the four best bid/ask fields, computes the mid they
    imply, and writes it back. If a WS update lands between that read and that
    write, the REST thread writes a mid derived from a book that no longer
    exists -- `mstate.mid` then disagrees with `mstate.up_bid`/`up_ask` et al.

    The interleave is forced deterministically: the mid helper blocks on the
    REST path while a second thread drives `on_book_update` with a different
    book. Whichever writer lands last is fine; what must hold is that the
    final `mid` is the mid of the final bests.
    """
    import threading
    from strategy import book_math

    engine = LiveTraderEngine()
    engine.start()
    slug = "btc-up-or-down-5m"
    mstate = engine.markets[slug]
    now = time.time()

    fake_market = LiveMarket(
        condition_id="0xrace01",
        market_slug="btc-up-down-5m",
        up_token="tok_up",
        down_token="tok_dn",
        start_ts=now - 10,
        end_ts=now + 290,
        tick_size=0.01,
        neg_risk=False,
    )
    mstate.up_token = "tok_up"
    mstate.down_token = "tok_dn"
    engine.stream_bridge.clob.is_connected = True

    # Establish a fresh WS book so the REST legs are rejected and only the
    # unconditional mid recompute remains in play.
    engine.on_book_update("tok_up", bids={0.60: 10.0}, asks={0.62: 10.0})
    engine.on_book_update("tok_dn", bids={0.36: 10.0}, asks={0.38: 10.0})

    rest_thread = threading.current_thread()
    ws_started = threading.Event()
    ws_done = threading.Event()
    tripped = threading.Event()

    def _ws_writer():
        ws_started.wait(timeout=2.0)
        # A genuinely newer book, far from the one the REST path just read.
        engine.on_book_update("tok_up", bids={0.70: 10.0}, asks={0.72: 10.0})
        engine.on_book_update("tok_dn", bids={0.26: 10.0}, asks={0.28: 10.0})
        ws_done.set()

    real_mid = book_math.two_sided_mid

    def _blocking_mid(up: Any, down: Any) -> Any:
        # Only stall the REST path's own call, and only once.
        if threading.current_thread() is rest_thread and not tripped.is_set():
            tripped.set()
            ws_started.set()
            # If the recompute is correctly locked, the WS thread is blocked on
            # the lock and this wait times out -- that is the passing shape.
            ws_done.wait(timeout=0.5)
        return real_mid(up, down)

    monkeypatch.setattr(book_math, "two_sided_mid", _blocking_mid)

    writer = threading.Thread(target=_ws_writer, daemon=True)
    writer.start()
    engine._update_market_strategy(slug, {
        "market": fake_market,
        "up_book": {"best_bid": 0.44, "best_ask": 0.46},
        "down_book": {"best_bid": 0.53, "best_ask": 0.55},
    }, now)
    writer.join(timeout=5.0)
    assert not writer.is_alive()
    assert tripped.is_set(), "the racing interleave was never exercised"

    expected = real_mid(
        {"best_bid": mstate.up_bid, "best_ask": mstate.up_ask},
        {"best_bid": mstate.down_bid, "best_ask": mstate.down_ask})
    assert mstate.mid == expected, (
        f"mid {mstate.mid} was derived from a book that no longer exists; "
        f"current bests imply {expected}")


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

    # Initial round 0 quoting at 0.50 mid -> resting bids 0.48 / 0.48
    _open_50_50_quotes(engine, slug, fake_market, now - 1)

    # Fill UP at 0.48
    poll1 = {
        "market": fake_market,
        "up_book": {"best_bid": 0.47, "best_ask": 0.479},
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


def test_fetch_polymarket_account_value_mocked(monkeypatch):
    from unittest.mock import MagicMock
    from strategy.live_trader import fetch_polymarket_account_value
    from unittest.mock import patch

    monkeypatch.setenv("POLY_FUNDER", "0xee3b778a783510bc833384919f709e3d2fee1624")
    monkeypatch.setenv("POLY_PRIVATE_KEY", "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef")
    monkeypatch.setenv("POLY_API_KEY", "test_key")
    monkeypatch.setenv("POLY_API_SECRET", "test_secret")
    monkeypatch.setenv("POLY_API_PASSPHRASE", "test_passphrase")

    fake_client = MagicMock()
    fake_client.get_balance_allowance.return_value = {"balance": "81218581"}
    fake_clob_cls = MagicMock(return_value=fake_client)

    # Mock requests session
    fake_sess = MagicMock()
    fake_pos_resp = MagicMock()
    fake_pos_resp.ok = True
    fake_pos_resp.json.return_value = [
        {"asset": "tok1", "currentValue": 10.50},
        {"asset": "tok2", "currentValue": 5.25},
    ]
    fake_sess.get.return_value = fake_pos_resp

    import sys
    dummy_clob_client_mod = MagicMock()
    dummy_clob_client_mod.ClobClient = fake_clob_cls
    dummy_clob_types_mod = MagicMock()
    with patch.dict(sys.modules, {
        "py_clob_client_v2": MagicMock(),
        "py_clob_client_v2.client": dummy_clob_client_mod,
        "py_clob_client_v2.clob_types": dummy_clob_types_mod,
        "py_clob_client": MagicMock(),
        "py_clob_client.client": dummy_clob_client_mod,
        "py_clob_client.clob_types": dummy_clob_types_mod,
    }):
        res = fetch_polymarket_account_value(wallet_address="0xee3b778a783510bc833384919f709e3d2fee1624", session=fake_sess)
    assert res["success"] is True
    assert res["wallet_address"] == "0xee3b778a783510bc833384919f709e3d2fee1624"
    assert res["cash_balance"] == 81.22
    assert res["positions_value"] == 15.75
    assert res["net_value"] == 96.97
    assert res["open_positions"] == 2


def test_fetch_polymarket_account_value_fallback_without_double_counting(monkeypatch):
    from unittest.mock import MagicMock
    from strategy.live_trader import fetch_polymarket_account_value

    monkeypatch.setenv("POLY_PRIVATE_KEY", "")
    monkeypatch.setenv("POLY_API_KEY", "")

    fake_sess = MagicMock()
    fake_pos_resp = MagicMock(ok=True)
    fake_pos_resp.json.return_value = [
        {"asset": "tok1", "currentValue": 20.00},
    ]
    fake_val_resp = MagicMock(ok=True)
    fake_val_resp.json.return_value = [{"value": 100.00}]

    def mock_get(url, *args, **kwargs):
        if "positions" in url:
            return fake_pos_resp
        elif "value" in url:
            return fake_val_resp
        return MagicMock(ok=False)

    fake_sess.get.side_effect = mock_get

    res = fetch_polymarket_account_value(
        wallet_address="0xee3b778a783510bc833384919f709e3d2fee1624",
        session=fake_sess,
    )
    assert res["success"] is True
    assert res["wallet_address"] == "0xee3b778a783510bc833384919f709e3d2fee1624"
    assert res["positions_value"] == 20.00
    assert res["net_value"] == 100.00
    assert res["cash_balance"] == 80.00
    # Confirm no double-counting of positions_value
    assert res["net_value"] != 120.00


def test_fetch_polymarket_account_value_invalid_and_checksum_address(monkeypatch):
    from unittest.mock import MagicMock
    from strategy.live_trader import fetch_polymarket_account_value

    monkeypatch.setenv("POLY_PRIVATE_KEY", "")
    monkeypatch.setenv("POLY_API_KEY", "")

    # Invalid address formats
    res_bad = fetch_polymarket_account_value(wallet_address="not-an-address")
    assert res_bad["success"] is False
    assert len(res_bad["errors"]) > 0
    assert "Invalid EVM wallet address" in res_bad["errors"][0]

    res_short = fetch_polymarket_account_value(wallet_address="0x12345")
    assert res_short["success"] is False
    assert "Invalid EVM wallet address" in res_short["errors"][0]

    # Valid mixed-case checksum address should be accepted and lowercased
    fake_sess = MagicMock()
    fake_pos_resp = MagicMock(ok=True)
    fake_pos_resp.json.return_value = []
    fake_val_resp = MagicMock(ok=True)
    fake_val_resp.json.return_value = [{"value": 50.0}]

    def mock_get(url, *args, **kwargs):
        if "positions" in url:
            return fake_pos_resp
        return fake_val_resp

    fake_sess.get.side_effect = mock_get
    res_check = fetch_polymarket_account_value(
        wallet_address="0xEE3B778A783510BC833384919F709E3D2FEE1624",
        session=fake_sess,
    )
    assert res_check["success"] is True
    assert res_check["wallet_address"] == "0xee3b778a783510bc833384919f709e3d2fee1624"


def test_live_mode_locks_starting_balance(monkeypatch):
    from unittest.mock import patch
    engine = LiveTraderEngine()

    # In paper mode, starting balance is user-defined
    st_paper = engine.update_config(mode="paper", starting_balance=3000.0)
    assert engine.starting_balance == 3000.0
    assert st_paper["starting_balance"] == 3000.0

    # In live mode, starting balance must be locked to fetched net account value
    with patch("strategy.live_trader.fetch_polymarket_account_value") as mock_fetch:
        mock_fetch.return_value = {
            "success": True,
            "wallet_address": "0x1234567890abcdef",
            "net_value": 81.22,
            "cash_balance": 81.22,
            "positions_value": 0.0,
            "open_positions": 0,
        }
        st_live = engine.update_config(mode="live", starting_balance=9999.0, wallet_address="0x1234567890abcdef")
        assert engine.mode == "live"
        # 9999.0 should be ignored, real net_value 81.22 should be enforced
        assert engine.starting_balance == 81.22
        assert st_live["starting_balance"] == 81.22
        assert st_live["wallet_address"] == "0x1234567890abcdef"


def test_seed_demo_data():
    engine = LiveTraderEngine()
    engine.is_running = True  # Verify seed_demo_data stops running engine
    engine.seed_demo_data()
    assert engine.is_running is False
    state = engine.get_state()
    assert len(state["trades"]) == 7
    assert len(state["timeline"]) == 120
    assert state["pairs_merged"] == 6
    assert state["stops_triggered"] == 1
    assert state["total_trades"] == 7
    assert state["win_rate"] > 80.0
    assert "btc-up-or-down-5m" in state["markets"]
    assert state["markets"]["btc-up-or-down-5m"]["total_pnl_usd"] == 0.40
    assert state["markets"]["sol-up-or-down-5m"]["total_pnl_usd"] == -0.25


def test_live_trader_advance_pre_quoting():
    from unittest.mock import MagicMock
    engine = LiveTraderEngine()
    engine.mode = "live"
    engine.is_running = True
    slug = "btc-up-or-down-5m"
    now = time.time()

    # Mock place_live_quote
    order_seq = 100
    def mock_place_quote(token_id, price, size, side):
        nonlocal order_seq
        order_seq += 1
        return {"order_id": f"ord_{token_id}_{order_seq}", "status": "RESTING", "token_id": token_id, "price": price, "size": size, "side": side}

    engine.place_live_quote = MagicMock(side_effect=mock_place_quote)

    current_mkt = {
        "conditionId": "0xcur123",
        "slug": "btc-up-down-0900",
        "up_token": "tok_cur_up",
        "down_token": "tok_cur_dn",
        "start_ts": now - 5,
        "end_ts": now + 295,
    }
    next_mkt = {
        "conditionId": "0xnext456",
        "slug": "btc-up-down-0905",
        "up_token": "tok_next_up",
        "down_token": "tok_next_dn",
        "start_ts": now + 240,
        "end_ts": now + 540,
    }

    poll_data = {
        "market": current_mkt,
        "next_market": next_mkt,
        "up_book": {"best_bid": 0.49, "best_ask": 0.51},
        "down_book": {"best_bid": 0.49, "best_ask": 0.51},
    }

    # First update: places quotes for current market AND advance quotes for next market
    engine._update_market_strategy(slug, poll_data, now)
    mstate = engine.markets[slug]

    assert mstate.order_id_up is not None
    assert mstate.order_id_down is not None
    assert mstate.next_quoted is True
    assert mstate.next_order_id_up is not None
    assert mstate.next_order_id_down is not None
    assert mstate.next_condition_id == "0xnext456"

    # Now simulate rollover to next market: conditionId changes to 0xnext456
    poll_rollover = {
        "market": next_mkt,
        "next_market": None,
        "up_book": {"best_bid": 0.49, "best_ask": 0.51},
        "down_book": {"best_bid": 0.49, "best_ask": 0.51},
    }
    old_next_up = mstate.next_order_id_up
    old_next_dn = mstate.next_order_id_down

    engine._update_market_strategy(slug, poll_rollover, now + 241)
    # The promoted active orders should equal the previous advance pre-quotes
    assert mstate.condition_id == "0xnext456"
    assert mstate.order_id_up == old_next_up
    assert mstate.order_id_down == old_next_dn


def test_live_trader_clob_order_placement_and_cancellation():
    from unittest.mock import MagicMock
    engine = LiveTraderEngine()
    fake_client = MagicMock()
    fake_client.create_and_post_order.return_value = {"orderID": "ord_999", "status": "delayed"}
    fake_client.cancel.return_value = {"success": True}
    fake_client.cancel_all.return_value = {"success": True}
    fake_client.get_orders.return_value = [
        {"id": "ord_999", "asset_id": "tok_btc_up", "side": "BUY", "price": "0.48", "original_size": "5"}
    ]
    engine._clob_client = fake_client

    # Test place quote
    res = engine.place_live_quote("tok_btc_up", 0.48, 5.0, "BUY")
    assert res is not None
    assert res["order_id"] == "ord_999"
    assert res["status"] == "RESTING"

    # Test get open orders list
    orders = engine.get_open_orders_list()
    assert len(orders) >= 1
    assert orders[0]["order_id"] == "ord_999"

    # Test cancel single order
    ok = engine.cancel_live_order("ord_999")
    assert ok is True
    fake_client.cancel.assert_called_with("ord_999")

    # Test emergency cancel all
    m = engine.markets["btc-up-or-down-5m"]
    m.order_id_up = "ord_up"
    m.order_id_down = "ord_dn"
    m.next_order_id_up = "ord_next_up"
    m.next_order_id_down = "ord_next_dn"
    m.next_quoted = True

    cancel_res = engine.cancel_all_orders()
    assert cancel_res["ok"] is True
    assert fake_client.cancel_all.called
    assert m.order_id_up is None
    assert m.order_id_down is None
    assert m.next_order_id_up is None
    assert m.next_quoted is False


def test_live_trader_live_stop_loss_order_routing():
    from unittest.mock import MagicMock
    engine = LiveTraderEngine()
    engine.mode = "live"
    engine.is_running = True
    slug = "btc-up-or-down-5m"
    now = time.time()

    fake_client = MagicMock()
    fake_client.create_and_post_order.return_value = {"orderID": "ord_stop_sell", "status": "matched"}
    fake_client.cancel.return_value = {"success": True}
    engine._clob_client = fake_client

    mstate = engine.markets[slug]
    mstate.condition_id = "0xbtc123"
    mstate.up_token = "tok_btc_up"
    mstate.down_token = "tok_btc_dn"
    mstate.order_id_up = "ord_active_up"
    mstate.order_id_down = "ord_active_dn"
    mstate.filled_up = True
    mstate.filled_down = False
    mstate.resting_up = 0.48
    mstate.resting_down = 0.48

    # Adverse drift: mid drops to 0.43, i.e. 0.05 below the 0.48 entry price,
    # which meets exit_thresh. Issue #209: the excursion is measured from the
    # entry, not from 0.50, so the mid that trips the stop moves with the fill.
    poll_stop = {
        "market": {"conditionId": "0xbtc123", "up_token": "tok_btc_up", "down_token": "tok_btc_dn", "start_ts": now - 100, "end_ts": now + 200},
        "up_book": {"best_bid": 0.42, "best_ask": 0.44},
        "down_book": {"best_bid": 0.56, "best_ask": 0.58},
    }

    engine._update_market_strategy(slug, poll_stop, now)
    assert mstate.exit_taken is True
    assert mstate.status == "STOP_EXIT"
    # Verify open down order was cancelled
    fake_client.cancel.assert_called_with("ord_active_dn")


def test_live_order_flow_smoke(monkeypatch):
    """Verify test_live_order_flow CLI runs successfully in dry-run mode."""
    from scripts.test_live_order_flow import main
    monkeypatch.setattr("scripts.test_live_order_flow.fetch_polymarket_account_value", lambda *_args, **_kwargs: {"success": True, "cash_balance": 100.0, "net_value": 100.0, "open_positions": 0})
    monkeypatch.setattr("scripts.test_live_order_flow.fetch_live_series_market", lambda *_args, **_kwargs: {"conditionId": "0x123", "up_token": "tok_up", "slug": "btc-up-5m", "end_ts": time.time() + 300})
    monkeypatch.setattr("sys.argv", ["test_live_order_flow.py", "--dry-run"])
    ret = main()
    assert ret == 0


def test_load_persisted_trades_restores_wallet_address(tmp_path, monkeypatch):
    meta_file = tmp_path / "live_trade_meta.json"
    trades_file = tmp_path / "live_trades.jsonl"
    meta_file.write_text('{"starting_balance": 1500.0, "wallet_address": "0x1234567890abcdef1234567890abcdef12345678"}')
    trades_file.write_text("")
    monkeypatch.setattr("strategy.live_trader.META_FILE", meta_file)
    monkeypatch.setattr("strategy.live_trader.TRADES_FILE", trades_file)

    engine = LiveTraderEngine()
    engine._load_persisted_trades()
    assert engine.starting_balance == 1500.0
    assert engine.wallet_address == "0x1234567890abcdef1234567890abcdef12345678"


def test_sync_wallet_trades_unmatched_start_marker(monkeypatch):
    engine = LiveTraderEngine()
    fake_activities = [
        {"slug": "btc-up-or-down-5m-1788380000", "outcome": "Down", "side": "BUY", "timestamp": 100, "usdcSize": 2.4, "type": "TRADE"}
    ]
    class FakeResponse:
        ok = True
        def json(self):
            return fake_activities

    class FakeSession:
        def get(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr("strategy.live_trader._get_thread_session", lambda: FakeSession())
    res = engine.sync_wallet_trades(wallet_address="0x1234567890abcdef1234", start_marker="non_existent_marker")
    assert res["success"] is False
    assert "not found in wallet activities" in res["error"]


def test_unified_stream_bridge_is_rtds_running():
    from strategy.streaming import UnifiedStreamBridge
    bridge = UnifiedStreamBridge()
    assert bridge.is_rtds_running is False
    bridge.is_running = True
    assert bridge.is_rtds_running is True


def test_live_trader_engine_market_selection():
    # By tokens and durations
    engine = LiveTraderEngine(tokens=["SOL"], durations=[900])
    assert len(engine.markets) == 1
    assert "sol-up-or-down-15m" in engine.markets
    assert engine.markets["sol-up-or-down-15m"].label == "SOL 15m"

    # By explicit selected_markets slugs
    engine2 = LiveTraderEngine(selected_markets=["btc-up-or-down-5m", "btc-up-or-down-15m"])
    assert len(engine2.markets) == 2
    assert "btc-up-or-down-5m" in engine2.markets
    assert "btc-up-or-down-15m" in engine2.markets


def test_live_trader_dynamic_reconfiguration():
    engine = LiveTraderEngine()
    assert len(engine.markets) == 5

    # Reconfigure to only two 15m markets
    state = engine.update_config(selected_markets=["eth-up-or-down-15m", "sol-up-or-down-15m"])
    assert set(engine.markets.keys()) == {"eth-up-or-down-15m", "sol-up-or-down-15m"}
    assert "eth-up-or-down-15m" in state["markets"]
    assert "sol-up-or-down-15m" in state["markets"]
    assert len(state["markets"]) == 2

    # Reconfigure using tokens and durations
    engine.update_config(tokens=["BTC", "BNB"], durations=[300])
    assert set(engine.markets.keys()) == {"btc-up-or-down-5m", "bnb-up-or-down-5m"}


def test_live_trader_deselected_market_order_cancellation(monkeypatch):
    engine = LiveTraderEngine()
    cancelled_orders = []
    monkeypatch.setattr(engine, "cancel_live_order", lambda oid: cancelled_orders.append(oid) or True)

    m = engine.markets["btc-up-or-down-5m"]
    m.order_id_up = "ord_up_btc"
    m.order_id_down = "ord_dn_btc"
    m.next_order_id_up = "ord_next_up"
    m.next_order_id_down = "ord_next_dn"

    # Reconfigure without btc-up-or-down-5m
    engine.update_config(selected_markets=["eth-up-or-down-5m"])
    assert "btc-up-or-down-5m" not in engine.markets
    assert "ord_up_btc" in cancelled_orders
    assert "ord_dn_btc" in cancelled_orders
    assert "ord_next_up" in cancelled_orders
    assert "ord_next_dn" in cancelled_orders


def test_live_trader_spot_fanout_to_multiple_series():
    engine = LiveTraderEngine(selected_markets=["btc-up-or-down-5m", "btc-up-or-down-15m"])
    engine.on_spot_tick("btcusdt", 1700000000000, 68500.0)

    assert engine.markets["btc-up-or-down-5m"].spot_price == 68500.0
    assert engine.markets["btc-up-or-down-15m"].spot_price == 68500.0


def test_live_trader_deselection_with_open_position_fails():
    engine = LiveTraderEngine(selected_markets=["btc-up-or-down-5m", "eth-up-or-down-5m"])
    engine.markets["btc-up-or-down-5m"].filled_up = True

    with pytest.raises(ValueError, match="Cannot deselect active market"):
        engine.update_config(selected_markets=["eth-up-or-down-5m"])

    # If stop exit has completed (exit_taken=True), deselection should succeed
    engine.markets["btc-up-or-down-5m"].exit_taken = True
    engine.update_config(selected_markets=["eth-up-or-down-5m"])
    assert "btc-up-or-down-5m" not in engine.markets


def test_live_trader_deselection_aborts_on_failed_cancellation(monkeypatch):
    engine = LiveTraderEngine(selected_markets=["btc-up-or-down-5m", "eth-up-or-down-5m"])
    m = engine.markets["btc-up-or-down-5m"]
    m.order_id_up = "ord_fail_cancel"

    # Simulate venue cancellation failure
    monkeypatch.setattr(engine, "cancel_live_order", lambda _oid: False)

    engine.update_config(selected_markets=["eth-up-or-down-5m"])
    # btc-up-or-down-5m must be retained to prevent unmanaged resting order
    assert "btc-up-or-down-5m" in engine.markets


def test_live_trader_realized_pnl_retained_on_deselection():
    engine = LiveTraderEngine(selected_markets=["btc-up-or-down-5m", "eth-up-or-down-5m"])
    engine.markets["btc-up-or-down-5m"].realized_pnl_usd = 25.50

    state_before = engine.get_state()
    assert state_before["realized_pnl"] == 25.50

    # Deselect btc-up-or-down-5m (safe because no position is open)
    engine.update_config(selected_markets=["eth-up-or-down-5m"])
    state_after = engine.get_state()
    assert "btc-up-or-down-5m" not in engine.markets
    assert state_after["realized_pnl"] == 25.50


def test_live_trader_invalid_selected_market_slug_fails():
    with pytest.raises(ValueError, match="Unknown series slug"):
        LiveTraderEngine(selected_markets=["invalid-slug"])
    with pytest.raises(ValueError, match="selected_markets cannot be empty"):
        LiveTraderEngine(selected_markets=[])


def test_live_trader_cannot_change_market_selection_while_running():
    """Verify market selection cannot be changed mid-run while is_running is True."""
    engine = LiveTraderEngine(selected_markets=["btc-up-or-down-5m", "eth-up-or-down-5m"])
    engine.is_running = True

    # Attempting to change market selection while running must raise ValueError
    with pytest.raises(ValueError, match="Cannot change market selection while the trading bot is running"):
        engine.update_config(selected_markets=["btc-up-or-down-5m"])

    with pytest.raises(ValueError, match="Cannot change market selection while the trading bot is running"):
        engine.update_config(tokens=["SOL"])

    with pytest.raises(ValueError, match="Cannot change market selection while the trading bot is running"):
        engine.update_config(durations=[900])

    # Idempotent market selection call while running is allowed
    engine.update_config(selected_markets=["btc-up-or-down-5m", "eth-up-or-down-5m"])

    # Once stopped, changing market selection is allowed
    engine.is_running = False
    engine.update_config(tokens=["SOL"], durations=[300])
    assert set(engine.markets.keys()) == {"sol-up-or-down-5m"}


def test_live_trader_cannot_change_parameters_while_running():
    """Verify strategy parameters cannot be modified mid-run while is_running is True."""
    engine = LiveTraderEngine(selected_markets=["btc-up-or-down-5m"])
    engine.wallet_address = "0x1111111111111111111111111111111111111111"
    engine.offset = 0.02
    engine.exit_thresh = 0.05
    engine.shares = 5
    engine.mode = "paper"
    engine.starting_balance = 1000.0
    engine.is_running = True

    # 1. offset change must raise ValueError
    with pytest.raises(ValueError, match="Cannot change strategy parameters while the trading bot is running"):
        engine.update_config(offset=0.03)

    # 2. exit_thresh change must raise ValueError
    with pytest.raises(ValueError, match="Cannot change strategy parameters while the trading bot is running"):
        engine.update_config(exit_thresh=0.08)

    # 3. shares change must raise ValueError
    with pytest.raises(ValueError, match="Cannot change strategy parameters while the trading bot is running"):
        engine.update_config(shares=10)

    # 4. mode change must raise ValueError
    with pytest.raises(ValueError, match="Cannot change strategy parameters while the trading bot is running"):
        engine.update_config(mode="live")

    # 5. wallet_address change must raise ValueError
    with pytest.raises(ValueError, match="Cannot change strategy parameters while the trading bot is running"):
        engine.update_config(wallet_address="0x2222222222222222222222222222222222222222")

    # 6. starting_balance change must raise ValueError (in paper and live mode)
    with pytest.raises(ValueError, match="Cannot change strategy parameters while the trading bot is running"):
        engine.update_config(starting_balance=2500.0)

    engine.mode = "live"
    with pytest.raises(ValueError, match="Cannot change strategy parameters while the trading bot is running"):
        engine.update_config(starting_balance=2500.0)
    engine.mode = "paper"

    # Verify no state was corrupted
    assert engine.offset == 0.02
    assert engine.exit_thresh == 0.05
    assert engine.shares == 5
    assert engine.mode == "paper"
    assert engine.wallet_address == "0x1111111111111111111111111111111111111111"
    assert engine.starting_balance == 1000.0

    # 7. Idempotent calls with identical values must succeed
    engine.update_config(
        offset=0.02,
        exit_thresh=0.05,
        shares=5,
        mode="paper",
        wallet_address="0x1111111111111111111111111111111111111111",
        starting_balance=1000.0,
    )
    assert engine.offset == 0.02

    # Tolerance-equivalent offset while running does not mutate stored value
    engine.update_config(offset=0.02000000001)
    assert engine.offset == 0.02

    # 8. Once stopped, changing all parameters succeeds
    engine.is_running = False
    engine.update_config(
        offset=0.03,
        exit_thresh=0.07,
        shares=12,
        starting_balance=2000.0,
        wallet_address="0x3333333333333333333333333333333333333333",
    )
    assert engine.offset == 0.03
    assert engine.exit_thresh == 0.07
    assert engine.shares == 12
    assert engine.starting_balance == 2000.0
    assert engine.wallet_address == "0x3333333333333333333333333333333333333333"


def test_live_trader_ticks_only_selected_markets():
    """Verify the trading loop polls and quotes only the user-selected markets and durations."""
    import asyncio

    engine = LiveTraderEngine(tokens=["BTC", "XRP"], durations=[900])
    assert set(engine.markets.keys()) == {"btc-up-or-down-15m", "xrp-up-or-down-15m"}

    polled = []
    engine._poll_single_market = lambda slug: polled.append(slug) or None

    asyncio.run(engine._tick_all_markets())

    assert sorted(polled) == ["btc-up-or-down-15m", "xrp-up-or-down-15m"]

    # Narrow the selection while stopped, then confirm the loop follows it
    polled.clear()
    engine.update_config(tokens=["XRP"], durations=[900])
    asyncio.run(engine._tick_all_markets())
    assert polled == ["xrp-up-or-down-15m"]

    # Widen to both durations for BTC and confirm both windows are traded
    polled.clear()
    engine.update_config(tokens=["BTC"], durations=[300, 900])
    asyncio.run(engine._tick_all_markets())
    assert sorted(polled) == ["btc-up-or-down-15m", "btc-up-or-down-5m"]


def test_live_trader_state_reports_selection_for_ui():
    """Verify get_state exposes the exact active selection the dashboard renders."""
    engine = LiveTraderEngine(tokens=["ETH", "SOL"], durations=[300])
    state = engine.get_state()

    assert sorted(state["selected_series"]) == ["eth-up-or-down-5m", "sol-up-or-down-5m"]
    assert sorted(state["markets"].keys()) == ["eth-up-or-down-5m", "sol-up-or-down-5m"]
    assert len(state["available_series"]) == 10

    durations = {s["slug"]: s["duration"] for s in state["available_series"]}
    assert durations["eth-up-or-down-5m"] == 300
    assert durations["eth-up-or-down-15m"] == 900


def test_live_trader_running_flag_is_guarded_by_engine_lock(monkeypatch):
    """Verify start/stop flip is_running under the lock update_config checks it with.

    Without shared locking, a concurrent start() could land between
    update_config()'s is_running check and its mutation of the market set,
    letting the traded markets change mid-run.
    """
    import threading

    engine = LiveTraderEngine()
    # Keep the test hermetic: start()/stop() must not open the stream bridge or
    # schedule wallet-balance network work.
    monkeypatch.setattr(engine.stream_bridge, "start", lambda: None)
    monkeypatch.setattr(engine.stream_bridge, "stop", lambda: None)
    monkeypatch.setattr(engine, "_schedule_wallet_balance_fetch", lambda: None)
    engine._engine_lock.acquire()
    try:
        t = threading.Thread(target=engine.start, daemon=True)
        t.start()
        t.join(timeout=0.3)
        assert t.is_alive(), "start() flipped is_running without holding _engine_lock"
        assert not engine.is_running
    finally:
        engine._engine_lock.release()
    t.join(timeout=2.0)
    assert engine.is_running

    engine.stop()
    assert not engine.is_running


def test_live_trader_running_guard_tracks_traded_markets():
    """Verify the mid-run guard compares against the markets actually traded."""
    engine = LiveTraderEngine(tokens=["BTC"], durations=[300])
    engine.is_running = True

    # Same set as engine.markets -> allowed (no-op reselection of the running set)
    engine.update_config(selected_markets=["btc-up-or-down-5m"])
    assert set(engine.markets.keys()) == {"btc-up-or-down-5m"}

    # Different set -> rejected while running
    with pytest.raises(ValueError, match="Cannot change market selection while the trading bot is running"):
        engine.update_config(selected_markets=["btc-up-or-down-5m", "eth-up-or-down-5m"])
    assert set(engine.markets.keys()) == {"btc-up-or-down-5m"}


def test_live_trader_rejected_config_leaves_parameters_unchanged():
    """Verify a rejected selection does not partially apply the rest of the payload."""
    engine = LiveTraderEngine(tokens=["BTC"], durations=[300])
    engine.is_running = True

    # Rejected because the bot is running: offset/shares must not be applied
    with pytest.raises(ValueError, match="Cannot change market selection while the trading bot is running"):
        engine.update_config(offset=0.04, shares=99, tokens=["ETH"], durations=[300])
    assert engine.offset == 0.02
    assert engine.shares == 5
    assert set(engine.markets.keys()) == {"btc-up-or-down-5m"}

    # Rejected because the slug is unknown: same guarantee while stopped
    engine.is_running = False
    with pytest.raises(ValueError, match="Unknown series slug"):
        engine.update_config(offset=0.04, shares=99, selected_markets=["not-a-market"])
    assert engine.offset == 0.02
    assert engine.shares == 5
    assert set(engine.markets.keys()) == {"btc-up-or-down-5m"}


def test_live_trader_start_sets_quoting_halted_under_lock(monkeypatch):
    """Verify start() clears quoting_halted inside the lifecycle lock, not before it."""
    import threading

    engine = LiveTraderEngine()
    monkeypatch.setattr(engine.stream_bridge, "start", lambda: None)
    monkeypatch.setattr(engine, "_schedule_wallet_balance_fetch", lambda: None)
    engine.quoting_halted = True

    engine._engine_lock.acquire()
    try:
        t = threading.Thread(target=engine.start, daemon=True)
        t.start()
        t.join(timeout=0.3)
        assert t.is_alive()
        assert engine.quoting_halted, "quoting_halted was cleared outside _engine_lock"
    finally:
        engine._engine_lock.release()
    t.join(timeout=2.0)
    assert engine.is_running
    assert not engine.quoting_halted


def test_fetch_polymarket_account_value_positions(monkeypatch):
    """Verify fetch_polymarket_account_value returns parsed positions array with full attributes."""
    monkeypatch.setattr("strategy.live_trader._load_env_file", lambda: None)
    for name in ("POLY_PRIVATE_KEY", "POLYMARKET_PRIVATE_KEY", "POLY_API_KEY", "POLY_API_SECRET", "POLY_API_PASSPHRASE"):
        monkeypatch.delenv(name, raising=False)
    from unittest.mock import MagicMock
    from strategy.live_trader import fetch_polymarket_account_value

    fake_sess = MagicMock()
    fake_pos_resp = MagicMock()
    fake_pos_resp.ok = True
    fake_pos_resp.json.return_value = [
        {
            "asset": "0xtoken123",
            "conditionId": "0xcond123",
            "size": "10.0",
            "avgPrice": "0.485",
            "curPrice": "0.52",
            "initialValue": "4.85",
            "currentValue": "5.20",
            "cashPnl": "0.35",
            "title": "Bitcoin Up or Down 5m",
            "outcome": "Up",
        }
    ]
    fake_sess.get.return_value = fake_pos_resp

    res = fetch_polymarket_account_value(wallet_address="0xee3b778a783510bc833384919f709e3d2fee1624", session=fake_sess)
    assert res["success"] is True
    assert "positions" in res
    assert len(res["positions"]) == 1
    p = res["positions"][0]
    assert p["asset"] == "0xtoken123"
    assert p["conditionId"] == "0xcond123"
    assert p["size"] == 10.0
    assert p["avgPrice"] == 0.485
    assert p["curPrice"] == 0.52
    assert p["cashPnl"] == 0.35
    assert p["title"] == "Bitcoin Up or Down 5m"
    assert p["outcome"] == "Up"


def test_fill_price_and_slippage_pair_merge_pnl(monkeypatch):
    """Verify live fill price records true match price from CLOB order and pair merge reflects slippage."""
    from unittest.mock import MagicMock
    from strategy.live_trader import LiveTraderEngine

    engine = LiveTraderEngine(selected_markets=["btc-up-or-down-5m"])
    engine.mode = "live"
    engine.is_running = True
    monkeypatch.setattr(engine.stream_bridge, "start", lambda: None)
    monkeypatch.setattr(engine, "_schedule_wallet_balance_fetch", lambda: None)
    monkeypatch.setattr(engine, "merge_positions", lambda *args, **kwargs: None)

    slug = "btc-up-or-down-5m"
    mstate = engine.markets[slug]
    mstate.start_ts = 100.0
    mstate.end_ts = 400.0
    mstate.up_token = "tok_up"
    mstate.down_token = "tok_dn"
    mstate.order_id_up = "ord_up_1"
    mstate.order_id_down = "ord_dn_1"
    mstate.resting_up = 0.48
    mstate.resting_down = 0.48
    mstate.order_shares = 5

    # Mock CLOB client returning orders with slippage
    fake_client = MagicMock()
    fake_client.get_order.side_effect = lambda order_id: {
        "ord_up_1": {
            "status": "MATCHED",
            "size_matched": "5.0",
            "price": "0.49",
            "associate_trades": [{"price": "0.49", "size": "5.0"}],
        },
        "ord_dn_1": {
            "status": "FILLED",
            "size_matched": "5.0",
            "price": "0.485",
            "associate_trades": [{"price": "0.485", "size": "5.0"}],
        },
    }.get(order_id, {})
    monkeypatch.setattr(engine, "get_clob_client", lambda: fake_client)

    poll_data = {
        "market": {
            "conditionId": "cond1",
            "slug": "mkt1",
            "up_token": "tok_up",
            "down_token": "tok_dn",
            "start_ts": 100.0,
            "end_ts": 400.0,
        },
        "up_book": {"best_bid": 0.47, "best_ask": 0.50},
        "down_book": {"best_bid": 0.47, "best_ask": 0.50},
    }

    # Run update
    engine._update_market_strategy(slug, poll_data, 150.0)

    # Assertions
    assert mstate.filled_up is True
    assert mstate.filled_down is True
    assert mstate.fill_price_up == 0.49
    assert mstate.fill_price_down == 0.485
    assert mstate.pair_captured is True

    # Realized PnL: (1.00 - (0.49 + 0.485)) * 5 = (1.00 - 0.975) * 5 = 0.025 * 5 = 0.125
    assert abs(mstate.realized_pnl_usd - 0.125) < 1e-4

    # Assert trade event recorded true entry prices and pnl
    assert len(engine.trades) == 1
    tr = engine.trades[0]
    assert tr.action == "PAIR_MERGE"
    assert tr.entry_price_up == 0.49
    assert tr.entry_price_down == 0.485
    assert abs(tr.pnl_usd - 0.125) < 1e-4


def test_paper_mode_ignores_wallet_positions(monkeypatch):
    """Verify paper simulation mode ignores on-chain wallet positions fetched from API."""
    engine = LiveTraderEngine(load_persisted=False)
    engine.mode = "paper"
    engine.wallet_address = "0x111122223333444455556666"
    fake_val = {
        "success": True,
        "wallet_address": "0x111122223333444455556666",
        "net_value": 500.0,
        "positions": [{"title": "Old Wallet Bet", "size": 10.0, "outcome": "Up"}],
    }
    monkeypatch.setattr("strategy.live_trader.fetch_polymarket_account_value", lambda addr: fake_val)
    engine._try_fetch_wallet_balance()
    assert engine.open_positions == []
    assert engine.get_open_positions() == []


def test_paper_mode_dynamic_open_positions():
    """Verify open positions are dynamically synthesized in paper mode and cleared on merge."""
    engine = LiveTraderEngine(load_persisted=False)
    engine.mode = "paper"
    engine.shares = 5
    slug = "btc-up-or-down-5m"
    m = engine.markets[slug]
    m.filled_up = True
    m.fill_price_up = 0.48
    m.order_shares = 5
    m.order_time_up = "12:00:00"
    m.mid = 0.50
    m.up_bid = 0.47
    
    open_pos = engine.get_open_positions()
    assert len(open_pos) == 1
    assert open_pos[0]["outcome"] == "Up"
    assert open_pos[0]["size"] == 5.0
    assert open_pos[0]["avgPrice"] == 0.48
    
    st = engine.get_state()
    assert len(st["positions"]) == 1
    assert st["positions"][0]["size"] == 5.0

    m.pair_captured = True
    assert engine.get_open_positions() == []


def test_reset_pnl_clears_open_positions():
    """Verify reset_pnl clears open positions and resets market fill states."""
    engine = LiveTraderEngine(load_persisted=False)
    engine.mode = "paper"
    engine.open_positions = [{"title": "Stale", "size": 10.0}]
    m = engine.markets["btc-up-or-down-5m"]
    m.filled_up = True
    m.fill_price_up = 0.48
    engine.reset_pnl()
    assert engine.open_positions == []
    assert engine.get_open_positions() == []
    assert m.filled_up is False


def test_reset_pnl_clears_all_order_state_and_orders_list(monkeypatch):
    """Issue #93: reset_pnl on a stopped engine empties every order row."""
    engine = LiveTraderEngine(load_persisted=False)
    engine.mode = "paper"
    monkeypatch.setattr(engine, "get_clob_client", lambda: None)
    m = engine.markets["btc-up-or-down-5m"]
    # Entry legs (one FILLED to prove FILLED rows are cleared too).
    m.order_id_up = "ord_up_1"
    m.order_status_up = "FILLED"
    m.order_time_up = "12:00:01"
    m.filled_up = True
    m.fill_price_up = 0.48
    m.order_id_down = "ord_dn_1"
    m.order_status_down = "RESTING"
    m.order_time_down = "12:00:02"
    # Advance pre-quotes for the next window.
    m.next_order_id_up = "nxt_up_1"
    m.next_order_time_up = "12:00:03"
    m.next_order_id_down = "nxt_dn_1"
    m.next_order_time_down = "12:00:04"
    m.next_quoted = True
    # Resting stop-loss + exit handles.
    m.stop_order_id = "stop_1"
    m.stop_order_status = "RESTING"
    m.stop_price = 0.40
    m.stop_side = "UP"
    m.order_id_exit_up = "exit_up_1"
    m.order_status_exit_up = "RESTING"
    m.order_id_exit_down = "exit_dn_1"
    m.order_status_exit_down = "RESTING"
    # Retained cancelled rows + timeout latch + warm orders cache.
    m.cancelled_orders = [{
        "order_id": "old_cancelled_1", "market": m.label, "status": "CANCELLED",
    }]
    m.entry_cancelled_timeout = True
    engine._orders_cache_ts = time.time()

    assert engine.get_open_orders_list() != []

    res = engine.reset_pnl()
    assert res["ok"] is True

    assert engine.get_open_orders_list() == []
    assert m.cancelled_orders == []
    assert m.order_id_up is None and m.order_id_down is None
    assert m.order_status_up == "NONE" and m.order_status_down == "NONE"
    assert m.next_order_id_up is None and m.next_order_id_down is None
    assert m.next_quoted is False
    assert m.stop_order_id is None and m.stop_order_status == "NONE"
    assert m.stop_price is None and m.stop_side is None
    assert m.order_id_exit_up is None and m.order_id_exit_down is None
    assert m.order_status_exit_up == "NONE" and m.order_status_exit_down == "NONE"
    assert m.entry_cancelled_timeout is False
    assert engine._orders_cache_ts == 0.0
    # No FILLED-status / fill-flag contradiction may survive the reset.
    assert not (m.filled_up is False and m.order_status_up == "FILLED")
    assert not (m.filled_down is False and m.order_status_down == "FILLED")


def test_reset_pnl_refuses_while_live_and_running(monkeypatch):
    """Issue #93: live + running + outstanding → refusal, nothing cleared."""
    from unittest.mock import MagicMock
    engine = LiveTraderEngine(load_persisted=False)
    engine.mode = "live"
    engine.is_running = True
    fake_client = MagicMock()
    monkeypatch.setattr(engine, "get_clob_client", lambda: fake_client)
    m = engine.markets["btc-up-or-down-5m"]
    m.order_id_up = "live_ord_up"
    m.order_status_up = "RESTING"
    m.cancelled_orders = [{"order_id": "old", "status": "CANCELLED"}]
    engine._orders_cache_ts = 1234.0

    res = engine.reset_pnl()

    assert isinstance(res, dict) and res.get("refused") is True
    assert res.get("ok") is False
    assert "Stop" in res.get("message", "")
    # Nothing cleared on the refusal path, venue untouched.
    assert m.order_id_up == "live_ord_up"
    assert m.cancelled_orders != []
    assert engine._orders_cache_ts == 1234.0
    fake_client.cancel_all.assert_not_called()
    fake_client.cancel.assert_not_called()
    engine.is_running = False


def test_reset_pnl_live_stopped_cancels_venue_first(monkeypatch):
    """Issue #93: live + stopped → venue cancel attempted before local clear."""
    from unittest.mock import MagicMock
    engine = LiveTraderEngine(load_persisted=False)
    engine.mode = "live"
    engine.is_running = False
    fake_client = MagicMock()
    fake_client.cancel_all.return_value = {"success": True}
    monkeypatch.setattr(engine, "get_clob_client", lambda: fake_client)
    m = engine.markets["btc-up-or-down-5m"]
    m.order_id_up = "live_ord_up"
    m.order_status_up = "RESTING"

    res = engine.reset_pnl()

    assert res.get("ok") is True
    assert res.get("venue_cancelled") is True
    fake_client.cancel_all.assert_called_once()
    assert m.order_id_up is None


def test_reset_pnl_live_stopped_venue_failure_refuses_without_clearing(monkeypatch):
    """Issue #93: live + stopped + cancel raises → error, nothing cleared."""
    from unittest.mock import MagicMock
    engine = LiveTraderEngine(load_persisted=False)
    engine.mode = "live"
    engine.is_running = False
    fake_client = MagicMock()
    fake_client.cancel_all.side_effect = RuntimeError("CLOB remote error")
    monkeypatch.setattr(engine, "get_clob_client", lambda: fake_client)
    m = engine.markets["btc-up-or-down-5m"]
    m.order_id_up = "live_ord_up"
    m.order_status_up = "RESTING"

    res = engine.reset_pnl()

    assert res.get("ok") is False
    assert res.get("venue_cancelled") is False
    assert "error" in res
    assert m.order_id_up == "live_ord_up"


def test_reset_pnl_live_stopped_cancel_all_failure_refuses_without_clearing(monkeypatch):
    """Issue #93: live + stopped + cancel_all reports failure → refusal, ids kept."""
    from unittest.mock import MagicMock
    engine = LiveTraderEngine(load_persisted=False)
    engine.mode = "live"
    engine.is_running = False
    fake_client = MagicMock()
    fake_client.cancel_all.return_value = {"success": False}
    monkeypatch.setattr(engine, "get_clob_client", lambda: fake_client)
    m = engine.markets["btc-up-or-down-5m"]
    m.order_id_up = "live_ord_up"
    m.order_status_up = "RESTING"

    res = engine.reset_pnl()

    assert res.get("ok") is False
    assert res.get("venue_cancelled") is False
    assert m.order_id_up == "live_ord_up"


def test_reset_pnl_live_stopped_no_client_refuses_without_clearing(monkeypatch):
    """Issue #93: live + stopped + no CLOB client → refusal, ids preserved."""
    engine = LiveTraderEngine(load_persisted=False)
    engine.mode = "live"
    engine.is_running = False
    monkeypatch.setattr(engine, "get_clob_client", lambda: None)
    m = engine.markets["btc-up-or-down-5m"]
    m.order_id_up = "live_ord_up"
    m.order_status_up = "RESTING"

    res = engine.reset_pnl()

    assert res.get("ok") is False
    assert res.get("venue_cancelled") is False
    assert "CLOB" in res.get("error", "")
    assert m.order_id_up == "live_ord_up"


def test_reset_pnl_paper_makes_no_clob_calls(monkeypatch):
    """Issue #93: paper reset clears everything with zero CLOB interaction."""
    from unittest.mock import MagicMock
    engine = LiveTraderEngine(load_persisted=False)
    engine.mode = "paper"
    fake_client = MagicMock()
    monkeypatch.setattr(engine, "get_clob_client", lambda: fake_client)
    m = engine.markets["btc-up-or-down-5m"]
    m.order_id_up = "paper_ord_up"
    m.order_status_up = "RESTING"

    res = engine.reset_pnl()

    assert res.get("ok") is True
    assert res.get("venue_cancelled") is False
    fake_client.cancel_all.assert_not_called()
    fake_client.cancel.assert_not_called()
    assert m.order_id_up is None


def test_cannot_switch_live_to_paper_with_open_exposure():
    """Verify switching from live to paper mode raises ValueError if unresolved live fills exist."""
    import pytest
    engine = LiveTraderEngine(load_persisted=False)
    engine.mode = "live"
    m = engine.markets["btc-up-or-down-5m"]
    m.filled_up = True
    m.fill_price_up = 0.48
    with pytest.raises(ValueError, match="Cannot switch from live to paper mode"):
        engine.update_config(mode="paper")


def test_get_open_orders_list_excludes_idle_and_tokenless_markets(monkeypatch):
    """Verify get_open_orders_list does not emit fabricated orders for IDLE or tokenless markets."""
    engine = LiveTraderEngine(load_persisted=False)
    monkeypatch.setattr(engine, "get_clob_client", lambda: None)
    engine.mode = "paper"
    engine.is_running = True
    # All markets start in IDLE with empty tokens
    orders = engine.get_open_orders_list()
    assert orders == []
    
    # When quoting with tokens, orders appear
    m = engine.markets["btc-up-or-down-5m"]
    m.status = "QUOTING"
    m.up_token = "tok_up_123"
    m.down_token = "tok_dn_123"
    orders_quoting = engine.get_open_orders_list()
    assert len(orders_quoting) == 2
    assert orders_quoting[0]["token_id"] == "tok_up_123"
    assert orders_quoting[1]["token_id"] == "tok_dn_123"


def test_trade_event_market_slug() -> None:
    """Verify TradeEvent supports market_slug with default empty string and preserves market_slug on events."""
    from strategy.live_trader import TradeEvent, LiveTraderEngine
    ev_default = TradeEvent(
        id="test_1",
        timestamp="12:00:00",
        slug="btc-5m",
        label="BTC 5m",
        action="PAIR_MERGE",
        shares=5,
        entry_price_up=0.48,
        entry_price_down=0.48,
        exit_price=1.00,
        pnl_usd=0.20,
        pnl_pct=4.2,
        notes="Spread capture",
    )
    assert ev_default.market_slug == ""

    ev_with_slug = TradeEvent(
        id="test_2",
        timestamp="12:00:00",
        slug="btc-5m",
        label="BTC 5m",
        action="PAIR_MERGE",
        shares=5,
        entry_price_up=0.48,
        entry_price_down=0.48,
        exit_price=1.00,
        pnl_usd=0.20,
        pnl_pct=4.2,
        notes="Spread capture",
        market_slug="btc-updown-5m-active",
    )
    assert ev_with_slug.market_slug == "btc-updown-5m-active"

    # Verify live trader engine seeds/passes market_slug
    engine = LiveTraderEngine(load_persisted=False)
    m = engine.markets["btc-up-or-down-5m"]
    m.market_slug = "btc-updown-5m-active"
    m.filled_up = True
    m.resting_up = 0.48
    m.fill_price_up = 0.48
    m.order_shares = 5
    m.status = "STOP_EXIT_PENDING"
    
    # Run execution cycle to trigger stop exit
    engine._execute_stop_exit("btc-up-or-down-5m", m, "UP", 0.43, "Test stop", 1000.0)
    assert len(engine.trades) == 1
    assert engine.trades[0].market_slug == "btc-updown-5m-active"


def test_open_orders_and_positions_market_slug() -> None:
    """Verify get_open_orders_list and get_open_positions propagate market_slug and series_slug."""
    from strategy.live_trader import LiveTraderEngine
    engine = LiveTraderEngine(load_persisted=False)
    engine.mode = "paper"
    engine.is_running = True

    m = engine.markets["btc-up-or-down-5m"]
    m.status = "QUOTING"
    m.market_slug = "btc-updown-5m-12345"
    m.up_token = "tok_up_1"
    m.down_token = "tok_dn_1"

    # Test open orders
    orders = engine.get_open_orders_list()
    assert len(orders) == 2
    assert orders[0]["market_slug"] == "btc-updown-5m-12345"
    assert orders[0]["series_slug"] == "btc-up-or-down-5m"
    assert orders[1]["market_slug"] == "btc-updown-5m-12345"
    assert orders[1]["series_slug"] == "btc-up-or-down-5m"

    # Test open positions
    m.filled_up = True
    m.fill_price_up = 0.48
    positions = engine.get_open_positions()
    assert len(positions) == 1
    assert positions[0]["market_slug"] == "btc-updown-5m-12345"
    assert positions[0]["series_slug"] == "btc-up-or-down-5m"


def test_live_positions_slug_enrichment() -> None:
    """Verify live mode open positions are enriched with market_slug and series_slug."""
    from strategy.live_trader import LiveTraderEngine
    engine = LiveTraderEngine(load_persisted=False)
    engine.mode = "live"
    m = engine.markets["btc-up-or-down-5m"]
    m.condition_id = "0xcond123"
    m.market_slug = "btc-updown-5m-live-window"

    engine.open_positions = [
        {"asset": "0xtok1", "conditionId": "0xcond123", "outcome": "Up", "size": 10.0}
    ]
    positions = engine.get_open_positions()
    assert len(positions) == 1
    assert positions[0]["market_slug"] == "btc-updown-5m-live-window"
    assert positions[0]["series_slug"] == "btc-up-or-down-5m"


def test_stop_exit_retains_cancelled_opposite_order_paper() -> None:
    """Issue #76: In paper mode, stop exit cancels opposite unhedged leg and retains it in open orders with CANCELLED status."""
    engine = LiveTraderEngine(load_persisted=False)
    engine.mode = "paper"
    engine.is_running = True

    m = engine.markets["btc-up-or-down-5m"]
    m.status = "FILLED_UP"
    m.market_slug = "btc-updown-5m-win1"
    m.up_token = "tok_up_1"
    m.down_token = "tok_dn_1"
    m.filled_up = True
    m.fill_price_up = 0.48
    m.resting_up = 0.48
    m.resting_down = 0.48
    m.order_shares = 5
    m.order_id_down = "paper_dn_custom_1"

    # Trigger stop loss exit for UP leg
    engine._execute_stop_exit("btc-up-or-down-5m", m, "UP", 0.43, "Stop test", 1000.0)

    assert m.exit_taken is True
    assert m.status == "STOP_EXIT"
    assert m.order_status_down == "CANCELLED"
    assert m.order_id_down is None
    assert len(m.cancelled_orders) >= 1
    cancelled_dn = [o for o in m.cancelled_orders if "DOWN" in o["side"]]
    assert len(cancelled_dn) == 1
    assert cancelled_dn[0]["status"] == "CANCELLED"
    assert cancelled_dn[0]["price"] == 0.48
    assert cancelled_dn[0]["order_id"] == "paper_dn_custom_1"

    from unittest.mock import MagicMock
    engine.get_clob_client = MagicMock(return_value=None)

    # Verify get_open_orders_list contains the cancelled order
    orders = engine.get_open_orders_list()
    cancelled_in_list = [o for o in orders if o.get("status") in ("CANCELLED", "CANCELED")]
    assert len(cancelled_in_list) == 1
    assert cancelled_in_list[0]["market"] == m.label
    assert cancelled_in_list[0]["side"] == "BUY (DOWN)"
    assert cancelled_in_list[0]["order_id"] == "paper_dn_custom_1"


def test_stop_exit_retains_cancelled_opposite_order_live() -> None:
    """Issue #76: In live mode, stop exit cancels opposite live order and retains it in open orders with CANCELLED status."""
    from unittest.mock import MagicMock
    engine = LiveTraderEngine(load_persisted=False)
    engine.mode = "live"
    engine.is_running = True

    m = engine.markets["btc-up-or-down-5m"]
    m.status = "FILLED_UP"
    m.market_slug = "btc-updown-5m-win1"
    m.up_token = "tok_up_1"
    m.down_token = "tok_dn_1"
    m.filled_up = True
    m.fill_price_up = 0.48
    m.resting_up = 0.48
    m.resting_down = 0.48
    m.order_shares = 5
    m.order_id_down = "ord_dn_active"
    m.order_status_down = "RESTING"

    mock_client = MagicMock()
    mock_client.cancel.return_value = True
    engine.get_clob_client = MagicMock(return_value=mock_client)
    engine.place_live_quote = MagicMock(return_value={"order_id": "exit_ord_1", "status": "FILLED"})

    # Trigger stop loss exit for UP leg
    engine._execute_stop_exit("btc-up-or-down-5m", m, "UP", 0.43, "Stop test", 1000.0)

    assert m.exit_taken is True
    assert m.status == "STOP_EXIT"
    assert m.order_status_down == "CANCELLED"
    assert m.order_id_down is None
    cancelled_dn = [o for o in m.cancelled_orders if "DOWN" in o["side"]]
    assert len(cancelled_dn) == 1
    assert cancelled_dn[0]["status"] == "CANCELLED"

    orders = engine.get_open_orders_list()
    matching = [o for o in orders if o.get("order_id") == "ord_dn_active"]
    assert len(matching) == 1
    assert matching[0]["status"] == "CANCELLED"


def test_stop_exit_live_failed_cancellation_preserves_handle() -> None:
    """Issue #76: In live mode, if cancelling opposite unhedged leg fails, preserve handle and mark STOP_EXIT_PENDING."""
    from unittest.mock import MagicMock
    engine = LiveTraderEngine(load_persisted=False)
    engine.mode = "live"
    engine.is_running = True

    m = engine.markets["btc-up-or-down-5m"]
    m.status = "FILLED_UP"
    m.market_slug = "btc-updown-5m-win1"
    m.up_token = "tok_up_1"
    m.down_token = "tok_dn_1"
    m.filled_up = True
    m.fill_price_up = 0.48
    m.order_id_down = "ord_dn_active"
    m.order_status_down = "RESTING"

    mock_client = MagicMock()
    mock_client.cancel.return_value = False
    mock_client.cancel_orders.return_value = False
    engine.get_clob_client = MagicMock(return_value=mock_client)
    engine.place_live_quote = MagicMock()

    engine._execute_stop_exit("btc-up-or-down-5m", m, "UP", 0.43, "Stop test", 1000.0)

    # Opposite handle must be preserved so cancellation can be retried
    assert m.order_id_down == "ord_dn_active"
    assert m.status == "STOP_EXIT_PENDING"
    assert m.exit_taken is False
    # Market sell order must NOT have been submitted
    engine.place_live_quote.assert_not_called()


def test_window_rollover_clears_cancelled_orders() -> None:
    """Issue #76: Window rollover clears retained cancelled orders for the new window."""
    import time
    engine = LiveTraderEngine(load_persisted=False)
    engine.mode = "paper"
    engine.is_running = True

    m = engine.markets["btc-up-or-down-5m"]
    m.market_slug = "btc-updown-5m-win1"
    m.cancelled_orders.append({
        "order_id": "test_cancel_1",
        "market": m.label,
        "market_slug": m.market_slug,
        "series_slug": m.slug,
        "token_id": "tok_1",
        "side": "BUY (DOWN)",
        "price": 0.48,
        "size": 5,
        "status": "CANCELLED",
        "source": "PAPER_SIMULATION",
        "time": "12:00:00",
    })
    assert len(m.cancelled_orders) == 1

    # Trigger rollover
    engine._handle_window_rollover(m, time.time(), new_cid="0xnewcid")
    assert len(m.cancelled_orders) == 0


def test_cancel_all_orders_retains_cancelled_orders() -> None:
    """Issue #76: Emergency panic cancel records active orders into cancelled_orders before clearing handles."""
    from unittest.mock import MagicMock
    engine = LiveTraderEngine(load_persisted=False)
    engine.mode = "paper"
    engine.is_running = True
    engine.get_clob_client = MagicMock(return_value=None)

    m = engine.markets["btc-up-or-down-5m"]
    m.order_id_up = "ord_panic_up"
    m.order_id_down = "ord_panic_dn"
    m.order_status_up = "RESTING"
    m.order_status_down = "RESTING"

    res = engine.cancel_all_orders()
    assert res["ok"] is True
    assert m.order_id_up is None
    assert m.order_id_down is None
    assert m.order_status_up == "CANCELLED"
    assert m.order_status_down == "CANCELLED"

    # Both orders must be retained in cancelled_orders
    cancelled_ids = [o["order_id"] for o in m.cancelled_orders]
    assert "ord_panic_up" in cancelled_ids
    assert "ord_panic_dn" in cancelled_ids

    # get_open_orders_list must return both cancelled orders
    open_orders = engine.get_open_orders_list()
    open_ids = [o["order_id"] for o in open_orders]
    assert "ord_panic_up" in open_ids
    assert "ord_panic_dn" in open_ids


def test_cancel_all_orders_live_failure_returns_false() -> None:
    """Issue #76: Emergency panic cancel fails cleanly if remote CLOB cancel raises, preserving local handles."""
    from unittest.mock import MagicMock
    engine = LiveTraderEngine(load_persisted=False)
    engine.mode = "live"
    engine.is_running = True

    mock_client = MagicMock()
    mock_client.cancel_all.side_effect = RuntimeError("CLOB remote error")
    engine.get_clob_client = MagicMock(return_value=mock_client)

    m = engine.markets["btc-up-or-down-5m"]
    m.order_id_up = "ord_panic_up"
    m.order_id_down = "ord_panic_dn"
    m.order_status_up = "RESTING"
    m.order_status_down = "RESTING"

    res = engine.cancel_all_orders()
    assert res["ok"] is False
    assert "CLOB remote error" in res["error"]
    # Local handles must NOT be cleared if remote cancel failed
    assert m.order_id_up == "ord_panic_up"
    assert m.order_id_down == "ord_panic_dn"


def test_live_trader_actual_vs_rtds_price_tracking() -> None:
    """Issue #78: Verify LiveTraderEngine tracks actual price, RTDS price, and calculates price_diff."""
    engine = LiveTraderEngine(load_persisted=False)
    slug = "btc-up-or-down-5m"
    m = engine.markets[slug]

    assert m.actual_price is None
    assert m.rtds_price is None
    assert m.price_diff is None
    assert m.price_diff_pct is None

    # 1. Simulate RTDS tick arriving
    engine.on_rtds_tick("btcusdt", 1000, 80000.0)
    assert m.rtds_price == 80000.0
    assert m.actual_price is None

    # 2. Simulate primary spot tick (Binance WS) arriving
    engine.on_spot_tick("btcusdt", 1050, 80012.0)
    assert m.actual_price == 80012.0
    assert m.spot_price == 80012.0
    assert m.rtds_price == 80000.0
    assert m.price_diff == 12.0
    assert m.price_diff_pct == round((12.0 / 80000.0) * 100.0, 4)

    # 3. Verify get_state() exposes these fields
    st = engine.get_state()
    mkt_st = st["markets"][slug]
    assert mkt_st["actual_price"] == 80012.0
    assert mkt_st["rtds_price"] == 80000.0
    assert mkt_st["price_diff"] == 12.0
    assert mkt_st["price_diff_pct"] == round((12.0 / 80000.0) * 100.0, 4)

    # 4. Subsequent RTDS tick updates divergence
    engine.on_rtds_tick("btcusdt", 1100, 80008.0)
    assert m.rtds_price == 80008.0
    assert m.price_diff == 4.0
    assert m.price_diff_pct == round((4.0 / 80008.0) * 100.0, 4)


def test_live_trader_divergence_edge_cases():
    """Verify negative divergence and zero price handling in LiveTraderEngine."""
    engine = LiveTraderEngine(load_persisted=False)
    slug = "btc-up-or-down-5m"
    m = engine.markets[slug]

    # Negative divergence: Binance spot lower than RTDS
    engine.on_rtds_tick("btcusdt", 1000, 80000.0)
    engine.on_spot_tick("btcusdt", 1001, 79990.0)
    assert m.price_diff == -10.0
    assert m.price_diff_pct == pytest.approx(-0.0125, 0.0001)

    # Zero price in RTDS does not raise ZeroDivisionError and sets pct to None
    engine.on_rtds_tick("btcusdt", 1002, 0.0)
    assert m.rtds_price == 0.0
    assert m.price_diff == 79990.0
    assert m.price_diff_pct is None

    # Zero RTDS price on spot tick update also safely resets pct
    engine.on_spot_tick("btcusdt", 1003, 80010.0)
    assert m.price_diff == 80010.0
    assert m.price_diff_pct is None


# ============================================================================
# Issue #92: adverse-open drift gate must be a window-open snapshot
# ============================================================================

def _drift_poll_data(now: float, up_book: dict, down_book: dict, cid: str = "cid_92") -> dict:
    """Build a poll payload for a freshly opened 5m window."""
    return {
        "market": {
            "conditionId": cid,
            "slug": f"btc-updown-5m-{cid}",
            "up_token": "tok_up",
            "down_token": "tok_dn",
            "start_ts": now - 1.0,
            "end_ts": now + 299.0,
        },
        "up_book": up_book,
        "down_book": down_book,
    }


def _drift_engine() -> LiveTraderEngine:
    """Paper engine with the default full-window entry timeout."""
    engine = LiveTraderEngine(load_persisted=False)
    engine.is_running = True
    engine.mode = "paper"
    engine.offset = 0.02
    engine.exit_thresh = 0.05
    engine.shares = 5
    return engine


def test_one_sided_book_at_open_holds_quotes_without_cancelling():
    """A one-sided book yields a synthetic mid that holds quoting without latching a skip,
    and under #207 an unpriceable leg holds quoting until both sides exist."""
    engine = _drift_engine()
    slug = "btc-up-or-down-5m"
    now = 1000.0
    # UP book has no ask: mid is None under #207; must not latch DRIFT_SKIPPED and must not quote blind.
    engine._update_market_strategy(slug, _drift_poll_data(
        now,
        {"best_bid": 0.34, "best_ask": None},
        {"best_bid": 0.64, "best_ask": 0.66},
    ), now)
    m = engine.markets[slug]
    assert m.entry_cancelled_timeout is False
    assert m.status != "DRIFT_SKIPPED"
    assert m.mid is None
    assert m.order_status_up != "RESTING"
    assert m.order_status_down != "RESTING"

    # Once both legs quote two sides, quoting begins safely
    engine._update_market_strategy(slug, _drift_poll_data(
        now + 1,
        {"best_bid": 0.49, "best_ask": 0.51},
        {"best_bid": 0.49, "best_ask": 0.51},
    ), now + 1)
    assert m.order_status_up == "RESTING"
    assert m.order_status_down == "RESTING"


def test_drift_after_a_healthy_open_never_cancels_resting_quotes():
    """Once a window opens near 0.50, later live drift must not cancel resting bids."""
    engine = _drift_engine()
    slug = "btc-up-or-down-5m"
    now = 1000.0
    engine._update_market_strategy(slug, _drift_poll_data(
        now,
        {"best_bid": 0.49, "best_ask": 0.51},
        {"best_bid": 0.49, "best_ask": 0.51},
    ), now)
    m = engine.markets[slug]
    assert m.order_status_up == "RESTING"
    assert m.order_status_down == "RESTING"

    # Second tick, same window: mid drifts to ~0.35. Asks stay above the resting
    # price so nothing fills and only the gate can change the order state.
    engine._update_market_strategy(slug, _drift_poll_data(
        now + 1.0,
        {"best_bid": 0.20, "best_ask": 0.49},
        {"best_bid": 0.60, "best_ask": 0.70},
    ), now + 1.0)
    assert m.entry_cancelled_timeout is False
    assert m.status != "DRIFT_SKIPPED"
    assert m.order_status_up == "RESTING"
    assert m.order_status_down == "RESTING"


# Issue #228: the #95 re-entry tests stood here (gate, grants, telemetry,
# stats, file). Removed with the behaviour; the quotable range is covered by
# tests/test_quote_range_parity.py (T3).



def test_update_config_quote_range_roundtrip_and_refusals():
    """Issue #228: quote_range round-trips through update_config; each end
    clamps to the price domain, and an inverted or degenerate pair is refused."""
    engine = _drift_engine()
    engine.is_running = False  # parameter changes are rejected while running
    assert engine.quote_range == (0.10, 0.90)

    res = engine.update_config(quote_range=(0.20, 0.80))
    assert engine.quote_range == (0.20, 0.80)
    assert res["params"]["quote_range"] == [0.20, 0.80]

    engine.update_config(quote_range=(-0.50, 1.50))
    assert engine.quote_range == (0.0, 1.0)

    for bad in [
        (0.80, 0.20), (0.50, 0.50), (0.10,), (True, 0.90), (0.10, False),
        (float("nan"), 0.90), (0.10, float("inf")), 123, "not-a-range",
    ]:
        with pytest.raises(ValueError):
            engine.update_config(quote_range=bad)

    # Atomicity: invalid quote_range aborts before any fields are modified
    orig_offset = engine.offset
    with pytest.raises(ValueError):
        engine.update_config(offset=0.045, quote_range=(0.80, 0.20))
    assert engine.offset == orig_offset

    # None is "unspecified" like every other knob — the range is untouched.
    engine.update_config(quote_range=None)
    assert engine.quote_range == (0.0, 1.0)

    engine.is_running = True
    with pytest.raises(ValueError, match="Cannot change strategy parameters while the trading bot is running"):
        engine.update_config(quote_range=(0.10, 0.90))


def test_zeroed_skip_tallies_appear_in_get_state():
    """Issue #228/232: with re-entry deleted, reentry_stats is removed from get_state."""
    engine = _drift_engine()
    state = engine.get_state()
    assert "reentry_stats" not in state
    assert state["band_skip_stats"]["band_skips"] == 0


# --- Issue #97: deterministic Open Orders ranking ---

def _order_lane_market(engine, slug, prefix, with_stop=True, with_advance=True, with_cancelled=True):
    """Populate one market with a full lane of order state (active/stop/advance/cancelled)."""
    m = engine.markets[slug]
    m.order_id_up = f"{prefix}_up"
    m.order_status_up = "RESTING"
    m.order_id_down = f"{prefix}_dn"
    m.order_status_down = "RESTING"
    if with_stop:
        m.stop_order_id = f"{prefix}_stop"
        m.stop_order_status = "RESTING"
        m.stop_price = 0.43
        m.stop_side = "UP"
    if with_advance:
        m.next_order_id_up = f"{prefix}_nxt_up"
        m.next_order_id_down = f"{prefix}_nxt_dn"
        m.next_quoted = True
    if with_cancelled:
        m.cancelled_orders.append({
            "order_id": f"{prefix}_old_cancel",
            "market": m.label,
            "market_slug": m.market_slug or "",
            "series_slug": m.slug,
            "status": "CANCELLED",
            "source": "PAPER_SIMULATION",
            "time": "13:59:00",
        })
    return m


def test_open_orders_sorted_current_above_next_window():
    """get_open_orders_list ranks live > stop > pre-quote > cancelled (Issue #97)."""
    from unittest.mock import MagicMock
    engine = LiveTraderEngine()
    assert not engine.is_running  # stopped: no paper-sim rows, deterministic lanes only

    _order_lane_market(engine, "btc-up-or-down-5m", "eng_btc")
    _order_lane_market(engine, "eth-up-or-down-5m", "eng_eth")

    # A venue CLOB row appends first pre-sort; it must rank with the live block by series.
    mock_client = MagicMock()
    mock_client.get_orders.return_value = [{
        "id": "clob_sol_up",
        "market": "SOL 5m",
        "market_slug": "sol-up-down-5m",
        "series_slug": "sol-up-or-down-5m",
        "side": "BUY (UP)",
        "price": 0.47,
        "original_size": 5.0,
        "size_matched": 0.0,
        "status": "OPEN",
    }]
    engine.get_clob_client = MagicMock(return_value=mock_client)

    ids = [o["order_id"] for o in engine.get_open_orders_list()]
    assert ids == [
        # Rank 0: current-window live, series order, Up before Down
        "eng_btc_up", "eng_btc_dn", "eng_eth_up", "eng_eth_dn", "clob_sol_up",
        # Rank 1: resting stop-loss
        "eng_btc_stop", "eng_eth_stop",
        # Rank 2: next-window pre-quotes
        "eng_btc_nxt_up", "eng_btc_nxt_dn", "eng_eth_nxt_up", "eng_eth_nxt_dn",
        # Rank 3: cancelled rows, any source
        "eng_btc_old_cancel", "eng_eth_old_cancel",
    ]


def test_open_orders_rank_cancelled_any_source_last():
    """A CANCELLED venue row sinks below pre-quotes even though it appended first."""
    from unittest.mock import MagicMock
    engine = LiveTraderEngine()
    _order_lane_market(engine, "btc-up-or-down-5m", "eng_btc",
                       with_stop=False, with_advance=True, with_cancelled=False)

    mock_client = MagicMock()
    mock_client.get_orders.return_value = [{
        "id": "clob_btc_cancelled",
        "market": "BTC 5m",
        "market_slug": "btc-up-down-5m",
        "series_slug": "btc-up-or-down-5m",
        "side": "BUY (UP)",
        "price": 0.48,
        "original_size": 5.0,
        "size_matched": 0.0,
        "status": "CANCELLED",
    }]
    engine.get_clob_client = MagicMock(return_value=mock_client)

    ids = [o["order_id"] for o in engine.get_open_orders_list()]
    assert ids == [
        "eng_btc_up", "eng_btc_dn",
        "eng_btc_nxt_up", "eng_btc_nxt_dn",
        "clob_btc_cancelled",
    ]
# --- Issue #89: multi-merge re-quoting on dynamic mid minus offset ---

def _fifteen_minute_market(now, condition_id="0xmerge15m", start_offset=10.0):
    """15m-window LiveMarket with plenty of time remaining."""
    return LiveMarket(
        condition_id=condition_id,
        market_slug="btc-up-down-15m",
        up_token="tok_up",
        down_token="tok_dn",
        start_ts=now - start_offset,
        end_ts=now - start_offset + 900.0,
        tick_size=0.01,
        neg_risk=False,
    )


def _fifteen_minute_engine(slug="btc-up-or-down-15m", **kwargs):
    """Paper engine tracking a single 15m series (default selection is 5m-only)."""
    return LiveTraderEngine(selected_markets=[slug], **kwargs)


def test_requote_after_merge_when_time_remains():
    """A 15m merge with time left allows fresh start instead of going terminal."""
    engine = _fifteen_minute_engine()
    engine.enable_leg_chase = False
    engine.start()
    slug = "btc-up-or-down-15m"
    now = time.time()
    market = _fifteen_minute_market(now)

    _open_50_50_quotes(engine, slug, market, now - 1)
    m = engine.markets[slug]
    # Round 1 fills at the 0.48 / 0.48 anchor.
    engine._update_market_strategy(slug, {
        "market": market,
        "up_book": {"best_bid": 0.47, "best_ask": 0.479},
        "down_book": {"best_bid": 0.51, "best_ask": 0.52},
    }, now)
    assert m.filled_up is True
    engine._update_market_strategy(slug, {
        "market": market,
        "up_book": {"best_bid": 0.51, "best_ask": 0.52},
        "down_book": {"best_bid": 0.47, "best_ask": 0.479},
    }, now + 1)
    assert m.pairs_count == 1
    assert round(m.realized_pnl_usd, 2) == 0.20
    assert m.pair_captured is True
    assert m.status == "PAIR_MERGED"

    # Fresh start on next tick completes into a second merge; PnL is cumulative.
    engine._update_market_strategy(slug, {
        "market": market,
        "up_book": {"best_bid": 0.49, "best_ask": 0.499},
        "down_book": {"best_bid": 0.45, "best_ask": 0.459},
    }, now + 2)
    assert m.pairs_count == 2
    assert round(m.realized_pnl_usd, 2) == 0.40
    assert len([t for t in engine.trades if t.action == "PAIR_MERGE"]) == 2


def test_a_requoted_round_is_a_freshly_placed_quote_and_fills_on_a_touch():
    """Issue #226/#232: round 2's quote is placed, not resting, so it is marketable."""
    engine = _fifteen_minute_engine()
    engine.enable_leg_chase = False
    engine.start()
    slug = "btc-up-or-down-15m"
    now = time.time()
    market = _fifteen_minute_market(now)

    _open_50_50_quotes(engine, slug, market, now - 1)
    m = engine.markets[slug]
    engine._update_market_strategy(slug, {
        "market": market,
        "up_book": {"best_bid": 0.47, "best_ask": 0.479},
        "down_book": {"best_bid": 0.51, "best_ask": 0.52},
    }, now)
    engine._update_market_strategy(slug, {
        "market": market,
        "up_book": {"best_bid": 0.51, "best_ask": 0.52},
        "down_book": {"best_bid": 0.47, "best_ask": 0.479},
    }, now + 1)
    assert m.pairs_count == 1

    # Round 2 quotes fresh and both asks touch the new anchors.
    engine._update_market_strategy(slug, {
        "market": market,
        "up_book": {"best_bid": 0.49, "best_ask": 0.50},
        "down_book": {"best_bid": 0.45, "best_ask": 0.46},
    }, now + 2)
    assert m.pairs_count == 2, "a fresh-start round must fill on a touch"
    assert round(m.realized_pnl_usd, 2) == 0.40


def test_requote_dynamic_anchor_math():
    """Fresh start resting prices anchor to mid - offset, summing to 1 - 2*offset."""
    engine = _fifteen_minute_engine()
    engine.start()
    slug = "btc-up-or-down-15m"
    now = time.time()
    market = _fifteen_minute_market(now)

    # Benign open at 0.50, inside the quotable range, so nothing holds placement.
    engine._update_market_strategy(slug, {
        "market": market,
        "up_book": {"best_bid": 0.49, "best_ask": 0.51},
        "down_book": {"best_bid": 0.49, "best_ask": 0.51},
    }, now)
    m = engine.markets[slug]

    # Skewed books whose asks touch the 0.48 static anchor.
    engine._update_market_strategy(slug, {
        "market": market,
        "up_book": {"best_bid": 0.47, "best_ask": 0.479},
        "down_book": {"best_bid": 0.27, "best_ask": 0.28},
    }, now + 1)
    assert m.pairs_count == 1
    # Next tick with mid 0.60 anchors fresh:
    engine._update_market_strategy(slug, {
        "market": market,
        "up_book": {"best_bid": 0.59, "best_ask": 0.61},
        "down_book": {"best_bid": 0.39, "best_ask": 0.41},
    }, now + 2)
    assert m.resting_up == round(0.60 - engine.offset, 3) == 0.58
    assert m.resting_down == round((1.0 - 0.60) - engine.offset, 3) == 0.38
    assert round(m.resting_up + m.resting_down, 3) == round(1.0 - 2 * engine.offset, 3)


def _quiet_start(engine):
    """Start an engine without its two outbound calls.

    `start()` runs `ensure_telemetry_streaming()` (which opens the WS bridge) and
    `_schedule_wallet_balance_fetch()`, which with no running loop calls
    `fetch_polymarket_account_value` inline. Both are swallowed by broad excepts,
    so they never fail a test -- they just make it slow and non-hermetic.
    """
    engine.stream_bridge.start = lambda *a, **k: None
    engine._schedule_wallet_balance_fetch = lambda *a, **k: None
    engine.start()


def test_initial_entry_anchors_to_live_mid():
    """Round-0 resting prices anchor to the live mid, not a hardcoded 0.50.

    Issue #206: the round-0 branch read `up_mid` / `down_mid`, names bound
    nowhere in the module, so its `locals()` guard always fell through to 0.50
    and every opening quote was 0.50 - offset on both legs. Round 1 was already
    covered by `test_requote_dynamic_anchor_math`; round 0 never was.
    """
    engine = _fifteen_minute_engine()
    _quiet_start(engine)
    slug = "btc-up-or-down-15m"
    now = time.time()
    market = _fifteen_minute_market(now)

    # Opening at mid 0.54: drifted enough to tell 0.52 from the old
    # 0.48, but inside the quotable range so placement is never held.
    # Both asks sit above their own leg's anchor, so nothing
    # fills and the prices the engine actually quoted stay observable.
    engine._update_market_strategy(slug, {
        "market": market,
        "up_book": {"best_bid": 0.53, "best_ask": 0.55},
        "down_book": {"best_bid": 0.45, "best_ask": 0.47},
    }, now)
    m = engine.markets[slug]
    assert m.mid == 0.54
    assert m.resting_up == round(0.54 - engine.offset, 3) == 0.52
    assert m.resting_down == round((1.0 - 0.54) - engine.offset, 3) == 0.44

    # Context: this is the opening round on a window nothing held, and the
    # prices above are what was actually quoted, not merely computed.
    assert m.pairs_count == 0, "still the opening round, not a re-quote"
    assert m.filled_up is False and m.filled_down is False
    assert m.order_status_up == "RESTING" and m.order_status_down == "RESTING"


def _anchor_at(engine, slug, market, now, up_book, down_book):
    """Round-0 prices for one book, with entry held so nothing latches.

    `entry_delay_sec` keeps `can_place_entry` false, so no order is ever placed
    and no leg fills: the round-0 branch re-runs every tick and `resting_*`
    always shows the anchor for the book just fed in. That is the same property
    that makes the placed price placement-time fresh.
    """
    engine._update_market_strategy(
        slug, {"market": market, "up_book": up_book, "down_book": down_book}, now)
    m = engine.markets[slug]
    assert not m.order_id_up and not m.order_id_down and not m.filled_up and not m.filled_down
    return m


def test_initial_entry_anchor_invariants():
    """Pair sum is 1 - 2*offset across the range, and mid 0.50 is unchanged."""
    engine = _fifteen_minute_engine()
    engine.entry_delay_sec = 900.0  # entry held open for the whole window
    _quiet_start(engine)
    slug = "btc-up-or-down-15m"
    now = time.time()
    market = _fifteen_minute_market(now)

    cases = (
        (0.20, {"best_bid": 0.19, "best_ask": 0.21}, {"best_bid": 0.79, "best_ask": 0.81}),
        (0.50, {"best_bid": 0.49, "best_ask": 0.51}, {"best_bid": 0.49, "best_ask": 0.51}),
        (0.80, {"best_bid": 0.79, "best_ask": 0.81}, {"best_bid": 0.19, "best_ask": 0.21}),
    )
    for i, (mid_target, up_book, down_book) in enumerate(cases):
        m = _anchor_at(engine, slug, market, now + i, up_book, down_book)
        assert m.mid == mid_target
        assert m.resting_up == round(mid_target - engine.offset, 3)
        assert m.resting_down == round((1.0 - mid_target) - engine.offset, 3)
        assert round(m.resting_up + m.resting_down, 3) == round(1.0 - 2 * engine.offset, 3)
        if mid_target == 0.50:
            # The historical fixture value survives untouched.
            assert m.resting_up == 0.48 and m.resting_down == 0.48


def test_initial_entry_anchor_clamps_at_the_edges():
    """A near-certain market never emits a price below 0.01 or above 0.99."""
    engine = _fifteen_minute_engine()
    engine.offset = 0.05
    engine.entry_delay_sec = 900.0
    _quiet_start(engine)
    slug = "btc-up-or-down-15m"
    now = time.time()
    market = _fifteen_minute_market(now)

    # Synthetic mid 0.02: the down leg wants (1 - 0.02) - 0.05 = 0.93, the up leg
    # wants 0.02 - 0.05 = -0.03 and must clamp instead of going negative.
    m = _anchor_at(engine, slug, market, now,
                   {"best_bid": 0.01, "best_ask": 0.03},
                   {"best_bid": 0.97, "best_ask": 0.99})
    assert m.mid == 0.02
    assert m.resting_up == 0.01
    assert m.resting_down == 0.93


def test_initial_entry_price_is_taken_at_placement_not_at_open():
    """With `entry_delay_sec` armed, the quote uses the mid on the placing tick.

    Issue #206, operator's acceptance criterion: the price computed before the
    delay expired is irrelevant if the market moved during it.
    """
    # Dead-zone guard off: this test isolates the delay, and a 61s-elapsed tick
    # would otherwise trip the skip before the anchor is reached.
    engine = _fifteen_minute_engine(dead_zone_val=0.0)
    engine.entry_delay_sec = 60.0
    _quiet_start(engine)
    slug = "btc-up-or-down-15m"
    now = time.time()
    # start_offset 1.0: only ~1s elapsed, so the delay is still running.
    market = _fifteen_minute_market(now, start_offset=1.0)
    _open_50_50_quotes(engine, slug, market, now)
    m = engine.markets[slug]
    assert not m.order_id_up and not m.order_id_down, "delay still holding entry"

    # Market moves to 0.65 while the delay runs, then the delay expires.
    engine._update_market_strategy(slug, {
        "market": market,
        "up_book": {"best_bid": 0.64, "best_ask": 0.66},
        "down_book": {"best_bid": 0.34, "best_ask": 0.36},
    }, now + 60)

    assert m.mid == 0.65
    assert m.resting_up == round(0.65 - engine.offset, 3) == 0.63
    assert m.resting_down == round(0.35 - engine.offset, 3) == 0.33


def test_no_requote_when_time_short():
    """A merge inside dead zone stays terminal: no second round."""
    # Under fresh_start (rule 13), dead zone (default 10% = 30s for 5m) is the sole time gate.
    engine = LiveTraderEngine()
    engine.start()
    slug = "btc-up-or-down-5m"
    now = time.time()
    market = LiveMarket(
        condition_id="0xmerge5m",
        market_slug="btc-up-down-5m",
        up_token="tok_up",
        down_token="tok_dn",
        start_ts=now - 265.0,
        end_ts=now + 35.0,
        tick_size=0.01,
        neg_risk=False,
    )
    _open_50_50_quotes(engine, slug, market, now - 1)
    m = engine.markets[slug]
    engine._update_market_strategy(slug, {
        "market": market,
        "up_book": {"best_bid": 0.47, "best_ask": 0.479},
        "down_book": {"best_bid": 0.51, "best_ask": 0.52},
    }, now)
    engine._update_market_strategy(slug, {
        "market": market,
        "up_book": {"best_bid": 0.51, "best_ask": 0.52},
        "down_book": {"best_bid": 0.47, "best_ask": 0.479},
    }, now + 1)
    assert m.pairs_count == 1
    assert m.pair_captured is True
    assert m.status == "PAIR_MERGED"

    # Touching books afterwards inside dead zone (remaining 29s <= 30s) must not open a new round.
    engine._update_market_strategy(slug, {
        "market": market,
        "up_book": {"best_bid": 0.47, "best_ask": 0.479},
        "down_book": {"best_bid": 0.47, "best_ask": 0.479},
    }, now + 6)
    assert m.pairs_count == 1


def test_no_requote_after_stop_exit():
    """Under fresh_start (rule 13), a stop exit outside dead zone allows fresh entry when conditions hold."""
    engine = _fifteen_minute_engine("eth-up-or-down-15m")
    engine.start()
    slug = "eth-up-or-down-15m"
    now = time.time()
    market = _fifteen_minute_market(now, condition_id="0xstop15m")

    _open_50_50_quotes(engine, slug, market, now - 1)
    engine._update_market_strategy(slug, {
        "market": market,
        "up_book": {"best_bid": 0.47, "best_ask": 0.479},
        "down_book": {"best_bid": 0.51, "best_ask": 0.52},
    }, now)
    m = engine.markets[slug]
    assert m.filled_up is True

    # Adverse drift triggers the stop exit on the single UP leg.
    engine._update_market_strategy(slug, {
        "market": market,
        "up_book": {"best_bid": 0.43, "best_ask": 0.45},
        "down_book": {"best_bid": 0.55, "best_ask": 0.57},
    }, now + 1)
    assert m.exit_taken is True
    assert m.stops_count == 1

    # Fresh start on next tick outside dead zone: enters fresh quotes!
    engine._update_market_strategy(slug, {
        "market": market,
        "up_book": {"best_bid": 0.49, "best_ask": 0.51},
        "down_book": {"best_bid": 0.49, "best_ask": 0.51},
    }, now + 2)
    assert m.exit_taken is False
    assert m.order_status_up in ("RESTING", "NONE")
    assert m.order_status_down in ("RESTING", "NONE")
    assert m.stops_count == 1


def test_dead_zone_is_sole_time_gate_for_fresh_start():
    """Under fresh_start (rule 13), the dead zone is the sole time gate."""
    # 15m window = 900s, dead zone 10% = 90s cutoff.
    # At elapsed 800s, remaining is 100s > 90s: outside dead zone, fresh start quotes.
    engine = _fifteen_minute_engine()
    engine.start()
    slug = "btc-up-or-down-15m"
    now = time.time()
    market = _fifteen_minute_market(now, condition_id="0xdzgate15m", start_offset=800.0)

    _open_50_50_quotes(engine, slug, market, now - 1)
    engine._update_market_strategy(slug, {
        "market": market,
        "up_book": {"best_bid": 0.47, "best_ask": 0.479},
        "down_book": {"best_bid": 0.51, "best_ask": 0.52},
    }, now)
    engine._update_market_strategy(slug, {
        "market": market,
        "up_book": {"best_bid": 0.51, "best_ask": 0.52},
        "down_book": {"best_bid": 0.47, "best_ask": 0.479},
    }, now + 1)
    m = engine.markets[slug]
    assert m.pairs_count == 1

    # At now + 2, elapsed is 802s, remaining 98s > 90s cutoff: fresh start quotes!
    engine._update_market_strategy(slug, {
        "market": market,
        "up_book": {"best_bid": 0.49, "best_ask": 0.51},
        "down_book": {"best_bid": 0.49, "best_ask": 0.51},
    }, now + 2)
    assert m.order_status_up == "RESTING"
    assert m.order_status_down == "RESTING"

    # In contrast, when the window reaches the dead zone (elapsed 815s, remaining 85s <= 90s cutoff):
    engine2 = _fifteen_minute_engine()
    engine2.start()
    now2 = time.time()
    market2 = _fifteen_minute_market(now2, condition_id="0xdzgate15m_b", start_offset=800.0)
    _open_50_50_quotes(engine2, slug, market2, now2 - 1)
    engine2._update_market_strategy(slug, {
        "market": market2,
        "up_book": {"best_bid": 0.47, "best_ask": 0.479},
        "down_book": {"best_bid": 0.51, "best_ask": 0.52},
    }, now2)
    engine2._update_market_strategy(slug, {
        "market": market2,
        "up_book": {"best_bid": 0.51, "best_ask": 0.52},
        "down_book": {"best_bid": 0.47, "best_ask": 0.479},
    }, now2 + 1)
    m2 = engine2.markets[slug]
    assert m2.pairs_count == 1
    assert m2.pair_captured is True

    # At now2 + 15, elapsed is 815s, remaining 85s <= 90s cutoff (in dead zone): no fresh start!
    engine2._update_market_strategy(slug, {
        "market": market2,
        "up_book": {"best_bid": 0.49, "best_ask": 0.51},
        "down_book": {"best_bid": 0.49, "best_ask": 0.51},
    }, now2 + 15)
    assert m2.pairs_count == 1
    assert m2.pair_captured is True


def test_rollover_resets_requote_round():
    """Window rollover clears the round counter and telemetry for the new window."""
    engine = _fifteen_minute_engine()
    engine.start()
    slug = "btc-up-or-down-15m"
    now = time.time()
    market = _fifteen_minute_market(now, condition_id="0xroll15m")

    _open_50_50_quotes(engine, slug, market, now - 1)
    engine._update_market_strategy(slug, {
        "market": market,
        "up_book": {"best_bid": 0.47, "best_ask": 0.479},
        "down_book": {"best_bid": 0.51, "best_ask": 0.52},
    }, now)
    engine._update_market_strategy(slug, {
        "market": market,
        "up_book": {"best_bid": 0.51, "best_ask": 0.52},
        "down_book": {"best_bid": 0.47, "best_ask": 0.479},
    }, now + 1)
    m = engine.markets[slug]
    assert m.pairs_count == 1

    engine._handle_window_rollover(m, now + 2, "0xroll15m_next")
    assert m.pairs_count == 1
    assert m.pair_captured is False
    assert m.exit_taken is False
    assert m.status == "QUOTING"


def test_exit_reversal_default_matches_backtest():
    """Issue #111: live and backtest share the same exit_reversal default (0.02)."""
    from backtest.engine import BacktestParams

    engine = LiveTraderEngine(load_persisted=False)
    assert engine.exit_reversal == BacktestParams().exit_reversal == 0.02


def test_update_config_exit_reversal():
    """Issue #111: update_config accepts and clamps exit_reversal."""
    engine = LiveTraderEngine(load_persisted=False)
    engine.update_config(exit_reversal=0.03)
    assert engine.exit_reversal == 0.03

    # Clamped into [0.001, 0.50] like the payload model range
    engine.update_config(exit_reversal=5.0)
    assert engine.exit_reversal == 0.50
    engine.update_config(exit_reversal=0.0)
    assert engine.exit_reversal == 0.001


def test_update_config_rejects_exit_reversal_change_while_running():
    """Issue #111: exit_reversal is guarded while the bot runs, like exit_thresh."""
    engine = LiveTraderEngine(load_persisted=False)
    engine.is_running = True
    engine.exit_reversal = 0.02
    with pytest.raises(ValueError, match="Cannot change strategy parameters while the trading bot is running"):
        engine.update_config(exit_reversal=0.03)
    assert engine.exit_reversal == 0.02


# ==========================================================================
# Issue #124: naked-leg risk controls (asymmetric stop, naked timeout,
# pairable re-entry gate).
# ==========================================================================

def _naked_market(now, elapsed=10.0, duration=300.0):
    """A live 5m market that opened `elapsed` seconds ago."""
    return LiveMarket(
        condition_id="0xnaked124",
        market_slug="btc-up-down-5m",
        up_token="tok_up",
        down_token="tok_dn",
        start_ts=now - elapsed,
        end_ts=now - elapsed + duration,
        tick_size=0.01,
        neg_risk=False,
    )


def test_naked_leg_stops_at_exit_thresh():
    """A naked UP leg exits at drift 0.04 when exit_thresh is 0.03."""
    engine = LiveTraderEngine(load_persisted=False)
    engine.exit_thresh = 0.03
    engine.start()
    slug = "btc-up-or-down-5m"
    now = time.time()
    market = _naked_market(now)

    _open_50_50_quotes(engine, slug, market, now - 1)
    # Fill UP at 0.48
    engine._update_market_strategy(slug, {
        "market": market,
        "up_book": {"best_bid": 0.47, "best_ask": 0.479},
        "down_book": {"best_bid": 0.51, "best_ask": 0.52},
    }, now)
    m = engine.markets[slug]
    assert m.filled_up is True and m.filled_down is False

    # Drift 0.04: reaches exit_thresh 0.03.
    engine._update_market_strategy(slug, {
        "market": market,
        "up_book": {"best_bid": 0.45, "best_ask": 0.47},
        "down_book": {"best_bid": 0.53, "best_ask": 0.55},
    }, now + 1)
    assert m.exit_taken is True
    assert m.status == "STOP_EXIT"
    assert "0.03" in engine.trades[-1].notes


def test_paired_position_not_stopped_by_naked_threshold():
    """Drift 0.04 does NOT exit a position once both legs are filled (pair path)."""
    engine = LiveTraderEngine(load_persisted=False)
    engine.exit_thresh = 0.03
    engine.start()
    slug = "btc-up-or-down-5m"
    now = time.time()
    market = _naked_market(now)

    _open_50_50_quotes(engine, slug, market, now - 1)
    # Fill UP, then DOWN -> PAIR_MERGED immediately (0.48 + 0.48)
    engine._update_market_strategy(slug, {
        "market": market,
        "up_book": {"best_bid": 0.47, "best_ask": 0.479},
        "down_book": {"best_bid": 0.51, "best_ask": 0.52},
    }, now)
    engine._update_market_strategy(slug, {
        "market": market,
        "up_book": {"best_bid": 0.51, "best_ask": 0.52},
        "down_book": {"best_bid": 0.47, "best_ask": 0.479},
    }, now + 1)
    m = engine.markets[slug]
    assert m.pair_captured is True
    assert m.naked_since_ts is None
    # No stop can fire on a paired position at naked-threshold drift.
    assert m.exit_taken is False


def test_naked_leg_dead_zone_force_exits_when_close():
    """An unpaired leg in the dead zone force-exits when naked_leg_at_expiry='close'."""
    engine = LiveTraderEngine(load_persisted=False, naked_leg_at_expiry="close", dead_zone_val=0.10, dead_zone_unit="pct")
    engine.start()
    slug = "btc-up-or-down-5m"
    now = time.time()
    # Window opened 10s ago; fill UP at t=now.
    market = _naked_market(now, elapsed=10.0, duration=300.0)
    _open_50_50_quotes(engine, slug, market, now - 1)
    engine._update_market_strategy(slug, {
        "market": market,
        "up_book": {"best_bid": 0.47, "best_ask": 0.479},
        "down_book": {"best_bid": 0.51, "best_ask": 0.52},
    }, now)
    m = engine.markets[slug]
    assert m.filled_up is True
    assert m.exit_taken is False

    # 300s window with 10% dead zone -> dead zone starts at 270s elapsed (30s remaining).
    # t = now + 261s -> elapsed = 271s -> remaining = 29s <= 30s cutoff.
    engine._update_market_strategy(slug, {
        "market": market,
        "up_book": {"best_bid": 0.47, "best_ask": 0.479},
        "down_book": {"best_bid": 0.51, "best_ask": 0.52},
    }, now + 261.0)
    assert m.exit_taken is True
    assert m.status == "STOP_EXIT"
    assert "Dead-zone expiry exit" in engine.trades[-1].notes


def test_naked_leg_dead_zone_holds_when_hold_configured():
    """An unpaired leg in the dead zone is held when naked_leg_at_expiry='hold'."""
    engine = LiveTraderEngine(load_persisted=False, naked_leg_at_expiry="hold", dead_zone_val=0.10, dead_zone_unit="pct")
    engine.start()
    slug = "btc-up-or-down-5m"
    now = time.time()
    market = _naked_market(now, elapsed=10.0, duration=300.0)
    _open_50_50_quotes(engine, slug, market, now - 1)
    engine._update_market_strategy(slug, {
        "market": market,
        "up_book": {"best_bid": 0.47, "best_ask": 0.479},
        "down_book": {"best_bid": 0.51, "best_ask": 0.52},
    }, now)
    m = engine.markets[slug]

    # In dead zone (29s remaining): hold does not force-exit.
    engine._update_market_strategy(slug, {
        "market": market,
        "up_book": {"best_bid": 0.47, "best_ask": 0.479},
        "down_book": {"best_bid": 0.51, "best_ask": 0.52},
    }, now + 261.0)
    assert m.exit_taken is False
    assert m.status == "FILLED_UP"


def test_update_config_dead_zone_knobs_roundtrip_and_clamp():
    """Issue #229 knobs round-trip through update_config with validation."""
    engine = LiveTraderEngine(load_persisted=False)
    state = engine.update_config(
        dead_zone_val=15.0,
        dead_zone_unit="sec",
        naked_leg_at_expiry="hold",
    )
    assert engine.dead_zone_val == 15.0
    assert engine.dead_zone_unit == "sec"
    assert engine.naked_leg_at_expiry == "hold"
    assert state["params"]["dead_zone_val"] == 15.0
    assert state["params"]["dead_zone_unit"] == "sec"
    assert state["params"]["naked_leg_at_expiry"] == "hold"

    # Validation: dead_zone_val must be >= 0; pct must be <= 1.0.
    with pytest.raises(ValueError, match="dead_zone_val"):
        engine.update_config(dead_zone_val=-1.0)
    with pytest.raises(ValueError, match="dead_zone_val"):
        engine.update_config(dead_zone_unit="pct", dead_zone_val=1.5)
    with pytest.raises(ValueError, match="dead_zone_unit"):
        engine.update_config(dead_zone_unit="invalid")
    with pytest.raises(ValueError, match="naked_leg_at_expiry"):
        engine.update_config(naked_leg_at_expiry="invalid")

    # Guarded while running like every other scalar param.
    engine.is_running = True
    with pytest.raises(ValueError, match="Cannot change strategy parameters while the trading bot is running"):
        engine.update_config(dead_zone_val=20.0)


# ============================================================================
# Issue #123: Chase the second leg after a one-sided fill (cross spread within cap)
# ============================================================================

def test_leg_chase_triggers_on_single_fill_and_respects_cap(monkeypatch):
    """When UP fills, DOWN quote steps up to ask bounded by max_pair_cost."""
    engine = LiveTraderEngine(load_persisted=False)
    monkeypatch.setattr(engine.stream_bridge, "start", lambda: None)
    monkeypatch.setattr(engine, "_schedule_wallet_balance_fetch", lambda: None)
    engine.start()
    slug = "btc-up-or-down-5m"
    now = time.time()

    fake_market = LiveMarket(
        condition_id="0xchase123",
        market_slug="btc-up-down-5m",
        up_token="tok_up",
        down_token="tok_dn",
        start_ts=now - 10,
        end_ts=now + 290,
        tick_size=0.01,
        neg_risk=False,
    )

    # Pinned rather than left at the #227 default of 0.99, which would put the
    # ceiling exactly on the DOWN ask and complete the pair -- this test is
    # about the cap biting, so it needs a cap below the ask.
    engine.max_pair_cost = 0.98
    _open_50_50_quotes(engine, slug, fake_market, now - 1)
    # Initial resting bids: 0.48 / 0.48.
    # Poll 1: UP ask is 0.48 -> UP fills at 0.48.
    # DOWN ask is 0.51.
    # Max allowed DOWN bid with max_pair_cost=0.98 is 0.98 - 0.48 = 0.50.
    # Issue #231: on fill tick (progress == 0), chase does not impulsively raise quote.
    poll1 = {
        "market": fake_market,
        "up_book": {"best_bid": 0.47, "best_ask": 0.479},
        "down_book": {"best_bid": 0.49, "best_ask": 0.51},
    }
    engine._update_market_strategy(slug, poll1, now)
    mstate = engine.markets[slug]
    assert mstate.filled_up is True
    assert mstate.filled_down is False
    assert mstate.chased_leg is None
    # Poll 2 at dead zone (progress == 1.0):
    # Ceiling reaches max allowed bid 0.50, DOWN ask is 0.499 -> DOWN fills at 0.50!
    t_dz = now + 260
    poll2 = {
        "market": fake_market,
        "up_book": {"best_bid": 0.48, "best_ask": 0.50},
        "down_book": {"best_bid": 0.49, "best_ask": 0.50},
    }
    engine._update_market_strategy(slug, poll2, t_dz)
    assert mstate.filled_down is True
    assert mstate.pair_captured is True
    assert mstate.status == "PAIR_MERGED"
    assert mstate.chased_fill is True
    assert mstate.chased_leg is None
    # Profit: (1.00 - (0.48 + 0.50)) * 5 = 0.02 * 5 = $0.10
    assert round(mstate.realized_pnl_usd, 2) == 0.10


def test_the_chase_before_the_fill_block_also_marks_the_quote_as_placed(monkeypatch):
    """Issue #226: the chase runs in two places and both make a quote marketable.

    Review caught only the one inside the paper fill block being flagged. This
    drives the other: UP fills on a tick where the DOWN book has no ask, so the
    chase cannot anchor and DOWN stays at 0.48. The next tick's chase — the one
    above the fill block — raises DOWN onto a standing 0.49 ask, which is a
    placement, not a touch against something we were queued behind.
    """
    engine = LiveTraderEngine(load_persisted=False)
    monkeypatch.setattr(engine.stream_bridge, "start", lambda: None)
    monkeypatch.setattr(engine, "_schedule_wallet_balance_fetch", lambda: None)
    engine.start()
    slug = "btc-up-or-down-5m"
    now = time.time()
    fake_market = LiveMarket(
        condition_id="0xchase226", market_slug="btc-up-down-5m",
        up_token="tok_up", down_token="tok_dn",
        start_ts=now - 10, end_ts=now + 290, tick_size=0.01, neg_risk=False,
    )
    _open_50_50_quotes(engine, slug, fake_market, now - 1)
    mstate = engine.markets[slug]

    engine._update_market_strategy(slug, {
        "market": fake_market,
        "up_book": {"best_bid": 0.47, "best_ask": 0.479},
        "down_book": {"best_bid": 0.47, "best_ask": 0.48},
    }, now)
    assert mstate.filled_up is True
    assert mstate.filled_down is False, "a touch does not fill a queued quote"
    assert mstate.resting_down == 0.48, "nothing above 0.48 to chase to"

    # Dead zone reached: progress reaches 1.0, enabling chase to 0.49
    engine._update_market_strategy(slug, {
        "market": fake_market,
        "up_book": {"best_bid": 0.47, "best_ask": 0.479},
        "down_book": {"best_bid": 0.48, "best_ask": 0.49},
    }, now + 260)
    assert mstate.resting_down == 0.49
    assert mstate.filled_down is True, "a chased quote lands on the ask and fills"
    assert mstate.fill_price_down == 0.49


def test_leg_chase_symmetric_down_first(monkeypatch):
    """When DOWN fills first, UP quote steps up to ask bounded by max_pair_cost."""
    engine = LiveTraderEngine(load_persisted=False)
    monkeypatch.setattr(engine.stream_bridge, "start", lambda: None)
    monkeypatch.setattr(engine, "_schedule_wallet_balance_fetch", lambda: None)
    engine.start()
    slug = "eth-up-or-down-5m"
    now = time.time()

    fake_market = LiveMarket(
        condition_id="0xchase_eth",
        market_slug="eth-up-down-5m",
        up_token="tok_eth_up",
        down_token="tok_eth_dn",
        start_ts=now - 10,
        end_ts=now + 290,
        tick_size=0.01,
        neg_risk=False,
    )

    _open_50_50_quotes(engine, slug, fake_market, now - 1)
    # DOWN fills at 0.48. UP ask is 0.49.
    # Allowed UP quote: 0.98 - 0.48 = 0.50.
    poll1 = {
        "market": fake_market,
        "up_book": {"best_bid": 0.48, "best_ask": 0.49},
        "down_book": {"best_bid": 0.47, "best_ask": 0.479},
    }
    engine._update_market_strategy(slug, poll1, now)
    mstate = engine.markets[slug]
    assert mstate.filled_down is True
    # Issue #231: on fill tick (progress == 0), UP quote does not impulsively step up
    assert mstate.filled_up is False

    # Advance to dead zone (progress == 1.0) -> UP chases and fills at 0.49
    engine._update_market_strategy(slug, poll1, now + 260)
    assert mstate.filled_up is True
    assert mstate.pair_captured is True
    assert mstate.chased_fill is True


def test_leg_chase_disabled_or_both_filled(monkeypatch):
    """When enable_leg_chase=False or both legs filled, quotes remain passive."""
    # Case 1: enable_leg_chase=False
    engine = LiveTraderEngine(load_persisted=False)
    monkeypatch.setattr(engine.stream_bridge, "start", lambda: None)
    monkeypatch.setattr(engine, "_schedule_wallet_balance_fetch", lambda: None)
    engine.enable_leg_chase = False
    engine.start()
    slug = "btc-up-or-down-5m"
    now = time.time()

    fake_market = LiveMarket(
        condition_id="0xnochase",
        market_slug="btc-up-down-5m",
        up_token="tok_up",
        down_token="tok_dn",
        start_ts=now - 10,
        end_ts=now + 290,
        tick_size=0.01,
        neg_risk=False,
    )

    _open_50_50_quotes(engine, slug, fake_market, now - 1)
    poll1 = {
        "market": fake_market,
        "up_book": {"best_bid": 0.47, "best_ask": 0.479},
        "down_book": {"best_bid": 0.49, "best_ask": 0.50},
    }
    engine._update_market_strategy(slug, poll1, now)
    mstate = engine.markets[slug]
    assert mstate.filled_up is True
    assert mstate.filled_down is False
    assert mstate.chased_leg is None
    assert mstate.resting_down == 0.48

    # Case 2: When both legs are filled and enable_leg_chase=True, chase does not trigger
    engine2 = LiveTraderEngine(load_persisted=False)
    monkeypatch.setattr(engine2.stream_bridge, "start", lambda: None)
    monkeypatch.setattr(engine2, "_schedule_wallet_balance_fetch", lambda: None)
    engine2.start()
    m2 = engine2.markets[slug]
    m2.filled_up = True
    m2.filled_down = True
    m2.fill_price_up = 0.48
    m2.fill_price_down = 0.48
    m2.resting_up = 0.48
    m2.resting_down = 0.48
    m2.pair_captured = True
    engine2._update_market_strategy(slug, poll1, now)
    assert m2.chased_leg is None


def test_update_config_leg_chase_knobs():
    """enable_leg_chase and max_pair_cost roundtrip and clamp properly."""
    engine = LiveTraderEngine(load_persisted=False)
    state = engine.update_config(enable_leg_chase=False, max_pair_cost=0.97)
    assert engine.enable_leg_chase is False
    assert engine.max_pair_cost == 0.97
    assert state["params"]["enable_leg_chase"] is False
    assert state["params"]["max_pair_cost"] == 0.97

    # Clamping
    engine.update_config(max_pair_cost=1.50)
    assert engine.max_pair_cost == 1.00
    engine.update_config(max_pair_cost=0.20)
    assert engine.max_pair_cost == 0.50

    # Guard while running
    engine.is_running = True
    with pytest.raises(ValueError, match="Cannot change strategy parameters while the trading bot is running"):
        engine.update_config(max_pair_cost=0.96)


# ==============================================================================
# Issue #160: Mark-to-book expiry settlement & anti-silent-0.50
# ==============================================================================

def test_rollover_settle_mark_to_book_replaces_silent_50():
    """Issue #160: Window rollover must mark naked legs to true book bid or complement, never silent 0.50."""
    engine = LiveTraderEngine(load_persisted=False)
    m = engine.markets["btc-up-or-down-5m"]
    m.condition_id = "0xold_cid"
    m.filled_up = True
    m.fill_price_up = 0.47
    m.resting_up = 0.47
    # Real market was a blowout loss: UP bid is empty/missing, DOWN ask is 0.99
    m.up_bid = None
    m.down_ask = 0.99
    # Settle at rollover
    engine._handle_window_rollover(m, time.time(), new_cid="0xnew_cid")
    
    trade = [t for t in engine.trades if t.action == "WINDOW_SETTLE"][-1]
    # Defective behavior booked: (0.50 - 0.47) * 5 = +0.150
    # True behavior must book: mark = 1.0 - 0.99 = 0.01 -> (0.01 - 0.47) * 5 = -2.300
    assert trade.exit_price != 0.50, "Settle must not silently fall back to 0.50"
    assert trade.exit_price == pytest.approx(0.01, abs=1e-4)
    assert trade.pnl_usd == pytest.approx(-2.30, abs=0.01)
    assert "complement_ask" in trade.notes


def test_rollover_settle_direct_bid_up_and_down():
    """Direct book bids are used when present and non-empty."""
    engine = LiveTraderEngine(load_persisted=False)
    m = engine.markets["eth-up-or-down-5m"]
    m.condition_id = "0xeth_cid"
    m.filled_up = True
    m.fill_price_up = 0.42
    m.up_bid = 0.01
    m.down_ask = 0.99
    engine._handle_window_rollover(m, time.time(), new_cid="0xeth_next")
    trade_up = [t for t in engine.trades if t.action == "WINDOW_SETTLE"][-1]
    assert trade_up.exit_price == pytest.approx(0.01, abs=1e-4)
    assert trade_up.pnl_usd == pytest.approx((0.01 - 0.42) * 5, abs=1e-3)
    assert "direct_bid" in trade_up.notes

    # Now test DOWN side
    m2 = engine.markets["sol-up-or-down-5m"]
    m2.condition_id = "0xsol_cid"
    m2.filled_down = True
    m2.fill_price_down = 0.45
    m2.down_bid = 0.95
    engine._handle_window_rollover(m2, time.time(), new_cid="0xsol_next")
    trade_dn = [t for t in engine.trades if t.action == "WINDOW_SETTLE"][-1]
    assert trade_dn.exit_price == pytest.approx(0.95, abs=1e-4)
    assert trade_dn.pnl_usd == pytest.approx((0.95 - 0.45) * 5, abs=1e-3)
    assert "direct_bid" in trade_dn.notes


def test_rollover_settle_latched_bid_when_book_wiped_at_boundary():
    """When boundary poll wipes book to empty/None, engine falls back to latched valid bids."""
    engine = LiveTraderEngine(load_persisted=False)
    m = engine.markets["xrp-up-or-down-5m"]
    m.condition_id = "0xxrp_cid"
    m.filled_up = True
    m.fill_price_up = 0.48
    # Earlier tick latched a real executable bid
    m.last_valid_up_bid = 0.02
    # Boundary poll cleared bids to None
    m.up_bid = None
    m.down_ask = None
    engine._handle_window_rollover(m, time.time(), new_cid="0xxrp_next")
    trade = [t for t in engine.trades if t.action == "WINDOW_SETTLE"][-1]
    assert trade.exit_price == pytest.approx(0.02, abs=1e-4)
    assert trade.pnl_usd == pytest.approx((0.02 - 0.48) * 5, abs=1e-3)
    assert "latched_bid" in trade.notes


def test_rollover_settle_fails_loud_on_empty_books():
    """Anti-cheat / safety gate: Settle must raise RuntimeError instead of silently using 0.50,
    and _handle_window_rollover must catch it, record MARK_UNAVAILABLE, and reset cleanly."""
    engine = LiveTraderEngine(load_persisted=False)
    m = engine.markets["bnb-up-or-down-5m"]
    m.condition_id = "0xbnb_cid"
    m.filled_up = True
    m.fill_price_up = 0.46
    # No books, no latched state, default 0.50 mid
    m.up_bid = None
    m.down_bid = None
    m.up_ask = None
    m.down_ask = None
    m.last_valid_up_bid = None
    m.last_valid_down_bid = None
    m.last_valid_up_ask = None
    m.last_valid_down_ask = None
    m.mid = 0.50

    # 1. Direct resolver call raises RuntimeError
    with pytest.raises(RuntimeError, match="No executable book mark available"):
        engine._resolve_exit_bid(m, "UP")

    # 2. Rollover catches it, logs critical, records MARK_UNAVAILABLE, and completes window reset
    now = time.time()
    engine._handle_window_rollover(m, now, new_cid="0xbnb_next")
    assert len(engine.trades) == 1
    t = engine.trades[-1]
    assert t.action == "MARK_UNAVAILABLE"
    assert t.exit_price == 0.0
    assert t.pnl_usd == pytest.approx(-0.46 * 5, abs=1e-3)
    assert t.pnl_pct == -100.0
    assert "MARK_UNAVAILABLE" in t.notes
    # Clean reset
    assert m.filled_up is False
    assert m.filled_down is False


def test_rollover_latching_pipeline_integration():
    """Integration: active tick latches valid quotes, subsequent empty boundary tick uses latched bid."""
    engine = LiveTraderEngine(load_persisted=False)
    slug = "btc-up-or-down-5m"
    m = engine.markets[slug]
    m.condition_id = "0xbtc_cid"
    m.filled_up = True
    m.fill_price_up = 0.50
    now = 1000.0

    # 1. Active tick with real books
    poll_active = {
        "market": {"conditionId": "0xbtc_cid", "start_ts": now - 100, "end_ts": now + 200},
        "up_book": {"best_bid": 0.42, "best_ask": 0.55},
        "down_book": {"best_bid": 0.44, "best_ask": 0.57},
    }
    engine._update_market_strategy(slug, poll_active, now)
    assert m.last_valid_up_bid == 0.42
    assert m.last_valid_down_ask == 0.57

    # 2. Boundary tick where book is wiped (None)
    poll_boundary = {
        "market": {"conditionId": "0xbtc_cid", "start_ts": now - 100, "end_ts": now + 200},
        "up_book": {"best_bid": None, "best_ask": None},
        "down_book": {"best_bid": None, "best_ask": None},
    }
    engine._update_market_strategy(slug, poll_boundary, now + 1.0)
    assert m.up_bid is None
    assert m.last_valid_up_bid == 0.42

    # 3. Rollover occurs, settles via latched_bid
    engine._handle_window_rollover(m, now + 200.0, new_cid="0xbtc_next")
    trade = engine.trades[-1]
    assert trade.action == "WINDOW_SETTLE"
    assert trade.exit_price == pytest.approx(0.42, abs=1e-4)
    assert "latched_bid" in trade.notes


def test_rollover_settle_down_leg_complement_and_clamping():
    """DOWN leg complement ask (1.0 - up_ask) and lower clamping to 0.0001."""
    engine = LiveTraderEngine(load_persisted=False)
    slug = "eth-up-or-down-5m"
    m = engine.markets[slug]
    m.condition_id = "0xeth_cid"
    m.filled_down = True
    m.fill_price_down = 0.45
    # When opposite ask is near 1.0 (e.g. 0.99999), 1.0 - 0.99999 = 0.00001, clamped to 0.0001
    m.down_bid = None
    m.up_ask = 0.99999
    now = 1000.0
    engine._handle_window_rollover(m, now, new_cid="0xeth_next")
    trade = engine.trades[-1]
    assert trade.action == "WINDOW_SETTLE"
    assert trade.exit_price == pytest.approx(0.0001, abs=1e-6)
    assert "complement_ask" in trade.notes


def test_rollover_settle_zero_bid_cascades_to_complement():
    """Zero or negative bids (invalid quotes) must not be accepted as direct_bid and cascade to complement."""
    engine = LiveTraderEngine(load_persisted=False)
    slug = "sol-up-or-down-5m"
    m = engine.markets[slug]
    m.condition_id = "0xsol_cid"
    m.filled_up = True
    m.fill_price_up = 0.45
    m.up_bid = 0.0  # Invalid non-positive bid
    m.down_ask = 0.35  # Valid opposite ask
    now = 1000.0
    engine._handle_window_rollover(m, now, new_cid="0xsol_next")
    trade = engine.trades[-1]
    assert trade.action == "WINDOW_SETTLE"
    assert trade.exit_price == pytest.approx(0.65, abs=1e-4)
    assert "complement_ask" in trade.notes


def test_rollover_clears_latched_bids_for_next_window():
    """Rollover must cleanly wipe latched quotes so they cannot leak into the next window."""
    engine = LiveTraderEngine(load_persisted=False)
    slug = "bnb-up-or-down-5m"
    m = engine.markets[slug]
    m.condition_id = "0xbnb_cid"
    m.last_valid_up_bid = 0.49
    m.last_valid_down_bid = 0.48
    m.last_valid_up_ask = 0.51
    m.last_valid_down_ask = 0.52
    engine._handle_window_rollover(m, time.time(), new_cid="0xbnb_next")
    assert m.last_valid_up_bid is None
    assert m.last_valid_down_bid is None
    assert m.last_valid_up_ask is None
    assert m.last_valid_down_ask is None


def test_shadow_snapshot_exports_book_bids():
    """Issue #160: snapshot() in shadow_ev_pilot exports book prices and latched bids."""
    from scripts.shadow_ev_pilot import snapshot
    engine = LiveTraderEngine(load_persisted=False)
    slug = "btc-up-or-down-5m"
    m = engine.markets[slug]
    m.up_bid = 0.48
    m.up_ask = 0.52
    m.down_bid = 0.47
    m.down_ask = 0.51
    m.last_valid_up_bid = 0.48
    m.last_valid_down_bid = 0.47
    m.last_valid_up_ask = 0.52
    m.last_valid_down_ask = 0.51

    snap = snapshot(engine)
    mkt = snap["markets"][slug]
    assert mkt["up_bid"] == 0.48
    assert mkt["up_ask"] == 0.52
    assert mkt["down_bid"] == 0.47
    assert mkt["down_ask"] == 0.51
    assert mkt["last_valid_up_bid"] == 0.48
    assert mkt["last_valid_down_bid"] == 0.47
    assert mkt["last_valid_up_ask"] == 0.52
    assert mkt["last_valid_down_ask"] == 0.51


def test_unpriceable_book_yields_none_mid_and_no_drift():
    """Issue #207: unpriceable book sets mstate.mid to None and does NOT accumulate drift."""
    engine = LiveTraderEngine(load_persisted=False)
    engine.is_running = True
    slug = "btc-up-or-down-5m"
    now = time.time()
    fake_market = LiveMarket(
        condition_id="0xunpriceable_cid",
        market_slug="btc-up-or-down-5m-fake",
        up_token="up_token",
        down_token="dn_token",
        start_ts=now,
        end_ts=now + 300,
        tick_size=0.01,
        neg_risk=False,
    )
    # up_book has quotes, but down_book is completely unpriceable (None bids/asks)
    poll_data = {
        "market": fake_market,
        "up_book": {"best_bid": 0.48, "best_ask": 0.52},
        "down_book": {"best_bid": None, "best_ask": None},
    }
    engine._update_market_strategy(slug, poll_data, now)
    m = engine.markets[slug]
    assert m.mid is None
    assert m.max_up_drift == 0.0
    assert m.max_down_drift == 0.0
    assert not m.order_id_up and not m.order_id_down


def test_unpriceable_book_prevents_quoting():
    """Issue #207: engine refuses to quote when a leg is unpriceable even with entry delay expired."""
    engine = LiveTraderEngine(load_persisted=False)
    engine.is_running = True
    engine.entry_delay_sec = 0.0
    slug = "eth-up-or-down-5m"
    now = time.time()
    fake_market = LiveMarket(
        condition_id="0xeth_unpriceable",
        market_slug="eth-up-or-down-5m-fake",
        up_token="up_tok",
        down_token="dn_tok",
        start_ts=now - 10,
        end_ts=now + 290,
        tick_size=0.01,
        neg_risk=False,
    )
    poll_data = {
        "market": fake_market,
        "up_book": {"best_bid": 0.50, "best_ask": None},
        "down_book": {"best_bid": 0.49, "best_ask": 0.51},
    }
    engine._update_market_strategy(slug, poll_data, now)
    m = engine.markets[slug]
    assert m.mid is None
    assert m.order_status_up != "RESTING"
    assert m.order_status_down != "RESTING"
    assert m.status == "NO_BOOK"


def test_unpriceable_book_timeout_sets_no_book_skipped():
    """Issue #207: window reaching dead zone without a valid two-sided book is marked NO_BOOK_SKIPPED."""
    engine = LiveTraderEngine(load_persisted=False, dead_zone_val=0.10, dead_zone_unit="pct")
    engine.is_running = True
    slug = "sol-up-or-down-5m"
    start_ts = time.time()
    fake_market = LiveMarket(
        condition_id="0xsol_unpriceable",
        market_slug="sol-up-or-down-5m-fake",
        up_token="sol_up",
        down_token="sol_dn",
        start_ts=start_ts,
        end_ts=start_ts + 300,
        tick_size=0.01,
        neg_risk=False,
    )
    poll_data = {
        "market": fake_market,
        "up_book": {"best_bid": None, "best_ask": None},
        "down_book": {"best_bid": None, "best_ask": None},
    }
    # Tick 1: 5s into window - engine attaches on time, but book is unpriceable
    engine._update_market_strategy(slug, poll_data, start_ts + 5)
    m = engine.markets[slug]
    assert m.mid is None
    assert m.late_start_skip is False
    assert m.status in ("IDLE", "PRE_QUOTING", "NO_BOOK")

    # Tick 2: 275s into window - in dead zone (remaining = 25s <= 30s)
    engine._update_market_strategy(slug, poll_data, start_ts + 275)
    assert m.mid is None
    assert m.status == "NO_BOOK_SKIPPED"
    assert "unpriceable" in m.last_action.lower() or "no book" in m.last_action.lower()


# ===========================================================================
# Issue #209: Stop loss is measured from entry price, not from 0.50
# ===========================================================================

def test_stop_loss_anchored_to_fill_price_up():
    """Issue #209: UP leg filled at 0.45 with exit_thresh=0.05 does not stop out at 0.45 or 0.42."""
    engine = LiveTraderEngine(load_persisted=False)
    engine.mode = "paper"
    engine.is_running = True
    engine.exit_thresh = 0.05
    engine.exit_reversal = 0.02
    slug = "btc-up-or-down-5m"
    now = time.time()
    fake_market = LiveMarket(
        condition_id="0xbtc_test_209",
        market_slug="btc-updown-5m-209",
        up_token="btc_up",
        down_token="btc_dn",
        start_ts=now - 20,
        end_ts=now + 280,
        tick_size=0.01,
        neg_risk=False,
    )
    m = engine.markets[slug]
    m.market_slug = fake_market.market_slug
    m.up_token = fake_market.up_token
    m.down_token = fake_market.down_token
    m.start_ts = fake_market.start_ts
    m.end_ts = fake_market.end_ts
    m.filled_up = True
    m.filled_down = False
    m.fill_price_up = 0.45
    m.resting_up = 0.45
    m.order_shares = 10
    m.status = "QUOTING"

    # Tick 1: mid = 0.45 (same as entry price).
    # With 0.50 anchor, drift was 0.05 (stopped out immediately!).
    # With entry anchor, drift is 0.00.
    poll_data_1 = {
        "market": fake_market,
        "up_book": {"best_bid": 0.44, "best_ask": 0.46},
        "down_book": {"best_bid": 0.54, "best_ask": 0.56},
    }
    engine._update_market_strategy(slug, poll_data_1, now)
    assert not m.exit_taken
    assert m.status != "STOP_EXIT_PENDING"
    assert m.max_down_drift == pytest.approx(0.0, abs=1e-4)

    # Tick 2: mid = 0.42. Adverse excursion = 0.45 - 0.42 = 0.03 < 0.05.
    now += 1
    poll_data_2 = {
        "market": fake_market,
        "up_book": {"best_bid": 0.41, "best_ask": 0.43},
        "down_book": {"best_bid": 0.57, "best_ask": 0.59},
    }
    engine._update_market_strategy(slug, poll_data_2, now)
    assert not m.exit_taken
    assert m.status != "STOP_EXIT_PENDING"
    assert m.max_down_drift == pytest.approx(0.03, abs=1e-4)

    # Tick 3: mid = 0.40. Adverse excursion = 0.45 - 0.40 = 0.05 >= exit_thresh (0.05).
    # Stop loss triggers!
    now += 1
    poll_data_3 = {
        "market": fake_market,
        "up_book": {"best_bid": 0.39, "best_ask": 0.41},
        "down_book": {"best_bid": 0.59, "best_ask": 0.61},
    }
    engine._update_market_strategy(slug, poll_data_3, now)
    assert m.max_down_drift == pytest.approx(0.05, abs=1e-4)
    assert m.exit_taken or m.status == "STOP_EXIT_PENDING"


def test_stop_loss_anchored_to_fill_price_down():
    """Issue #209: DOWN leg filled at 0.45 (implied mid 0.55) does not stop out until mid >= 0.60."""
    engine = LiveTraderEngine(load_persisted=False)
    engine.mode = "paper"
    engine.is_running = True
    engine.exit_thresh = 0.05
    engine.exit_reversal = 0.02
    slug = "eth-up-or-down-5m"
    now = time.time()
    fake_market = LiveMarket(
        condition_id="0xeth_test_209",
        market_slug="eth-updown-5m-209",
        up_token="eth_up",
        down_token="eth_dn",
        start_ts=now - 20,
        end_ts=now + 280,
        tick_size=0.01,
        neg_risk=False,
    )
    m = engine.markets[slug]
    m.market_slug = fake_market.market_slug
    m.up_token = fake_market.up_token
    m.down_token = fake_market.down_token
    m.start_ts = fake_market.start_ts
    m.end_ts = fake_market.end_ts
    m.filled_up = False
    m.filled_down = True
    m.fill_price_down = 0.45
    m.resting_down = 0.45
    m.order_shares = 10
    m.status = "QUOTING"

    # Tick 1: mid = 0.55 (implied down mid = 0.45, same as entry).
    poll_data_1 = {
        "market": fake_market,
        "up_book": {"best_bid": 0.54, "best_ask": 0.56},
        "down_book": {"best_bid": 0.44, "best_ask": 0.46},
    }
    engine._update_market_strategy(slug, poll_data_1, now)
    assert not m.exit_taken
    assert m.status != "STOP_EXIT_PENDING"
    assert m.max_up_drift == pytest.approx(0.0, abs=1e-4)

    # Tick 2: mid = 0.58. Adverse excursion = 0.58 - 0.55 = 0.03 < 0.05.
    now += 1
    poll_data_2 = {
        "market": fake_market,
        "up_book": {"best_bid": 0.57, "best_ask": 0.59},
        "down_book": {"best_bid": 0.41, "best_ask": 0.43},
    }
    engine._update_market_strategy(slug, poll_data_2, now)
    assert not m.exit_taken
    assert m.status != "STOP_EXIT_PENDING"
    assert m.max_up_drift == pytest.approx(0.03, abs=1e-4)

    # Tick 3: mid = 0.60. Adverse excursion = 0.60 - 0.55 = 0.05 >= 0.05.
    # Stop loss triggers!
    now += 1
    poll_data_3 = {
        "market": fake_market,
        "up_book": {"best_bid": 0.59, "best_ask": 0.61},
        "down_book": {"best_bid": 0.39, "best_ask": 0.41},
    }
    engine._update_market_strategy(slug, poll_data_3, now)
    assert m.max_up_drift == pytest.approx(0.05, abs=1e-4)
    assert m.exit_taken or m.status == "STOP_EXIT_PENDING"


def test_reversal_anchored_to_entry_price():
    """Issue #209: Reversal detection checks distance to entry price, not to 0.50."""
    engine = LiveTraderEngine(load_persisted=False)
    engine.mode = "paper"
    engine.is_running = True
    engine.exit_thresh = 0.05
    engine.exit_reversal = 0.02
    slug = "sol-up-or-down-5m"
    now = time.time()
    fake_market = LiveMarket(
        condition_id="0xsol_test_209",
        market_slug="sol-updown-5m-209",
        up_token="sol_up",
        down_token="sol_dn",
        start_ts=now - 20,
        end_ts=now + 280,
        tick_size=0.01,
        neg_risk=False,
    )
    m = engine.markets[slug]
    m.market_slug = fake_market.market_slug
    m.up_token = fake_market.up_token
    m.down_token = fake_market.down_token
    m.start_ts = fake_market.start_ts
    m.end_ts = fake_market.end_ts
    m.filled_up = True
    m.filled_down = False
    m.fill_price_up = 0.45
    m.resting_up = 0.45
    # Simulate adverse excursion directly: adverse drift 0.06 >= exit_thresh 0.05
    m.max_down_drift = 0.06
    assert not m.reversal_seen_down

    # Retrace tick: mid retraces to 0.44 (within 0.01 of entry 0.45, < exit_reversal 0.02).
    # Anchored on the 0.45 entry the reversal arms and suppresses the exit;
    # anchored on 0.50 the distance is 0.06 and it does not.
    now += 1
    poll_data_2 = {
        "market": fake_market,
        "up_book": {"best_bid": 0.43, "best_ask": 0.45},
        "down_book": {"best_bid": 0.55, "best_ask": 0.57},
    }
    engine._update_market_strategy(slug, poll_data_2, now)
    assert m.reversal_seen_down
    assert not m.exit_taken
    assert m.status != "STOP_EXIT_PENDING"


def test_stop_loss_anchored_to_an_entry_above_050():
    """Issue #209: a leg filled above 0.50 was under-protected, not over-protected.

    The old anchor only counted a down excursion once the mid was below 0.50, so
    an UP leg entered at 0.55 could give back five full cents on the way down to
    0.50 with `max_down_drift` still reading 0.00 and the stop never arming --
    the mirror image of the reported bug, and the more dangerous direction.
    """
    engine = LiveTraderEngine(load_persisted=False)
    engine.mode = "paper"
    engine.is_running = True
    engine.exit_thresh = 0.05
    engine.exit_reversal = 0.02
    # The DOWN book sits close enough to the cap here that the chase would pair
    # the window on tick 1; this test is about the naked UP leg's stop.
    engine.enable_leg_chase = False
    slug = "btc-up-or-down-5m"
    now = time.time()
    fake_market = LiveMarket(
        condition_id="0xbtc_test_209_high",
        market_slug="btc-updown-5m-209-high",
        up_token="btc_up",
        down_token="btc_dn",
        start_ts=now - 20,
        end_ts=now + 280,
        tick_size=0.01,
        neg_risk=False,
    )
    m = engine.markets[slug]
    m.market_slug = fake_market.market_slug
    m.up_token = fake_market.up_token
    m.down_token = fake_market.down_token
    m.start_ts = fake_market.start_ts
    m.end_ts = fake_market.end_ts
    m.filled_up = True
    m.filled_down = False
    m.fill_price_up = 0.55
    m.resting_up = 0.55
    m.resting_down = 0.20
    m.order_shares = 10
    m.status = "QUOTING"

    # Tick 1: mid = 0.53. Excursion = 0.55 - 0.53 = 0.02 < 0.05, no stop.
    poll_data_1 = {
        "market": fake_market,
        "up_book": {"best_bid": 0.52, "best_ask": 0.54},
        "down_book": {"best_bid": 0.46, "best_ask": 0.48},
    }
    engine._update_market_strategy(slug, poll_data_1, now)
    assert not m.exit_taken
    assert m.max_down_drift == pytest.approx(0.02, abs=1e-4)

    # Tick 2: mid = 0.50. Excursion = 0.55 - 0.50 = 0.05 >= 0.05, so the stop
    # fires. Anchored on 0.50 this excursion read 0.00 and the leg rode on.
    now += 1
    poll_data_2 = {
        "market": fake_market,
        "up_book": {"best_bid": 0.49, "best_ask": 0.51},
        "down_book": {"best_bid": 0.49, "best_ask": 0.51},
    }
    engine._update_market_strategy(slug, poll_data_2, now)
    assert m.max_down_drift == pytest.approx(0.05, abs=1e-4)
    assert m.exit_taken or m.status == "STOP_EXIT_PENDING"


# --- Issue #224: invariants (no invented numbers, one window clock) ----------

def _clock_engine(slug: str = "btc-up-or-down-5m") -> LiveTraderEngine:
    engine = LiveTraderEngine(load_persisted=False)
    engine.mode = "paper"
    engine.is_running = True
    engine.enable_leg_chase = False
    return engine


def test_window_with_no_clock_is_not_traded():
    """Metadata whose end_ts does not follow start_ts leaves the window untraded.

    Invariant 1 (#224): every time gate is a fraction or offset of the window,
    so a window with no usable pair has no clock, no gate may be evaluated, and
    nothing is quoted into it.
    """
    engine = _clock_engine()
    slug = "btc-up-or-down-5m"
    now = time.time()
    broken = LiveMarket(
        condition_id="0xno_clock",
        market_slug="btc-updown-5m-no-clock",
        up_token="btc_up",
        down_token="btc_dn",
        start_ts=now + 100.0,
        end_ts=now + 50.0,   # end before start: not a usable pair
        tick_size=0.01,
        neg_risk=False,
    )
    m = engine.markets[slug]
    engine._update_market_strategy(slug, {
        "market": broken,
        "up_book": {"best_bid": 0.49, "best_ask": 0.51},
        "down_book": {"best_bid": 0.49, "best_ask": 0.51},
    }, now)

    assert not m.filled_up and not m.filled_down
    assert m.order_id_up is None and m.order_id_down is None
    assert "clock" in m.last_action.lower()


def test_window_length_ignores_the_market_name():
    """A 15-minute window whose slug says nothing is still measured as 900s.

    The duration used to be read out of the slug: `900.0 if "15m" in slug else
    300.0`. With `entry_timeout_pct=0.10` a window 120s old is past the cutoff
    of a guessed 300s window but well inside the real 900s one, so a quote here
    proves the length came from the metadata. 60s keeps the window inside the
    late-start guard's 10% too (90s of 900), which is the other gate this
    length feeds.
    """
    engine = _clock_engine()
    slug = "btc-up-or-down-5m"
    now = time.time()
    quarter_hour = LiveMarket(
        condition_id="0xfifteen",
        market_slug="btc-updown-quarterly-no-duration-in-the-name",
        up_token="btc_up",
        down_token="btc_dn",
        start_ts=now - 60.0,
        end_ts=now + 840.0,      # 900s total, 60s elapsed = 6.7%
        tick_size=0.01,
        neg_risk=False,
    )
    m = engine.markets[slug]
    engine._update_market_strategy(slug, {
        "market": quarter_hour,
        "up_book": {"best_bid": 0.49, "best_ask": 0.51},
        "down_book": {"best_bid": 0.49, "best_ask": 0.51},
    }, now)

    # 60s is 6.7% of 900 -- past a guessed 300s window's 30s cutoff, inside the
    # real one's 90s cutoff.
    assert not m.entry_cancelled_timeout
    assert m.status == "QUOTING"


def test_stop_exit_holds_when_no_bid_can_be_resolved():
    """With no book and no latched quote, the stop holds instead of selling at 0.40.

    Invariant 0 (#224). `_resolve_exit_bid` raising means there is no executable
    mark anywhere, so the position stays open and the exit re-evaluates on the
    next tick.
    """
    engine = _clock_engine()
    slug = "btc-up-or-down-5m"
    now = time.time()
    m = engine.markets[slug]
    m.slug = slug
    m.filled_up = True
    m.fill_price_up = 0.55
    m.order_shares = 10
    m.status = "STOP_EXIT_PENDING"
    # Nothing priceable: no book, no latch, no observed update.
    m.up_bid = m.up_ask = m.down_bid = m.down_ask = None
    m.last_valid_up_bid = m.last_valid_down_bid = None
    m.last_valid_up_ask = m.last_valid_down_ask = None
    m.mid = None
    m.last_update_ts = 0

    engine._execute_stop_exit(slug, m, "UP", None, "no-book stop", now)

    assert not m.exit_taken
    assert m.status == "STOP_EXIT_PENDING"
    assert not engine.trades


def test_stop_exit_uses_the_resolver_ladder_not_a_constant():
    """A leg with no bid of its own still exits off the complement ask, not 0.40."""
    engine = _clock_engine()
    slug = "btc-up-or-down-5m"
    now = time.time()
    m = engine.markets[slug]
    m.slug = slug
    m.filled_up = True
    m.fill_price_up = 0.55
    m.order_shares = 10
    m.status = "STOP_EXIT_PENDING"
    m.up_bid = None
    m.down_ask = 0.62          # complement: UP is worth 0.38
    m.last_update_ts = now

    engine._execute_stop_exit(slug, m, "UP", None, "complement stop", now)

    assert m.exit_taken
    assert engine.trades[-1].exit_price == pytest.approx(0.38, abs=1e-6)


