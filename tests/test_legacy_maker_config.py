"""Guard tests verifying that MakerConfig is retired from runtime execution (Issue #426).

`strategy/config.py` is an archival/legacy surface only. Configuration is owned
exclusively by `LiveTraderEngine.__init__` and `BacktestParams`.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_no_live_code_imports_strategy_config():
    """Assert that no production code or tests import strategy.config or MakerConfig."""
    py_files = [
        p
        for p in ROOT.rglob("*.py")
        if not any(
            part.startswith(".")
            or part in ("venv", ".venv", "build", "dist", "__pycache__", "run", "runs")
            for part in p.parts
        )
    ]

    allowed_files = {
        ROOT / "strategy" / "config.py",
        ROOT / "tests" / "test_legacy_maker_config.py",
    }

    violating_imports: list[str] = []
    unparsable: list[str] = []

    for file_path in py_files:
        if file_path in allowed_files:
            continue
        try:
            tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
        except (SyntaxError, UnicodeDecodeError) as exc:
            unparsable.append(f"{file_path.relative_to(ROOT)}: {exc}")
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "strategy.config" or alias.name.startswith("strategy.config."):
                        violating_imports.append(f"{file_path.relative_to(ROOT)}:{node.lineno}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module == "strategy.config" or (node.module and node.module.startswith("strategy.config.")):
                    violating_imports.append(f"{file_path.relative_to(ROOT)}:{node.lineno}: from {node.module} import ...")
                elif node.module == "strategy":
                    for alias in node.names:
                        if alias.name in ("config", "MakerConfig"):
                            violating_imports.append(f"{file_path.relative_to(ROOT)}:{node.lineno}: from strategy import {alias.name}")

    assert not unparsable, f"Could not scan files: {unparsable}"
    assert not violating_imports, f"Found active imports of strategy.config: {violating_imports}"


def test_strategy_config_does_not_expose_load():
    """`load()` must not exist in `strategy.config`."""
    from strategy import config

    assert not hasattr(config, "load"), "strategy.config must not expose load()"


def test_strategy_config_docstrings_name_owners_and_warn():
    """Module and MakerConfig docstrings must warn and point to real configuration owners."""
    from strategy import config

    mod_doc = config.__doc__ or ""
    assert "LiveTraderEngine" in mod_doc, "Module docstring must name LiveTraderEngine"
    assert "BacktestParams" in mod_doc, "Module docstring must name BacktestParams"
    assert "legacy" in mod_doc.lower() or "archival" in mod_doc.lower() or "retired" in mod_doc.lower()

    cls_doc = config.MakerConfig.__doc__ or ""
    assert "LiveTraderEngine" in cls_doc, "MakerConfig docstring must name LiveTraderEngine"
    assert "BacktestParams" in cls_doc, "MakerConfig docstring must name BacktestParams"
    assert "legacy" in cls_doc.lower() or "retired" in cls_doc.lower()


def test_live_engine_default_pair_cost_is_099_not_0995():
    """Verify that the trading engine default is 0.99 and not the historical 0.995."""
    from strategy.live_trader import LiveTraderEngine

    engine = LiveTraderEngine(load_persisted=False)
    assert engine.max_pair_cost == 0.99, f"Expected 0.99, got {engine.max_pair_cost}"
