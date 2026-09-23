"""Tests for the day-file safety module (issue #302 — no silent rewrites)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.tick_safety import (  # noqa: E402
    TickRewriteRefused,
    day_file_path,
    guard_day_write,
    is_day_file,
    loud_log,
    read_hash_store,
    read_rewrite_events,
    record_rewrite_event,
    update_hash_store,
)


def _make_day(directory: Path, name: str = "ticks_2026-09-13.jsonl",
              body: bytes = b'{"a": 1}\n') -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / name
    p.write_bytes(body)
    return p


def test_day_path_and_convention():
    assert is_day_file("ticks_2026-09-13.jsonl")
    assert is_day_file("ticks_2026-09-13.jsonl.gz")
    assert not is_day_file("ticks_2026-09-13.jsonl.idx")
    assert not is_day_file("golden_manifest.json")
    assert day_file_path(Path("d"), "2026-09-13", gzip=False).name == "ticks_2026-09-13.jsonl"
    assert day_file_path(Path("d"), "2026-09-13", gzip=True).name == "ticks_2026-09-13.jsonl.gz"


def test_guard_allows_create_and_append(tmp_path: Path):
    target = tmp_path / "ticks_2026-09-13.jsonl"
    # create of a missing file never raises; append of an existing file never raises.
    assert guard_day_write(target, "create") is None
    _make_day(tmp_path)
    assert guard_day_write(target, "append") is None


def test_guard_refuses_rewrite_without_flag(tmp_path: Path):
    target = _make_day(tmp_path)
    with pytest.raises(TickRewriteRefused, match="refusing to rewrite existing day file"):
        guard_day_write(target, "rewrite")
    # The file was not touched by the refusal.
    assert target.read_bytes() == b'{"a": 1}\n'
    # ...and no backup or event was produced.
    assert not (tmp_path / "backup").exists()
    assert read_rewrite_events(tmp_path) == []


def test_guard_rewrite_with_flag_backs_up_and_moves(tmp_path: Path):
    target = _make_day(tmp_path)
    from scripts.ship_to_drive import sha256_of
    old_sha = sha256_of(target)

    notice = guard_day_write(target, "rewrite", allow_rewrite=True)
    assert notice is not None and notice.old_sha256 == old_sha
    # The superseded generation was moved, not destroyed.
    backup = tmp_path / "backup" / f"{target.name}.{old_sha[:8]}"
    assert backup.exists() and backup.read_bytes() == b'{"a": 1}\n'
    assert not target.exists()
    # A second rewrite of the same bytes does not clobber the first backup.
    target.write_bytes(b'{"a": 1}\n')
    notice2 = guard_day_write(target, "rewrite", allow_rewrite=True)
    assert notice2 is not None and notice2.backup_path != backup
    assert backup.exists()


def test_record_and_read_rewrite_events(tmp_path: Path):
    assert read_rewrite_events(tmp_path) == []
    record_rewrite_event(tmp_path, "ticks_2026-09-13.jsonl",
                         "a" * 64, "b" * 64, tmp_path / "backup" / "x")
    record_rewrite_event(tmp_path, "ticks_2026-09-14.jsonl",
                         "c" * 64, "d" * 64, tmp_path / "backup" / "y")
    events = read_rewrite_events(tmp_path)
    assert [e["day_file"] for e in events] == [
        "ticks_2026-09-13.jsonl", "ticks_2026-09-14.jsonl"]
    assert events[0]["old_sha256"] == "a" * 64 and events[0]["new_sha256"] == "b" * 64
    # A torn tail line does not hide the earlier events.
    with open(tmp_path / "rewrite_events.jsonl", "a", encoding="utf-8") as f:
        f.write('{"day_file": "torn')
    assert len(read_rewrite_events(tmp_path)) == 2


def test_hash_store_roundtrip(tmp_path: Path):
    assert read_hash_store(tmp_path) == {}
    update_hash_store(tmp_path, "ticks_2026-09-13.jsonl", "e" * 64)
    update_hash_store(tmp_path, "ticks_2026-09-14.jsonl", "f" * 64)
    update_hash_store(tmp_path, "ticks_2026-09-13.jsonl", "0" * 64)
    store = read_hash_store(tmp_path)
    assert store == {
        "ticks_2026-09-13.jsonl": "0" * 64,
        "ticks_2026-09-14.jsonl": "f" * 64,
    }


def test_loud_log_contains_path_mode_size_and_hashes(capsys):
    target = Path("run/ticks/ticks_2026-09-13.jsonl")
    loud_log(target, "create")
    out = capsys.readouterr().err
    assert "[tick-safety] CREATE" in out and str(target) in out and "size=" in out

    loud_log(target, "rewrite", old_sha256="a" * 64, new_sha256="b" * 64)
    out = capsys.readouterr().err
    assert "old_sha256=" + "a" * 64 in out and "new_sha256=" + "b" * 64 in out
    assert "--allow-rewrite" in out  # loud about the flag that permitted it
