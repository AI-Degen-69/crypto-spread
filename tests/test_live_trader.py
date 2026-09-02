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


def test_fetch_polymarket_account_value_mocked(monkeypatch):
    from unittest.mock import MagicMock
    from strategy.live_trader import fetch_polymarket_account_value
    from unittest.mock import patch

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

    with patch("py_clob_client_v2.client.ClobClient", fake_clob_cls):
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
        "start_ts": now - 60,
        "end_ts": now + 240,
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

    # Adverse drift: mid drops down to 0.44 (drift = 0.06 >= 0.05 exit_thresh)
    poll_stop = {
        "market": {"conditionId": "0xbtc123", "up_token": "tok_btc_up", "down_token": "tok_btc_dn", "start_ts": now - 100, "end_ts": now + 200},
        "up_book": {"best_bid": 0.43, "best_ask": 0.45},
        "down_book": {"best_bid": 0.55, "best_ask": 0.57},
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




