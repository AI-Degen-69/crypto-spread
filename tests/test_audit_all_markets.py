"""Unit tests for scripts/audit_all_markets.py orchestrator."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from scripts.monitor_stream_latency import LatencyAuditor
from scripts.audit_all_markets import (
    parse_args,
    get_series_to_audit,
    format_comparison_table,
    run_all_audits,
    save_audit_artifact,
)


def test_parse_args_defaults():
    """Verify default CLI arguments for audit_all_markets."""
    args = parse_args([])
    assert args.duration == 60.0
    assert args.ticks == 0
    assert pytest.approx(args.threshold, 0.0001) == 0.001
    assert args.tokens is None
    assert args.durations is None
    assert args.output is None
    assert args.quiet is False


def test_parse_args_custom():
    """Verify custom CLI argument parsing for audit_all_markets."""
    args = parse_args([
        "--duration", "30",
        "--ticks", "10",
        "--threshold", "0.002",
        "--tokens", "BTC", "ETH",
        "--durations", "300",
        "--output", "run/custom_audit.json",
        "--quiet",
    ])
    assert args.duration == 30.0
    assert args.ticks == 10
    assert pytest.approx(args.threshold, 0.0001) == 0.002
    assert args.tokens == ["BTC", "ETH"]
    assert args.durations == [300]
    assert args.output == "run/custom_audit.json"
    assert args.quiet is True


def test_get_series_to_audit_default():
    """Verify default returns all 10 series in canonical order."""
    series = get_series_to_audit()
    assert len(series) == 10
    slugs = [s[0] for s in series]
    assert "btc-up-or-down-5m" in slugs
    assert "bnb-up-or-down-5m" in slugs
    assert "btc-up-or-down-15m" in slugs


def test_get_series_to_audit_filtered():
    """Verify token and duration filtering works via strategy.series."""
    series = get_series_to_audit(tokens=["BTC", "ETH"], durations=[300])
    assert len(series) == 2
    slugs = [s[0] for s in series]
    assert slugs == ["btc-up-or-down-5m", "eth-up-or-down-5m"]


def test_format_comparison_table():
    """Verify comparison table generates expected columns and annotations."""
    results = [
        {
            "series": "btc-up-or-down-5m",
            "token": "BTC",
            "duration_sec": 300,
            "duration_label": "5m",
            "transport": "RTDS",
            "summary": {
                "total_shocks": 4,
                "reaction_count": 4,
                "reaction_rate_pct": 100.0,
                "min_latency_ms": 450.0,
                "median_latency_ms": 1850.0,
                "mean_latency_ms": 2120.0,
                "p95_latency_ms": 4300.0,
                "min_drift_pct": 0.0010,
                "median_drift_pct": 0.0015,
                "mean_drift_pct": 0.0018,
                "p95_drift_pct": 0.0025,
            },
        },
        {
            "series": "bnb-up-or-down-5m",
            "token": "BNB",
            "duration_sec": 300,
            "duration_label": "5m",
            "transport": "REST",
            "summary": {
                "total_shocks": 2,
                "reaction_count": 2,
                "reaction_rate_pct": 100.0,
                "min_latency_ms": 1200.0,
                "median_latency_ms": 2500.0,
                "mean_latency_ms": 2600.0,
                "p95_latency_ms": 3800.0,
                "min_drift_pct": 0.0012,
                "median_drift_pct": 0.0016,
                "mean_drift_pct": 0.0017,
                "p95_drift_pct": 0.0022,
            },
        },
        {
            "series": "sol-up-or-down-5m",
            "token": "SOL",
            "duration_sec": 300,
            "duration_label": "5m",
            "transport": "RTDS",
            "summary": {
                "total_shocks": 0,
                "reaction_count": 0,
                "reaction_rate_pct": 0.0,
                "min_latency_ms": 0.0,
                "median_latency_ms": 0.0,
                "mean_latency_ms": 0.0,
                "p95_latency_ms": 0.0,
                "min_drift_pct": 0.0,
                "median_drift_pct": 0.0,
                "mean_drift_pct": 0.0,
                "p95_drift_pct": 0.0,
            },
        },
    ]

    table = format_comparison_table(results)
    assert "CROSS-MARKET EMPIRICAL LATENCY AUDIT BENCHMARK" in table
    assert "BTC" in table
    assert "BNB" in table
    assert "SOL" in table
    assert "RTDS" in table
    assert "REST" in table
    assert "1850.0" in table
    assert "2500.0" in table
    assert "--" in table  # Zero reaction entries format as --
    assert "BNB REST fallback path" in table


def test_run_all_audits_and_save_artifact(tmp_path):
    """Verify orchestration loop runs across series and saves JSON artifact."""
    mock_auditor_btc = LatencyAuditor()
    mock_auditor_btc.events = [
        {"clob_reacted": True, "reaction_time_sec": 1.5, "reaction_time_ms": 1500.0, "drift_pct": 0.0015},
    ]

    out_file = tmp_path / "test_audit.json"

    with patch("scripts.audit_all_markets.run_monitor", return_value=mock_auditor_btc):
        results = run_all_audits(
            tokens=["BTC"],
            durations=[300],
            duration=1.0,
            ticks=1,
            threshold=0.001,
            quiet=True,
        )

        assert len(results) == 1
        assert results[0]["token"] == "BTC"
        assert results[0]["duration_label"] == "5m"
        assert results[0]["summary"]["total_shocks"] == 1
        assert results[0]["summary"]["median_latency_ms"] == 1500.0

        path = save_audit_artifact(results, threshold=0.001, output_path=str(out_file))
        assert Path(path).exists()

        data = json.loads(Path(path).read_text(encoding="utf-8"))
        assert "created_at" in data
        assert data["threshold"] == 0.001
        assert len(data["series_audits"]) == 1
        assert data["series_audits"][0]["token"] == "BTC"
