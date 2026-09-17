"""Tests for automated backtest parameter sweep engine (scripts/sweep_backtest.py)."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from backtest.engine import BacktestParams, WindowResult
from scripts.sweep_backtest import (
    SweepRunResult,
    compute_metrics,
    filter_sensitivity_grid,
    format_markdown_table,
    generate_joint_grid,
    generate_random_grid,
    generate_sensitivity_grid,
    main,
    run_sweep,
)


def _make_window_result(
    cid: str = "0x123",
    series: str = "btc-up-or-down-5m",
    pnl_cents: float = 4.0,
    pair_captured: bool = True,
    exit_taken: bool = False,
    fees_cents: float = 0.0,
    reentry_count: int = 0,
) -> WindowResult:
    return WindowResult(
        cid=cid,
        series=series,
        slug=series,
        duration=300,
        n_snaps=10,
        class_label="oscillating",
        max_up=0.03,
        max_down=0.03,
        filled_up=pair_captured,
        filled_down=pair_captured,
        pair_captured=pair_captured,
        exit_taken=exit_taken,
        exit_side="",
        pnl_cents=pnl_cents,
        fees_cents=fees_cents,
        reentry_count=reentry_count,
    )


def test_compute_metrics_empty():
    p = BacktestParams()
    res = compute_metrics([], p, label="empty_test")
    assert res.n_windows == 0
    assert res.total_pnl_cents == 0.0
    assert res.win_rate == 0.0
    assert res.param_label == "empty_test"


def test_compute_metrics_reentry_telemetry():
    """Recovered windows are counted and their net PnL aggregated, scaled by size."""
    recovered_win = _make_window_result(cid="r1", pnl_cents=4.0, pair_captured=True,
                                        reentry_count=1)
    recovered_loss = _make_window_result(cid="r2", pnl_cents=-2.0, pair_captured=False,
                                         exit_taken=True, fees_cents=0.5,
                                         reentry_count=1)
    never = _make_window_result(cid="n1", pnl_cents=4.0, pair_captured=True)
    res = compute_metrics([recovered_win, recovered_loss, never], BacktestParams(),
                          label="reentry_test", size=5)
    assert res.reentered_windows == 2
    # (4.0 * 5) + ((-2.0 - 0.5) * 5) = 20.0 - 12.5 = 7.5
    assert res.reentered_pnl_cents == pytest.approx(7.5, abs=0.01)
    assert res.to_dict()["reentered_windows"] == 2
    assert res.to_dict()["reentered_pnl_cents"] == pytest.approx(7.5, abs=0.01)


def test_compute_metrics_positive_and_drawdown():
    p = BacktestParams()
    w1 = _make_window_result(cid="1", pnl_cents=4.0, pair_captured=True)
    w2 = _make_window_result(cid="2", pnl_cents=-10.0, pair_captured=False, exit_taken=True)
    w3 = _make_window_result(cid="3", pnl_cents=4.0, pair_captured=True)

    res = compute_metrics([w1, w2, w3], p, label="pnl_test", size=5)
    assert res.n_windows == 3
    assert res.pair_rate == pytest.approx(2 / 3, 0.01)
    assert res.exit_rate == pytest.approx(1 / 3, 0.01)
    assert res.win_rate == pytest.approx(2 / 3, 0.01)
    assert res.total_pnl_cents == -10.0
    assert res.avg_pnl_cents == pytest.approx(-10.0 / 3, 0.01)
    assert res.max_drawdown_cents == 50.0
    assert res.profit_factor == pytest.approx(40.0 / 50.0, 0.01)


def test_generate_sensitivity_grid():
    base = BacktestParams(offset=0.02, queue_gate=50)
    grid = generate_sensitivity_grid(base, include_structural=True)
    assert len(grid) > 10
    labels = [label for label, _params in grid]
    assert "Baseline" in labels
    assert any("offset=" in label for label in labels)
    assert any("queue=" in label for label in labels)
    assert any("exit_5m=" in label for label in labels)
    assert any("quote_range=" in label for label in labels)
    qr_rows = [(lbl, p) for lbl, p in grid if "quote_range=" in lbl]
    assert qr_rows
    assert all(p.offset == base.offset for _, p in qr_rows)


def test_generate_joint_grid():
    grid = generate_joint_grid(
        offsets=[0.015, 0.020],
        queues=[0.0, 50.0],
        exit_5ms=[0.08, 0.12],
        exit_reversals=[0.02],
        quote_ranges=[(0.10, 0.90)],
    )
    # 2 * 2 * 2 * 1 * 1 = 8 combinations
    assert len(grid) == 8
    label, p = grid[0]
    assert isinstance(p, BacktestParams)
    assert "off=" in label


def test_generate_joint_grid_sweeps_quote_range():
    """The joint grid sweeps quote range bounds when explicitly provided."""
    grid = generate_joint_grid(
        offsets=[0.015, 0.020],
        queues=[0.0, 50.0],
        exit_5ms=[0.08, 0.12],
        exit_reversals=[0.02],
        quote_ranges=[(0.05, 0.95), (0.10, 0.90), (0.15, 0.85)],
        include_structural=True,
    )
    # 2 * 2 * 2 * 1 * 3 = 24 combinations
    assert len(grid) == 24
    ranges = sorted({p.quote_range for _, p in grid})
    assert ranges == [(0.05, 0.95), (0.10, 0.90), (0.15, 0.85)]
    labels = [label for label, _ in grid]
    assert all("_qr=" in label for label in labels)

    # The CLI grid preset default now holds quote_range at baseline (#233).
    default_grid = generate_joint_grid()
    assert len({p.quote_range for _, p in default_grid}) == 1


def test_run_sweep_with_grouped_windows():
    base = BacktestParams(offset=0.02, queue_gate=0)
    snap = {
        "cid": "0xabc",
        "series": "btc-up-or-down-5m",
        "slug": "btc-up-or-down-5m",
        "duration": 300,
        "ts": 100.0,
        "start_ts": 100.0,
        "up_book": {"best_bid": 0.48, "best_ask": 0.50, "bids": {"0.48": 10}},
        "down_book": {"best_bid": 0.48, "best_ask": 0.50, "bids": {"0.48": 10}},
    }
    grouped = [("0xabc", [snap])]
    grid = [("run1", base)]

    results = run_sweep(grouped, grid)
    assert len(results) == 1
    assert results[0].param_label == "run1"
    assert results[0].n_windows == 1


def test_generate_random_grid():
    """Verify deterministic sampling of random parameter combinations."""
    grid1 = generate_random_grid(count=10, seed=123)
    grid2 = generate_random_grid(count=10, seed=123)
    assert len(grid1) == 10
    assert len(grid2) == 10
    assert [label for label, _ in grid1] == [label for label, _ in grid2]
    label, p = grid1[0]
    assert isinstance(p, BacktestParams)
    assert "rand_off=" in label


def test_default_joint_grid_holds_structural_limits():
    """Issue #233: the default joint grid varies tuning knobs only.

    Structural limits (`max_pair_cost`, `quote_range`) sit at the BacktestParams
    baseline unless the caller opts in with `include_structural=True`.
    """
    grid = generate_joint_grid()
    assert grid
    assert all(p.max_pair_cost == 0.99 for _, p in grid)
    assert all(p.quote_range == (0.10, 0.90) for _, p in grid)
    assert not any("_qr=" in label for label, _ in grid)


def test_joint_grid_include_structural_sweeps_structural_limits():
    """Explicit opt-in sweeps the structural axes as before."""
    grid = generate_joint_grid(include_structural=True)
    assert len({p.quote_range for _, p in grid}) == 4
    assert all(p.max_pair_cost == 1.00 for _, p in grid)


def test_default_random_grid_holds_structural_limits():
    """Issue #233: the random sampler holds structural limits at baseline by default."""
    grid = generate_random_grid(count=20, seed=7)
    assert len(grid) == 20
    assert all(p.max_pair_cost == 0.99 for _, p in grid)
    assert all(p.quote_range == (0.10, 0.90) for _, p in grid)
    assert not any("_qr=" in label for label, _ in grid)
    # Still varies the tuning knobs.
    assert len({p.offset for _, p in grid}) > 1
    assert len({p.exit_reversal for _, p in grid}) > 1


def test_random_grid_include_structural_varies_structural_limits():
    """Explicit opt-in restores the structural axes in the random sampler."""
    grid = generate_random_grid(count=20, seed=7, include_structural=True)
    assert len({p.quote_range for _, p in grid}) > 1


def test_sensitivity_grid_defaults_to_tuning_knobs_only():
    """Issue #233: the 1D sensitivity grid sweeps tuning knobs only by default.

    The structural axes (`pair_cost`, `quote_range`) are dropped from the
    default grid; they remain reachable via `--only pair_cost` / `--only
    quote_range`, which is explicit operator intent.
    """
    base = BacktestParams()
    grid = generate_sensitivity_grid(base)
    labels = [lbl for lbl, _ in grid]
    assert "Baseline" in labels
    assert not any(lbl.startswith("pair_cost=") for lbl in labels)
    assert not any(lbl.startswith("quote_range=") for lbl in labels)
    # Tuning axes are all still present.
    assert any(lbl.startswith("offset=") for lbl in labels)
    assert any(lbl.startswith("queue=") for lbl in labels)
    assert any(lbl.startswith("exit_5m=") for lbl in labels)
    assert any(lbl.startswith("exit_rev=") for lbl in labels)


def test_sensitivity_grid_include_structural_restores_structural_axes():
    """Explicit opt-in restores the structural 1D axes."""
    base = BacktestParams()
    grid = generate_sensitivity_grid(base, include_structural=True)
    labels = [lbl for lbl, _ in grid]
    assert any(lbl.startswith("pair_cost=") for lbl in labels)
    assert any(lbl.startswith("quote_range=") for lbl in labels)


def test_cli_include_structural_flag_exists(tmp_path: Path):
    """--include-structural opts the grid preset into structural axes."""
    dummy_tick_file = tmp_path / "ticks_test.jsonl"
    snap = {
        "cid": "0x1", "series": "btc-up-or-down-5m", "slug": "btc-up-or-down-5m",
        "duration": 300, "ts": 100.0, "start_ts": 100.0,
        "up_book": {"best_bid": 0.48, "best_ask": 0.52},
        "down_book": {"best_bid": 0.48, "best_ask": 0.52},
    }
    dummy_tick_file.write_text(json.dumps(snap) + "\n", encoding="utf-8")
    out_default = tmp_path / "sweep_default.json"
    out_struct = tmp_path / "sweep_struct.json"
    assert main([str(dummy_tick_file), "--preset", "grid", "--out", str(out_default)]) == 0
    assert main([str(dummy_tick_file), "--preset", "grid",
                 "--include-structural", "--out", str(out_struct)]) == 0
    d_def = json.loads(out_default.read_text(encoding="utf-8"))
    d_str = json.loads(out_struct.read_text(encoding="utf-8"))
    assert d_def["include_structural"] is False
    assert d_str["include_structural"] is True
    assert d_str["runs"] and d_def["runs"]


def test_cli_only_pair_cost_is_explicit_structural_opt_in(tmp_path: Path):
    """--only pair_cost sweeps the structural axis despite the tuning-only default."""
    dummy_tick_file = tmp_path / "ticks_test.jsonl"
    snap = {
        "cid": "0x1", "series": "btc-up-or-down-5m", "slug": "btc-up-or-down-5m",
        "duration": 300, "ts": 100.0, "start_ts": 100.0,
        "up_book": {"best_bid": 0.48, "best_ask": 0.52},
        "down_book": {"best_bid": 0.48, "best_ask": 0.52},
    }
    dummy_tick_file.write_text(json.dumps(snap) + "\n", encoding="utf-8")
    out_json = tmp_path / "sweep_pc.json"
    code = main([str(dummy_tick_file), "--preset", "sensitivity",
                 "--only", "pair_cost", "--out", str(out_json)])
    assert code == 0
    data = json.loads(out_json.read_text(encoding="utf-8"))
    # Baseline + the 5 pair-cost points (0.99 is the baseline row itself).
    assert data["only"] == "pair_cost"
    labels = [r["param_label"] for r in data["runs"]]
    assert labels[0] == "Baseline"
    assert all(l == "Baseline" or l.startswith("pair_cost=") for l in labels)
    assert len(data["runs"]) == 5


def test_cli_only_dead_zone_is_explicit_structural_opt_in(tmp_path: Path):
    """Issue #208: --only dead_zone sweeps BOTH dead-zone axes structurally."""
    dummy_tick_file = tmp_path / "ticks_test.jsonl"
    snap = {
        "cid": "0x1", "series": "btc-up-or-down-5m", "slug": "btc-up-or-down-5m",
        "duration": 300, "ts": 100.0, "start_ts": 100.0,
        "up_book": {"best_bid": 0.48, "best_ask": 0.52},
        "down_book": {"best_bid": 0.48, "best_ask": 0.52},
    }
    dummy_tick_file.write_text(json.dumps(snap) + "\n", encoding="utf-8")
    out_json = tmp_path / "sweep_dz.json"
    code = main([str(dummy_tick_file), "--preset", "sensitivity",
                 "--only", "dead_zone", "--out", str(out_json)])
    assert code == 0
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["only"] == "dead_zone"
    labels = [r["param_label"] for r in data["runs"]]
    assert labels[0] == "Baseline"
    assert all(l == "Baseline" or l.startswith(("dead_zone_pct=", "dead_zone_sec="))
               for l in labels)
    # 1 baseline + 5 pct + 6 sec; the structural opt-in must have been
    # implied, or the dead-zone rows would be missing entirely.
    assert len(data["runs"]) == 12
    assert any(l.startswith("dead_zone_sec=") for l in labels)


def test_format_markdown_table():
    """Verify markdown table formatting with proper headers and rank."""
    p = BacktestParams()
    w = _make_window_result(pnl_cents=5.0)
    r1 = compute_metrics([w], p, label="config_A", size=5)
    table = format_markdown_table([r1], top_n=5)
    assert "| Rank | Configuration |" in table
    assert "`config_A`" in table
    assert "+25.00c" in table


def test_cli_smoke(tmp_path: Path):
    """Verify CLI entrypoint with sensitivity and random presets and JSON dumping."""
    out_json = tmp_path / "sweep_results.json"
    dummy_tick_file = tmp_path / "ticks_test.jsonl"
    snap = {
        "cid": "0x1",
        "series": "btc-up-or-down-5m",
        "slug": "btc-up-or-down-5m",
        "duration": 300,
        "ts": 100.0,
        "start_ts": 100.0,
        "up_book": {"best_bid": 0.48, "best_ask": 0.52},
        "down_book": {"best_bid": 0.48, "best_ask": 0.52},
    }
    dummy_tick_file.write_text(json.dumps(snap) + "\n", encoding="utf-8")

    code = main([
        str(dummy_tick_file),
        "--preset", "sensitivity",
        "--top", "3",
        "--out", str(out_json),
    ])
    assert code == 0
    assert out_json.exists()
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["preset"] == "sensitivity"
    assert len(data["runs"]) > 0

    # Test random preset
    out_rand = tmp_path / "sweep_rand.json"
    code_rand = main([
        str(dummy_tick_file),
        "--preset", "random",
        "--count", "5",
        "--seed", "99",
        "--out", str(out_rand),
    ])
    assert code_rand == 0
    data_rand = json.loads(out_rand.read_text(encoding="utf-8"))
    assert data_rand["preset"] == "random"
    assert data_rand["count"] == 5
    assert data_rand["seed"] == 99
    assert len(data_rand["runs"]) == 5


def test_sensitivity_grid_has_0025_exit_reversal():
    """Issue #110: the 1D exit_reversal axis uses 0.005 steps incl. 0.025."""
    base = BacktestParams()
    grid = generate_sensitivity_grid(base)
    rev_rows = {lbl: p for lbl, p in grid if lbl.startswith("exit_rev=")}
    assert sorted(rev_rows) == [
        "exit_rev=0.010",
        "exit_rev=0.015",
        "exit_rev=0.025",
        "exit_rev=0.030",
    ]
    assert rev_rows["exit_rev=0.025"] == replace(base, exit_reversal=0.025)


def test_filter_sensitivity_grid_only_exit_rev():
    """Issue #110: --only exit_rev keeps Baseline + the 4 reversal rows.

    The 0.020 point is the Baseline row itself (the grid loop skips the
    value equal to base), so the filter covers all five mercy distances.
    """
    base = BacktestParams()
    grid = generate_sensitivity_grid(base)
    filtered = filter_sensitivity_grid(grid, "exit_rev")
    labels = [lbl for lbl, _ in filtered]
    assert labels[0] == "Baseline"
    assert len(filtered) == 5
    assert all(lbl == "Baseline" or lbl.startswith("exit_rev=")
              for lbl in labels)
    covered = sorted({base.exit_reversal}
                     | {p.exit_reversal for _, p in filtered})
    assert covered == pytest.approx([0.010, 0.015, 0.020, 0.025, 0.030])


def test_sensitivity_grid_dead_zone_axes_structural_opt_in():
    """Issue #208: the dead-zone 1D axes are structural and opt-in only.

    Two axes are generated under include_structural: dead_zone_pct (fraction
    of window remaining, default 0.10 held by the Baseline row) and
    dead_zone_sec (absolute seconds). Both must be absent by default, per
    ADR-0003, and both must leave every tuning knob at the baseline value.
    """
    base = BacktestParams()
    default_labels = [lbl for lbl, _ in generate_sensitivity_grid(base)]
    assert not any(lbl.startswith("dead_zone") for lbl in default_labels)

    grid = generate_sensitivity_grid(base, include_structural=True)
    by_label = dict(grid)
    pct_rows = {lbl: p for lbl, p in grid if lbl.startswith("dead_zone_pct=")}
    sec_rows = {lbl: p for lbl, p in grid if lbl.startswith("dead_zone_sec=")}
    # The baseline value (0.10 pct) is the Baseline row itself, so the pct
    # axis spans the remaining six points of the declared range.
    assert sorted(pct_rows) == [
        "dead_zone_pct=0.000",
        "dead_zone_pct=0.050",
        "dead_zone_pct=0.150",
        "dead_zone_pct=0.200",
        "dead_zone_pct=0.300",
    ]
    assert all(p.dead_zone_unit == "pct" for p in pct_rows.values())
    assert sorted(p.dead_zone_val for p in pct_rows.values()) == pytest.approx(
        [0.0, 0.05, 0.15, 0.20, 0.30])
    assert all(p.dead_zone_unit == "sec" for p in sec_rows.values())
    assert all(p.dead_zone_val == float(v) for v, p in
               ((lbl.split("=")[1], p) for lbl, p in sec_rows.items()))
    # The dead-zone rows must touch nothing else: one knob at a time.
    for p in list(pct_rows.values()) + list(sec_rows.values()):
        assert p.offset == base.offset
        assert p.queue_gate == base.queue_gate
        assert p.quote_range == base.quote_range
        assert p.max_pair_cost == base.max_pair_cost
        assert by_label["Baseline"].offset == base.offset


def test_dead_zone_sec_axis_values():
    """Issue #208: the sec axis covers the 5m and 15m 10% tails plus context."""
    grid = generate_sensitivity_grid(BacktestParams(), include_structural=True)
    sec_vals = sorted(float(lbl.split("=")[1]) for lbl, _ in grid
                      if lbl.startswith("dead_zone_sec="))
    # 30s is the 10% tail of a 5m window and 90s of a 15m window — the two
    # readings of the shipped default; the axis must include both plus
    # neighbors on each side and the disabled point.
    assert sec_vals == [0.0, 15.0, 30.0, 60.0, 90.0, 120.0]


def test_filter_sensitivity_grid_only_dead_zone():
    """Issue #208: --only dead_zone keeps Baseline + BOTH dead-zone axes.

    This is the accepted improvement from planning: one run per dataset
    answers the pct-vs-sec question from a single execution environment.
    """
    grid = generate_sensitivity_grid(BacktestParams(), include_structural=True)
    filtered = filter_sensitivity_grid(grid, "dead_zone")
    labels = [lbl for lbl, _ in filtered]
    assert labels[0] == "Baseline"
    assert all(lbl == "Baseline" or lbl.startswith("dead_zone_pct=")
               or lbl.startswith("dead_zone_sec=") for lbl in labels)
    assert any(lbl.startswith("dead_zone_pct=") for lbl in labels)
    assert any(lbl.startswith("dead_zone_sec=") for lbl in labels)
    assert len(filtered) == 12  # 1 baseline + 5 pct + 6 sec


def test_cli_only_rejects_unknown_axis(tmp_path: Path):
    """Issue #110: an unknown --only axis fails fast with exit code 2."""
    dummy_tick_file = tmp_path / "ticks_test.jsonl"
    snap = {
        "cid": "0x1",
        "series": "btc-up-or-down-5m",
        "slug": "btc-up-or-down-5m",
        "duration": 300,
        "ts": 100.0,
        "start_ts": 100.0,
        "up_book": {"best_bid": 0.48, "best_ask": 0.52},
        "down_book": {"best_bid": 0.48, "best_ask": 0.52},
    }
    dummy_tick_file.write_text(json.dumps(snap) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main([str(dummy_tick_file), "--preset", "sensitivity",
              "--only", "bogus"])
    assert exc.value.code == 2


def test_cli_only_exit_rev_end_to_end(tmp_path: Path):
    """Issue #110: --only exit_rev runs Baseline + 4 reversal configs."""
    out_json = tmp_path / "sweep_only.json"
    dummy_tick_file = tmp_path / "ticks_test.jsonl"
    snap = {
        "cid": "0x1",
        "series": "btc-up-or-down-5m",
        "slug": "btc-up-or-down-5m",
        "duration": 300,
        "ts": 100.0,
        "start_ts": 100.0,
        "up_book": {"best_bid": 0.48, "best_ask": 0.52},
        "down_book": {"best_bid": 0.48, "best_ask": 0.52},
    }
    dummy_tick_file.write_text(json.dumps(snap) + "\n", encoding="utf-8")
    code = main([
        str(dummy_tick_file),
        "--preset", "sensitivity",
        "--only", "exit_rev",
        "--out", str(out_json),
    ])
    assert code == 0
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["only"] == "exit_rev"
    assert len(data["runs"]) == 5


def test_cli_only_rejected_with_non_sensitivity_preset(tmp_path: Path):
    """CodeRabbit on #112: --only must not label a grid/random run as isolated."""
    dummy_tick_file = tmp_path / "ticks_test.jsonl"
    snap = {
        "cid": "0x1",
        "series": "btc-up-or-down-5m",
        "slug": "btc-up-or-down-5m",
        "duration": 300,
        "ts": 100.0,
        "start_ts": 100.0,
        "up_book": {"best_bid": 0.48, "best_ask": 0.52},
        "down_book": {"best_bid": 0.48, "best_ask": 0.52},
    }
    dummy_tick_file.write_text(json.dumps(snap) + "\n", encoding="utf-8")
    for preset in ("grid", "random", "assets"):
        with pytest.raises(SystemExit) as exc:
            main([str(dummy_tick_file), "--preset", preset, "--only", "exit_rev"])
        assert exc.value.code == 2
