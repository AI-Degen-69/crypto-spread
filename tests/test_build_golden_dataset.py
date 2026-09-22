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
    assert names == {"ticks_2026-09-13.jsonl", "ticks_2026-09-14.jsonl"}
    assert len(manifest["excluded"]) == 1
    exc = manifest["excluded"][0]
    assert exc["day"] == "ticks_2026-09-15.jsonl"
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
    assert manifest["totals"]["windows_total"] == sum(d["windows_count"] for d in manifest["days"])
    # Copied file is byte-identical to its pristine source.
    assert (golden / day["day"]).read_bytes() == (pristine / day["day"]).read_bytes()
    # Pristine sources untouched.
    assert (pristine / day["day"]).exists()


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
        rep = verify_tick_file(golden / d["day"])
        passed, reason = day_gate_verdict(rep)
        assert passed, (d["day"], reason)
