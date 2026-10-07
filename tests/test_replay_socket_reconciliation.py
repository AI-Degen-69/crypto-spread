"""Tests for offline per-delta socket-book reconciliation replay (Issue #359)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.replay_socket_reconciliation import (
    ReconciliationReport,
    SocketReconciler,
    _load_fixture_object,
    age_bucket,
    extract_fixture,
    replay_file,
    replay_fixture,
    tick_bucket,
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


# --- Issue #438: magnitude buckets, per-series split and skew classification ---


def test_tick_bucket_edges_are_expressed_in_venue_ticks():
    """The bucket edges sit at one and three venue ticks (0.001 each)."""
    assert tick_bucket(0.0) == "exact"
    assert tick_bucket(0.0005) == "sub_tick"
    assert tick_bucket(0.001) == "sub_tick"
    assert tick_bucket(0.0011) == "1_3_ticks"
    assert tick_bucket(0.003) == "1_3_ticks"
    assert tick_bucket(0.0031) == ">3_ticks"
    assert tick_bucket(0.08) == ">3_ticks"


def test_report_helpers_bucket_magnitude_series_and_skew():
    """The report aggregates every comparable pair, not only the divergent ones."""
    rep = ReconciliationReport()

    rep.note_magnitude(0.0)
    rep.note_magnitude(0.0005)
    rep.note_magnitude(0.002)
    rep.note_magnitude(0.01)
    assert rep.magnitude_buckets == {
        "exact": 1, "sub_tick": 1, "1_3_ticks": 1, ">3_ticks": 1,
    }

    rep.note_series("btc-5m", True, 0.01)
    rep.note_series("btc-5m", False, 0.0005)
    assert rep.per_series["btc-5m"] == {
        "comparisons": 2,
        "rest_divergences": 1,
        "divergence_rate": 0.5,
        "max_gap": 0.01,
    }

    rep.note_skew(100.5, 100.0)
    rep.note_skew(100.0, 100.5)
    rep.note_skew(None, 100.0)
    assert rep.skew_buckets == {
        "rest_after_ws": 1, "rest_before_ws": 1, "unknown": 1,
    }


def test_a_sub_tick_gap_is_measured_but_never_divergent():
    """A gap at or below one tick is invisible to `divergent` yet still counted.

    This is the population the issue's own ``<= 1 tick`` bucket was meant to
    hold; before #438 the instrument could not see it at all, so the claimed
    sub-tick bulk was an assumption rather than a measurement.
    """
    reconciler = SocketReconciler()
    token = "tok_sub"
    reconciler.feed_line(json.dumps({
        "type": "ws",
        "rx": 10.0,
        "ev": {
            "event_type": "book",
            "asset_id": token,
            "bids": [{"price": "0.50", "size": "100"}],
            "asks": [{"price": "0.52", "size": "100"}],
        },
    }))
    # A level below the top leaves the maintained best bid at 0.50, while the
    # frame declares 0.5005 -- half a tick away, so not a divergence.
    reconciler.feed_line(json.dumps({
        "type": "ws",
        "rx": 10.5,
        "ev": {
            "event_type": "price_change",
            "price_changes": [{
                "asset_id": token,
                "side": "BUY",
                "price": "0.44",
                "size": "10",
                "best_bid": "0.5005",
                "best_ask": "0.52",
            }],
        },
    }))

    rep = reconciler.report
    assert rep.in_frame_divergences.get("price_change", 0) == 0
    assert rep.magnitude_buckets.get("sub_tick", 0) >= 1
    assert rep.first_divergence is None


def test_a_rest_divergence_carries_series_bucket_and_both_timestamps():
    """A counted REST divergence is attributable to a series and to a timing order."""
    reconciler = SocketReconciler()
    token = "tok_rest"
    series = "btc-up-or-down-5m"

    reconciler.feed_line(json.dumps({
        "type": "ws",
        "rx": 100.0,
        "ev": {
            "event_type": "book",
            "asset_id": token,
            "bids": [{"price": "0.50", "size": "100"}],
            "asks": [{"price": "0.52", "size": "100"}],
        },
    }))
    reconciler.feed_line(json.dumps({
        "type": "rest",
        "rx": 100.0,
        "series": series,
        "token": token,
        "book": {
            "best_bid": 0.53,
            "best_ask": 0.52,
            "bids": {"0.53": "100"},
            "asks": {"0.52": "100"},
        },
    }))
    # The frame declares the REST ground truth (0.53) while the maintained book
    # still holds 0.50: the REST comparison is therefore allowed through and the
    # 3c gap is counted against the series.
    reconciler.feed_line(json.dumps({
        "type": "ws",
        "rx": 100.1,
        "ev": {
            "event_type": "price_change",
            "price_changes": [{
                "asset_id": token,
                "side": "BUY",
                "price": "0.44",
                "size": "10",
                "best_bid": "0.53",
                "best_ask": "0.52",
            }],
        },
    }))

    rep = reconciler.report
    assert rep.rest_divergences.get("price_change", 0) == 1
    assert rep.per_series[series]["comparisons"] == 1
    assert rep.per_series[series]["rest_divergences"] == 1
    assert rep.per_series[series]["divergence_rate"] == 1.0
    assert rep.skew_buckets.get("rest_before_ws", 0) == 1
    # Both comparable pairs land in the same bucket: the in-frame pair (maintained
    # book vs the frame's declared quotes) and the REST pair (maintained book vs
    # ground truth) are each a comparison, and each is 3c wide here.
    assert rep.magnitude_buckets.get(">3_ticks", 0) == 2

    rest_recs = [r for r in rep.all_divergences if r.source == "rest"]
    assert len(rest_recs) == 1
    rec = rest_recs[0]
    assert rec.ws_rx == 100.1
    assert rec.rest_rx == 100.0
    assert rec.tick_bucket == ">3_ticks"


def test_per_series_keeps_unknown_when_no_rest_record_names_the_series():
    """A token with no REST record has no series to attribute, and says so."""
    rep = ReconciliationReport()
    rep.note_series("unknown", True, 0.01)
    assert rep.per_series["unknown"]["rest_divergences"] == 1


def test_exact_agreement_is_not_folded_into_the_drift_bucket():
    """A zero gap is agreement, not sub-tick drift: the bimodality claim needs both."""
    rep = ReconciliationReport()
    rep.note_magnitude(0.0)
    assert rep.magnitude_buckets == {"exact": 1}
    assert rep.magnitude_buckets.get("sub_tick", 0) == 0


def test_summary_dict_carries_the_new_aggregates():
    """The serializable summary exposes buckets, skew and the per-series split."""
    rep = ReconciliationReport()
    rep.note_magnitude(0.01)
    rep.note_skew(100.5, 100.0)
    rep.note_series("btc-5m", True, 0.01)
    summary = rep.summary_dict()
    assert summary["magnitude_buckets"] == {">3_ticks": 1}
    assert summary["skew_buckets"] == {"rest_after_ws": 1}
    assert summary["per_series"]["btc-5m"]["rest_divergences"] == 1


# --- Issue #438 T3b: the reference's age, not merely which read came first ---


def test_age_bucket_edges_split_the_reference_age():
    """Age buckets are ordered by how stale the REST reference is."""
    assert age_bucket(None) == "unknown"
    assert age_bucket(0.0) == "le_50ms"
    assert age_bucket(0.05) == "le_50ms"
    assert age_bucket(0.051) == "le_100ms"
    assert age_bucket(0.1) == "le_100ms"
    assert age_bucket(0.2) == "le_250ms"
    assert age_bucket(0.3) == "le_500ms"
    assert age_bucket(0.5) == "le_500ms"


def test_age_histograms_separate_all_comparisons_from_divergences():
    """Both histograms exist, so divergent pairs can be compared to the population."""
    rep = ReconciliationReport()
    rep.note_age(0.02, False)
    rep.note_age(0.02, True)
    rep.note_age(0.4, True)
    rep.note_age(None, False)

    assert rep.age_buckets_all == {"le_50ms": 2, "le_500ms": 1, "unknown": 1}
    assert rep.age_buckets_divergent == {"le_50ms": 1, "le_500ms": 1}


def test_a_stale_divergent_reference_is_recorded_with_its_age():
    """A divergence against an old REST read carries that age into the record."""
    reconciler = SocketReconciler()
    token = "tok_age"
    series = "btc-up-or-down-5m"

    reconciler.feed_line(json.dumps({
        "type": "ws",
        "rx": 100.0,
        "ev": {
            "event_type": "book",
            "asset_id": token,
            "bids": [{"price": "0.50", "size": "100"}],
            "asks": [{"price": "0.52", "size": "100"}],
        },
    }))
    # The REST ground truth was polled 300ms before the WS event that follows.
    reconciler.feed_line(json.dumps({
        "type": "rest",
        "rx": 99.7,
        "series": series,
        "token": token,
        "book": {
            "best_bid": 0.53,
            "best_ask": 0.52,
            "bids": {"0.53": "100"},
            "asks": {"0.52": "100"},
        },
    }))
    reconciler.feed_line(json.dumps({
        "type": "ws",
        "rx": 100.0,
        "ev": {
            "event_type": "price_change",
            "price_changes": [{
                "asset_id": token,
                "side": "BUY",
                "price": "0.44",
                "size": "10",
                "best_bid": "0.53",
                "best_ask": "0.52",
            }],
        },
    }))

    rep = reconciler.report
    assert rep.rest_divergences.get("price_change", 0) == 1
    assert rep.age_buckets_divergent.get("le_500ms", 0) == 1
    assert rep.age_buckets_all.get("le_500ms", 0) == 1

    rec = next(r for r in rep.all_divergences if r.source == "rest")
    assert rec.age_s is not None
    assert abs(rec.age_s - 0.3) < 1e-9


def test_an_event_without_a_timestamp_has_an_unknown_age():
    """No WS timestamp means no measurable age, and it is reported as unknown."""
    rep = ReconciliationReport()
    rep.note_age(None, True)
    assert rep.age_buckets_all == {"unknown": 1}
    assert rep.age_buckets_divergent == {"unknown": 1}
    assert rep.summary_dict()["age_buckets_all"] == {"unknown": 1}


REST_FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "socket_rest_reference_staleness.json"
)


def _stale_rest_divergence_reconciler() -> SocketReconciler:
    """A reconciler whose only divergence is measured against an old REST read.

    The frame is a `book` snapshot, which carries no declared top-of-book quote,
    so the concordance guard in `_select_rest_book` has nothing to reject on and
    the pair is counted. That is the shape of the whole residual population in
    the 2026-10-07 capture: a REST divergence can only be *counted* on a frame
    with no same-frame declared quote to check the REST read against, because on
    a frame that has one, the guard drops the disagreeing pair (Issue #438).
    """
    reconciler = SocketReconciler()
    token = "tok_rest_fixture"
    # The REST ground truth was polled 300ms before the WS event it is compared to.
    reconciler.feed_line(json.dumps({
        "type": "rest",
        "rx": 99.7,
        "series": "btc-up-or-down-5m",
        "token": token,
        "book": {
            "best_bid": 0.53,
            "best_ask": 0.54,
            "bids": {"0.53": "100"},
            "asks": {"0.54": "100"},
        },
    }))
    reconciler.feed_line(json.dumps({
        "type": "ws",
        "rx": 100.0,
        "ev": {
            "event_type": "book",
            "asset_id": token,
            "bids": [{"price": "0.52", "size": "100"}],
            "asks": [{"price": "0.55", "size": "100"}],
        },
    }))
    return reconciler


def test_extracted_rest_divergence_fixture_keeps_the_reference_it_compared(tmp_path):
    """A REST-sourced divergence must carry the REST read it diverged from.

    Without it the fixture cannot reproduce its own failure: replaying it would
    compare the book against nothing, and the #359 fixture shape never had to
    carry a reference because its divergence was against the frame's own quotes
    (Issue #438).
    """
    out = tmp_path / "rest_fixture.json"
    ok = extract_fixture(_stale_rest_divergence_reconciler().report, out)
    assert ok is True
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["source"] == "rest"
    assert data["event_type"] == "book"
    assert data["ws_rx"] == 100.0
    assert data["rest_reference"]["rx"] == 99.7
    assert data["rest_reference"]["book"]["best_bid"] == 0.53
    assert data["rest_reference"]["book"]["best_ask"] == 0.54
    assert abs(data["age_s"] - 0.3) < 1e-9


def test_extracted_rest_fixture_reproduces_the_divergence_through_the_ws_client(tmp_path):
    """The extracted fixture replays through CLOBMarketWSClient and reproduces
    the divergence *and its age* — the mechanism the #438 verdict names."""
    out = tmp_path / "rest_fixture.json"
    extract_fixture(_stale_rest_divergence_reconciler().report, out)

    rep = replay_fixture(out)
    assert rep.total_ws_events == 1
    assert rep.total_rest_snapshots == 1
    assert rep.in_frame_divergences == {}
    assert rep.rest_divergences.get("book", 0) == 1
    rec = next(r for r in rep.all_divergences if r.source == "rest")
    assert rec.tick_bucket == ">3_ticks"
    assert rec.age_s is not None
    assert abs(rec.age_s - 0.3) < 1e-9


def test_the_committed_rest_fixture_reproduces_the_captured_divergence():
    """The fixture extracted from the 2026-10-07 capture reproduces its own
    divergence, at the reference age recorded in the capture (Issue #438)."""
    assert REST_FIXTURE_PATH.exists(), f"missing fixture: {REST_FIXTURE_PATH}"
    fixture = json.loads(REST_FIXTURE_PATH.read_text(encoding="utf-8"))
    assert fixture["source"] == "rest"
    assert "rest_reference" in fixture

    rep = replay_fixture(REST_FIXTURE_PATH)
    assert rep.total_ws_events == 1
    assert rep.in_frame_divergences == {}
    assert rep.rest_divergences.get(fixture["event_type"], 0) == 1
    rec = next(r for r in rep.all_divergences if r.source == "rest")
    assert abs(rec.max_gap - fixture["max_gap"]) < 1e-9
    assert rec.age_s is not None
    assert abs(rec.age_s - fixture["age_s"]) < 1e-6


def test_an_in_frame_fixture_omits_the_rest_reference(tmp_path):
    """Additive only: an in-frame divergence keeps the exact #359 fixture shape,
    so the existing fixture and its replay path are untouched (Issue #438)."""
    reconciler = SocketReconciler()
    token = "tok_in_frame"
    reconciler.feed_line(json.dumps({
        "type": "ws",
        "ev": {
            "event_type": "price_change",
            "price_changes": [{
                "asset_id": token,
                "side": "BUY",
                "price": "0.46",
                "size": "10",
                "best_bid": "0.50",
                "best_ask": "0.62",
            }],
        },
    }))
    assert reconciler.report.in_frame_divergences["price_change"] == 1

    out = tmp_path / "in_frame.json"
    assert extract_fixture(reconciler.report, out) is True
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["source"] == "in_frame"
    assert "rest_reference" not in data
    assert "ws_rx" not in data

