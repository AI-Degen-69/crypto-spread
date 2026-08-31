"""Tests for the 4-tab SPA dashboard and API endpoints."""
from fastapi.testclient import TestClient
from server.osc_dash import app

client = TestClient(app)


def test_root_returns_4tab_spa():
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text
    assert "tab-btn-live" in html
    assert "tab-btn-backtest" in html
    assert "tab-btn-summary" in html
    assert "tab-btn-ticks" in html
    assert "tab-live" in html
    assert "tab-backtest" in html
    assert "tab-summary" in html
    assert "tab-ticks" in html
    assert "switchTab" in html


def test_api_oscillation():
    response = client.get("/api/oscillation")
    assert response.status_code == 200
    data = response.json()
    assert "summary" in data
    assert "windows" in data
    assert "live" in data
    assert "goals" in data


def test_api_ticks_manifest():
    response = client.get("/api/ticks/manifest")
    assert response.status_code == 200
    data = response.json()
    assert "files" in data


def test_api_collector_status():
    response = client.get("/api/collector/status")
    assert response.status_code == 200
    data = response.json()
    assert "running" in data


def test_api_backtest_fake_file():
    response = client.get("/api/backtest?file=fake_round.jsonl")
    assert response.status_code == 200
    data = response.json()
    assert "params_hash" in data
    assert "overall" in data
    assert "per_series" in data
    assert "equity_curve" in data
    assert "trades_sample" in data
    assert data["n_windows"] == 4
