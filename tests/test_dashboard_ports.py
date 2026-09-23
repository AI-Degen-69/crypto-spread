"""Regression: single canonical dashboard port (issue #313).

Digit-lookaround matching on purpose: a naive ``8802`` substring scan
false-matches research floats such as ``0.008802...`` in
``research/sweeps/*.json``. Port literals are always bounded by non-digits
(``:8802``, ``"8802"``, ``= 8802``), research decimals are not.
"""
import re
from pathlib import Path

from server.ports import DASHBOARD_HOST, DASHBOARD_PORT, DASHBOARD_URL

ROOT = Path(__file__).resolve().parent.parent
PORT_LITERAL_RE = re.compile(r"(?<!\d)8802(?!\d)")

# In-scope paths per issue #313 acceptance criteria.
# Note: this gate file itself is excluded from the scan — it must spell the
# banned literal to forbid it (pattern + vectors below).
SCOPED_FILES = [
    ROOT / "server" / "osc_dash.py",
    ROOT / "server" / "ports.py",
    ROOT / "scripts" / "crypto-spread-menu.ps1",
    ROOT / "scripts" / "observe_paper.py",
    ROOT / "tests" / "test_crypto_spread_menu.py",
    ROOT / "AGENTS.md",
    ROOT / "README.md",
    ROOT / "docs" / "operations.md",
]


def test_canonical_port_value():
    assert DASHBOARD_PORT == 5515
    assert DASHBOARD_HOST == "127.0.0.1"
    assert DASHBOARD_URL == "http://127.0.0.1:5515"


def test_no_8802_port_literal_in_scope():
    offenders = []
    for path in SCOPED_FILES:
        text = path.read_text(encoding="utf-8", errors="replace")
        if PORT_LITERAL_RE.search(text):
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"stale :8802 port literal in {offenders}"


def test_port_pattern_ignores_research_decimals():
    # Guard the guard: research floats must never trip the gate.
    assert not PORT_LITERAL_RE.search('"pair_rate": 0.008802816901408451')
    assert PORT_LITERAL_RE.search("http://127.0.0.1:8802/api/backtest")
    assert PORT_LITERAL_RE.search("$Port = 8802")
