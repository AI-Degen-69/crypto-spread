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

    # Updating other parameters like offset or shares while running is allowed
    engine.update_config(offset=0.03, shares=10)
    assert engine.offset == 0.03
    assert engine.shares == 10

    # Once stopped, changing market selection is allowed
    engine.is_running = False
    engine.update_config(tokens=["SOL"], durations=[300])
    assert set(engine.markets.keys()) == {"sol-up-or-down-5m"}


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


def test_adverse_open_drift_gate():
    engine = LiveTraderEngine(load_persisted=False)
    engine.is_running = True
    engine.mode = "paper"
    engine.offset = 0.02
    engine.exit_thresh = 0.05
    engine.shares = 5
    slug = "btc-up-or-down-5m"
    now = 1000.0
    poll_data = {
        "market": {
            "conditionId": "cid_drifted",
            "slug": "btc-updown-5m-drifted",
            "up_token": "tok_up",
            "down_token": "tok_dn",
            "start_ts": now - 5.0,  # 5s elapsed < 30s cutoff
            "end_ts": now + 295.0,
        },
        "up_book": {"best_bid": 0.34, "best_ask": 0.36},
        "down_book": {"best_bid": 0.64, "best_ask": 0.66},
    }
    engine._update_market_strategy(slug, poll_data, now)
    m = engine.markets[slug]
    assert m.filled_up is False
    assert m.filled_down is False
    assert m.entry_cancelled_timeout is True
    assert m.status in ("DRIFT_SKIPPED", "TIMEOUT_NO_FILL")


def test_reset_pnl_clears_open_positions():
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

