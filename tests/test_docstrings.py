"""Unit tests for AST-based docstring coverage verification."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_docstring_coverage():
    """Verify that all non-test modules achieve 100% docstring coverage."""
    files = [
        p for p in ROOT.rglob("*.py")
        if not any(x in p.parts for x in (".git", "tests", "run", "__pycache__", ".pytest_cache"))
    ]
    missing = []
    total = 0

    for f in sorted(files):
        tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                total += 1
                doc = ast.get_docstring(node)
                if not doc:
                    missing.append((str(f.relative_to(ROOT)), node.name, getattr(node, "lineno", 0)))

    assert total > 0
    assert missing == [], f"Missing docstrings ({len(missing)}/{total}): {missing}"
