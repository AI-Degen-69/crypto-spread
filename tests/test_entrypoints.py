"""Unit tests for execution entrypoint governance, deprecation guards, and ownership."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_canonical_trading_engine_importable():
    """Verify strategy.live_trader.LiveTraderEngine is importable as the canonical engine."""
    from strategy.live_trader import LiveTraderEngine
    assert LiveTraderEngine is not None
    engine = LiveTraderEngine(load_persisted=False)
    assert engine.mode in ("paper", "live")


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
    assert "Removed" in agents_md


def test_ten_bankrolls_deleted():
    """Verify ten-bankrolls directory is completely removed from the filesystem."""
    ten_bankrolls_dir = ROOT / "ten-bankrolls"
    assert not ten_bankrolls_dir.exists(), "ten-bankrolls/ should not exist in the repository"


def test_bot_directory_deleted():
    """Verify legacy bot directory is completely removed from the filesystem."""
    bot_dir = ROOT / "bot"
    assert not bot_dir.exists(), "bot/ should not exist in the repository"
