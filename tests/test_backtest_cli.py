"""CLI-level tests for `scripts/backtest.py`.

The execution parameters are defined by `entry_delay_sec` and `quote_range`
as much as by `offset`, so a CLI that silently defaulted those to
0 replayed a *different* strategy than the bot runs — and the tape-vs-book
comparison built on it proved nothing about the configuration being shipped.
These tests pin the wiring from flag to `BacktestParams`.
"""
import json

import pytest

from backtest import BacktestParams
from scripts import backtest as cli


def _one_window_ticks(tmp_path):
    """A minimal two-snapshot window the replay engine will accept."""
    path = tmp_path / "ticks.jsonl"
    rows = []
    start = 1_760_000_000.0
    for i in range(4):
        rows.append({
            "ts": start + i,
            "cid": "0xcafe",
            "series": "eth-up-or-down-5m",
            "slug": "eth-up-or-down-5m",
            "start_ts": start,
            "end_ts": start + 300.0,
            "up": {"best_bid": 0.49, "best_ask": 0.51,
                   "bids": {"0.49": 100.0}, "asks": {"0.51": 100.0}},
            "down": {"best_bid": 0.49, "best_ask": 0.51,
                     "bids": {"0.49": 100.0}, "asks": {"0.51": 100.0}},
            "tape_delta": [],
        })
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def _captured_params(monkeypatch):
    """Intercept the params the CLI hands to `replay` without running it."""
    seen = {}

    def _fake_replay(snaps, params):
        seen["params"] = params
        return {"n_windows": 0,
                "aggregate": {"overall": {"windows": 0, "pair_rate": 0.0,
                                          "exit_rate": 0.0, "total_pnl_cents": 0.0,
                                          "avg_pnl_cents": 0.0,
                                          "total_fees_cents": 0.0},
                              "per_series": {}}}

    monkeypatch.setattr(cli, "replay", _fake_replay)
    return seen


def test_entry_delay_and_quote_range_reach_the_engine(tmp_path, monkeypatch, capsys):
    """The knobs that define execution parameters must be settable here."""
    seen = _captured_params(monkeypatch)
    src = _one_window_ticks(tmp_path)
    assert cli.main([str(src), "--entry-delay", "60", "--quote-lo", "0.20", "--quote-hi", "0.80"]) == 0
    params = seen["params"]
    assert params.entry_delay_sec == 60.0
    assert params.quote_range == (0.20, 0.80)
    # Echoed in the header line, so a logged run states which strategy it was.
    out = capsys.readouterr().out
    assert "entry_delay=60.0s" in out
    assert "quote_range=(0.2, 0.8)" in out


def test_entry_delay_and_quote_range_default_to_baseline(tmp_path, monkeypatch):
    """Omitting them keeps the previous baseline behavior exactly."""
    seen = _captured_params(monkeypatch)
    src = _one_window_ticks(tmp_path)
    assert cli.main([str(src)]) == 0
    assert seen["params"].entry_delay_sec == 0.0
    assert seen["params"].quote_range == (0.10, 0.90)
    assert seen["params"].params_hash() == BacktestParams(
        offset=0.020, queue_gate=50.0, max_pair_cost=0.99,
        exit_thresh_by_slug=seen["params"].exit_thresh_by_slug,
        exit_reversal=0.02, quote_shares=120,
        merge_gas_usd=0.0).params_hash()


def test_the_two_knobs_change_the_params_hash(tmp_path, monkeypatch):
    """Sweep caches key on `params_hash`; custom runs must not reuse baseline."""
    src = _one_window_ticks(tmp_path)
    seen = _captured_params(monkeypatch)
    cli.main([str(src)])
    baseline = seen["params"].params_hash()
    cli.main([str(src), "--entry-delay", "60", "--quote-lo", "0.20", "--quote-hi", "0.80"])
    assert seen["params"].params_hash() != baseline, (
        "a delay/quote_range run would collide with the baseline in the sweep cache")


@pytest.mark.parametrize("flag,value", [
    ("--entry-delay", "-1"),
    ("--entry-delay", "3601"),
    ("--quote-lo", "-0.01"),
    ("--quote-hi", "1.01"),
])
def test_out_of_range_values_are_refused_not_silently_clamped(
        tmp_path, monkeypatch, flag, value):
    """The engine's own bounds must surface as an error, not a quiet coercion.

    A clamped value would run a strategy the operator did not ask for and
    report it under the requested name.
    """
    _captured_params(monkeypatch)
    src = _one_window_ticks(tmp_path)
    with pytest.raises(ValueError):
        cli.main([str(src), flag, value])


def test_winning_preset_invocation_from_the_module_docstring_parses(
        tmp_path, monkeypatch):
    """The copy-paste command in the docstring must actually work."""
    seen = _captured_params(monkeypatch)
    src = _one_window_ticks(tmp_path)
    assert cli.main([
        str(src),
        "--offset", "0.03", "--queue", "0", "--pair-cost", "0.98",
        "--size", "5", "--entry-delay", "60", "--quote-lo", "0.10", "--quote-hi", "0.90",
        "--exit-default-5m", "0.49", "--exit-default-15m", "0.50",
        "--max-start-delay", "0",
    ]) == 0
    p = seen["params"]
    assert (p.offset, p.queue_gate, p.max_pair_cost) == (0.03, 0.0, 0.98)
    assert (p.entry_delay_sec, p.quote_range) == (60.0, (0.10, 0.90))
    assert p.quote_shares == 5
    assert p.exit_thresh_by_slug["default_5m"] == 0.49
    assert p.exit_thresh_by_slug["default_15m"] == 0.50


def test_help_renders_instead_of_crashing(capsys):
    """`--help` must print usage, not die formatting its own help strings.

    argparse runs every help string through `% params`, so a bare `%` in the
    text is read as a format spec: `10% (0 disables)` raised
    `ValueError: unsupported format character '('`. The crash hid the whole
    flag list from anyone running the CLI for the first time.
    """
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    # Issue #229: the dead-zone flags replaced --entry-timeout.
    assert "--dead-zone-val" in out
    assert "--dead-zone-unit" in out
    assert "--naked-leg-at-expiry" in out
    assert "--entry-timeout" not in out


def test_max_start_delay_and_filter_partial_filter_dataset(tmp_path, monkeypatch):
    """--max-start-delay and --filter-partial filter late-started windows before replay."""
    path = tmp_path / "ticks_multi.jsonl"
    rows = []
    # Window 1: on time (first tick at start)
    start1 = 1_760_000_000.0
    for i in range(2):
        rows.append({
            "ts": start1 + i,
            "cid": "0xwin1",
            "series": "eth-up-or-down-5m",
            "slug": "eth-up-or-down-5m",
            "start_ts": start1,
            "end_ts": start1 + 300.0,
            "up": {"best_bid": 0.49, "best_ask": 0.51},
            "down": {"best_bid": 0.49, "best_ask": 0.51},
        })
    # Window 2: late start (first tick 10s after open)
    start2 = 1_760_001_000.0
    for i in range(2):
        rows.append({
            "ts": start2 + 10.0 + i,
            "cid": "0xwin2",
            "series": "eth-up-or-down-5m",
            "slug": "eth-up-or-down-5m",
            "start_ts": start2,
            "end_ts": start2 + 300.0,
            "up": {"best_bid": 0.49, "best_ask": 0.51},
            "down": {"best_bid": 0.49, "best_ask": 0.51},
        })
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    captured_snaps = []

    def _fake_replay(snaps, params):
        captured_snaps.append(list(snaps))
        return {
            "n_windows": len(set(s["cid"] for s in snaps)),
            "aggregate": {
                "overall": {
                    "windows": 1, "pair_rate": 0.0, "exit_rate": 0.0,
                    "total_pnl_cents": 0.0, "avg_pnl_cents": 0.0, "total_fees_cents": 0.0
                },
                "per_series": {}
            }
        }

    monkeypatch.setattr(cli, "replay", _fake_replay)

    # 1. No filter -> all 4 snaps (both windows) passed
    cli.main([str(path), "--max-start-delay", "0"])
    assert len(captured_snaps[-1]) == 4

    # 2. --max-start-delay 5 -> only on-time window (2 snaps) passed
    cli.main([str(path), "--max-start-delay", "5"])
    assert len(captured_snaps[-1]) == 2
    assert {s["cid"] for s in captured_snaps[-1]} == {"0xwin1"}

    # 3. --filter-partial -> defaults to 5s delay -> only 2 snaps passed
    cli.main([str(path), "--filter-partial"])
    assert len(captured_snaps[-1]) == 2
    assert {s["cid"] for s in captured_snaps[-1]} == {"0xwin1"}

