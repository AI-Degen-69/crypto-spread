"""Tests for offline per-delta socket-book reconciliation replay (Issue #359)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.replay_socket_reconciliation import (
    ReconciliationReport,
    SocketReconciler,
    extract_fixture,
    replay_file,
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


def test_reconciler_detects_in_frame_divergence():
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
    assert rep.in_frame_divergences["price_change"] == 1
    assert rep.first_divergence is not None
    assert rep.first_divergence.event_type == "price_change"
    assert rep.first_divergence.source == "in_frame"
    assert rep.first_divergence.ws_bb == 0.55
    assert rep.first_divergence.ref_bb == 0.50
    assert abs(rep.first_divergence.max_gap - 0.05) < 1e-6


def test_reconciler_fixture_extraction(tmp_path):
    reconciler = SocketReconciler()
    token = "tok_test_3"
    reconciler.feed_line(
        json.dumps({
            "type": "ws",
            "ev": {
                "event_type": "book",
                "asset_id": token,
                "bids": [{"price": "0.60", "size": "10"}],
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
                        "price": "0.50",
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
    assert data["max_gap"] == 0.10


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

