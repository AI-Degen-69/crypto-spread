"""Tests for offline per-delta socket-book reconciliation replay (Issue #359)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.replay_socket_reconciliation import (
    ReconciliationReport,
    SocketReconciler,
    _load_fixture_object,
    extract_fixture,
    replay_file,
    replay_fixture,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "socket_divergence_smoking_gun.json"
)


def test_reconciler_equal_books_zero_divergence():
    reconciler = SocketReconciler()
    token = "tok_test_1"

    # Initial book snapshot
    reconciler.feed_line(
        json.dumps({
            "type": "ws",
            "ev": {
                "event_type": "book",
                "asset_id": token,
                "bids": [{"price": "0.50", "size": "100"}],
                "asks": [{"price": "0.52", "size": "100"}],
            },
        })
    )

    # REST snapshot matching
    reconciler.feed_line(
        json.dumps({
            "type": "rest",
            "token": token,
            "book": {
                "bids": {"0.50": "100"},
                "asks": {"0.52": "100"},
                "best_bid": 0.50,
                "best_ask": 0.52,
            },
        })
    )

    # In-frame price_change matching
    reconciler.feed_line(
        json.dumps({
            "type": "ws",
            "ev": {
                "event_type": "price_change",
                "price_changes": [
                    {
                        "asset_id": token,
                        "side": "BUY",
                        "price": "0.50",
                        "size": "120",
                        "best_bid": "0.50",
                        "best_ask": "0.52",
                    }
                ],
            },
        })
    )

    rep = reconciler.report
    assert rep.total_ws_events == 2
    assert rep.total_rest_snapshots == 1
    assert rep.in_frame_divergences.get("price_change", 0) == 0
    assert rep.rest_divergences.get("price_change", 0) == 0
    assert rep.first_divergence is None


def test_reconciler_prunes_the_ghost_bid_the_frame_contradicts():
    """The prune removes the ghost bid, so the frame no longer diverges (Issue #362).

    Same 0.55/0.50 setup as the old detector test: after pruning, the local best
    bid is the declared 0.50, so there is nothing left to record.
    """
    reconciler = SocketReconciler()
    token = "tok_test_2"

    # Initial book snapshot at 0.50
    reconciler.feed_line(
        json.dumps({
            "type": "ws",
            "ev": {
                "event_type": "book",
                "asset_id": token,
                "bids": [{"price": "0.55", "size": "100"}],
                "asks": [{"price": "0.57", "size": "100"}],
            },
        })
    )

    # Price change frame where venue declared best_bid is 0.50, but local book still has 0.55!
    reconciler.feed_line(
        json.dumps({
            "type": "ws",
            "ev": {
                "event_type": "price_change",
                "price_changes": [
                    {
                        "asset_id": token,
                        "side": "BUY",
                        "price": "0.50",
                        "size": "50",
                        "best_bid": "0.50",
                        "best_ask": "0.57",
                    }
                ],
            },
        })
    )

    rep = reconciler.report
    assert rep.in_frame_divergences.get("price_change", 0) == 0
    assert rep.first_divergence is None


def test_reconciler_detects_a_level_missing_from_local_depth():
    """Detector coverage survives the prune (Issue #362).

    The prune cannot invent a level the venue declares: the local ladder tops out
    at 0.46 while the frame declares best_bid 0.50, so exactly one in-frame
    divergence is recorded.
    """
    reconciler = SocketReconciler()
    token = "tok_test_missing"
    reconciler.feed_line(
        json.dumps({
            "type": "ws",
            "ev": {
                "event_type": "book",
                "asset_id": token,
                "bids": [{"price": "0.45", "size": "100"}],
                "asks": [{"price": "0.57", "size": "100"}],
            },
        })
    )
    reconciler.feed_line(
        json.dumps({
            "type": "ws",
            "ev": {
                "event_type": "price_change",
                "price_changes": [
                    {
                        "asset_id": token,
                        "side": "BUY",
                        "price": "0.46",
                        "size": "50",
                        "best_bid": "0.50",
                        "best_ask": "0.57",
                    }
                ],
            },
        })
    )

    rep = reconciler.report
    assert rep.in_frame_divergences["price_change"] == 1
    assert rep.first_divergence is not None
    assert rep.first_divergence.event_type == "price_change"
    assert rep.first_divergence.source == "in_frame"
    assert rep.first_divergence.ws_bb == 0.46
    assert rep.first_divergence.ref_bb == 0.50
    assert abs(rep.first_divergence.max_gap - 0.04) < 1e-6


def test_reconciler_fixture_extraction(tmp_path):
    """Extraction keeps working on a divergence the prune cannot resolve.

    Local depth tops out at 0.46 while the venue declares 0.50 — the pruned
    book disagrees, so a fixture is exported with the exact gap.
    """
    reconciler = SocketReconciler()
    token = "tok_test_3"
    reconciler.feed_line(
        json.dumps({
            "type": "ws",
            "ev": {
                "event_type": "book",
                "asset_id": token,
                "bids": [{"price": "0.45", "size": "10"}],
                "asks": [{"price": "0.62", "size": "10"}],
            },
        })
    )
    reconciler.feed_line(
        json.dumps({
            "type": "ws",
            "ev": {
                "event_type": "price_change",
                "price_changes": [
                    {
                        "asset_id": token,
                        "side": "BUY",
                        "price": "0.46",
                        "size": "10",
                        "best_bid": "0.50",
                        "best_ask": "0.62",
                    }
                ],
            },
        })
    )

    fixture_path = tmp_path / "test_fixture.json"
    ok = extract_fixture(reconciler.report, fixture_path)
    assert ok is True
    assert fixture_path.exists()
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert data["event_type"] == "price_change"
    assert data["token"] == token
    assert data["max_gap"] == 0.04


def test_replay_fixture_zero_divergence_on_the_smoking_gun():
    """The Issue #362 proof: the extracted fixture replays with 0 divergences."""
    assert FIXTURE_PATH.exists(), f"missing fixture: {FIXTURE_PATH}"
    rep = replay_fixture(FIXTURE_PATH)
    assert rep.total_ws_events == 1
    assert rep.events_by_type == {"price_change": 1}
    assert rep.in_frame_divergences.get("price_change", 0) == 0
    assert rep.rest_divergences == {}
    assert rep.first_divergence is None


def test_replay_fixture_skips_preceding_events():
    """`preceding_events` are history, not input: exactly one event is replayed."""
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert fixture.get("preceding_events"), "fixture has no history to skip"
    rep = replay_fixture(FIXTURE_PATH)
    assert rep.total_ws_events == 1


def test_fixture_detection_leaves_jsonl_sessions_alone(tmp_path):
    """Detection is cheap and exact: JSONL first line means JSONL, fixture object
    means fixture — a big session is never slurped (Issue #362)."""
    session = tmp_path / "session.jsonl"
    session.write_text(
        '{"type": "ws", "ev": {"event_type": "book", "asset_id": "t", "bids": [], "asks": []}}\n'
        '{"type": "rest", "token": "t", "book": {"bids": {}, "asks": {}}}\n',
        encoding="utf-8",
    )
    assert _load_fixture_object(session) is None

    detected = _load_fixture_object(FIXTURE_PATH)
    assert isinstance(detected, dict)
    assert "breaking_event" in detected
    assert detected["token"] == json.loads(
        FIXTURE_PATH.read_text(encoding="utf-8"))["token"]


def test_reconciler_quote_change_with_rest_snapshot_does_not_false_diverge():
    """Verify that a valid quote-changing price_change is not counted as a REST divergence."""
    reconciler = SocketReconciler()
    token = "tok_test_4"

    # 1. Matching initial book
    reconciler.feed_line(
        json.dumps({
            "type": "ws",
            "rx": 100.0,
            "ev": {
                "event_type": "book",
                "asset_id": token,
                "bids": [{"price": "0.50", "size": "100"}],
                "asks": [{"price": "0.52", "size": "100"}],
            },
        })
    )

    # 2. REST snapshot at time 100.0 matching the initial book
    reconciler.feed_line(
        json.dumps({
            "type": "rest",
            "rx": 100.0,
            "token": token,
            "book": {
                "bids": {"0.50": "100"},
                "asks": {"0.52": "100"},
                "best_bid": 0.50,
                "best_ask": 0.52,
            },
        })
    )

    # 3. Valid quote-changing price_change at time 100.2 (new bid at 0.51)
    reconciler.feed_line(
        json.dumps({
            "type": "ws",
            "rx": 100.2,
            "ev": {
                "event_type": "price_change",
                "price_changes": [
                    {
                        "asset_id": token,
                        "side": "BUY",
                        "price": "0.51",
                        "size": "50",
                        "best_bid": "0.51",
                        "best_ask": "0.52",
                    }
                ],
            },
        })
    )

    rep = reconciler.report
    assert rep.total_ws_events == 2
    assert rep.total_rest_snapshots == 1
    assert rep.in_frame_divergences.get("price_change", 0) == 0
    assert rep.rest_divergences.get("price_change", 0) == 0
    assert rep.first_divergence is None

