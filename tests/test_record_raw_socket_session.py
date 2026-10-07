"""Tests for the raw socket + REST diagnostic recorder (Issue #438).

The recorder is the only source of REST ground truth for the socket-book
reconciliation replay, and until #438 both of its REST failure modes were
silent: an empty book was skipped with no log at all, and an exception was
downgraded to a warning. A session that captured zero REST snapshots therefore
looked exactly like a healthy one — which is how the residual divergence came
to have no ground truth to be measured against.

These tests pin the three REST outcomes apart, pin the per-token counts, and
pin the non-zero exit that makes a ground-truth-less capture unmissable.
"""
from __future__ import annotations

from pathlib import Path

from scripts.record_raw_socket_session import (
    SessionResult,
    _event_tokens,
    capture_verdict,
    main,
    new_rest_stats,
    poll_rest_once,
)


class _RecordingSink:
    """Stand-in for RawStreamRecorder that records what was written."""

    def __init__(self) -> None:
        """Initialize an empty log of written snapshots."""
        self.written: list[tuple[str, str]] = []

    def write_rest_snapshot(self, series: str, token: str, book: dict) -> None:
        """Record one snapshot write without touching the filesystem."""
        self.written.append((series, token))


def _fetch_factory() -> object:
    """Build a fetcher: 'bad*' raises, 'empty' returns a falsy book, else a book."""

    def fetch(_host: str, token: str):
        """Return, empty or raise depending on the token's prefix."""
        if token.startswith("bad"):
            raise RuntimeError("venue refused")
        if token == "empty":
            return {}
        return {"best_bid": 0.50, "best_ask": 0.52}

    return fetch


def test_capture_verdict_passes_a_rest_bearing_session():
    """A session with REST snapshots exits zero and reports no failure."""
    code, reason = capture_verdict(ws_events=100, rest_snapshots=10)
    assert code == 0
    assert reason is None


def test_capture_verdict_fails_a_rest_only_session():
    """REST with no WebSocket traffic is as unusable as the mirror case.

    The replay counts comparisons off WS events, so a REST-only capture yields
    zero comparisons and cannot support the diagnosis (PR #469 review).
    """
    code, reason = capture_verdict(ws_events=0, rest_snapshots=10)
    assert code == 1
    assert reason is not None
    assert "WS" in reason


def test_capture_verdict_fails_a_rest_less_session():
    """WS events without REST ground truth is a failed capture, not a neutral one."""
    code, reason = capture_verdict(ws_events=35333, rest_snapshots=0)
    assert code == 1
    assert reason is not None
    assert "REST" in reason


def test_capture_verdict_names_a_session_that_recorded_nothing():
    """A session with neither stream names the stronger failure."""
    code, reason = capture_verdict(ws_events=0, rest_snapshots=0)
    assert code == 1
    assert reason is not None
    assert "nothing" in reason


def test_event_tokens_extracts_asset_id_and_price_change_entries():
    """Token ids come from asset_id or from price_changes entries, like the replay."""
    assert _event_tokens({"asset_id": "tok1"}) == {"tok1"}
    assert _event_tokens({"price_changes": [{"asset_id": "a"}, {"asset_id": "b"}]}) == {"a", "b"}
    assert _event_tokens(
        {"asset_id": "tok1", "price_changes": [{"asset_id": "tok2"}]}
    ) == {"tok1", "tok2"}


def test_event_tokens_ignores_events_without_token_ids():
    """An event naming no token contributes nothing, and never raises."""
    assert _event_tokens({}) == set()
    assert _event_tokens({"asset_id": None, "price_changes": [{"size": "1"}]}) == set()
    assert _event_tokens({"asset_id": None, "price_changes": "not-a-list"}) == set()


def test_poll_rest_once_counts_recorded_empty_and_error_separately():
    """Three REST outcomes are counted apart so a silent side cannot look healthy."""
    markets = [("s1", "good", "empty"), ("s2", "bad", "good")]
    stats = new_rest_stats()
    sink = _RecordingSink()

    poll_rest_once(markets, sink, stats, fetch=_fetch_factory())

    assert stats["rest_ok"]["good"] == 2
    assert stats["rest_empty"]["empty"] == 1
    assert stats["rest_error"]["bad"] == 1
    assert stats["rest_empty"]["good"] == 0
    assert stats["rest_error"]["empty"] == 0
    assert sink.written == [("s1", "good"), ("s2", "good")]


def test_poll_rest_once_survives_every_token_failing():
    """A wholly failed REST side still completes the sweep and counts every token."""
    markets = [("s1", "bad1", "bad2")]
    stats = new_rest_stats()
    sink = _RecordingSink()

    poll_rest_once(markets, sink, stats, fetch=_fetch_factory())

    assert sink.written == []
    assert stats["rest_ok"]["bad1"] == 0
    assert stats["rest_error"]["bad1"] == 1
    assert stats["rest_error"]["bad2"] == 1


def test_main_exits_non_zero_when_the_session_captured_no_rest(monkeypatch, tmp_path):
    """The CLI propagates a ground-truth-less capture as a non-zero exit."""
    out = tmp_path / "session.jsonl"
    out.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        "scripts.record_raw_socket_session.run_session",
        lambda **kwargs: SessionResult(out, 35333, 0, {}, {}),
    )

    assert main([]) == 1


def test_main_exits_zero_when_rest_snapshots_were_captured(monkeypatch, tmp_path):
    """The CLI exits zero once REST ground truth exists."""
    out = tmp_path / "session.jsonl"
    out.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        "scripts.record_raw_socket_session.run_session",
        lambda **kwargs: SessionResult(out, 100, 7, {"t": 100}, {"t": 7}),
    )

    assert main([]) == 0


def test_session_result_carries_the_per_token_counts():
    """SessionResult exposes the counts the summary and the caller both need."""
    result = SessionResult(Path("x.jsonl"), 5, 2, {"a": 5}, {"a": 2})
    assert result.rest_snapshots == 2
    assert result.per_token_rest == {"a": 2}
    assert result.per_token_ws == {"a": 5}
