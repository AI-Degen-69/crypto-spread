"""CLI-level tests for `scripts/backtest.py`.

The winning preset (`patient_band_maker`) is defined by `entry_delay_sec` and
`entry_band` as much as by `offset`, so a CLI that silently defaulted those to
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


def test_entry_delay_and_band_reach_the_engine(tmp_path, monkeypatch, capsys):
    """The two knobs that define the winning preset must be settable here."""
    seen = _captured_params(monkeypatch)
    src = _one_window_ticks(tmp_path)
    assert cli.main([str(src), "--entry-delay", "60", "--entry-band", "0.04"]) == 0
    params = seen["params"]
    assert params.entry_delay_sec == 60.0
    assert params.entry_band == 0.04
    # Echoed in the header line, so a logged run states which strategy it was.
    out = capsys.readouterr().out
    assert "entry_delay=60.0s" in out
    assert "entry_band=0.04" in out


def test_entry_delay_and_band_default_to_disabled(tmp_path, monkeypatch):
    """Omitting them keeps the previous baseline behavior exactly."""
    seen = _captured_params(monkeypatch)
    src = _one_window_ticks(tmp_path)
    assert cli.main([str(src)]) == 0
    assert seen["params"].entry_delay_sec == 0.0
    assert seen["params"].entry_band == 0.0
    assert seen["params"].params_hash() == BacktestParams(
        offset=0.020, queue_gate=50.0, pair_cost_gate=1.05,
        exit_thresh_by_slug=seen["params"].exit_thresh_by_slug,
        exit_reversal=0.02, quote_shares=120, fill_model="tape",
        merge_gas_usd=0.0, max_start_delay_sec=0.0,
        entry_timeout_pct=0.10).params_hash()


def test_the_two_knobs_change_the_params_hash(tmp_path, monkeypatch):
    """Sweep caches key on `params_hash`; a preset run must not reuse baseline."""
    src = _one_window_ticks(tmp_path)
    seen = _captured_params(monkeypatch)
    cli.main([str(src)])
    baseline = seen["params"].params_hash()
    cli.main([str(src), "--entry-delay", "60", "--entry-band", "0.04"])
    assert seen["params"].params_hash() != baseline, (
        "a delay/band run would collide with the baseline in the sweep cache")


@pytest.mark.parametrize("flag,value", [
    ("--entry-delay", "-1"),
    ("--entry-delay", "3601"),
    ("--entry-band", "-0.01"),
    ("--entry-band", "0.51"),
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
        str(src), "--fill-model", "tape",
        "--offset", "0.03", "--queue", "0", "--pair-cost", "0.98",
        "--size", "5", "--entry-delay", "60", "--entry-band", "0.04",
        "--exit-default-5m", "0.49", "--exit-default-15m", "0.50",
        "--max-start-delay", "0",
    ]) == 0
    p = seen["params"]
    assert (p.offset, p.queue_gate, p.pair_cost_gate) == (0.03, 0.0, 0.98)
    assert (p.entry_delay_sec, p.entry_band) == (60.0, 0.04)
    assert p.quote_shares == 5
    assert p.fill_model == "tape"
    assert p.exit_thresh_by_slug["default_5m"] == 0.49
    assert p.exit_thresh_by_slug["default_15m"] == 0.50
