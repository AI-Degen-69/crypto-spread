"""Tests for the Drive shipper (`scripts/ship_to_drive.py`, issue #285).

The shipper moves finished day files to storage the trial disk cannot hold.
Its contracts: never touch the live (or freshly-rolled) day, never re-upload
an unchanged day, prune what shipped so the small disk never fills, and never
raise into the caller (a stuck shipper must not kill capture).
`rclone` is always faked — these tests never touch the network.
"""
import pytest

from unittest import mock

from scripts import ship_to_drive as sh

import os
import time


def _day(out, name, content=b"x" * 100, *, fresh=False):
    """Create a day file. Backdated 1h by default so the write-stability gate
    passes deterministically: on Windows `time.time()` ticks in ~15.6ms
    quanta while NTFS mtimes are 100ns-precise, so a just-written file can
    read as "modified in the future" and trip the gate. `fresh=True` keeps
    real mtime for the gate's own test."""
    p = out / name
    p.write_bytes(content)
    if not fresh:
        old = time.time() - 3600
        os.utime(p, (old, old))
    return p


def _ok(monkeypatch, calls=None):
    def run(cmd, **k):
        if calls is not None:
            calls.append(cmd)
        return mock.Mock(returncode=0, stderr="")
    monkeypatch.setattr(sh.subprocess, "run", run)


def test_live_day_is_never_listed(tmp_path):
    _day(tmp_path, "ticks_2026-09-20.jsonl.gz")
    _day(tmp_path, "ticks_2026-09-20.jsonl")
    _day(tmp_path, "ticks_2026-09-19.jsonl.gz")
    assert sh.closed_day_files(tmp_path, "2026-09-20") == [
        tmp_path / "ticks_2026-09-19.jsonl.gz"]


def test_non_day_files_are_ignored(tmp_path):
    _day(tmp_path, "manifest.json")
    _day(tmp_path, "shipped.json")
    assert sh.closed_day_files(tmp_path, "2026-09-19") == []


def test_oldest_first_mixed_suffixes(tmp_path):
    for d in ("2026-09-18", "2026-09-19.jsonl", "2026-09-17"):
        name = d if d.endswith(".jsonl") else d + ".jsonl.gz"
        _day(tmp_path, f"ticks_{name}")
    _day(tmp_path, "ticks_2026-09-20.jsonl.gz")  # live day sorts out
    assert [p.name for p in sh.closed_day_files(tmp_path, "2026-09-20")] == [
        "ticks_2026-09-17.jsonl.gz",
        "ticks_2026-09-18.jsonl.gz",
        "ticks_2026-09-19.jsonl"]


def test_freshly_rolled_file_waits(tmp_path, monkeypatch):
    _day(tmp_path, "ticks_2026-09-19.jsonl.gz", fresh=True)
    _ok(monkeypatch)
    summary = sh.ship_all(tmp_path, "gdrive:ticks", "2026-09-20")
    assert summary == {"shipped": [], "failed": [], "skipped": []}


def test_ship_all_uploads_data_sidecar_and_snapshot(tmp_path, monkeypatch):
    content = b"data" * 1000
    day = _day(tmp_path, "ticks_2026-09-19.jsonl.gz", content)
    (tmp_path / "manifest.json").write_text('{"windows_count": 550}')
    calls = []
    _ok(monkeypatch, calls)
    summary = sh.ship_all(tmp_path, "gdrive:ticks", "2026-09-20",
                          tmp_path / "manifest.json", prune=False)
    assert summary == {"shipped": [day.name], "failed": [], "skipped": []}
    dests = [c[3] for c in calls]
    assert dests == [f"gdrive:ticks/{day.name}",
                     f"gdrive:ticks/{day.name}.sha256",
                     f"gdrive:ticks/{day.name}.ship-manifest.json"]
    # exact `sha256sum -c` format: hex, two spaces, name, newline
    import hashlib
    expect = hashlib.sha256(content).hexdigest()
    assert (tmp_path / (day.name + ".sha256")).read_text() == \
        f"{expect}  {day.name}\n"
    state = sh.load_state(tmp_path)
    assert state[day.name] == {"sha256": expect,
                               "remote": f"gdrive:ticks/{day.name}",
                               "shipped_ts": state[day.name]["shipped_ts"]}


def test_shipped_days_are_pruned_locally(tmp_path, monkeypatch):
    day = _day(tmp_path, "ticks_2026-09-19.jsonl.gz")
    _ok(monkeypatch)
    assert sh.ship_all(tmp_path, "gdrive:ticks", "2026-09-20")["shipped"] == [day.name]
    assert not day.exists()
    assert not (tmp_path / (day.name + ".sha256")).exists()
    assert not (tmp_path / (day.name + ".ship-manifest.json")).exists()
    assert (tmp_path / "shipped.json").exists()  # state survives pruning


def test_second_pass_skips_shipped(tmp_path, monkeypatch):
    day = _day(tmp_path, "ticks_2026-09-19.jsonl.gz")
    calls = []
    _ok(monkeypatch, calls)
    first = sh.ship_all(tmp_path, "gdrive:ticks", "2026-09-20",
                        prune=False)
    assert first["shipped"] == [day.name]
    n_calls = len(calls)
    second = sh.ship_all(tmp_path, "gdrive:ticks", "2026-09-20",
                         prune=False)
    assert second == {"shipped": [], "failed": [], "skipped": [day.name]}
    assert len(calls) == n_calls  # no rclone ran at all


def test_ship_all_skips_unchanged_days_without_rclone(tmp_path, monkeypatch):
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
    _ok(monkeypatch)
    summary = sh.ship_all(tmp_path, "gdrive:ticks", "2026-09-20",
                          prune=False)
    assert summary["shipped"] == [day.name]


def test_one_failure_does_not_abort_the_rest(tmp_path, monkeypatch):
    _day(tmp_path, "ticks_2026-09-17.jsonl.gz", b"bad")
    _day(tmp_path, "ticks_2026-09-18.jsonl.gz", b"good")

    def run(cmd, **k):
        if "2026-09-17" in cmd[2]:
            return mock.Mock(returncode=1, stderr="boom")
        return mock.Mock(returncode=0, stderr="")
    monkeypatch.setattr(sh.subprocess, "run", run)
    summary = sh.ship_all(tmp_path, "gdrive:ticks", "2026-09-20")
    assert summary["failed"] == ["ticks_2026-09-17.jsonl.gz"]
    assert summary["shipped"] == ["ticks_2026-09-18.jsonl.gz"]
    # failed day stays local; shipped day is pruned
    assert (tmp_path / "ticks_2026-09-17.jsonl.gz").exists()
    assert not (tmp_path / "ticks_2026-09-18.jsonl.gz").exists()


def test_rclone_timeout_is_reported_not_raised(tmp_path, monkeypatch):
    import subprocess as sp
    _day(tmp_path, "ticks_2026-09-19.jsonl.gz")
    monkeypatch.setattr(sh.subprocess, "run",
                        mock.Mock(side_effect=sp.TimeoutExpired("rclone", 600)))
    summary = sh.ship_all(tmp_path, "gdrive:ticks", "2026-09-20",
                          prune=False)
    assert summary["failed"] == ["ticks_2026-09-19.jsonl.gz"]


def test_missing_rclone_is_reported_not_raised(tmp_path, monkeypatch):
    _day(tmp_path, "ticks_2026-09-19.jsonl.gz")
    monkeypatch.setattr(sh.subprocess, "run",
                        mock.Mock(side_effect=FileNotFoundError("rclone")))
    assert sh.ship_all(tmp_path, "gdrive:ticks", "2026-09-20",
                       prune=False)["failed"] == [
        "ticks_2026-09-19.jsonl.gz"]


def test_unreadable_file_does_not_abort_pass(tmp_path, monkeypatch):
    _ok(monkeypatch)
    monkeypatch.setattr(sh, "sha256_of",
                        mock.Mock(side_effect=[OSError("gone"), "abc"]))
    _day(tmp_path, "ticks_2026-09-17.jsonl.gz")
    _day(tmp_path, "ticks_2026-09-18.jsonl.gz")
    summary = sh.ship_all(tmp_path, "gdrive:ticks", "2026-09-20",
                          prune=False)
    assert summary["failed"] == ["ticks_2026-09-17.jsonl.gz"]


def test_corrupt_state_starts_empty(tmp_path):
    (tmp_path / "shipped.json").write_text("not json{{")
    assert sh.load_state(tmp_path) == {}


def test_non_dict_state_starts_empty(tmp_path):
    (tmp_path / "shipped.json").write_text('["ticks_2026-09-19.jsonl.gz"]')
    assert sh.load_state(tmp_path) == {}


def test_state_without_sha_key_reships(tmp_path, monkeypatch):
    day = _day(tmp_path, "ticks_2026-09-19.jsonl.gz")
    sh.save_state(tmp_path, {day.name: {}})
    _ok(monkeypatch)
    summary = sh.ship_all(tmp_path, "gdrive:ticks", "2026-09-20",
                          prune=False)
    assert summary["shipped"] == [day.name]


def test_leading_dash_remote_is_refused(tmp_path):
    _day(tmp_path, "ticks_2026-09-19.jsonl.gz")
    with pytest.raises(ValueError):
        sh.ship_all(tmp_path, "--remote=x", "2026-09-20")


def test_stderr_tokens_are_redacted(tmp_path, monkeypatch, capsys):
    _day(tmp_path, "ticks_2026-09-19.jsonl.gz")
    monkeypatch.setattr(sh.subprocess, "run", mock.Mock(return_value=mock.Mock(
        returncode=1,
        stderr='config {"refresh_token": "SECRET123"} denied')))
    sh.ship_all(tmp_path, "gdrive:ticks", "2026-09-20", prune=False)
    out = capsys.readouterr().out
    assert "SECRET123" not in out
    assert "***" in out
