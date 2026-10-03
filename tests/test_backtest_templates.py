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



# ---------------------------------------------------------------------------
# API endpoint integration tests (TestClient + monkeypatched dirs)
# ---------------------------------------------------------------------------

@pytest.fixture()
def template_client(tmp_path, monkeypatch):
    """TestClient with BACKTEST_TEMPLATES_DIR and _COMPLETED_RUNS redirected."""
    import server.osc_dash as mod
    monkeypatch.setattr(mod, "BACKTEST_TEMPLATES_DIR", tmp_path / "templates")
    monkeypatch.setattr(mod, "TICKS_DIR", tmp_path / "ticks")
    (tmp_path / "ticks").mkdir()
    # Clear the completed runs registry
    with mod._COMPLETED_RUNS_LOCK:
        mod._COMPLETED_RUNS.clear()

    from starlette.testclient import TestClient
    return TestClient(mod.app)


def _inject_completed_run(run_id: str = "test-run-1"):
    """Inject a fake completed run into the registry for save testing.

    The params_hash is computed through the real `_build_backtest_params` so
    the load endpoint's stale-hash check sees a fresh template (matching
    production, where the worker result carries the real hash).
    """
    import server.osc_dash as mod
    _params, _echo = mod._build_backtest_params(
        offset=0.02, queue=0.0, pair_cost=0.99,
        exit_default_5m=0.05, exit_default_15m=0.05,
        exit_btc_5m=0.05, exit_sol_5m=0.05,
        exit_reversal=0.02, size=5,
        quote_lo=0.10, quote_hi=0.90,
        entry_delay_sec=0.0, entry_delay_pct=None,
        dead_zone_val=0.10, dead_zone_pct=None,
        dead_zone_unit="pct",
        naked_leg_at_expiry="close", enable_leg_chase=False,
    )
    _real_hash = _params.params_hash()
    entry = {
        "run_id": run_id,
        "completed_at": 1727958000.0,
        "query": "offset=0.02&queue=0.0&pair_cost=0.99",
        "request_args": {
            "offset": 0.02, "queue": 0.0, "pair_cost": 0.99,
            "exit_default_5m": 0.05, "exit_default_15m": 0.05,
            "exit_btc_5m": 0.05, "exit_sol_5m": 0.05,
            "exit_reversal": 0.02, "size": 5,
            "quote_lo": 0.10, "quote_hi": 0.90,
            "entry_delay_sec": 0.0, "entry_delay_pct": None,
            "dead_zone_val": 0.10, "dead_zone_pct": None,
            "dead_zone_unit": "pct",
            "naked_leg_at_expiry": "close", "enable_leg_chase": False,
            "max_start_delay": 0.0, "filter_partial": False,
            "limit_windows": 0, "series": "", "durations": "", "file": "",
        },
        "echo": {
            "offset": 0.02, "queue": 0.0, "pair_cost": 0.99,
            "exit_default_5m": 0.05, "exit_default_15m": 0.05,
            "exit_btc_5m": 0.05, "exit_sol_5m": 0.05,
            "exit_reversal": 0.02, "size": 5,
            "quote_lo": 0.10, "quote_hi": 0.90,
            "entry_delay_sec": 0.0, "entry_delay_pct": None,
            "dead_zone_pct": None,
            "naked_leg_at_expiry": "close", "enable_leg_chase": False,
        },
        "params_dict": {"offset": 0.02, "queue_gate": 0.0},
        "params_hash": _real_hash,
        "dataset": {"file": "", "resolved": ""},
        "scope": {
            "series": "", "durations": "",
            "series_tokens": ["BTC", "ETH", "BNB", "SOL", "XRP"],
            "duration_values": [300, 900],
        },
        "summary": {
            "params_hash": _real_hash,
            "n_windows": 50,
            "total_pnl_cents": 120.5,
            "pairs": 40,
            "win_rate": 0.80,
            "selection": "All Files | 5 tokens | 5m, 15m",
        },
    }
    with mod._COMPLETED_RUNS_LOCK:
        mod._COMPLETED_RUNS[run_id] = entry
    return entry


def test_api_template_save_and_list(template_client):
    """POST /api/backtest/templates saves, GET /api/backtest/templates lists."""
    _inject_completed_run("run-save-1")
    resp = template_client.post(
        "/api/backtest/templates",
        json={"name": "my_template", "run_id": "run-save-1"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "saved"
    assert data["name"] == "my_template"
    assert data["overwritten"] is False

    # List should contain the template
    resp2 = template_client.get("/api/backtest/templates")
    assert resp2.status_code == 200
    templates = resp2.json()["templates"]
    assert len(templates) == 1
    assert templates[0]["name"] == "my_template"
    assert templates[0]["invalid"] is False

    # Overwrite
    resp3 = template_client.post(
        "/api/backtest/templates",
        json={"name": "my_template", "run_id": "run-save-1"},
    )
    assert resp3.status_code == 200
    assert resp3.json()["overwritten"] is True


def test_api_template_save_invalid_name(template_client):
    """POST with bad name returns 400."""
    _inject_completed_run("run-1")
    resp = template_client.post(
        "/api/backtest/templates",
        json={"name": "../evil", "run_id": "run-1"},
    )
    assert resp.status_code == 400
    assert "path traversal" in resp.json()["error"]


def test_api_template_save_unknown_run_id(template_client):
    """POST with unknown run_id returns 404."""
    resp = template_client.post(
        "/api/backtest/templates",
        json={"name": "good_name", "run_id": "nonexistent"},
    )
    assert resp.status_code == 404


def test_api_template_delete(template_client):
    """DELETE removes saved template."""
    _inject_completed_run("run-del-1")
    template_client.post(
        "/api/backtest/templates",
        json={"name": "to_delete", "run_id": "run-del-1"},
    )
    resp = template_client.delete("/api/backtest/templates/to_delete")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"

    # Second delete returns 404
    resp2 = template_client.delete("/api/backtest/templates/to_delete")
    assert resp2.status_code == 404


def test_api_template_delete_not_found(template_client):
    """DELETE on missing template returns 404."""
    resp = template_client.delete("/api/backtest/templates/no_such")
    assert resp.status_code == 404


def test_api_template_list_empty(template_client):
    """GET list on empty store returns empty list."""
    resp = template_client.get("/api/backtest/templates")
    assert resp.status_code == 200
    assert resp.json()["templates"] == []


def test_api_template_load_round_trip(template_client):
    """GET single template returns the full saved record."""
    _inject_completed_run("run-load-1")
    saved = template_client.post(
        "/api/backtest/templates",
        json={"name": "loadable", "run_id": "run-load-1"},
    )
    assert saved.status_code == 200

    resp = template_client.get("/api/backtest/templates/loadable")
    assert resp.status_code == 200
    record = resp.json()
    assert record["name"] == "loadable"
    assert record["run_id"] == "run-load-1"
    assert record["request_args"]["offset"] == 0.02
    assert record["scope"]["series_tokens"] == ["BTC", "ETH", "BNB", "SOL", "XRP"]
    assert record["summary"]["n_windows"] == 50


def test_api_template_load_not_found(template_client):
    """GET on missing template returns 404."""
    resp = template_client.get("/api/backtest/templates/no_such")
    assert resp.status_code == 404


def test_api_template_load_invalid_name(template_client):
    """GET with traversal name returns 400."""
    resp = template_client.get("/api/backtest/templates/..%2Fevil")
    assert resp.status_code in (400, 404)


def test_api_template_load_malformed_returns_422(template_client, tmp_path):
    """GET on a corrupt template file returns 422."""
    import server.osc_dash as mod
    tpl_dir = mod.BACKTEST_TEMPLATES_DIR
    tpl_dir.mkdir(parents=True, exist_ok=True)
    (tpl_dir / "broken.json").write_text("{broken json", encoding="utf-8")
    resp = template_client.get("/api/backtest/templates/broken")
    assert resp.status_code == 422


def test_api_template_load_stale_hash_returns_409(template_client):
    """GET on a template whose params_hash drifted returns 409."""
    import json as _json
    import server.osc_dash as mod
    _inject_completed_run("run-stale-1")
    saved = template_client.post(
        "/api/backtest/templates",
        json={"name": "stale_one", "run_id": "run-stale-1"},
    )
    assert saved.status_code == 200
    tpl_file = mod.BACKTEST_TEMPLATES_DIR / "stale_one.json"
    record = _json.loads(tpl_file.read_text(encoding="utf-8"))
    record["params_hash"] = "drifted-hash-that-matches-nothing"
    tpl_file.write_text(_json.dumps(record), encoding="utf-8")
    resp = template_client.get("/api/backtest/templates/stale_one")
    assert resp.status_code == 409
    body = resp.json()
    assert body["stored_hash"] == "drifted-hash-that-matches-nothing"
    assert body["current_hash"]


def test_api_template_delete_invalid_name(template_client):
    """DELETE with traversal name returns 400."""
    resp = template_client.delete("/api/backtest/templates/..%2Fevil")
    assert resp.status_code in (400, 404)


def test_completed_runs_fifo_eviction():
    """_COMPLETED_RUNS evicts oldest entries when exceeding max."""
    import server.osc_dash as mod
    with mod._COMPLETED_RUNS_LOCK:
        mod._COMPLETED_RUNS.clear()
    old_max = mod._COMPLETED_RUNS_MAX
    try:
        mod._COMPLETED_RUNS_MAX = 3
        ctx = {
            "echo": {}, "params_dict": {}, "size": 5,
            "file": "", "source_path_str": "",
            "series": "", "durations": "",
            "series_tokens": [], "duration_values": [],
        }
        result = {"params_hash": "h", "n_windows": 1, "overall": {}}
        ids = []
        for i in range(5):
            rid = mod._record_completed_run(ctx, f"q{i}", {}, result)
            ids.append(rid)
        with mod._COMPLETED_RUNS_LOCK:
            assert len(mod._COMPLETED_RUNS) == 3
            # First two should be evicted
            assert ids[0] not in mod._COMPLETED_RUNS
            assert ids[1] not in mod._COMPLETED_RUNS
            assert ids[2] in mod._COMPLETED_RUNS
            assert ids[4] in mod._COMPLETED_RUNS
    finally:
        mod._COMPLETED_RUNS_MAX = old_max
        with mod._COMPLETED_RUNS_LOCK:
            mod._COMPLETED_RUNS.clear()
