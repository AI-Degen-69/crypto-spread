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
import copy
import json
import time
from unittest.mock import MagicMock

import strategy.live_trader as lt


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
    # Deterministic: join tape and append inline instead of the worker thread.
    engine.fill_telemetry_async = False
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
    def boom(*a, **k):
        raise ConnectionError("down")

    monkeypatch.setattr(lt.requests, "get", boom)
    assert _fetch_price_prints("0xdead") is None


# ============================================================================
# TASK 3: CLOB + paper fill-path hooks
# ============================================================================

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
    engine.fill_telemetry_async = False
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
    engine.fill_telemetry_async = False
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


# ============================================================================
# TASK 5: bucketing helper
# ============================================================================

def test_issue138_bucket_helper_prints_per_bucket_table(tmp_path, capsys):
    from scripts.bucket_fills import main
    fills = tmp_path / "fills.jsonl"
    trades = tmp_path / "trades.jsonl"
    rows = [
        {"fill_ratio": 0.1, "market_slug": "w1"},
        {"fill_ratio": 0.2, "market_slug": "w1"},
        {"fill_ratio": 0.6, "market_slug": "w2"},
        {"fill_ratio": 2.5, "market_slug": "w3"},
        {"fill_ratio": None, "market_slug": "w4"},
    ]
    fills.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    marvels = [
        {"action": "WINDOW_SETTLE", "market_slug": "w1", "pnl_usd": 0.10},
        {"action": "WINDOW_SETTLE", "market_slug": "w2", "pnl_usd": -0.40},
        {"action": "WINDOW_SETTLE", "market_slug": "w3", "pnl_usd": 0.05},
        {"action": "PAIR_MERGE", "market_slug": "w1", "pnl_usd": 9.99},
    ]
    trades.write_text("\n".join(json.dumps(t) for t in marvels) + "\n", encoding="utf-8")
    assert main([str(fills), "--trades", str(trades)]) == 0
    out = capsys.readouterr().out
    assert "0.00-0.25" in out and "2" in out  # two fills, mean +0.10
    assert "+0.10" in out
    assert "0.50-1.00" in out and "-0.40" in out
    assert "1.00+" in out and "+0.05" in out
    # Null-ratio and non-settle actions never leak into a bucket.
    assert "9.99" not in out


def test_issue138_bucket_helper_empty_input(tmp_path, capsys):
    from scripts.bucket_fills import main
    fills = tmp_path / "fills.jsonl"
    fills.write_text("", encoding="utf-8")
    assert main([str(fills)]) == 0
    assert "no fill" in capsys.readouterr().out.lower()


# ============================================================================
# TASK 6: degenerate nulls
# ============================================================================

def test_issue138_empty_book_records_nulls_without_blocking(monkeypatch, tmp_path):
    """A book without depth still fills; telemetry degrades to nulls."""
    path = _telemetry_env(monkeypatch, tmp_path)
    engine = _paper_engine()
    slug = "btc-up-or-down-5m"
    bare = {
        "market": {
            "conditionId": "0x138", "slug": "mkt-138",
            "up_token": "tok_up_138", "down_token": "tok_dn_138",
            "start_ts": 1000.0, "end_ts": 1300.0,
        },
        "up_book": {"best_bid": 0.49, "best_ask": 0.51},
        "down_book": {"best_bid": 0.49, "best_ask": 0.51},
    }
    engine._update_market_strategy(slug, bare, now=1000.0)
    m = engine.markets[slug]
    assert m.rest_up_queue is None
    fill_tick = copy.deepcopy(bare)
    fill_tick["up_book"]["best_ask"] = 0.47
    engine._update_market_strategy(slug, fill_tick, now=1001.0)
    assert m.filled_up is True
    lines = _fill_lines(path)
    assert len(lines) == 1
    assert lines[0]["queue_ahead_at_rest"] is None
    assert lines[0]["fill_ratio"] is None
    assert lines[0]["ratio_flagged"] is False


# ============================================================================
# REVIEW FIXES (Station 3, self-review round)
# ============================================================================

def _base_record(**over):
    kw = dict(ts=1010.0, slug="s", market_slug="m", condition_id="c", leg="UP",
              chased=False, resting_price=0.48, fill_price=0.48,
              queue_ahead=120.0, printed_size=60.0, filled_size=5,
              window_elapsed_sec=10.0, mid_at_fill=0.50, resting_pair_cost=0.96)
    kw.update(over)
    return _build_fill_record(**kw)


def test_issue138_ratio_boundary_and_zero_queue():
    assert _base_record(queue_ahead=100.0, printed_size=1000.0)["ratio_flagged"] is False
    assert _base_record(queue_ahead=100.0, printed_size=1000.0)["fill_ratio"] == 10.0
    rec = _base_record(queue_ahead=0.0, printed_size=5.0)
    assert rec["fill_ratio"] == 5.0  # max(queue, 1) guard, no ZeroDivision
    assert rec["ratio_flagged"] is False


def test_issue138_async_worker_appends_line(monkeypatch, tmp_path):
    """Default async mode joins and appends off-thread; the line lands."""
    path = _telemetry_env(monkeypatch, tmp_path)
    engine = LiveTraderEngine()
    engine.is_running = True
    assert engine.fill_telemetry_async is True  # production default
    slug = "btc-up-or-down-5m"
    engine._update_market_strategy(
        slug, _books_poll(1000.0, {0.48: 120.0}, {0.48: 80.0}), now=1000.0)
    tick2 = _books_poll(1000.0, {0.48: 120.0}, {0.48: 80.0})
    tick2["up_book"]["best_ask"] = 0.47
    engine._update_market_strategy(slug, tick2, now=1001.0)
    deadline = time.time() + 5.0
    lines: list = []
    while time.time() < deadline:
        lines = _fill_lines(path)
        if lines:
            break
        time.sleep(0.05)
    assert len(lines) == 1
    assert lines[0]["leg"] == "UP"
    assert lines[0]["fill_ratio"] == 0.5


def test_issue138_reset_pnl_clears_telemetry_state():
    """reset_pnl clears rest context, stash, and done flags like rollover."""
    engine = _paper_engine()
    slug = "btc-up-or-down-5m"
    engine._update_market_strategy(
        slug, _books_poll(1000.0, {0.48: 120.0}, {0.48: 80.0}), now=1000.0)
    m = engine.markets[slug]
    m.fill_telemetry_done_up = True
    assert m.rest_up_price == 0.48
    engine.reset_pnl()
    assert m.rest_up_price is None
    assert m.rest_up_queue is None
    assert m.rest_up_ts is None
    assert m.last_bids_up == {}
    assert m.fill_telemetry_done_up is False


def test_issue138_settlement_join_uses_last_window(tmp_path, capsys):
    """A duplicated settle must not inflate the bucket mean (last wins)."""
    from scripts.bucket_fills import main
    fills = tmp_path / "fills.jsonl"
    trades = tmp_path / "trades.jsonl"
    fills.write_text(
        json.dumps({"fill_ratio": 0.1, "market_slug": "w1"}) + "\n", encoding="utf-8")
    trades.write_text("\n".join([
        json.dumps({"action": "WINDOW_SETTLE", "market_slug": "w1", "pnl_usd": 100.0}),
        json.dumps({"action": "WINDOW_SETTLE", "market_slug": "w1", "pnl_usd": 0.10}),
    ]) + "\n", encoding="utf-8")
    assert main([str(fills), "--trades", str(trades)]) == 0
    out = capsys.readouterr().out
    assert "+0.10" in out
    assert "100.00" not in out


# ============================================================================
# FALLBACK REVIEW (agent, PR #141): blocker regression + gap tests
# ============================================================================

def test_issue138_none_pair_cost_never_burns_claim(monkeypatch, tmp_path):
    """Blocker: stream fill before any quote must still record a line."""
    path = _telemetry_env(monkeypatch, tmp_path)
    engine = LiveTraderEngine()
    engine.fill_telemetry_async = False
    slug = "btc-up-or-down-5m"
    m = engine.markets[slug]
    m.order_id_up = "oid_pre_quote"
    m.resting_up = None
    m.resting_down = None
    m.last_bids_up = {}
    engine.on_user_order_event(
        {"order_id": "oid_pre_quote", "status": "FILLED", "price": 0.48})
    assert m.filled_up is True
    lines = _fill_lines(path)
    assert len(lines) == 1
    assert lines[0]["resting_pair_cost"] is None
    assert lines[0]["fill_ratio"] is None


def test_issue138_unknown_side_claims_no_flag():
    engine = LiveTraderEngine()
    engine.fill_telemetry_async = False
    m = engine.markets["btc-up-or-down-5m"]
    engine._record_fill_telemetry(m, "SIDEWAYS", 0.5, 5, 1000.0)
    assert m.fill_telemetry_done_up is False
    assert m.fill_telemetry_done_down is False


def test_issue138_cross_path_dedup_stream_then_poll(monkeypatch, tmp_path):
    """A stream-claimed leg is not re-recorded by the later poll fill."""
    path = _telemetry_env(monkeypatch, tmp_path)
    engine = _paper_engine()
    slug = "btc-up-or-down-5m"
    engine._update_market_strategy(
        slug, _books_poll(1000.0, {0.48: 120.0}, {0.48: 80.0}), now=1000.0)
    m = engine.markets[slug]
    engine.on_user_order_event(
        {"order_id": m.order_id_up, "status": "FILLED", "price": 0.48})
    assert m.filled_up is True
    assert len(_fill_lines(path)) == 1
    tick2 = _books_poll(1000.0, {0.48: 120.0}, {0.48: 80.0})
    tick2["up_book"]["best_ask"] = 0.47
    engine._update_market_strategy(slug, tick2, now=1001.0)
    assert len(_fill_lines(path)) == 1


def test_issue138_rollover_clears_telemetry_state():
    engine = _paper_engine()
    slug = "btc-up-or-down-5m"
    engine._update_market_strategy(
        slug, _books_poll(1000.0, {0.48: 120.0}, {0.48: 80.0}), now=1000.0)
    m = engine.markets[slug]
    m.fill_telemetry_done_up = True
    engine._handle_window_rollover(m, 1300.0, "cid_next_138")
    assert m.rest_up_price is None
    assert m.rest_up_queue is None
    assert m.last_bids_up == {}
    assert m.fill_telemetry_done_up is False
    assert m.fill_telemetry_done_down is False


def test_issue138_print_ts_units():
    from strategy.live_trader import _parse_print_ts
    assert _parse_print_ts(1006) == 1006.0
    assert _parse_print_ts(1757000000000) == 1757000000.0  # millis
    assert _parse_print_ts(1757000000000000) == 1757000000.0  # micros
    assert _parse_print_ts("2026-09-11T12:00:00Z") is not None
    for bad in ("junk", "", None, True, -5):
        assert _parse_print_ts(bad) is None


def test_issue138_join_skips_negative_sizes():
    rows = [
        {"asset": "t", "price": 0.48, "size": 30.0, "timestamp": 1006},
        {"asset": "t", "price": 0.48, "size": -999.0, "timestamp": 1007},
    ]
    assert _sum_prints_at_price(rows, "t", 0.48, 1000.0) == 30.0


def test_issue138_bucket_boundaries_and_exclusions():
    from scripts.bucket_fills import bucketize
    fills = [{"fill_ratio": r, "market_slug": "w"} for r in
             (0.0, 0.25, 0.5, 1.0, 5.0, -1.0, None, float("nan"), float("inf"))]
    table = {row["bucket"]: row["count"] for row in bucketize(fills, {})}
    assert table == {"0.00-0.25": 1, "0.25-0.50": 1, "0.50-1.00": 1, "1.00+": 2}


def test_issue138_filled_size_fallback_to_shares(monkeypatch, tmp_path):
    path = _telemetry_env(monkeypatch, tmp_path)
    engine = LiveTraderEngine()
    engine.fill_telemetry_async = False
    slug = "btc-up-or-down-5m"
    m = engine.markets[slug]
    m.order_id_up = "oid_sz"
    m.resting_up = 0.48
    m.last_bids_up = {0.48: 10.0}
    for bad_size in (0, -3, None, True):
        m.fill_telemetry_done_up = False
        engine.on_user_order_event(
            {"order_id": "oid_sz", "status": "FILLED", "price": 0.48, "size": bad_size})
    lines = _fill_lines(path)
    assert len(lines) == 4
    assert all(r["filled_size"] == engine.shares for r in lines)


def test_async_telemetry_binds_its_path_at_dispatch(monkeypatch, tmp_path):
    """A path swapped under an in-flight worker must not redirect its line.

    The worker runs on its own daemon thread. It used to resolve the module
    global whenever it happened to be scheduled, so a late write could land in
    whatever file the global pointed at by then — across tests, that meant one
    test's fill appearing in another test's sidecar.
    """
    intended = _telemetry_env(monkeypatch, tmp_path)
    elsewhere = tmp_path / "somewhere-else.jsonl"
    engine = LiveTraderEngine()
    engine.is_running = True
    slug = "btc-up-or-down-5m"
    engine._update_market_strategy(
        slug, _books_poll(1000.0, {0.48: 120.0}, {0.48: 80.0}), now=1000.0)
    tick2 = _books_poll(1000.0, {0.48: 120.0}, {0.48: 80.0})
    tick2["up_book"]["best_ask"] = 0.47
    engine._update_market_strategy(slug, tick2, now=1001.0)

    # Repoint the global the instant the worker is in flight.
    monkeypatch.setattr(lt, "FILL_TELEMETRY_FILE", elsewhere)

    deadline = time.time() + 5.0
    lines: list = []
    while time.time() < deadline:
        lines = _fill_lines(intended)
        if lines:
            break
        time.sleep(0.05)

    assert len(lines) == 1, "the fill did not land in the path bound at dispatch"
    assert _fill_lines(elsewhere) == [], "the line followed the global instead"


# ============================================================================
# Issue #173: the fill sidecar joins the socket tape, not only the REST tape
# ============================================================================

def _ws_authority(engine, slug, leg="UP", *, connected=True, warm=True,
                  printing=True):
    """Force the three conditions `_ws_tape_authoritative()` checks."""
    m = engine.markets[slug]
    engine.stream_bridge.clob.is_connected = connected
    token = m.up_token if leg == "UP" else m.down_token
    now = time.time()
    engine._ws_token_ready_ts[token] = (
        now - lt.WS_TAPE_WARMUP_SEC - 1.0 if warm else now)
    stamp = now if printing else now - lt.WS_TAPE_AUTHORITY_HORIZON_SEC - 1.0
    if leg == "UP":
        m.ws_last_print_ts_up = stamp
    else:
        m.ws_last_print_ts_down = stamp
    return m


def test_issue173_ws_print_sum_matches_rest_join_semantics():
    """The socket numerator must use the same tolerance and cutoff as REST."""
    ledger = [
        (0.48, 30.0, 1006.0),
        (0.4805, 10.0, 1007.0),   # inside FILL_PRICE_TICK_TOL
        (0.49, 99.0, 1007.0),     # outside the tick tolerance
        (0.48, 40.0, 999.0),      # printed before the order rested
        (0.48, -5.0, 1008.0),     # malformed size
        ("x", 1.0, 1008.0),       # malformed price
        (0.48, 20.0),             # malformed shape
    ]
    assert lt._sum_ws_prints_at_price(ledger, 0.48, 1000.0) == 40.0
    # Same answer as the REST join on the equivalent rows.
    rows = [{"asset": "t", "price": 0.48, "size": 30.0, "timestamp": 1006},
            {"asset": "t", "price": 0.4805, "size": 10.0, "timestamp": 1007},
            {"asset": "t", "price": 0.49, "size": 99.0, "timestamp": 1007}]
    assert lt._sum_prints_at_price(rows, "t", 0.48, 1000.0) == 40.0


def test_issue173_ws_print_sum_tolerates_garbage_input():
    """A ledger that is not a list of triples yields 0.0, never an exception."""
    assert lt._sum_ws_prints_at_price(None, 0.48, 0.0) == 0.0
    assert lt._sum_ws_prints_at_price("nonsense", 0.48, 0.0) == 0.0
    assert lt._sum_ws_prints_at_price([], 0.48, 0.0) == 0.0


def test_issue173_socket_tape_authority_needs_all_three_conditions():
    """Connected, warmed up, and printing recently — any one missing is False."""
    engine = _paper_engine()
    slug = "btc-up-or-down-5m"
    engine._update_market_strategy(
        slug, _books_poll(1000.0, {0.48: 120.0}, {0.48: 80.0}), now=1000.0)

    m = _ws_authority(engine, slug)
    assert engine._ws_tape_authoritative(m, "UP") is True

    _ws_authority(engine, slug, connected=False)
    assert engine._ws_tape_authoritative(m, "UP") is False
    _ws_authority(engine, slug, warm=False)
    assert engine._ws_tape_authoritative(m, "UP") is False
    _ws_authority(engine, slug, printing=False)
    assert engine._ws_tape_authoritative(m, "UP") is False

    # The DOWN leg has its own print clock and is not dragged along by UP.
    _ws_authority(engine, slug, leg="UP")
    engine._ws_token_ready_ts[m.down_token] = time.time() - 100.0
    assert engine._ws_tape_authoritative(m, "DOWN") is False


def test_issue173_resubscribing_a_live_token_does_not_restart_its_warmup():
    """Rollover re-sends the same token list; that must not drop authority."""
    engine = _paper_engine()
    engine._mark_ws_tokens_subscribed(["tok_a", "tok_b"])
    first = dict(engine._ws_token_ready_ts)
    time.sleep(0.01)
    engine._mark_ws_tokens_subscribed(["tok_a", "tok_b"])
    assert engine._ws_token_ready_ts == first
    # A token that leaves the active set is forgotten, so its next
    # subscription starts a fresh warm-up rather than inheriting a stale one.
    engine._mark_ws_tokens_subscribed(["tok_a"])
    assert "tok_b" not in engine._ws_token_ready_ts


def test_issue173_ws_trade_lands_in_the_ledger_and_survives_the_tick():
    """Socket prints must outlive the pending queue the tick loop drains."""
    engine = _paper_engine()
    slug = "btc-up-or-down-5m"
    engine._update_market_strategy(
        slug, _books_poll(1000.0, {0.48: 120.0}, {0.48: 80.0}), now=1000.0)
    m = engine.markets[slug]

    engine.on_ws_trade({"asset": m.up_token, "price": "0.48", "size": "7",
                        "timestamp": 1006})
    engine.on_ws_trade({"asset": m.down_token, "price": "0.50", "size": "3",
                        "timestamp": 1007})
    assert m.ws_tape_up == [(0.48, 7.0, 1006.0)]
    assert m.ws_tape_down == [(0.50, 3.0, 1007.0)]

    engine._update_market_strategy(
        slug, _books_poll(1000.0, {0.48: 120.0}, {0.48: 80.0}), now=1001.0)
    assert m.pending_ws_trades_up == [], "the pending queue was not drained"
    assert m.ws_tape_up == [(0.48, 7.0, 1006.0)], "the tick discarded the ledger"


def test_issue173_malformed_ws_print_never_reaches_the_ledger():
    """The socket callback thread must not raise on a junk frame."""
    engine = _paper_engine()
    slug = "btc-up-or-down-5m"
    engine._update_market_strategy(
        slug, _books_poll(1000.0, {0.48: 120.0}, {0.48: 80.0}), now=1000.0)
    m = engine.markets[slug]
    for bad in ({"asset": m.up_token, "price": None, "size": "5"},
                {"asset": m.up_token, "price": "abc", "size": "5"},
                {"asset": m.up_token, "price": "0.48", "size": "0"},
                {"asset": m.up_token, "price": "0.48", "size": "-2"}):
        engine.on_ws_trade(bad)
    assert m.ws_tape_up == []
    # A print with no usable venue stamp still counts: arrival time stands in.
    before = time.time()
    engine.on_ws_trade({"asset": m.up_token, "price": "0.48", "size": "5"})
    assert len(m.ws_tape_up) == 1
    assert m.ws_tape_up[0][:2] == (0.48, 5.0)
    assert m.ws_tape_up[0][2] >= before


def test_issue173_ledger_is_bounded_and_drops_the_oldest_first():
    """A hot market must not grow the ledger without limit inside one window."""
    engine = _paper_engine()
    slug = "btc-up-or-down-5m"
    engine._update_market_strategy(
        slug, _books_poll(1000.0, {0.48: 120.0}, {0.48: 80.0}), now=1000.0)
    m = engine.markets[slug]
    for i in range(lt.WS_TAPE_LEDGER_MAX + 25):
        engine.on_ws_trade({"asset": m.up_token, "price": "0.48",
                            "size": "1", "timestamp": 1000 + i})
    assert len(m.ws_tape_up) == lt.WS_TAPE_LEDGER_MAX
    assert m.ws_tape_up[0][2] == 1025.0, "newest prints were dropped, not oldest"


def test_issue173_fill_joins_the_socket_tape_and_skips_rest_entirely(
        monkeypatch, tmp_path):
    """An authoritative socket answers `printed_size` with no REST call at all.

    The canned REST tape and the seeded ledger describe the *same* two prints.
    An implementation that merged both sources would report 120.0; picking one
    reports 60.0, which is what makes a print seen twice impossible to
    double-count.
    """
    path = _telemetry_env(monkeypatch, tmp_path)
    rest_calls = []

    def _tracked_fetch(condition_id, limit=500):
        rest_calls.append(condition_id)
        return [dict(r) for r in _CANNED_TAPE]

    monkeypatch.setattr(lt, "_fetch_price_prints", _tracked_fetch)
    engine = _paper_engine()
    slug = "btc-up-or-down-5m"
    engine._update_market_strategy(
        slug, _books_poll(1000.0, {0.48: 120.0}, {0.48: 80.0}), now=1000.0)
    m = _ws_authority(engine, slug)
    m.ws_tape_up = [(0.48, 30.0, 1006.0), (0.48, 30.0, 1008.0)]

    tick2 = _books_poll(1000.0, {0.48: 120.0}, {0.48: 80.0})
    tick2["up_book"]["best_ask"] = 0.47
    engine._update_market_strategy(slug, tick2, now=1001.0)

    lines = _fill_lines(path)
    assert len(lines) == 1
    rec = lines[0]
    assert rec["tape_source"] == "ws"
    assert rec["printed_size_at_price_since_rest"] == 60.0
    assert rec["fill_ratio"] == 0.5
    assert rest_calls == [], "the REST tape was fetched despite socket authority"


def test_issue173_rest_join_is_unchanged_when_the_socket_is_not_authoritative(
        monkeypatch, tmp_path):
    """A socket outage costs accuracy, never a line: REST behaves as before."""
    path = _telemetry_env(monkeypatch, tmp_path)
    engine = _paper_engine()
    slug = "btc-up-or-down-5m"
    engine._update_market_strategy(
        slug, _books_poll(1000.0, {0.48: 120.0}, {0.48: 80.0}), now=1000.0)
    m = _ws_authority(engine, slug, connected=False)
    # Prints captured before the socket dropped must be ignored: a
    # half-populated ledger would undercount worse than REST does.
    m.ws_tape_up = [(0.48, 500.0, 1006.0)]

    tick2 = _books_poll(1000.0, {0.48: 120.0}, {0.48: 80.0})
    tick2["up_book"]["best_ask"] = 0.47
    engine._update_market_strategy(slug, tick2, now=1001.0)

    rec = _fill_lines(path)[0]
    assert rec["tape_source"] == "rest"
    assert rec["printed_size_at_price_since_rest"] == 60.0


def _seeded_ledger_engine():
    """A market whose socket ledger holds one print on each leg."""
    engine = _paper_engine()
    slug = "btc-up-or-down-5m"
    engine._update_market_strategy(
        slug, _books_poll(1000.0, {0.48: 120.0}, {0.48: 80.0}), now=1000.0)
    m = engine.markets[slug]
    engine.on_ws_trade({"asset": m.up_token, "price": "0.48", "size": "9",
                        "timestamp": 1006})
    engine.on_ws_trade({"asset": m.down_token, "price": "0.50", "size": "4",
                        "timestamp": 1006})
    assert m.ws_tape_up and m.ws_tape_down
    assert m.ws_last_print_ts_up is not None
    return engine, m


def _assert_ledger_cleared(m):
    assert m.ws_tape_up == []
    assert m.ws_tape_down == []
    assert m.ws_last_print_ts_up is None
    assert m.ws_last_print_ts_down is None


def test_issue173_rollover_clears_the_socket_ledger():
    """`printed_size` is scoped to one resting order, so the ledger is too."""
    engine, m = _seeded_ledger_engine()
    engine._handle_window_rollover(m, 1300.0, "cid_next_173")
    _assert_ledger_cleared(m)


def test_issue173_reset_pnl_clears_the_socket_ledger():
    """RESET P&L drops the rest context; the ledger that pairs with it goes too."""
    engine, m = _seeded_ledger_engine()
    engine.reset_pnl()
    _assert_ledger_cleared(m)


def test_issue173_record_defaults_to_rest_when_no_source_is_named():
    """The builder never invents a source it was not told about."""
    rec = lt._build_fill_record(
        ts=1.0, slug="s", market_slug="m", condition_id="c", leg="UP",
        chased=False, resting_price=0.48, fill_price=0.48, queue_ahead=10.0,
        printed_size=None, filled_size=5.0, window_elapsed_sec=1.0,
        mid_at_fill=0.5, resting_pair_cost=0.96)
    assert rec["tape_source"] == "rest"
    assert rec["printed_size_at_price_since_rest"] is None
    assert rec["fill_ratio"] is None
    assert lt._build_fill_record(
        ts=1.0, slug="s", market_slug="m", condition_id="c", leg="UP",
        chased=False, resting_price=None, fill_price=0.48, queue_ahead=None,
        printed_size=None, filled_size=5.0, window_elapsed_sec=1.0,
        mid_at_fill=None, resting_pair_cost=None,
        tape_source="none")["tape_source"] == "none"


def _write_fills(tmp_path, rows):
    """Write fill-telemetry lines and return the path."""
    p = tmp_path / "fills.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return p


def _bucket_counts(out):
    """Parse the helper's text table into {bucket: fills} (spacing-agnostic).

    Every bucket must appear: a table that lost a row is a regression, not a
    zero, so this raises rather than quietly returning a short dict.
    """
    from scripts.bucket_fills import BUCKETS
    names = [name for name, _, _ in BUCKETS]
    counts = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] in names:
            counts[parts[0]] = int(parts[1])
    missing = [n for n in names if n not in counts]
    assert not missing, f"table is missing bucket rows: {missing}\n{out}"
    return counts


_MIXED_FILLS = [
    {"fill_ratio": 0.1, "market_slug": "w1", "tape_source": "rest"},
    {"fill_ratio": 0.6, "market_slug": "w2", "tape_source": "ws"},
    {"fill_ratio": 2.5, "market_slug": "w3", "tape_source": "ws"},
]


def test_issue173_bucket_helper_filters_by_tape_source(tmp_path, capsys):
    """The #138 table must be restrictable to one tape, or it averages two."""
    from scripts.bucket_fills import main
    fills = _write_fills(tmp_path, _MIXED_FILLS)

    assert main([str(fills), "--tape-source", "ws"]) == 0
    out = capsys.readouterr().out
    assert "tape_source=ws (2 of 3 lines)" in out
    ws = _bucket_counts(out)
    assert ws["0.50-1.00"] == 1 and ws["1.00+"] == 1
    assert ws["0.00-0.25"] == 0, "the REST line leaked into the ws table"

    assert main([str(fills), "--tape-source", "rest"]) == 0
    out = capsys.readouterr().out
    assert "tape_source=rest (1 of 3 lines)" in out
    rest = _bucket_counts(out)
    assert rest["0.00-0.25"] == 1
    assert rest["0.50-1.00"] == 0 and rest["1.00+"] == 0


def test_issue173_bucket_helper_warns_on_a_mixed_file(tmp_path, capsys):
    """Pooling two tapes describes neither; that must not happen silently."""
    from scripts.bucket_fills import main
    assert main([str(_write_fills(tmp_path, _MIXED_FILLS))]) == 0
    out = capsys.readouterr().out
    assert "mixed tape sources" in out
    assert "ws=2" in out and "rest=1" in out


def test_issue173_bucket_helper_quiet_when_one_tape(tmp_path, capsys):
    """A single-tape file is comparable, so no warning is warranted."""
    from scripts.bucket_fills import main
    rows = [dict(r, tape_source="ws") for r in _MIXED_FILLS]
    assert main([str(_write_fills(tmp_path, rows))]) == 0
    assert "mixed tape sources" not in capsys.readouterr().out


def test_issue173_legacy_lines_without_the_field_count_as_rest(tmp_path, capsys):
    """Pre-#173 lines were REST-measured; calling them anything else lies."""
    from scripts.bucket_fills import main, tape_source_of, source_counts
    assert tape_source_of({"fill_ratio": 0.1}) == "rest"
    assert tape_source_of({"fill_ratio": 0.1, "tape_source": "bogus"}) == "rest"
    assert tape_source_of({"fill_ratio": 0.1, "tape_source": "ws"}) == "ws"
    assert source_counts([{}, {"tape_source": "ws"}]) == {"ws": 1, "rest": 1, "none": 0}

    rows = [{"fill_ratio": 0.1, "market_slug": "w1"},          # legacy line
            {"fill_ratio": 0.6, "market_slug": "w2", "tape_source": "ws"}]
    assert main([str(_write_fills(tmp_path, rows)), "--tape-source", "rest"]) == 0
    out = capsys.readouterr().out
    assert "tape_source=rest (1 of 2 lines)" in out
    assert _bucket_counts(out)["0.00-0.25"] == 1


def test_issue173_bucketize_without_a_filter_is_unchanged(tmp_path):
    """Existing callers passing no source still get every line."""
    from scripts.bucket_fills import bucketize
    table = {r["bucket"]: r["count"] for r in bucketize(_MIXED_FILLS, {})}
    assert table["0.00-0.25"] == 1
    assert table["0.50-1.00"] == 1
    assert table["1.00+"] == 1


def test_issue173_non_finite_print_never_poisons_the_numerator():
    """NaN passes every comparison, so it must be rejected by type, not by test.

    A single NaN entry would make `printed_size` NaN for the whole window, and
    `fill_ratio` with it — which is a silently wrong input to the #138
    tape-vs-queue verdict, not a visible failure.
    """
    poisoned = [
        (0.48, 30.0, 1006.0),
        (0.48, float("nan"), 1007.0),
        (float("nan"), 5.0, 1007.0),
        (0.48, float("inf"), 1008.0),
        (float("inf"), 5.0, 1008.0),
        (0.48, 10.0, float("nan")),
    ]
    assert lt._sum_ws_prints_at_price(poisoned, 0.48, 1000.0) == 30.0
    # The REST join must reject the same shapes, or the two tapes stop being
    # comparable exactly where the socket path was built to match them.
    rows = [{"asset": "t", "price": 0.48, "size": 30.0, "timestamp": 1006},
            {"asset": "t", "price": 0.48, "size": float("nan"), "timestamp": 1007},
            {"asset": "t", "price": float("nan"), "size": 5.0, "timestamp": 1007},
            {"asset": "t", "price": 0.48, "size": float("inf"), "timestamp": 1008}]
    assert lt._sum_prints_at_price(rows, "t", 0.48, 1000.0) == 30.0


def test_issue173_non_finite_ws_print_never_reaches_the_ledger():
    """The socket callback drops a non-finite print instead of storing it."""
    engine = _paper_engine()
    slug = "btc-up-or-down-5m"
    engine._update_market_strategy(
        slug, _books_poll(1000.0, {0.48: 120.0}, {0.48: 80.0}), now=1000.0)
    m = engine.markets[slug]
    for bad in ({"asset": m.up_token, "price": "0.48", "size": "nan"},
                {"asset": m.up_token, "price": "nan", "size": "5"},
                {"asset": m.up_token, "price": "0.48", "size": "inf"},
                {"asset": m.up_token, "price": "inf", "size": "5"},
                {"asset": m.up_token, "price": "0.48", "size": "-inf"}):
        engine.on_ws_trade(bad)
    assert m.ws_tape_up == []
