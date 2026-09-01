"""Tests for automated backtest parameter sweep engine (scripts/sweep_backtest.py)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from backtest.engine import BacktestParams, WindowResult
from scripts.sweep_backtest import (
    SweepRunResult,
    compute_metrics,
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
    )


def test_compute_metrics_empty():
    p = BacktestParams()
    res = compute_metrics([], p, label="empty_test")
    assert res.n_windows == 0
    assert res.total_pnl_cents == 0.0
    assert res.win_rate == 0.0
    assert res.param_label == "empty_test"


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
    grid = generate_sensitivity_grid(base)
    assert len(grid) > 10
    labels = [label for label, _params in grid]
    assert "Baseline" in labels
    assert any("offset=" in label for label in labels)
    assert any("queue=" in label for label in labels)
    assert any("exit_5m=" in label for label in labels)


def test_generate_joint_grid():
    grid = generate_joint_grid(
        offsets=[0.015, 0.020],
        queues=[0.0, 50.0],
        exit_5ms=[0.08, 0.12],
        exit_reversals=[0.02],
    )
    # 2 * 2 * 2 * 1 = 8 combinations
    assert len(grid) == 8
    label, p = grid[0]
    assert isinstance(p, BacktestParams)
    assert "off=" in label


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
