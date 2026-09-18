"""Tests for live tick interval instrumentation and timing statistics (Issue #221)."""
from __future__ import annotations

import time
from typing import Any, Dict
import pytest

from strategy.live_trader import LiveTraderEngine, MarketLiveState


def _make_dummy_poll_data(
    condition_id: str = "0xabc",
    slug: str = "btc-up-or-down-5m",
    start_ts: float = 1000.0,
    end_ts: float = 1300.0,
) -> Dict[str, Any]:
    return {
        "market": {
            "conditionId": condition_id,
            "slug": slug,
            "start_ts": start_ts,
            "end_ts": end_ts,
            "up_token": "token_up",
            "down_token": "token_dn",
            "series": slug,
        },
        "next_market": None,
        "up_book": {
            "bids": [{"price": 0.48, "size": 100.0}],
            "asks": [{"price": 0.52, "size": 100.0}],
        },
        "down_book": {
            "bids": [{"price": 0.48, "size": 100.0}],
            "asks": [{"price": 0.52, "size": 100.0}],
        },
    }


def test_empty_stats_returns_safe_nulls():
    engine = LiveTraderEngine(load_persisted=False)
    stats = engine.get_tick_timing_stats("btc-up-or-down-5m")
    assert stats["count"] == 0
    assert stats["min_ms"] is None
    assert stats["p50_ms"] is None
    assert stats["p95_ms"] is None
    assert stats["p99_ms"] is None
    assert stats["max_ms"] is None
    assert stats["mean_ms"] is None


def test_tick_intervals_recorded(monkeypatch):
    engine = LiveTraderEngine(load_persisted=False)
    slug = "btc-up-or-down-5m"
    poll = _make_dummy_poll_data(slug=slug)

    perf_times = [100.0, 101.05, 102.10, 103.12]
    perf_idx = 0

    def mock_perf():
        nonlocal perf_idx
        t = perf_times[perf_idx]
        perf_idx = min(perf_idx + 1, len(perf_times) - 1)
        return t

    monkeypatch.setattr(time, "perf_counter", mock_perf)

    # Tick 1: sets initial _last_strategy_tick_perf
    engine._update_market_strategy(slug, poll, now=1005.0)
    mstate = engine.markets[slug]
    assert len(mstate._tick_intervals) == 0

    # Tick 2: delta = 101.05 - 100.0 = 1.05s
    engine._update_market_strategy(slug, poll, now=1006.0)
    assert len(mstate._tick_intervals) == 1
    assert abs(mstate._tick_intervals[0] - 1.05) < 1e-6

    # Tick 3: delta = 102.10 - 101.05 = 1.05s
    engine._update_market_strategy(slug, poll, now=1007.0)
    assert len(mstate._tick_intervals) == 2

    # Tick 4: delta = 103.12 - 102.10 = 1.02s
    engine._update_market_strategy(slug, poll, now=1008.0)
    assert len(mstate._tick_intervals) == 3

    stats = engine.get_tick_timing_stats(slug)
    assert stats["count"] == 3
    assert stats["min_ms"] == 1020.0  # 1.02s * 1000
    assert stats["max_ms"] == 1050.0  # 1.05s * 1000
    assert 1020.0 <= stats["p50_ms"] <= 1050.0


def test_tick_timing_stats_aggregates_across_markets(monkeypatch):
    engine = LiveTraderEngine(load_persisted=False)
    slug1 = "btc-up-or-down-5m"
    slug2 = "eth-up-or-down-5m"

    poll1 = _make_dummy_poll_data(slug=slug1)
    poll2 = _make_dummy_poll_data(slug=slug2)

    perf = 1000.0

    def mock_perf():
        nonlocal perf
        perf += 0.5
        return perf

    monkeypatch.setattr(time, "perf_counter", mock_perf)

    # 3 ticks for slug1 -> 2 intervals
    engine._update_market_strategy(slug1, poll1, now=1005.0)
    engine._update_market_strategy(slug1, poll1, now=1006.0)
    engine._update_market_strategy(slug1, poll1, now=1007.0)

    # 4 ticks for slug2 -> 3 intervals
    engine._update_market_strategy(slug2, poll2, now=1005.0)
    engine._update_market_strategy(slug2, poll2, now=1006.0)
    engine._update_market_strategy(slug2, poll2, now=1007.0)
    engine._update_market_strategy(slug2, poll2, now=1008.0)

    stats = engine.get_tick_timing_stats()
    assert stats["count"] == 5  # 2 + 3
    assert stats["per_market"][slug1]["count"] == 2
    assert stats["per_market"][slug2]["count"] == 3
    assert stats["p50_ms"] is not None


def test_reset_clears_timing_stats(monkeypatch):
    engine = LiveTraderEngine(load_persisted=False)
    slug = "btc-up-or-down-5m"
    poll = _make_dummy_poll_data(slug=slug)

    engine._update_market_strategy(slug, poll, now=1005.0)
    engine._update_market_strategy(slug, poll, now=1006.0)
    assert engine.get_tick_timing_stats(slug)["count"] == 1

    engine.reset_tick_timing_stats()
    assert engine.get_tick_timing_stats(slug)["count"] == 0
    assert engine.markets[slug]._last_strategy_tick_perf is None

    # Test reset_pnl also resets
    engine._update_market_strategy(slug, poll, now=1007.0)
    engine._update_market_strategy(slug, poll, now=1008.0)
    assert engine.get_tick_timing_stats(slug)["count"] == 1

    engine.reset_pnl()
    assert engine.get_tick_timing_stats(slug)["count"] == 0
