"""Smoke tests for scripts/collect_ticks.py — no live network.

Verifies the file/manifest schema, slate, and CLI arg parsing.
"""
from __future__ import annotations
import gzip
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "collect_ticks.py"


def _build_snap() -> dict:
    return {
        "ts": 1700000000.0, "iso": "2023-11-14T22:13:20+00:00",
        "series": "btc-up-or-down-5m", "duration": 300, "label": "BTC 5m",
        "cid": "0xDEAD", "slug": "btc-updown-5m-1700000000",
        "start_ts": 1700000000.0, "end_ts": 1700000300.0, "t_rem": 300.0,
        "up_book": {"bids": {"0.49": 100.0}, "asks": {"0.51": 100.0},
                    "best_bid": 0.49, "best_ask": 0.51, "malformed": 0,
                    "token_id": "0xA"},
        "down_book": {"bids": {"0.49": 100.0}, "asks": {"0.51": 100.0},
                      "best_bid": 0.49, "best_ask": 0.51, "malformed": 0,
                      "token_id": "0xB"},
        "tape_delta": [],
        "mid": 0.50, "touch_pair": 1.02, "resting_pair": 0.96,
        "queue_up": 0.0, "queue_down": 0.0, "err": None,
    }


def test_ticks_file_roundtrip(tmp_path: Path):
    """Write a synthetic tick file and read it back via iter_ticks."""
    from backtest import iter_ticks
    f = tmp_path / "ticks_2026-08-29.jsonl"
    line = json.dumps(_build_snap()) + "\n"
    f.write_text(line, encoding="utf-8")
    out = list(iter_ticks(f))
    assert len(out) == 1
    assert out[0]["cid"] == "0xDEAD"
    assert out[0]["up_book"]["bids"]["0.49"] == 100.0

def test_gzip_roundtrip(tmp_path: Path):
    from backtest import iter_ticks
    f = tmp_path / "ticks_2026-08-29.jsonl.gz"
    with gzip.open(f, "wt", encoding="utf-8") as gz:
        gz.write(json.dumps(_build_snap()) + "\n")
    out = list(iter_ticks(f))
    assert len(out) == 1

def test_module_imports_cleanly():
    import importlib
    mod = importlib.import_module("scripts.collect_ticks")
    assert hasattr(mod, "poll_once")
    assert hasattr(mod, "SERIES") or mod.SERIES is not None
    # SERIES comes from strategy.series via the module's import
    from strategy.series import SERIES as S
    assert mod.SERIES == S

def test_cli_help_runs():
    """--help must exit 0 even if no API is reachable."""
    r = subprocess.run(
        [sys.executable, "-m", "scripts.collect_ticks", "--help"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=10,
    )
    assert r.returncode == 0
    assert "out" in r.stdout or "ticks" in r.stdout.lower()


def test_manifest_tape_empty_rate_tracking(tmp_path: Path):
    """Verify update_manifest writes tape stats including tape_empty_rate."""
    import scripts.collect_ticks as ct

    stats = {
        "lines": 100,
        "series_seen": ["btc-up-or-down-5m"],
        "day": "2026-09-01",
        "tape_empty_count": 98,
        "tape_non_empty_count": 2,
        "tape_empty_rate": 0.98,
        "tape_entries_total": 5,
    }
    ct.update_manifest(tmp_path, stats)
    mf = tmp_path / "manifest.json"
    assert mf.exists()
    data = json.loads(mf.read_text(encoding="utf-8"))
    assert data["tape_empty_rate"] == 0.98
    assert data["tape_empty_count"] == 98
    assert data["tape_entries_total"] == 5
    assert data["day"] == "2026-09-01"

