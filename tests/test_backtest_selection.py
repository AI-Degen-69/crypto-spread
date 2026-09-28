"""Unit tests for `backtest/selection.py` (issue #308).

Selection is a dataset filter: unknown tokens must raise (never replay zero
windows silently), empty selection must leave input untouched, and coverage
must compare found pairs against the golden manifest when adjacent, else the
canonical SERIES universe.
"""
import json

import pytest

from backtest.selection import (
    apply_selection,
    build_coverage,
    expected_pairs,
    find_golden_manifest,
    found_pairs,
    parse_durations,
    parse_series_tokens,
)
from strategy.series import SERIES


def _tick(series, duration=300, cid="0x1"):
    return {"cid": cid, "series": series, "slug": series, "duration": duration, "ts": 1.0}


def test_empty_selection_parses_to_all():
    """No flags means every market and both frames."""
    assert parse_series_tokens(None) == ()
    assert parse_series_tokens([]) == ()
    assert parse_series_tokens("") == ()
    assert parse_durations(None) == ()
    assert parse_durations([]) == ()


def test_series_tokens_split_comma_repeatable_and_case_insensitive():
    """`--series btc --series eth,sol` style input normalizes to lowercase tokens."""
    assert parse_series_tokens(["BTC", "eth,sol "]) == ("btc", "eth", "sol")
    assert parse_series_tokens("btc-up-or-down-5m") == ("btc-up-or-down-5m",)


def test_unknown_series_token_raises_not_silent_zero():
    """A typo must fail loudly instead of replaying zero windows."""
    with pytest.raises(ValueError, match="bcc"):
        parse_series_tokens(["bcc"])


def test_durations_parse_and_validate():
    """Comma strings and ints both work; garbage and unknown frames raise."""
    assert parse_durations("300,900") == (300, 900)
    assert parse_durations([300]) == (300,)
    assert parse_durations(" 300 ") == (300,)
    with pytest.raises(ValueError, match="abc"):
        parse_durations("abc")
    with pytest.raises(ValueError, match="60"):
        parse_durations("60")


def test_empty_selection_returns_input_unchanged():
    """Unfiltered behavior must stay identical — same object back."""
    snaps = [_tick("btc-up-or-down-5m")]
    assert apply_selection(snaps) is snaps


def test_btc_token_matches_both_frames():
    """Substring semantics: `btc` catches the 5m and the 15m windows."""
    snaps = [
        _tick("btc-up-or-down-5m", 300, "0xa"),
        _tick("btc-up-or-down-15m", 900, "0xb"),
        _tick("eth-up-or-down-5m", 300, "0xc"),
    ]
    out = apply_selection(snaps, ("btc",), ())
    assert {s["cid"] for s in out} == {"0xa", "0xb"}


def test_duration_only_selection():
    """`--durations 300` keeps every 5-minute window across all series."""
    snaps = [
        _tick("btc-up-or-down-5m", 300, "0xa"),
        _tick("btc-up-or-down-15m", 900, "0xb"),
    ]
    out = apply_selection(snaps, (), (300,))
    assert [s["cid"] for s in out] == ["0xa"]


def test_combined_selection_is_exact():
    """`--series eth --durations 900` selects exactly the ETH 15-minute windows."""
    snaps = [
        _tick("eth-up-or-down-5m", 300, "0xa"),
        _tick("eth-up-or-down-15m", 900, "0xb"),
        _tick("btc-up-or-down-15m", 900, "0xc"),
    ]
    out = apply_selection(snaps, ("eth",), (900,))
    assert [s["cid"] for s in out] == ["0xb"]


def test_valid_selection_with_no_matching_ticks_is_empty_not_error():
    """A valid filter over a source that lacks the market is zero windows."""
    snaps = [_tick("eth-up-or-down-5m", 300, "0xa")]
    assert apply_selection(snaps, ("sol",), ()) == []


def test_found_pairs_counts_windows_per_pair():
    """Grouped windows collapse to (series, duration) → window counts."""
    grouped = [
        ("0xa", [_tick("btc-up-or-down-5m", 300, "0xa")]),
        ("0xb", [_tick("btc-up-or-down-5m", 300, "0xb")]),
        ("0xc", [_tick("eth-up-or-down-15m", 900, "0xc")]),
    ]
    assert found_pairs(grouped) == {
        ("btc-up-or-down-5m", 300): 2,
        ("eth-up-or-down-15m", 900): 1,
    }


def _write_manifest(tmp_path, days):
    mp = tmp_path / "golden_manifest.json"
    mp.write_text(json.dumps({"days": days}), encoding="utf-8")
    return tmp_path


def _day(fname, entries):
    return {"day": "2026-09-18", "file": fname,
            "market_breakdown": [
                {"series": s, "duration": d, "windows": w}
                for s, d, w in entries]}


def test_expected_pairs_from_manifest_directory_source(tmp_path):
    """Directory source reads every day entry; selection narrows the pairs."""
    src = _write_manifest(tmp_path, [
        _day("ticks_a.jsonl", [("btc-up-or-down-5m", 300, 67),
                               ("eth-up-or-down-15m", 900, 18),
                               ("sol-up-or-down-5m", 300, 0)]),
    ])
    pairs, windows, origin = expected_pairs(src, ("btc",), ())
    assert origin == "golden_manifest"
    assert pairs == {("btc-up-or-down-5m", 300)}
    assert windows == {("btc-up-or-down-5m", 300): 67}


def test_expected_pairs_file_source_restricted_to_matching_day(tmp_path):
    """A file source only expects pairs from its own day entry."""
    src = _write_manifest(tmp_path, [
        _day("ticks_a.jsonl", [("btc-up-or-down-5m", 300, 67)]),
        _day("ticks_b.jsonl", [("eth-up-or-down-5m", 300, 10)]),
    ])
    f = tmp_path / "ticks_b.jsonl"
    f.write_text("{}\n", encoding="utf-8")
    pairs, windows, origin = expected_pairs(f, (), ())
    assert origin == "golden_manifest"
    assert pairs == {("eth-up-or-down-5m", 300)}
    assert windows == {("eth-up-or-down-5m", 300): 10}


def test_expected_pairs_canonical_fallback_without_manifest(tmp_path):
    """No manifest → the canonical SERIES universe, selection still applies."""
    pairs, windows, origin = expected_pairs(tmp_path, ("btc",), (300,))
    assert origin == "canonical"
    assert pairs == {("btc-up-or-down-5m", 300)}
    assert windows == {}
    all_pairs, _, _ = expected_pairs(tmp_path, (), ())
    assert len(all_pairs) == len(SERIES) == 10


def test_find_golden_manifest_missing_returns_none(tmp_path):
    """Non-golden sources fall back instead of crashing."""
    assert find_golden_manifest(tmp_path) is None


def test_build_coverage_reports_found_vs_expected(tmp_path):
    """The completeness dict carries pair counts plus informational windows."""
    src = _write_manifest(tmp_path, [
        _day("ticks_a.jsonl", [("btc-up-or-down-5m", 300, 67),
                               ("btc-up-or-down-15m", 900, 18)]),
    ])
    grouped = [("0xa", [_tick("btc-up-or-down-5m", 300, "0xa")])]
    cov = build_coverage(src, grouped, ("btc",), ())
    assert cov["filtered"] is True
    assert cov["selection"] == {"series": ["btc"], "durations": []}
    assert cov["pairs_found"] == 1
    assert cov["pairs_expected"] == 2
    assert cov["missing_pairs"] == ["btc-up-or-down-15m@900"]
    assert cov["expected_source"] == "golden_manifest"


def test_build_coverage_unfiltered_marks_not_filtered(tmp_path):
    """Full runs report filtered=False with the canonical pair count."""
    cov = build_coverage(tmp_path, [], (), ())
    assert cov["filtered"] is False
    assert cov["pairs_expected"] == 10
    assert cov["expected_source"] == "canonical"
