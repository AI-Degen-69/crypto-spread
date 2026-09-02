"""Tests for the per-file cid offset index (Plan T6)."""
from __future__ import annotations
import json
import time
from pathlib import Path

import pytest

from backtest.index import build_index, load_index, is_fresh, group_by_cid_indexed


def _write_ticks(path: Path, n: int = 5) -> None:
    lines = []
    for i in range(n):
        lines.append(json.dumps({
            "cid": f"0xCID_{i}", "ts": 1700000000.0 + i,
            "series": "btc-up-or-down-5m", "duration": 300,
            "slug": f"x-{i}", "tape_delta": [], "up_book": {}, "down_book": {},
        }))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_build_index_creates_sidecar(tmp_path: Path):
    src = tmp_path / "ticks.jsonl"
    _write_ticks(src, 3)
    idx, n = build_index(src)
    assert idx.exists()
    assert n == 3
    assert idx.name == "ticks.jsonl.idx"


def test_load_index_returns_entries(tmp_path: Path):
    src = tmp_path / "ticks.jsonl"
    _write_ticks(src, 4)
    ents = load_index(src)
    assert len(ents) == 4
    assert ents[0].cid == "0xCID_0"
    assert ents[3].line_no == 3


def test_is_fresh_after_rebuild(tmp_path: Path):
    src = tmp_path / "ticks.jsonl"
    _write_ticks(src, 2)
    build_index(src)
    idx = src.with_suffix(src.suffix + ".idx")
    time.sleep(1.05)
    _write_ticks(src, 3)
    assert not is_fresh(src, idx)
    time.sleep(1.05)
    build_index(src)
    assert is_fresh(src, idx)


def test_group_by_cid_indexed_matches_full_scan(tmp_path: Path):
    src = tmp_path / "ticks.jsonl"
    _write_ticks(src, 6)
    # Replace line 3 with a duplicate cid "0xCID_0" (so the same cid appears
    # at line 0 and line 3) with a later ts.
    lines = src.read_text(encoding="utf-8").splitlines()
    dup = json.loads(lines[0])
    dup["ts"] = 1700000000.99
    lines[3] = json.dumps(dup)
    src.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Force a fresh index build (mtime may not have advanced enough in fast CI)
    from backtest.index import build_index
    build_index(src)
    indexed = group_by_cid_indexed(src)
    by_cid = {cid: snaps for cid, snaps in indexed}
    snaps_for_0 = by_cid[dup["cid"]]
    assert len(snaps_for_0) == 2
    assert [s[0]["ts"] for s in snaps_for_0] == [1700000000.0, 1700000000.99]
    # 6 lines but cid 0 is duplicated -> 5 unique cids
    assert len(by_cid) == 5


def test_index_directory_with_mixed_files(tmp_path: Path):
    d = tmp_path / "day"
    d.mkdir()
    (d / "a.jsonl").write_text(
        json.dumps({"cid": "x", "ts": 1, "series": "a", "duration": 300,
                    "slug": "a", "tape_delta": [], "up_book": {}, "down_book": {}}) + "\n",
        encoding="utf-8",
    )
    (d / "b.jsonl").write_text(
        json.dumps({"cid": "y", "ts": 2, "series": "b", "duration": 300,
                    "slug": "b", "tape_delta": [], "up_book": {}, "down_book": {}}) + "\n",
        encoding="utf-8",
    )
    out = group_by_cid_indexed(d)
    assert [c for c, _ in out] == ["x", "y"]
