"""Unit tests for AST-based docstring coverage verification."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


EXCLUDED_PARTS = (".git", ".agents", ".claude", "tests", "run",
                  "__pycache__", ".pytest_cache")

# `research/sweeps/` holds preserved sweep code, not maintained production code.
# It was committed out of `run/` (gitignored) so a committed findings doc would
# stop citing files any cleanup could delete — and the point of preserving it is
# that it is the *exact* code whose parity against `backtest/engine.py` was
# verified on 6,840 window-checks. Editing it to satisfy a style gate would
# invalidate that claim, so the gate skips it the same way it skips `run/`.
#
# Scoped to this one directory, not to `research/` as a whole: a plain
# "research" entry in EXCLUDED_PARTS would also exempt any module dropped
# directly at `research/*.py`, which is production code by any other name.
EXCLUDED_DIRS = (ROOT / "research" / "sweeps",)


def _is_excluded(path: Path) -> bool:
    """True when `path` is outside the docstring gate's scope."""
    if any(x in path.parts for x in EXCLUDED_PARTS):
        return True
    return any(d in path.parents for d in EXCLUDED_DIRS)


def test_docstring_coverage():
    """Verify that all non-test modules achieve 100% docstring coverage."""
    files = [p for p in ROOT.rglob("*.py") if not _is_excluded(p)]
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


def test_exclusion_covers_only_the_preserved_sweep_lab():
    """Nothing under `research/` escapes the gate except `research/sweeps/`.

    An exclusion is a hole in a quality gate, so this asserts on the predicate
    itself rather than on the directory listing: a module dropped at
    `research/anything.py`, or a new `research/<other>/mod.py`, must still be
    covered. Checking paths that do not exist on disk is the point — the test
    has to fail *before* someone adds such a file, not after.
    """
    assert _is_excluded(ROOT / "research" / "sweeps" / "ev_lab.py")
    assert _is_excluded(ROOT / "research" / "sweeps" / "nested" / "mod.py")
    assert not _is_excluded(ROOT / "research" / "new_module.py")
    assert not _is_excluded(ROOT / "research" / "other" / "mod.py")
    assert not _is_excluded(ROOT / "strategy" / "live_trader.py")


def test_gate_actually_scans_production_code():
    """An exclusion bug that emptied the file list would make the gate vacuous."""
    files = [p for p in ROOT.rglob("*.py") if not _is_excluded(p)]
    names = {p.name for p in files}
    for expected in ("live_trader.py", "osc_dash.py", "backtest.py"):
        assert expected in names, f"{expected} fell out of the docstring gate"
