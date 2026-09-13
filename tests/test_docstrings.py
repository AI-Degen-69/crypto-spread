"""Unit tests for AST-based docstring coverage verification."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


# `research/` holds preserved sweep code, not maintained production code. It was
# committed out of `run/` (gitignored) so a committed findings doc would stop
# citing files any cleanup could delete — and the point of preserving it is that
# it is the *exact* code whose parity against `backtest/engine.py` was verified
# on 6,840 window-checks. Editing it to satisfy a style gate would invalidate
# that claim, so the gate skips it the same way it already skips `run/`.
EXCLUDED_PARTS = (".git", ".agents", ".claude", "tests", "run", "research",
                  "__pycache__", ".pytest_cache")


def test_docstring_coverage():
    """Verify that all non-test modules achieve 100% docstring coverage."""
    files = [
        p for p in ROOT.rglob("*.py")
        if not any(x in p.parts for x in EXCLUDED_PARTS)
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


def test_excluded_paths_do_not_hide_production_code():
    """The `research` exclusion must cover only the preserved sweep lab.

    Adding a directory to `EXCLUDED_PARTS` silently drops it from the coverage
    gate, so the one exclusion that is not obviously non-production is pinned
    here: if `research/` ever grows a second subdirectory, this fails and the
    author has to decide whether it really belongs outside the gate.
    """
    research = ROOT / "research"
    if not research.exists():
        return
    subdirs = sorted(p.name for p in research.iterdir() if p.is_dir())
    assert subdirs == ["sweeps"], (
        f"research/ gained {subdirs}; only research/sweeps/ is exempt from the "
        "docstring gate — narrow the exclusion or document the new directory")
