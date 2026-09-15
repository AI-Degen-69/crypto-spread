"""Regression tests for the WS top-of-book recorder (`scripts/record_ws_deltas`).

The recorder exists to answer one question the 1.44s tick collector cannot:
how long a crossable two-sided quote actually lasts. That makes it an
instrument, so what it writes has to be trustworthy on its own terms — a
duplicate line or a second spelling of the same number is a measurement error,
not a cosmetic one.
"""
import json

import pytest

from scripts.record_ws_deltas import Recorder

TOK = "5949168580672789881355692646945014192146674827215874979386802930780984398807"
DN = "74086961480240447773681594032582397183438133454033538203498975718604505324075"


def _lines(out_dir):
    """Every record written so far, oldest first."""
    files = sorted(out_dir.glob("deltas_*.jsonl"))
    return [json.loads(ln) for f in files
            for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_a_book_snapshot_repeating_a_price_change_quote_is_not_written_twice(tmp_path):
    """The venue is not consistent about its own encoding.

    A `price_change` reports an ask of `"1"` where a `book` snapshot yields
    `1.0`. Keyed on the raw strings the two never matched, so every `book` that
    merely repeated the preceding quote was written again and the file carried
    two spellings of one number. Found in review of #189.
    """
    rec = Recorder(tmp_path)
    rec.write({"event_type": "price_change", "price_changes": [
        {"asset_id": TOK, "best_bid": "0.99", "best_ask": "1"}]})
    rec.write({"event_type": "book", "asset_id": TOK,
               "bids": [{"price": "0.99", "size": "5"}],
               "asks": [{"price": "1", "size": "5"}]})

    rows = _lines(tmp_path)
    assert len(rows) == 1, f"the repeated quote was written again: {rows}"


def test_every_quote_is_written_as_a_number(tmp_path):
    """One encoding in the file, so no consumer has to normalize before
    comparing two records."""
    rec = Recorder(tmp_path)
    rec.write({"event_type": "price_change", "price_changes": [
        {"asset_id": TOK, "best_bid": "0.48", "best_ask": "0.52"}]})
    rec.write({"event_type": "book", "asset_id": DN,
               "bids": [{"price": "0.47", "size": "5"}],
               "asks": [{"price": "0.53", "size": "5"}]})

    for r in _lines(tmp_path):
        assert isinstance(r["bb"], float), f"{r['et']} wrote bb as {type(r['bb'])}"
        assert isinstance(r["ba"], float), f"{r['et']} wrote ba as {type(r['ba'])}"


def test_a_real_quote_move_is_still_recorded(tmp_path):
    """Deduplication must not swallow the events the instrument exists for."""
    rec = Recorder(tmp_path)
    for ask in ("0.52", "0.51", "0.50"):
        rec.write({"event_type": "price_change", "price_changes": [
            {"asset_id": TOK, "best_bid": "0.48", "best_ask": ask}]})
    assert [r["ba"] for r in _lines(tmp_path)] == [0.52, 0.51, 0.50]


def test_both_legs_of_one_frame_are_recorded_separately(tmp_path):
    """A single `price_change` routinely names both legs of a market, and the
    two are independent quotes."""
    rec = Recorder(tmp_path)
    rec.write({"event_type": "price_change", "price_changes": [
        {"asset_id": TOK, "best_bid": "0.99", "best_ask": "1"},
        {"asset_id": DN, "best_bid": "0", "best_ask": "0.01"},
    ]})
    rows = _lines(tmp_path)
    assert {r["tok"] for r in rows} == {TOK, DN}
    assert len(rows) == 2


@pytest.mark.parametrize("entry", [
    {"asset_id": TOK, "best_bid": None, "best_ask": "0.52"},
    {"asset_id": TOK, "best_bid": "junk", "best_ask": "0.52"},
    {"best_bid": "0.48", "best_ask": "0.52"},            # no token
])
def test_an_unusable_entry_is_skipped_rather_than_written(tmp_path, entry):
    """A malformed entry must not become a record that looks like a quote."""
    rec = Recorder(tmp_path)
    rec.write({"event_type": "price_change", "price_changes": [entry]})
    assert _lines(tmp_path) == []
