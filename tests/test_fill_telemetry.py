"""Unit tests for Issue #138: per-fill queue-position telemetry.

Covers: queue-ahead math, rest-context snapshots, the telemetry record
builder/writer/tape-join, one line per fill path (paper, CLOB, stream),
degenerate nulls, and the bucketing helper.
"""

from strategy.live_trader import LiveTraderEngine, _queue_ahead


def _books_poll(start_ts: float, up_bids: dict, dn_bids: dict,
                duration: float = 300.0) -> dict:
    """Two-sided poll with full-depth bid books and a 0.50 synthetic mid."""
    def side(bids: dict, center: float) -> dict:
        return {
            "best_bid": round(center - 0.01, 4),
            "best_ask": round(center + 0.01, 4),
            "bids": dict(bids),
            "asks": {},
            "malformed": 0,
        }
    return {
        "market": {
            "conditionId": "0x138",
            "slug": "mkt-138",
            "up_token": "tok_up_138",
            "down_token": "tok_dn_138",
            "start_ts": start_ts,
            "end_ts": start_ts + duration,
        },
        "up_book": side(up_bids, 0.50),
        "down_book": side(dn_bids, 0.50),
        "tape_delta": [],
    }


def _paper_engine(**config) -> LiveTraderEngine:
    engine = LiveTraderEngine()
    if config:
        engine.update_config(**config)
    engine.is_running = True
    return engine


# ============================================================================
# TASK 1: queue-ahead math + rest context
# ============================================================================

def test_issue138_queue_ahead_sums_levels_at_or_above_price():
    bids = {0.48: 100.0, 0.47: 50.0, 0.46: 25.0}
    assert _queue_ahead(bids, 0.47) == 150.0
    assert _queue_ahead(bids, 0.48) == 100.0
    assert _queue_ahead(bids, 0.40) == 175.0


def test_issue138_queue_ahead_degenerate_is_none():
    assert _queue_ahead({}, 0.47) is None
    assert _queue_ahead(None, 0.47) is None


def test_issue138_rest_context_snapshot_on_placement():
    """First resting tick snapshots price, queue, and timestamps per leg."""
    engine = _paper_engine()
    slug = "btc-up-or-down-5m"
    poll = _books_poll(1000.0, {0.48: 120.0, 0.47: 30.0}, {0.48: 80.0})
    engine._update_market_strategy(slug, poll, now=1005.0)
    m = engine.markets[slug]
    assert m.rest_up_price == 0.48
    assert m.rest_up_queue == 120.0
    assert m.rest_up_ts == 1005.0
    assert m.rest_dn_price == 0.48
    assert m.rest_dn_queue == 80.0
    # Second tick keeps the original rest context (sim2 snapshot semantics).
    poll2 = _books_poll(1000.0, {0.48: 5.0}, {0.48: 5.0})
    engine._update_market_strategy(slug, poll2, now=1006.0)
    assert m.rest_up_queue == 120.0
    assert m.rest_up_ts == 1005.0
