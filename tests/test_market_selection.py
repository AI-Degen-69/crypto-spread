"""Unit tests for pure select_window helper and live market fetcher adapters."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from strategy.markets import LiveMarket, fetch_live_market, select_window
from scripts.collect_ticks import fetch_live_for_series


def test_select_window_empty_or_invalid_events():
    """Verify non-list or empty events gracefully return None."""
    assert select_window([], 1000.0) is None
    assert select_window(None, 1000.0) is None
    assert select_window("invalid", 1000.0) is None
    assert select_window({"markets": []}, 1000.0) is None


def test_select_window_malformed_event_or_market_containers():
    """Verify malformed nested containers (non-dict events, null markets) are skipped."""
    events = [
        "not-a-dict",
        {"markets": None},
        {"markets": ["not-a-market-dict"]},
        {"markets": []},
    ]
    assert select_window(events, 1000.0) is None


def test_select_window_no_open_window():
    """Verify past and future windows are filtered out."""
    now = 1700000200.0
    events = [
        {
            "markets": [
                # Past window: 0 to 100
                {
                    "eventStartTime": "2023-11-14T22:00:00Z",
                    "endDate": "2023-11-14T22:01:40Z",
                    "clobTokenIds": '["1", "2"]',
                },
                # Future window: 300 to 400
                {
                    "eventStartTime": "2023-11-14T22:05:00Z",
                    "endDate": "2023-11-14T22:06:40Z",
                    "clobTokenIds": '["3", "4"]',
                },
            ]
        }
    ]
    # Check with now outside the windows
    assert select_window(events, 1700000200.0) is None


def test_select_window_picks_newest_start():
    """Verify that among multiple active windows, the one with the newest start wins."""
    now = 1700000150.0  # 2023-11-14T22:15:50Z
    # 1700000000 is 2023-11-14T22:13:20Z
    # Window A: start 100, end 300
    # Window B: start 120, end 300 (newer start)
    m_a = {
        "id": "A",
        "eventStartTime": "2023-11-14T22:15:00Z",  # 1700000100 approx
        "endDate": "2023-11-14T22:18:00Z",
        "clobTokenIds": '["1", "2"]',
    }
    m_b = {
        "id": "B",
        "eventStartTime": "2023-11-14T22:15:20Z",  # 20s later start
        "endDate": "2023-11-14T22:18:00Z",
        "clobTokenIds": '["3", "4"]',
    }
    events = [{"markets": [m_a, m_b]}]
    res = select_window(events, 1700000130.0)
    assert res is not None
    st, et, winning_m = res
    assert winning_m is m_b  # Preserves exact dict identity


def test_select_window_tie_breaking_preserves_first():
    """Verify that when start times are identical, the first encountered row wins."""
    m_first = {
        "id": "first",
        "eventStartTime": "2023-11-14T22:15:00Z",
        "endDate": "2023-11-14T22:20:00Z",
        "clobTokenIds": '["1", "2"]',
    }
    m_second = {
        "id": "second",
        "eventStartTime": "2023-11-14T22:15:00Z",
        "endDate": "2023-11-14T22:20:00Z",
        "clobTokenIds": '["3", "4"]',
    }
    events = [{"markets": [m_first, m_second]}]
    res = select_window(events, 1700000120.0)
    assert res is not None
    assert res[2] is m_first


def test_select_window_boundary_conditions():
    """Verify start-inclusive and end-exclusive interval semantics."""
    # 2023-11-14T22:15:00+00:00 = 1700000100
    # 2023-11-14T22:20:00+00:00 = 1700000400
    m = {
        "eventStartTime": "2023-11-14T22:15:00Z",
        "endDate": "2023-11-14T22:20:00Z",
        "clobTokenIds": '["1", "2"]',
    }
    events = [{"markets": [m]}]
    st_val = 1700000100.0  # exact start
    et_val = 1700000400.0  # exact end

    # Test exact start (inclusive)
    res_start = select_window(events, st_val)
    assert res_start is not None
    assert res_start[2] is m

    # Test exact end (exclusive)
    res_end = select_window(events, et_val)
    assert res_end is None

    # Test 1ms before end (inclusive)
    res_inside = select_window(events, et_val - 0.001)
    assert res_inside is not None


def test_select_window_end_date_iso_fallback():
    """Verify endDateIso is used when endDate is absent."""
    m = {
        "eventStartTime": "2023-11-14T22:15:00Z",
        "endDateIso": "2023-11-14T22:20:00Z",
        "clobTokenIds": '["1", "2"]',
    }
    events = [{"markets": [m]}]
    res = select_window(events, 1700000200.0)
    assert res is not None
    assert res[2] is m


def test_select_window_rejects_only_start_date():
    """Verify row with only startDate (missing eventStartTime) is rejected."""
    m = {
        "startDate": "2023-11-14T20:00:00Z",
        "endDate": "2023-11-14T22:20:00Z",
        "clobTokenIds": '["1", "2"]',
    }
    events = [{"markets": [m]}]
    assert select_window(events, 1700000200.0) is None


def test_select_window_token_ids_formats_and_malformed():
    """Verify list vs JSON string clobTokenIds and robust tolerance of malformed tokens."""
    # List format
    m_list = {
        "id": "list_fmt",
        "eventStartTime": "2023-11-14T22:15:00Z",
        "endDate": "2023-11-14T22:20:00Z",
        "clobTokenIds": ["100", "200"],
    }
    events_list = [{"markets": [m_list]}]
    res = select_window(events_list, 1700000200.0)
    assert res is not None
    assert res[2] is m_list

    # Malformed tokens: invalid json, 1 token, 3 tokens, null, missing
    malformed_markets = [
        {"eventStartTime": "2023-11-14T22:15:00Z", "endDate": "2023-11-14T22:20:00Z", "clobTokenIds": "invalid json"},
        {"eventStartTime": "2023-11-14T22:15:00Z", "endDate": "2023-11-14T22:20:00Z", "clobTokenIds": '["1"]'},
        {"eventStartTime": "2023-11-14T22:15:00Z", "endDate": "2023-11-14T22:20:00Z", "clobTokenIds": '["1", "2", "3"]'},
        {"eventStartTime": "2023-11-14T22:15:00Z", "endDate": "2023-11-14T22:20:00Z", "clobTokenIds": None},
        {"eventStartTime": "2023-11-14T22:15:00Z", "endDate": "2023-11-14T22:20:00Z"},
    ]
    # Malformed along with a valid sibling
    events_mixed = [{"markets": malformed_markets + [m_list]}]
    res_mixed = select_window(events_mixed, 1700000200.0)
    assert res_mixed is not None
    assert res_mixed[2] is m_list


def test_fetch_live_market_adapter():
    """Verify strategy.markets.fetch_live_market returns LiveMarket or None."""
    fake_market = {
        "conditionId": "0xCOND123",
        "slug": "btc-updown-5m-1700000100",
        "clobTokenIds": '["111", "222"]',
        "eventStartTime": "2023-11-14T22:15:00Z",
        "endDate": "2023-11-14T22:20:00Z",
        "orderPriceMinTickSize": "0.01",
        "negRisk": False,
    }
    fake_resp = MagicMock()
    fake_resp.json.return_value = [{"markets": [fake_market]}]
    fake_resp.raise_for_status.return_value = None

    with patch("strategy.markets._SESSION.get", return_value=fake_resp), \
         patch("strategy.markets.get_real_utc_time", return_value=1700000200.0):
        live_m = fetch_live_market("https://gamma-api.polymarket.com", "btc-up-or-down-5m")
        assert isinstance(live_m, LiveMarket)
        assert live_m.condition_id == "0xCOND123"
        assert live_m.market_slug == "btc-updown-5m-1700000100"
        assert live_m.up_token == "111"
        assert live_m.down_token == "222"
        assert live_m.start_ts == 1700000100.0
        assert live_m.end_ts == 1700000400.0
        assert live_m.tick_size == 0.01
        assert live_m.neg_risk is False

    # When no open window
    with patch("strategy.markets._SESSION.get", return_value=fake_resp), \
         patch("strategy.markets.get_real_utc_time", return_value=1700000999.0):
        assert fetch_live_market("https://gamma-api.polymarket.com", "btc-up-or-down-5m") is None


def test_fetch_live_for_series_adapter():
    """Verify scripts.collect_ticks.fetch_live_for_series returns (dict, None) or (None, err)."""
    fake_market = {
        "conditionId": "0xCOND456",
        "slug": "eth-updown-5m-1700000100",
        "clobTokenIds": '["333", "444"]',
        "eventStartTime": "2023-11-14T22:15:00Z",
        "endDate": "2023-11-14T22:20:00Z",
    }
    fake_resp = MagicMock()
    fake_resp.json.return_value = [{"markets": [fake_market]}]
    fake_resp.raise_for_status.return_value = None

    with patch("scripts.collect_ticks.SESSION.get", return_value=fake_resp), \
         patch("time.time", return_value=1700000200.0):
        info, err = fetch_live_for_series("eth-up-or-down-5m")
        assert err is None
        assert info == {
            "conditionId": "0xCOND456",
            "slug": "eth-updown-5m-1700000100",
            "start_ts": 1700000100.0,
            "end_ts": 1700000400.0,
            "up_token": "333",
            "down_token": "444",
            "series": "eth-up-or-down-5m",
        }

    # No live window
    with patch("scripts.collect_ticks.SESSION.get", return_value=fake_resp), \
         patch("time.time", return_value=1700000999.0):
        info, err = fetch_live_for_series("eth-up-or-down-5m")
        assert info is None
        assert err == "no live"

    # Network / HTTP exception
    with patch("scripts.collect_ticks.SESSION.get", side_effect=Exception("connection timed out")):
        info, err = fetch_live_for_series("eth-up-or-down-5m")
        assert info is None
        assert "gamma err" in err
