"""Tests for scripts.run_overnight_sweep."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from backtest.engine import BacktestParams
from scripts.run_overnight_sweep import (
    ALL_SERIES,
    CSV_FIELDNAMES,
    PARAM_SPACE,
    TARGET_ASSETS,
    create_baseline_params,
    evaluate_configuration,
    generate_candidates,
    generate_summary_report,
    init_csv_file,
    log_run_to_csv,
    run_overnight_sweep,
)


def test_create_baseline_params():
    """Verify baseline parameter values adhere to Issue #442 specification."""
    base = create_baseline_params()
    assert base.offset == 0.02
    assert base.queue_gate == 50.0
    assert base.quote_shares == 120
    assert base.entry_delay_sec == 0.0
    assert base.entry_delay_pct is None
    assert base.enable_leg_chase is False
    assert base.exit_reversal == 0.02
    assert base.exit_thresh_by_slug["default_5m"] == 0.05
    assert base.exit_thresh_by_slug["default_15m"] == 0.05
    assert base.exit_thresh_by_slug["btc-up-or-down-5m"] == 0.05


def test_generate_candidates_seed_determinism():
    """Verify candidate generation is deterministic with fixed seed and starts with Baseline."""
    c1 = generate_candidates(n_iterations=10, seed=42)
    c2 = generate_candidates(n_iterations=10, seed=42)
    assert len(c1) == len(c2)
    assert c1[0][0] == "Baseline"
    assert c1[0][1] == create_baseline_params()
    for (l1, p1), (l2, p2) in zip(c1, c2):
        assert l1 == l2
        assert p1 == p2


def test_generate_candidates_param_space_bounds():
    """Verify sampled parameters are strictly drawn from PARAM_SPACE."""
    candidates = generate_candidates(n_iterations=20, seed=123)
    for _label, p in candidates:
        assert p.offset in PARAM_SPACE["offset"]
        assert p.queue_gate in PARAM_SPACE["queue_depth"]
        assert p.quote_shares in PARAM_SPACE["share_size"]
        assert p.enable_leg_chase in PARAM_SPACE["leg_chase"]
        assert p.exit_reversal in PARAM_SPACE["reversal_buffer"]


def test_csv_init_and_incremental_logging(tmp_path):
    """Verify CSV file initialization and incremental row logging."""
    csv_file = tmp_path / "test_results.csv"
    init_csv_file(csv_file)
    assert csv_file.exists()

    with open(csv_file, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        assert header == CSV_FIELDNAMES

    row = {
        "run_id": "RUN_001",
        "timestamp": "2026-10-05T00:00:00Z",
        "label": "Baseline",
        "asset": "BTC",
        "series": "btc-5m+15m",
        "offset": 0.02,
        "queue_gate": 50.0,
        "quote_shares": 120,
        "entry_delay_sec": 0.0,
        "entry_delay_pct": 0.0,
        "enable_leg_chase": False,
        "exit_reversal": 0.02,
        "exit_stops_json": "{}",
        "n_windows": 10,
        "net_pnl_usd": 15.50,
        "win_rate_pct": 70.0,
        "total_trades": 8,
        "max_drawdown_usd": 2.10,
        "profit_factor": 2.5,
        "sharpe_proxy": 1.8,
    }
    log_run_to_csv(csv_file, row)

    with open(csv_file, "r", encoding="utf-8") as f:
        lines = list(csv.DictReader(f))
        assert len(lines) == 1
        assert lines[0]["run_id"] == "RUN_001"
        assert lines[0]["asset"] == "BTC"


def test_evaluate_configuration_with_mock_window():
    """Verify evaluation generates overall summary and 5 per-asset breakdown rows."""
    params = create_baseline_params()
    # Mock window snaps
    snap1 = {
        "cid": "test_cid_1",
        "series": "btc-up-or-down-5m",
        "slug": "btc-up-or-down-5m",
        "duration": 300,
        "ts": 1700000000.0,
        "bids": [[0.48, 100]],
        "asks": [[0.52, 100]],
        "best_bid": 0.48,
        "best_ask": 0.52,
        "mid": 0.50,
    }
    snap2 = dict(snap1, ts=1700000010.0, best_bid=0.49, best_ask=0.51, mid=0.50)
    windows = [("test_cid_1", [snap1, snap2])]

    overall, assets = evaluate_configuration("RUN_001", "Baseline", params, windows)

    assert overall["asset"] == "ALL"
    assert overall["series"] == "ALL_10_SERIES"
    assert overall["n_windows"] == 1
    assert len(assets) == len(TARGET_ASSETS)
    btc_row = next(a for a in assets if a["asset"] == "BTC")
    assert btc_row["n_windows"] == 1
    eth_row = next(a for a in assets if a["asset"] == "ETH")
    assert eth_row["n_windows"] == 0


def test_generate_summary_report(tmp_path):
    """Verify markdown summary report generation and table ranking."""
    csv_file = tmp_path / "results.csv"
    summary_md = tmp_path / "summary.md"
    init_csv_file(csv_file)

    # Log Baseline
    base_row = {
        "run_id": "RUN_001",
        "timestamp": "2026-10-05T00:00:00Z",
        "label": "Baseline",
        "asset": "ALL",
        "series": "ALL_10_SERIES",
        "offset": 0.02,
        "queue_gate": 50.0,
        "quote_shares": 120,
        "entry_delay_sec": 0.0,
        "entry_delay_pct": 0.0,
        "enable_leg_chase": False,
        "exit_reversal": 0.02,
        "exit_stops_json": "{}",
        "n_windows": 100,
        "net_pnl_usd": 50.00,
        "win_rate_pct": 60.0,
        "total_trades": 80,
        "max_drawdown_usd": 10.00,
        "profit_factor": 1.5,
        "sharpe_proxy": 1.2,
    }
    log_run_to_csv(csv_file, base_row)

    # Log an optimized run
    opt_row = dict(base_row, run_id="RUN_002", label="Optimized_01", net_pnl_usd=120.00, win_rate_pct=75.0, max_drawdown_usd=8.00, profit_factor=2.8)
    log_run_to_csv(csv_file, opt_row)

    # Asset rows
    for asset in TARGET_ASSETS:
        a_row = dict(base_row, run_id="RUN_001", asset=asset, series=f"{asset.lower()}-5m+15m", net_pnl_usd=10.0)
        log_run_to_csv(csv_file, a_row)

    md = generate_summary_report(csv_file, summary_md)
    assert summary_md.exists()
    assert "# Overnight Simulation Summary" in md
    assert "Baseline Performance Benchmark" in md
    assert "Top 3 Configurations Overall" in md
    assert "Top 3 Configurations per Asset" in md
    assert "Optimized_01" in md
