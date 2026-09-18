"""Unit tests for execution entrypoint governance, deprecation guards, and ownership."""
from __future__ import annotations

import subprocess
import sys
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_canonical_trading_engine_importable():
    """Verify strategy.live_trader.LiveTraderEngine is importable as the canonical engine."""
    from strategy.live_trader import LiveTraderEngine
    assert LiveTraderEngine is not None
    engine = LiveTraderEngine(load_persisted=False)
    assert engine.mode in ("paper", "live")


def test_paper_bot_deprecation_warning():
    """Verify importing bot.paper_bot emits a DeprecationWarning."""
    with pytest.warns(DeprecationWarning, match="bot.paper_bot is deprecated"):
        if "bot.paper_bot" in sys.modules:
            del sys.modules["bot.paper_bot"]
        import bot.paper_bot  # noqa: F401


def test_paper_bot_cli_blocks_live():
    """Verify invoking bot/paper_bot.py with --live fails fast with exit code 1."""
    cmd = [sys.executable, str(ROOT / "bot" / "paper_bot.py"), "--live"]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    assert res.returncode == 1
    assert "Live execution is disabled in deprecated bot/paper_bot.py" in res.stderr


def test_paper_bot_cli_help():
    """Verify invoking bot/paper_bot.py with --help succeeds."""
    cmd = [sys.executable, str(ROOT / "bot" / "paper_bot.py"), "--help"]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    assert res.returncode == 0
    assert "SPREAD-2 Paper Bot" in res.stdout


def test_agents_md_documents_entrypoints():
    """Verify AGENTS.md explicitly documents all entrypoints and their status."""
    agents_md = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "## Execution Entrypoints & Ownership" in agents_md
    assert "LiveTraderEngine" in agents_md
    assert "scripts/shadow_ev_pilot.py" in agents_md
    assert "server/osc_dash.py" in agents_md
    assert "scripts/collect_ticks.py" in agents_md
    assert "scripts/backtest.py" in agents_md
    assert "bot/paper_bot.py" in agents_md
    assert "ten-bankrolls/" in agents_md
    assert "Canonical" in agents_md
    assert "Deprecated" in agents_md
    assert "Removed" in agents_md


def test_ten_bankrolls_deleted():
    """Verify ten-bankrolls directory is completely removed from the filesystem."""
    ten_bankrolls_dir = ROOT / "ten-bankrolls"
    assert not ten_bankrolls_dir.exists(), "ten-bankrolls/ should not exist in the repository"
