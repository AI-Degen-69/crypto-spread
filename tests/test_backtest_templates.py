"""Unit and API integration tests for backtest templates."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backtest.templates import (
    delete_template,
    list_templates,
    normalize_name,
    read_template,
    save_template,
    template_path,
    validate_record,
)


def _sample_valid_record(name: str = "test_template") -> dict[str, Any]:
    return {
        "name": name,
        "saved_at": 1727958000.0,
        "run_id": "run-12345",
        "query": "offset=0.02&queue=0.0&pair_cost=0.99",
        "request_args": {
            "offset": 0.02,
            "queue": 0.0,
            "pair_cost": 0.99,
            "exit_default_5m": 0.05,
            "exit_default_15m": 0.05,
            "exit_btc_5m": 0.05,
            "exit_sol_5m": 0.05,
            "exit_reversal": 0.02,
            "size": 5,
            "quote_lo": 0.10,
            "quote_hi": 0.90,
            "entry_delay_sec": 0.0,
            "entry_delay_pct": None,
            "dead_zone_val": 0.10,
            "dead_zone_pct": None,
            "dead_zone_unit": "pct",
            "naked_leg_at_expiry": "close",
            "enable_leg_chase": False,
            "max_start_delay": 0.0,
            "filter_partial": False,
            "limit_windows": 0,
            "series": "",
            "durations": "",
            "file": "",
        },
        "echo": {
            "offset": 0.02,
            "queue": 0.0,
            "pair_cost": 0.99,
            "exit_default_5m": 0.05,
            "exit_default_15m": 0.05,
            "exit_btc_5m": 0.05,
            "exit_sol_5m": 0.05,
            "exit_reversal": 0.02,
            "size": 5,
            "quote_lo": 0.10,
            "quote_hi": 0.90,
            "entry_delay_sec": 0.0,
            "entry_delay_pct": None,
            "dead_zone_pct": None,
            "naked_leg_at_expiry": "close",
            "enable_leg_chase": False,
        },
        "params_dict": {"offset": 0.02, "queue_gate": 0.0},
        "params_hash": "hash-abc1234",
        "dataset": {"file": "", "resolved": ""},
        "scope": {
            "series": "",
            "durations": "",
            "series_tokens": ["BTC", "ETH", "BNB", "SOL", "XRP"],
            "duration_values": [300, 900],
        },
        "summary": {
            "params_hash": "hash-abc1234",
            "n_windows": 50,
            "total_pnl_cents": 120.5,
            "pairs": 40,
            "win_rate": 0.80,
            "selection": "All Files | 5 tokens | 5m, 15m",
        },
    }


def test_normalize_name():
    assert normalize_name("  my_template-1.0 ") == "my_template-1.0"
    assert normalize_name("Template Name With Space") == "Template Name With Space"

    with pytest.raises(ValueError, match="empty"):
        normalize_name("")
    with pytest.raises(ValueError, match="empty"):
        normalize_name("   ")
    with pytest.raises(ValueError, match="path traversal"):
        normalize_name("../bad_path")
    with pytest.raises(ValueError, match="path traversal"):
        normalize_name("bad/name")
    with pytest.raises(ValueError, match="path traversal"):
        normalize_name("bad\\name")
    with pytest.raises(ValueError, match="allowed characters"):
        normalize_name("bad*name")
    with pytest.raises(ValueError, match="allowed characters"):
        normalize_name("bad?name")
    with pytest.raises(ValueError, match="allowed characters"):
        normalize_name("a" * 65)


def test_validate_record():
    rec = _sample_valid_record("valid")
    assert validate_record(rec) == rec

    with pytest.raises(ValueError, match="must be a dict"):
        validate_record("not a dict")  # type: ignore

    # Missing top-level key
    incomplete = dict(rec)
    del incomplete["params_hash"]
    with pytest.raises(ValueError, match="missing required record keys.*params_hash"):
        validate_record(incomplete)

    # Missing summary key
    incomplete_summary = dict(rec)
    incomplete_summary["summary"] = dict(rec["summary"])
    del incomplete_summary["summary"]["win_rate"]
    with pytest.raises(ValueError, match="missing required summary keys.*win_rate"):
        validate_record(incomplete_summary)

    # Non-dict summary
    bad_summary = dict(rec)
    bad_summary["summary"] = None  # type: ignore
    with pytest.raises(ValueError, match="record\\['summary'\\] must be a dict"):
        validate_record(bad_summary)


def test_save_read_overwrite_delete_template(tmp_path: Path):
    rec = _sample_valid_record("alpha")
    
    # Save new
    existed = save_template(tmp_path, "alpha", rec)
    assert existed is False
    assert (tmp_path / "alpha.json").exists()

    # Read back
    loaded = read_template(tmp_path, "alpha")
    assert loaded == rec

    # Overwrite
    rec2 = dict(rec)
    rec2["saved_at"] = 1727959000.0
    existed2 = save_template(tmp_path, "alpha", rec2)
    assert existed2 is True
    assert read_template(tmp_path, "alpha")["saved_at"] == 1727959000.0

    # Delete
    delete_template(tmp_path, "alpha")
    assert not (tmp_path / "alpha.json").exists()

    # Delete missing raises FileNotFoundError
    with pytest.raises(FileNotFoundError):
        delete_template(tmp_path, "alpha")


def test_read_template_missing_and_corrupt(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        read_template(tmp_path, "nonexistent")

    corrupt_file = tmp_path / "corrupt.json"
    corrupt_file.write_text("{broken json", encoding="utf-8")
    with pytest.raises(ValueError, match="malformed template JSON"):
        read_template(tmp_path, "corrupt")

    invalid_rec_file = tmp_path / "invalid.json"
    invalid_rec_file.write_text(json.dumps({"name": "only_name"}), encoding="utf-8")
    with pytest.raises(ValueError, match="missing required record keys"):
        read_template(tmp_path, "invalid")


def test_list_templates(tmp_path: Path):
    # Nonexistent dir returns []
    assert list_templates(tmp_path / "does_not_exist") == []

    # Empty dir returns []
    assert list_templates(tmp_path) == []

    # Populate 3 templates with different timestamps and 1 corrupt file
    rec1 = _sample_valid_record("t1")
    rec1["saved_at"] = 100.0
    rec1["summary"]["total_pnl_cents"] = 10.0

    rec2 = _sample_valid_record("t2")
    rec2["saved_at"] = 200.0
    rec2["summary"]["total_pnl_cents"] = 20.0

    save_template(tmp_path, "t1", rec1)
    save_template(tmp_path, "t2", rec2)

    (tmp_path / "bad.json").write_text("not json", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("text file", encoding="utf-8")

    listed = list_templates(tmp_path)
    assert len(listed) == 3

    # Sorted newest saved_at first, invalid files at the end
    assert listed[0]["name"] == "t2"
    assert listed[0]["saved_at"] == 200.0
    assert listed[0]["total_pnl_cents"] == 20.0
    assert listed[0]["invalid"] is False

    assert listed[1]["name"] == "t1"
    assert listed[1]["saved_at"] == 100.0
    assert listed[1]["total_pnl_cents"] == 10.0
    assert listed[1]["invalid"] is False

    assert listed[2]["name"] == "bad"
    assert listed[2]["invalid"] is True
