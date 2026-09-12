"""Tests for scripts/run_layout.py (issue #147, Stage 1).

Naming: runs/{paper|live}/YYYY-MM-DD_HH-MM_TZ/ with data/ + research-papers/.
Manifest schema keys per SPEC.md §6. Pilot smoke test is skipped until Task 3
wires shadow_ev_pilot.py to the new layout.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from scripts import run_layout as rl

ROOT = Path(__file__).resolve().parent.parent


def test_naming_format_and_subdirs(tmp_path, monkeypatch):
    monkeypatch.setattr(rl, "RUNS_ROOT", tmp_path / "runs")
    d = rl.new_run_dir("paper", datetime(2026, 9, 11, 22, 10), "IDT")
    assert d == tmp_path / "runs" / "paper" / "2026-09-11_22-10_IDT"
    assert (d / "data").is_dir()
    assert (d / "research-papers").is_dir()
    assert ":" not in d.name


def test_kind_validation_rejects_non_paper_live(tmp_path, monkeypatch):
    monkeypatch.setattr(rl, "RUNS_ROOT", tmp_path / "runs")
    for bad in ("prod", "PAPER", "", "paper/live"):
        with pytest.raises(ValueError):
            rl.new_run_dir(bad, datetime(2026, 9, 11, 22, 10), "IDT")


def test_tz_validation_rejects_colons_and_blanks(tmp_path, monkeypatch):
    monkeypatch.setattr(rl, "RUNS_ROOT", tmp_path / "runs")
    for bad in ("22:10", "", "id t"):
        with pytest.raises(ValueError):
            rl.new_run_dir("paper", datetime(2026, 9, 11, 22, 10), bad)


def _payload():
    return {
        "kind": "paper",
        "run_id": "2026-09-11_22-10_IDT",
        "started_local": "2026-09-11T22:10:00+03:00",
        "started_utc": "2026-09-11T19:10:00+00:00",
        "stopped_utc": "2026-09-12T06:10:00+00:00",
        "tz": "IDT",
        "preset": "patient_band_maker",
        "config_hypothesis": {"entry_band": 0.04},
        "planned_hours": 11.0,
        "final": {"total_pnl": 6.16, "realized_pnl": 6.74, "total_trades": 66,
                  "win_rate": 97.0, "pairs_merged": 53, "stops_triggered": 0},
        "data": ["data/meta.json", "data/final.json"],
        "papers": {"abstract_and_methodology": "research-papers/abstract-and-methodology.html",
                   "results_and_findings": "research-papers/results-and-findings.html",
                   "conclusions_and_projections": "research-papers/conclusions-and-projections.html"},
        "summary": "summary.html",
    }


def test_write_manifest_schema_and_missing_key(tmp_path):
    p = rl.write_manifest(tmp_path, _payload())
    assert p.name == "manifest.json"
    assert json.loads(p.read_text(encoding="utf-8"))["run_id"] == "2026-09-11_22-10_IDT"
    bad = _payload()
    del bad["final"]
    with pytest.raises(ValueError):
        rl.write_manifest(tmp_path, bad)


def test_paper_stub_and_summary_round_trip(tmp_path):
    papers = tmp_path / "research-papers"
    papers.mkdir()
    stub = rl.write_paper_stub(tmp_path, "abstract-and-methodology",
                               "Abstract", "<p>hello</p>")
    assert stub == papers / "abstract-and-methodology.html"
    assert "hello" in stub.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        rl.write_paper_stub(tmp_path, "../escape", "X", "y")
    summ = rl.write_summary_html(tmp_path, {
        "title": "T", "run_id": "R", "kind": "paper",
        "started_local": "S", "config_hypothesis": {"a": 1},
        "papers": {"abstract_and_methodology": "research-papers/abstract-and-methodology.html"},
        "data": ["data/final.json"],
    })
    assert summ.name == "summary.html"


def test_local_tz_abbr_nonempty():
    assert rl.local_tz_abbr().strip() != ""


def test_fixed_offset_tz_normalized(tmp_path, monkeypatch):
    monkeypatch.setattr(rl, "RUNS_ROOT", tmp_path / "runs")
    d = rl.new_run_dir("paper", datetime(2026, 9, 11, 22, 10), "UTC+03:00")
    assert d.name == "2026-09-11_22-10_UTC+03-00"


def test_timestamp_collision_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(rl, "RUNS_ROOT", tmp_path / "runs")
    rl.new_run_dir("paper", datetime(2026, 9, 11, 22, 10), "IDT")
    with pytest.raises(FileExistsError):
        rl.new_run_dir("paper", datetime(2026, 9, 11, 22, 10), "IDT")


def test_pilot_smoke_writes_new_layout(tmp_path):
    import subprocess
    import sys
    out = tmp_path / "smoke_run"
    r = subprocess.run(
        [sys.executable, "-m", "scripts.shadow_ev_pilot",
         "--hours", "0", "--snap-every", "1", "--outdir", str(out)],
        capture_output=True, text=True, timeout=300,
        cwd=str(ROOT),
    )
    assert r.returncode == 0, r.stderr[-2000:]
    for rel in ("data/meta.json", "data/final.json", "manifest.json",
                "summary.html",
                "research-papers/abstract-and-methodology.html",
                "research-papers/results-and-findings.html"):
        assert (out / rel).is_file(), rel
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["kind"] == "paper"
    assert manifest["final"]["total_trades"] == 0
