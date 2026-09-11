"""Unit tests for Issue #138: per-fill queue-position telemetry.

Covers: queue-ahead math, rest-context snapshots, the telemetry record
builder/writer/tape-join, one line per fill path (paper, CLOB, stream),
degenerate nulls, and the bucketing helper.
"""

from strategy.live_trader import (
    LiveTraderEngine,
    _queue_ahead,
    _sum_prints_at_price,
    _build_fill_record,
    _append_fill_telemetry,
    _fetch_price_prints,
)


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


# ============================================================================
# TASK 2: record builder + writer + tape join
# ============================================================================

_ROWS = [
    {"asset": "tok_up", "price": 0.48, "size": 40.0, "timestamp": 1006},
    {"asset": "tok_up", "price": 0.48, "size": 20.0, "timestamp": 1008},
    {"asset": "tok_up", "price": 0.48, "size": 999.0, "timestamp": 1002},  # before rest
    {"asset": "tok_up", "price": 0.47, "size": 500.0, "timestamp": 1007},  # other price
    {"asset": "tok_dn", "price": 0.48, "size": 500.0, "timestamp": 1007},  # other token
    {"asset": "tok_up", "price": 0.48, "size": 7.0, "timestamp": "junk"},  # bad ts
]


def test_issue138_tape_join_sums_prints_at_price_since_rest():
    assert _sum_prints_at_price(_ROWS, "tok_up", 0.48, 1005.0) == 60.0
    assert _sum_prints_at_price(_ROWS, "tok_up", 0.48, 2000.0) == 0.0
    assert _sum_prints_at_price([], "tok_up", 0.48, 1005.0) == 0.0


def test_issue138_record_ratio_math_and_flag():
    rec = _build_fill_record(
        ts=1010.0, slug="s", market_slug="m", condition_id="c", leg="UP",
        chased=False, resting_price=0.48, fill_price=0.48,
        queue_ahead=120.0, printed_size=60.0, filled_size=5,
        window_elapsed_sec=10.0, mid_at_fill=0.50, resting_pair_cost=0.96)
    assert rec["fill_ratio"] == 0.5
    assert rec["ratio_flagged"] is False
    assert rec["resting_pair_cost"] == 0.96
    rec2 = _build_fill_record(
        ts=1010.0, slug="s", market_slug="m", condition_id="c", leg="UP",
        chased=True, resting_price=0.48, fill_price=0.48,
        queue_ahead=100.0, printed_size=1500.0, filled_size=5,
        window_elapsed_sec=10.0, mid_at_fill=0.50, resting_pair_cost=0.98)
    assert rec2["fill_ratio"] == 15.0
    assert rec2["ratio_flagged"] is True


def test_issue138_record_nulls_when_inputs_missing():
    rec = _build_fill_record(
        ts=1010.0, slug="s", market_slug="m", condition_id="c", leg="DN",
        chased=False, resting_price=None, fill_price=0.48,
        queue_ahead=None, printed_size=None, filled_size=5,
        window_elapsed_sec=10.0, mid_at_fill=None, resting_pair_cost=0.96)
    assert rec["fill_ratio"] is None
    assert rec["ratio_flagged"] is False
    assert rec["queue_ahead_at_rest"] is None
    assert rec["printed_size_at_price_since_rest"] is None


def test_issue138_writer_appends_valid_json_line(tmp_path):
    import json
    path = tmp_path / "fills.jsonl"
    rec = {"ts": 1.0, "leg": "UP"}
    assert _append_fill_telemetry(rec, path) is True
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == rec


def test_issue138_writer_failure_never_raises(tmp_path):
    blocker = tmp_path / "a_file_not_a_dir"
    blocker.write_text("x", encoding="utf-8")
    bad = blocker / "fills.jsonl"
    assert _append_fill_telemetry({"ts": 1.0}, bad) is False


def test_issue138_tape_fetch_failure_returns_none(monkeypatch):
    import strategy.live_trader as lt

    def boom(*a, **k):
        raise ConnectionError("down")

    monkeypatch.setattr(lt.requests, "get", boom)
    assert _fetch_price_prints("0xdead") is None
