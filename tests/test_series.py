"""Tests for series universe filtering and token resolution."""
import pytest
from strategy.series import (
    SERIES,
    filter_series,
    token_for_slug,
    supported_tokens,
    supported_durations,
)


def test_token_for_slug():
    assert token_for_slug("btc-up-or-down-5m") == "BTC"
    assert token_for_slug("eth-up-or-down-15m") == "ETH"
    assert token_for_slug("sol-up-or-down-5m") == "SOL"
    assert token_for_slug("bnb-up-or-down-15m") == "BNB"
    assert token_for_slug("xrp-up-or-down-5m") == "XRP"


def test_supported_tokens_and_durations():
    assert supported_tokens() == ("BTC", "ETH", "BNB", "SOL", "XRP")
    assert supported_durations() == (300, 900)


def test_filter_series_all():
    assert filter_series() == SERIES
    assert filter_series(tokens=None, durations=None) == SERIES
    assert filter_series(tokens=[], durations=[]) == SERIES


def test_filter_series_by_token():
    res = filter_series(tokens=["btc"])
    assert len(res) == 2
    assert [s[0] for s in res] == ["btc-up-or-down-5m", "btc-up-or-down-15m"]

    res2 = filter_series(tokens=["ETH", "sol"])
    assert len(res2) == 4
    assert [s[0] for s in res2] == [
        "eth-up-or-down-5m",
        "sol-up-or-down-5m",
        "eth-up-or-down-15m",
        "sol-up-or-down-15m",
    ]


def test_filter_series_by_duration():
    res_5m = filter_series(durations=[300])
    assert len(res_5m) == 5
    assert all(s[1] == 300 for s in res_5m)

    res_15m = filter_series(durations=[900])
    assert len(res_15m) == 5
    assert all(s[1] == 900 for s in res_15m)


def test_filter_series_by_token_and_duration():
    res = filter_series(tokens=["BNB", "XRP"], durations=[900])
    assert len(res) == 2
    assert [s[0] for s in res] == ["bnb-up-or-down-15m", "xrp-up-or-down-15m"]


def test_filter_series_invalid_token():
    with pytest.raises(ValueError, match="Unsupported token"):
        filter_series(tokens=["DOGE"])


def test_filter_series_invalid_duration():
    with pytest.raises(ValueError, match="Unsupported duration"):
        filter_series(durations=[60])
