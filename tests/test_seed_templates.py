"""Unit and API integration tests for seed preset templates (issue #445)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backtest.templates import read_template, validate_record


def _seed_path() -> Path:
    return Path(__file__).resolve().parent.parent / "backtest" / "seed_templates" / "overnight-majors-btc-eth.json"


def _load_seed() -> dict:
    return json.loads(_seed_path().read_text(encoding="utf-8"))


def test_majors_seed_request_args_match_measured_run():
    """Seed knobs equal the measured RUN_0153 sweep run (no invented values)."""
    args = _load_seed()["request_args"]
    assert args["offset"] == 0.10
    assert args["queue"] == 5.0
    assert args["pair_cost"] == 0.99
    assert args["exit_default_5m"] == 0.30
    assert args["exit_default_15m"] == 0.045
    assert args["exit_btc_5m"] == 0.30
    assert args["exit_reversal"] == 0.02
    assert args["size"] == 75
    assert args["entry_delay_sec"] == 60.0
    assert args["enable_leg_chase"] is False
    assert args["series"] == "BTC,ETH"


def test_majors_seed_summary_matches_measured_scope():
    """Summary equals the combined measured BTC+ETH scope (2011 windows, +$15.00)."""
    summary = _load_seed()["summary"]
    assert summary["n_windows"] == 2011
    assert summary["total_pnl_cents"] == 1500.00
    assert summary["pairs"] == 1
    assert summary["win_rate"] == 0.0005


def test_materialize_returns_none_on_garbage():
    """Unvalidatable seeds fail loud (None), never half-written records."""
    import server.osc_dash as mod

    assert mod._materialize_seed_template({}) is None
    assert mod._materialize_seed_template([]) is None
    assert mod._materialize_seed_template({"name": "x", "request_args": {"series": "bcc-nope"}}) is None
    bare = mod._materialize_seed_template({"name": "x"})
    assert bare["params_dict"]["offset"] == 0.02


def test_ensure_materializes_valid_record(tmp_path):
    """Copied record validates and its hash equals a fresh rebuild (drift guard)."""
    import server.osc_dash as mod

    seeded = mod.ensure_seed_templates(tmp_path / "templates")
    assert seeded == ["overnight-majors-btc-eth"]
    record = read_template(tmp_path / "templates", "overnight-majors-btc-eth")
    validate_record(record)
    params, _echo = mod._build_backtest_params(
        offset=record["request_args"]["offset"],
        queue=record["request_args"]["queue"],
        pair_cost=record["request_args"]["pair_cost"],
        exit_default_5m=record["request_args"]["exit_default_5m"],
        exit_default_15m=record["request_args"]["exit_default_15m"],
        exit_btc_5m=record["request_args"]["exit_btc_5m"],
        exit_sol_5m=record["request_args"]["exit_sol_5m"],
        exit_reversal=record["request_args"]["exit_reversal"],
        size=record["request_args"]["size"],
        quote_lo=record["request_args"]["quote_lo"],
        quote_hi=record["request_args"]["quote_hi"],
        entry_delay_sec=record["request_args"]["entry_delay_sec"],
        entry_delay_pct=record["request_args"]["entry_delay_pct"],
        dead_zone_val=record["request_args"]["dead_zone_val"],
        dead_zone_pct=record["request_args"]["dead_zone_pct"],
        dead_zone_unit=record["request_args"]["dead_zone_unit"],
        naked_leg_at_expiry=record["request_args"]["naked_leg_at_expiry"],
        enable_leg_chase=record["request_args"]["enable_leg_chase"],
    )
    assert record["params_hash"] == params.params_hash()
    assert record["scope"]["series_tokens"] == ["btc", "eth"]


def test_ensure_never_overwrites(tmp_path):
    """Operator-saved templates of the same name always win over seeds."""
    import server.osc_dash as mod

    target = tmp_path / "templates"
    assert mod.ensure_seed_templates(target) == ["overnight-majors-btc-eth"]
    tampered = target / "overnight-majors-btc-eth.json"
    tampered.write_text('{"operator": true}', encoding="utf-8")
    assert mod.ensure_seed_templates(target) == []
    assert json.loads(tampered.read_text(encoding="utf-8")) == {"operator": True}


def test_seeded_preset_loads_200(tmp_path, monkeypatch):
    """Seeded preset passes the load endpoint's registry validation."""
    import server.osc_dash as mod

    monkeypatch.setattr(mod, "BACKTEST_TEMPLATES_DIR", tmp_path / "templates")
    monkeypatch.setattr(mod, "TICKS_DIR", tmp_path / "ticks")
    (tmp_path / "ticks").mkdir()
    assert mod.ensure_seed_templates(tmp_path / "templates") == ["overnight-majors-btc-eth"]

    from starlette.testclient import TestClient

    resp = TestClient(mod.app).get("/api/backtest/templates/overnight-majors-btc-eth")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["params_dict"]["offset"] == 0.10
    assert body["params_dict"]["enable_leg_chase"] is False
