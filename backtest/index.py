"""Per-file cid offset index for fast backtest (Plan T6 / D8).

Slider UI on the dashboard calls /api/backtest many times per session on the
same tick file. A full jsonl scan per call costs ~1.5s/day; a precomputed
cid -> [(byte_offset, line_no, ts)] index drops it to ~50ms.

The index is a sidecar file (`<source>.idx`, jsonl) with one line per snap
in the source file. First backtest call after a file change rebuilds it;
subsequent calls load it from disk.
"""
from __future__ import annotations
import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, Iterator

from .engine import _json_or_skip


def _index_path(source: Path) -> Path:
    """`<file>.idx` for files, `<dir>/_index.jsonl` for directories."""
    if source.is_dir():
        return source / "_index.jsonl"
    return source.with_suffix(source.suffix + ".idx")


@dataclass
class IndexEntry:
    cid: str
    ts: float
    series: str
    duration: int
    byte_offset: int
    line_no: int


def build_index(source: Path) -> tuple[Path, int]:
    """Scan source once, write per-snap index entries to the sidecar file.

    Returns (index_path, total_snaps). The index is appended line-by-line so
    it can be re-built incrementally if a collector crashes mid-day.
    """
    import gzip
    idx_path = _index_path(source)
    idx_path.parent.mkdir(parents=True, exist_ok=True)

    is_gz = source.suffix == ".gz"
    opener = lambda: gzip.open(source, "rt", encoding="utf-8") if is_gz \
        else open(source, "r", encoding="utf-8")

    line_no = 0
    byte_offset = 0
    n = 0
    with opener() as f, open(idx_path, "w", encoding="utf-8") as idx:
        while True:
            line = f.readline()
            if not line:
                break
            # Use the file's actual byte position after readline, not the
            # text-mode string length -- text mode can rewrite line endings
            # (e.g. Windows -> \r\n -> \n), so .encode() won't match disk.
            j = _json_or_skip(line)
            if j is not None:
                cid = j.get("cid", "")
                if cid:
                    idx.write(json.dumps({
                        "cid": cid,
                        "ts": j.get("ts", 0.0),
                        "series": j.get("series", ""),
                        "duration": j.get("duration", 0),
                        "byte_offset": byte_offset,
                        "line_no": line_no,
                    }) + "\n")
                    n += 1
            byte_offset = f.tell()
            line_no += 1
    return idx_path, n


def is_fresh(source: Path, idx_path: Path) -> bool:
    """True if index exists and source mtime is older than index mtime."""
    if not idx_path.exists():
        return False
    return idx_path.stat().st_mtime >= source.stat().st_mtime


def load_index(source: Path) -> list[IndexEntry]:
    """Load the sidecar; rebuild if missing or stale."""
    idx_path = _index_path(source)
    if not is_fresh(source, idx_path):
        build_index(source)
    out: list[IndexEntry] = []
    with open(idx_path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                d = json.loads(line)
                out.append(IndexEntry(**d))
            except Exception:
                continue
    return out


def group_by_cid_indexed(source: Path) -> list[tuple[str, list[tuple[dict, int]]]]:
    """Like `engine.group_by_cid(snaps)` but reads only the bytes the index
    points to — no full-file scan.

    Each group is (cid, [(snap_dict, line_no), ...]) sorted by ts. Cids are
    sorted by the first snap's ts.
    """
    import gzip
    all_entries: list[IndexEntry] = []
    entry_to_source: dict[int, Path] = {}
    if source.is_dir():
        # Concat the per-file indexes, preserving ts order across files.
        for f in sorted(source.iterdir()):
            if f.is_file() and f.suffix in (".jsonl", ".gz"):
                for e in load_index(f):
                    all_entries.append(e)
                    entry_to_source[id(e)] = f
        all_entries.sort(key=lambda e: e.ts)
    else:
        if not is_fresh(source, _index_path(source)):
            build_index(source)
        all_entries = load_index(source)
        for e in all_entries:
            entry_to_source[id(e)] = source

    grouped: dict[str, list[tuple[dict, int]]] = {}
    open_files: dict[str, object] = {}

    def _get_handle(src: Path):
        key = str(src)
        if key not in open_files:
            if src.suffix == ".gz":
                import gzip as _g
                open_files[key] = _g.open(src, "rt", encoding="utf-8")
            else:
                open_files[key] = open(src, "r", encoding="utf-8")
        return open_files[key]

    try:
        for entry in all_entries:
            src = entry_to_source[id(entry)]
            f = _get_handle(src)
            f.seek(entry.byte_offset)
            line = f.readline()
            j = _json_or_skip(line)
            if j is None:
                continue
            j["cid"] = entry.cid
            j["ts"] = entry.ts
            grouped.setdefault(entry.cid, []).append((j, entry.line_no))
    finally:
        for f in open_files.values():
            try:
                f.close()
            except Exception:
                pass

    out = [(cid, sorted(group, key=lambda kv: kv[0].get("ts", 0.0)))
           for cid, group in grouped.items()]
    out.sort(key=lambda kv: kv[1][0][0].get("ts", 0.0) if kv[1] else 0.0)
    return out
