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


# ============================================================================
# TASK 3: CLOB + paper fill-path hooks
# ============================================================================

import json
from unittest.mock import MagicMock

import strategy.live_trader as lt

_CANNED_TAPE = [
    {"asset": "tok_up_138", "price": 0.48, "size": 30.0, "timestamp": 1006},
    {"asset": "tok_up_138", "price": 0.48, "size": 30.0, "timestamp": 1008},
    {"asset": "tok_dn_138", "price": 0.50, "size": 11.0, "timestamp": 1009},
]


def _telemetry_env(monkeypatch, tmp_path):
    """Redirect sidecar writes and tape fetch to canned fixtures."""
    path = tmp_path / "fills.jsonl"
    monkeypatch.setattr(lt, "FILL_TELEMETRY_FILE", path)
    monkeypatch.setattr(
        lt, "_fetch_price_prints",
        lambda condition_id, limit=500: [dict(r) for r in _CANNED_TAPE])
    return path


def _fill_lines(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_issue138_paper_fill_appends_exactly_one_line(monkeypatch, tmp_path):
    path = _telemetry_env(monkeypatch, tmp_path)
    engine = _paper_engine()
    slug = "btc-up-or-down-5m"
    engine._update_market_strategy(
        slug, _books_poll(1000.0, {0.48: 120.0}, {0.48: 80.0}), now=1000.0)
    assert _fill_lines(path) == []
    # UP ask collapses onto the 0.48 resting bid.
    tick2 = _books_poll(1000.0, {0.48: 120.0}, {0.48: 80.0})
    tick2["up_book"]["best_ask"] = 0.47
    engine._update_market_strategy(slug, tick2, now=1001.0)
    lines = _fill_lines(path)
    assert len(lines) == 1
    rec = lines[0]
    assert rec["leg"] == "UP"
    assert rec["chased"] is False
    assert rec["resting_price"] == 0.48
    assert rec["fill_price"] == 0.48
    assert rec["queue_ahead_at_rest"] == 120.0
    assert rec["printed_size_at_price_since_rest"] == 60.0
    assert rec["fill_ratio"] == 0.5
    assert rec["market_slug"] == "mkt-138"
    # A later tick with no new fill records nothing more.
    engine._update_market_strategy(slug, tick2, now=1002.0)
    assert len(_fill_lines(path)) == 1


def test_issue138_clob_fill_appends_line_with_venue_price(monkeypatch, tmp_path):
    path = _telemetry_env(monkeypatch, tmp_path)
    engine = LiveTraderEngine()
    engine.mode = "live"
    engine.is_running = True
    engine.place_live_quote = MagicMock(
        side_effect=lambda tok, px, sz, side: {"order_id": f"ord_{tok}", "status": "RESTING"})
    engine.cancel_live_order = MagicMock(return_value=True)
    client = MagicMock()

    def _one_sided_fill(order_id, *a, **k):
        if "up_138" in str(order_id):
            return {"status": "MATCHED", "size_matched": 5.0, "price": 0.47,
                    "associate_trades": [{"price": 0.47, "size": 5.0}]}
        return {"status": "OPEN", "size_matched": 0.0}

    client.get_order = MagicMock(side_effect=_one_sided_fill)
    engine.get_clob_client = MagicMock(return_value=client)
    slug = "btc-up-or-down-5m"
    engine._update_market_strategy(
        slug, _books_poll(1000.0, {0.48: 120.0}, {0.48: 80.0}), now=1000.0)
    engine._update_market_strategy(
        slug, _books_poll(1000.0, {0.48: 120.0}, {0.48: 80.0}), now=1001.0)
    lines = _fill_lines(path)
    assert len(lines) == 1
    assert lines[0]["leg"] == "UP"
    assert lines[0]["fill_price"] == 0.47
    assert lines[0]["queue_ahead_at_rest"] == 120.0


def test_issue138_chased_fill_flagged_chased(monkeypatch, tmp_path):
    path = _telemetry_env(monkeypatch, tmp_path)
    engine = _paper_engine()
    slug = "btc-up-or-down-5m"
    engine._update_market_strategy(
        slug, _books_poll(1000.0, {0.48: 120.0}, {0.48: 80.0}), now=1000.0)
    # Tick 2: UP fills; DOWN ask 0.55 chases the DOWN quote to 0.50 w/o filling.
    tick2 = _books_poll(1000.0, {0.48: 120.0}, {0.48: 80.0, 0.50: 200.0})
    tick2["up_book"]["best_ask"] = 0.46
    tick2["down_book"]["best_bid"] = 0.48
    tick2["down_book"]["best_ask"] = 0.55
    engine._update_market_strategy(slug, tick2, now=1001.0)
    m = engine.markets[slug]
    assert m.filled_up is True
    assert m.filled_down is False
    assert m.chased_leg == "DOWN"
    # Tick 3: DOWN ask meets the chased 0.50 quote.
    tick3 = _books_poll(1000.0, {0.48: 120.0}, {0.48: 80.0, 0.50: 200.0})
    tick3["up_book"]["best_ask"] = 0.46
    tick3["down_book"]["best_bid"] = 0.48
    tick3["down_book"]["best_ask"] = 0.50
    engine._update_market_strategy(slug, tick3, now=1002.0)
    assert m.filled_down is True
    by_leg = {r["leg"]: r for r in _fill_lines(path)}
    assert set(by_leg) == {"UP", "DOWN"}
    assert by_leg["UP"]["chased"] is False
    assert by_leg["DOWN"]["chased"] is True


# ============================================================================
# TASK 4: stream fill-path hook
# ============================================================================

def test_issue138_stream_fill_uses_stashed_book(monkeypatch, tmp_path):
    """Stream fills (no book in the event) join the last stashed books."""
    path = _telemetry_env(monkeypatch, tmp_path)
    engine = LiveTraderEngine()
    slug = "btc-up-or-down-5m"
    m = engine.markets[slug]
    m.order_id_up = "oid_stream_up"
    m.resting_up = 0.48
    m.resting_down = 0.48
    m.last_bids_up = {0.48: 60.0, 0.47: 10.0}
    engine.on_user_order_event(
        {"order_id": "oid_stream_up", "status": "FILLED", "price": 0.48})
    assert m.filled_up is True
    lines = _fill_lines(path)
    assert len(lines) == 1
    rec = lines[0]
    assert rec["leg"] == "UP"
    assert rec["fill_price"] == 0.48
    assert rec["queue_ahead_at_rest"] == 60.0
    assert rec["printed_size_at_price_since_rest"] is None
    # Duplicate stream event for the same fill records nothing more.
    engine.on_user_order_event(
        {"order_id": "oid_stream_up", "status": "FILLED", "price": 0.48})
    assert len(_fill_lines(path)) == 1
