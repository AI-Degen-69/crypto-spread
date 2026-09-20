"""Tests for the Drive shipper (`scripts/ship_to_drive.py`, issue #285).

The shipper moves finished day files to storage the trial disk cannot hold.
Its contracts: never touch the live day, never re-upload an unchanged day,
never raise into the caller (a stuck shipper must not kill capture).
`rclone` is always faked — these tests never touch the network.
"""
from unittest import mock

from scripts import ship_to_drive as sh


def _day(out, name, content=b"x" * 100):
    p = out / name
    p.write_bytes(content)
    return p


def test_live_day_is_never_listed(tmp_path):
    _day(tmp_path, "ticks_2026-09-20.jsonl.gz")
    _day(tmp_path, "ticks_2026-09-19.jsonl.gz")
    assert sh.closed_day_files(tmp_path, "2026-09-20") == [
        tmp_path / "ticks_2026-09-19.jsonl.gz"]


def test_non_day_files_are_ignored(tmp_path):
    _day(tmp_path, "manifest.json")
    _day(tmp_path, "shipped.json")
    _day(tmp_path, "ticks_2026-09-19.jsonl.gz")
    assert [p.name for p in sh.closed_day_files(tmp_path, "2026-09-19")] == []


def test_oldest_first(tmp_path):
    for d in ("2026-09-18", "2026-09-17", "2026-09-19"):
        _day(tmp_path, f"ticks_{d}.jsonl.gz")
    assert [p.name for p in sh.closed_day_files(tmp_path, "2026-09-20")] == [
        "ticks_2026-09-17.jsonl.gz",
        "ticks_2026-09-18.jsonl.gz",
        "ticks_2026-09-19.jsonl.gz"]


def test_ship_all_uploads_data_sidecar_and_snapshot(tmp_path, monkeypatch):
    day = _day(tmp_path, "ticks_2026-09-19.jsonl.gz", b"data" * 1000)
    (tmp_path / "manifest.json").write_text("{}")
    calls = []
    monkeypatch.setattr(sh.subprocess, "run",
                        lambda *a, **k: calls.append(a[0]) or mock.Mock(
                            returncode=0, stderr=""))
    summary = sh.ship_all(tmp_path, "gdrive:ticks", "2026-09-20",
                          tmp_path / "manifest.json")
    assert summary == {"shipped": [day.name], "failed": [], "skipped": []}
    dests = [c[3] for c in calls]
    assert dests == [f"gdrive:ticks:{day.name}",
                     f"gdrive:ticks:{day.name}.sha256",
                     f"gdrive:ticks:{day.name}.manifest.json"]
    sidecar = tmp_path / (day.name + ".sha256")
    assert sidecar.read_text().startswith(sh.sha256_of(day))


def test_ship_all_skips_unchanged_days(tmp_path, monkeypatch):
    day = _day(tmp_path, "ticks_2026-09-19.jsonl.gz")
    sh.save_state(tmp_path, {day.name: {"sha256": sh.sha256_of(day)}})
    run = mock.Mock()
    monkeypatch.setattr(sh.subprocess, "run", run)
    summary = sh.ship_all(tmp_path, "gdrive:ticks", "2026-09-20")
    assert summary["skipped"] == [day.name]
    run.assert_not_called()


def test_ship_all_reuploads_changed_days(tmp_path, monkeypatch):
    day = _day(tmp_path, "ticks_2026-09-19.jsonl.gz", b"v1")
    sh.save_state(tmp_path, {day.name: {"sha256": "stale"}})
    monkeypatch.setattr(sh.subprocess, "run",
                        mock.Mock(return_value=mock.Mock(returncode=0, stderr="")))
    summary = sh.ship_all(tmp_path, "gdrive:ticks", "2026-09-20")
    assert summary["shipped"] == [day.name]


def test_rclone_failure_is_reported_not_raised(tmp_path, monkeypatch):
    _day(tmp_path, "ticks_2026-09-19.jsonl.gz")
    monkeypatch.setattr(sh.subprocess, "run",
                        mock.Mock(return_value=mock.Mock(returncode=1,
                                                         stderr="boom")))
    summary = sh.ship_all(tmp_path, "gdrive:ticks", "2026-09-20")
    assert summary["failed"] == ["ticks_2026-09-19.jsonl.gz"]
    assert summary["shipped"] == []


def test_missing_rclone_is_reported_not_raised(tmp_path, monkeypatch):
    _day(tmp_path, "ticks_2026-09-19.jsonl.gz")
    monkeypatch.setattr(sh.subprocess, "run",
                        mock.Mock(side_effect=FileNotFoundError("rclone")))
    assert sh.ship_all(tmp_path, "gdrive:ticks", "2026-09-20")["failed"] == [
        "ticks_2026-09-19.jsonl.gz"]


def test_corrupt_state_starts_empty(tmp_path):
    (tmp_path / "shipped.json").write_text("not json{{")
    assert sh.load_state(tmp_path) == {}
