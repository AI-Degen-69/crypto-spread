"""Tests for Control Center menu script (csm) structure, PID registry schema, and telemetry utilities."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "scripts" / "crypto-spread-menu.ps1"
RUN_DIR = ROOT / "run"
PID_FILE = RUN_DIR / "dash.pids.json"


def test_menu_script_exists():
    """Verify scripts/crypto-spread-menu.ps1 exists and contains required actions."""
    assert SCRIPT_PATH.exists(), f"Menu script missing at {SCRIPT_PATH}"
    content = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "Show-SystemStatus" in content
    assert "Host-Dashboard" in content
    assert "Stop-DashboardProcess" in content
    assert "Start-PriceMonitor" in content
    assert "dash.pids.json" in content
    assert "8802" in content


def test_pid_registry_format(tmp_path):
    """Test reading and writing PID registry JSON schema for dash.pids.json."""
    fake_pid_file = tmp_path / "dash.pids.json"
    data = {
        "strategy": "crypto-spread",
        "saved": "2026-09-06T04:00:00.000Z",
        "dash": {
            "pid": 12345,
            "started_ticks": 638600000000000000,
            "started": "2026-09-06T04:00:00.000Z",
            "port": 8802
        }
    }
    fake_pid_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    
    read_data = json.loads(fake_pid_file.read_text(encoding="utf-8"))
    assert read_data["strategy"] == "crypto-spread"
    assert read_data["dash"]["pid"] == 12345
    assert read_data["dash"]["started_ticks"] == 638600000000000000
    assert read_data["dash"]["port"] == 8802


def test_menu_script_status_execution():
    """Test executing scripts/crypto-spread-menu.ps1 status via pwsh -NoProfile."""
    cmd = ["pwsh", "-NoProfile", "-Command", f"$env:OutputEncoding=[System.Text.Encoding]::UTF8; & '{SCRIPT_PATH}' status"]
    res = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, encoding="utf-8", errors="replace", timeout=15)
    assert res.returncode == 0, f"Script failed with stdout={res.stdout} stderr={res.stderr}"
    assert "CRYPTO SPREAD" in res.stdout
    assert "8802" in res.stdout
