"""Tests for the golden dataset assembly script (issue #281)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.test_verify_tick_data import make_sample_tick  # noqa: E402
from scripts.build_golden_dataset import (  # noqa: E402
    build_golden_dataset,
    day_gate_verdict,
    MANIFEST_NAME,
)
from backtest.index import is_fresh  # noqa: E402
from scripts.verify_tick_data import verify_tick_file, READINESS_POLICY_VERSION  # noqa: E402


def _write_day(directory: Path, name: str, *, n_windows: int = 2) -> None:
    """Write a deterministic, gate-passing day file (window starts 2026-09-13 00:00 UTC).

    Each window gets ticks from its exact open (start_ts) to its close (end_ts),
    every 5s — the cadence verify_tick_file needs for PASS / COMPLETE CAPTURE.
    """
    base = 1789296000.0  # 2026-09-13T00:00:00Z, aligned to the day
    lines = []
    for i in range(n_windows):
        start = base + i * 400
        for k in range(0, 301, 5):
            ts = start + k
            t = make_sample_tick(
                cid=f"0xWIN{i:02d}",
                series="btc-up-or-down-5m",
                ts=ts,
                start_ts=start,
                end_ts=start + 300,
                duration=300,
            )
            lines.append(json.dumps(t))
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_failing_day(directory: Path, name: str) -> None:
    """A day that fails §1.1: an early cutoff makes it WARN / PARTIAL CAPTURE."""
    _write_day(directory, name, n_windows=1)
    # Truncate the file mid-window: the last window never reaches its end_ts.


def _truncate_day(directory: Path, name: str) -> None:
    path = directory / name
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:2]) + "\n", encoding="utf-8")


def test_day_gate_verdict_accepts_and_rejects():
    ok_report = {
        "status": "PASS",
        "capture_state": {"label": "COMPLETE CAPTURE"},
        "corrupt_lines": 0,
        "collector_errors": 0,
        "time_reversals": 0,
    }
    passed, reason = day_gate_verdict(ok_report)
    assert passed is True and reason is None

    for field, bad in (("status", "FAIL"), ("collector_errors", 3), ("time_reversals", 1)):
        report = {**ok_report, field: bad}
        passed, reason = day_gate_verdict(report)
        assert passed is False and reason, field


def test_build_golden_excludes_failing_day_and_records_reason(tmp_path: Path):
    pristine = tmp_path / "pristine"
    golden = tmp_path / "golden"
    _write_day(pristine, "ticks_2026-09-13.jsonl")
    _write_day(pristine, "ticks_2026-09-14.jsonl")
    _write_day(pristine, "ticks_2026-09-15.jsonl")
    _truncate_day(pristine, "ticks_2026-09-15.jsonl")

    manifest = build_golden_dataset(pristine, golden, build_indexes=False)
    names = {d["day"] for d in manifest["days"]}
    # Charter §3.1: `day` is the UTC day key, not the filename.
    assert names == {"2026-09-13", "2026-09-14"}
    assert len(manifest["excluded"]) == 1
    exc = manifest["excluded"][0]
    assert exc["day"] == "2026-09-15"
    assert exc["file"] == "ticks_2026-09-15.jsonl"
    assert exc["reason"] and ("COMPLETE CAPTURE" in exc["reason"] or "PASS" in exc["reason"])
    # The failing day must not land in golden/.
    assert not (golden / "ticks_2026-09-15.jsonl").exists()


def test_manifest_schema_and_provenance(tmp_path: Path):
    pristine = tmp_path / "pristine"
    golden = tmp_path / "golden"
    _write_day(pristine, "ticks_2026-09-13.jsonl")
    _write_day(pristine, "ticks_2026-09-14.jsonl")

    manifest = build_golden_dataset(pristine, golden, build_indexes=False)
    assert manifest["policy_version"] == READINESS_POLICY_VERSION
    assert manifest["charter"] == "docs/golden-tick-dataset.md"
    day = manifest["days"][0]
    assert day["source"].startswith("pristine/")
    assert len(day["sha256"]) == 64 and len(day["source_sha256"]) == 64
    assert day["verify_verdict"]["status"] == "PASS"
    assert day["verify_verdict"]["capture_label"] == "COMPLETE CAPTURE"
    # Charter §3.1 set-level vocabulary (audit without re-streaming the files).
    totals = manifest["totals"]
    assert totals["windows_count"] == sum(d["windows_count"] for d in manifest["days"])
    assert totals["valid_ticks"] == sum(d["valid_ticks"] for d in manifest["days"])
    assert isinstance(totals["time_blocks"], list)
    assert isinstance(totals["sampling_gap_rate"], float)
    assert {g["name"] for g in manifest["set_gates"]} >= {
        "windows_count", "windows_per_market_pair", "time_blocks",
        "valid_ticks", "sampling_gap_rate", "all_10_series_present"}
    # Copied file is byte-identical to its pristine source.
    assert (golden / day["file"]).read_bytes() == (pristine / day["file"]).read_bytes()
    # Pristine sources untouched.
    assert (pristine / day["file"]).exists()
    # A 2-day set passes §1.1 per day but cannot meet §1.2 targets (≥5 time
    # blocks, ≥50 windows per pair): published explicitly uncertified.
    assert manifest["status"] == "incomplete"
    assert manifest["certified_utc"] is None


def test_build_golden_creates_fresh_idx_sidecars(tmp_path: Path):
    pristine = tmp_path / "pristine"
    golden = tmp_path / "golden"
    _write_day(pristine, "ticks_2026-09-13.jsonl")

    build_golden_dataset(pristine, golden, build_indexes=True)
    df = golden / "ticks_2026-09-13.jsonl"
    idx = df.with_name(df.name + ".idx")
    assert idx.exists()
    assert is_fresh(df, idx)
    # The promoted day still passes the verifier unchanged.
    report = verify_tick_file(df)
    assert report["status"] == "PASS"
    assert (report["capture_state"] or {}).get("label") == "COMPLETE CAPTURE"


def test_build_golden_is_idempotent(tmp_path: Path):
    pristine = tmp_path / "pristine"
    golden = tmp_path / "golden"
    _write_day(pristine, "ticks_2026-09-13.jsonl")
    _write_day(pristine, "ticks_2026-09-14.jsonl")

    first = build_golden_dataset(pristine, golden, build_indexes=False)
    manifest_path = golden / MANIFEST_NAME
    snapshot = manifest_path.read_bytes()
    second = build_golden_dataset(pristine, golden, build_indexes=False)
    # Same content ⇒ byte-identical manifest and unchanged day files.
    assert manifest_path.read_bytes() == snapshot
    assert first["certified_utc"] == second["certified_utc"]
    assert first == second


def test_assembled_days_pass_day_gates_on_real_pristine(tmp_path: Path):
    """End-to-end on the repo's real pristine dir: every golden day passes §1.1.

    Opt-in (GOLDEN_E2E=1): the full pass streams ~1.4M ticks twice and takes
    minutes — the default suite stays fast; the real certification run goes
    through the CLI (python -m scripts.build_golden_dataset).
    """
    import os
    import pytest

    if not os.environ.get("GOLDEN_E2E"):
        pytest.skip("GOLDEN_E2E=1 not set (slow real-data end-to-end)")
    real_pristine = Path(__file__).resolve().parent.parent / "run" / "ticks" / "pristine"
    if not real_pristine.is_dir():
        pytest.skip("run/ticks/pristine not present on this machine")
    golden = tmp_path / "golden"
    manifest = build_golden_dataset(real_pristine, golden, build_indexes=False)
    assert manifest["days"], "expected at least one passing day"
    assert manifest["excluded"] == []
    for d in manifest["days"]:
        rep = verify_tick_file(golden / d["file"])
        passed, reason = day_gate_verdict(rep)
        assert passed, (d["day"], reason)


def test_stale_golden_day_is_removed_on_rebuild(tmp_path: Path):
    """A golden day whose source no longer passes (or vanished) is removed."""
    pristine = tmp_path / "pristine"
    golden = tmp_path / "golden"
    _write_day(pristine, "ticks_2026-09-13.jsonl")
    _write_day(pristine, "ticks_2026-09-14.jsonl")
    build_golden_dataset(pristine, golden, build_indexes=False)
    assert (golden / "ticks_2026-09-14.jsonl").exists()

    # The source of 09-14 degrades: truncate it below the gate.
    _truncate_day(pristine, "ticks_2026-09-14.jsonl")
    manifest = build_golden_dataset(pristine, golden, build_indexes=False)
    # Removed from the published set, recorded in removed_stale and excluded.
    assert not (golden / "ticks_2026-09-14.jsonl").exists()
    assert not (golden / "ticks_2026-09-14.jsonl.idx").exists()
    assert any(r["day"] == "ticks_2026-09-14.jsonl" for r in manifest["removed_stale"])
    assert any(e["file"] == "ticks_2026-09-14.jsonl" for e in manifest["excluded"])


def test_certified_utc_stable_for_unchanged_inputs(tmp_path, monkeypatch):
    """A rebuild on a later date keeps the original certification date when
    the input identity (content hashes) is unchanged, and recertifies when
    it changes. The §1.2 gate is stubbed OK here — the reuse path is the
    behavior under test."""
    import scripts.build_golden_dataset as bgd

    monkeypatch.setattr(
        bgd, "charter_set_gates",
        lambda days: [{"name": "stub", "measured": 1, "required": 1, "ok": True}])
    pristine = tmp_path / "pristine"
    golden = tmp_path / "golden"
    _write_day(pristine, "ticks_2026-09-13.jsonl")
    _write_day(pristine, "ticks_2026-09-14.jsonl")
    first = build_golden_dataset(pristine, golden, build_indexes=False)
    assert first["status"] == "certified" and first["certified_utc"]
    # Simulate a rebuild on the next UTC day with a stale recorded date.
    first["certified_utc"] = "2000-01-01"
    (golden / MANIFEST_NAME).write_text(json.dumps(first), encoding="utf-8")
    second = build_golden_dataset(pristine, golden, build_indexes=False)
    assert second["certified_utc"] == "2000-01-01"  # identity unchanged → reused
    # Now change the inputs: the certification date must move off the old one.
    _write_day(pristine, "ticks_2026-09-15.jsonl")
    third = build_golden_dataset(pristine, golden, build_indexes=False)
    assert third["certified_utc"] != "2000-01-01"


def test_incomplete_set_is_published_uncertified(tmp_path: Path):
    """A set that fails §1.2 is written with status='incomplete' and a null
    certification date — never certified by accident."""
    pristine = tmp_path / "pristine"
    golden = tmp_path / "golden"
    _write_day(pristine, "ticks_2026-09-13.jsonl", n_windows=1)
    manifest = build_golden_dataset(pristine, golden, build_indexes=False)
    assert manifest["days"], "day passed §1.1 but the set is tiny"
    assert manifest["status"] == "incomplete"
    assert manifest["certified_utc"] is None
    assert any(not g["ok"] for g in manifest["set_gates"])
