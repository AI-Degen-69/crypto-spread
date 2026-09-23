"""Day-file safety plumbing: no silent rewrites of captured tick data (issue #302).

Raw day files under run/ticks/ are the only non-reproducible artifact in the
repo — pristine and golden can always be rebuilt from them byte-identical, but
a lost raw generation is lost forever (the ticks_2026-09-21 incident, #298).

One module owns the whole safety surface so every tool behaves identically:

- Day-file path convention (`ticks_<YYYY-MM-DD>.jsonl[.gz]`) and `DAY_RE`.
- `guard_day_write(path, mode, allow_rewrite)` — the refuse/backup gate. A
  truncating rewrite of an existing day file raises `TickRewriteRefused`
  unless `--allow-rewrite` was passed; read and append paths never raise.
- Backup-on-rewrite: the superseded generation is MOVED to
  `<ticks_dir>/backup/<name>.<old-sha8>` — never destroyed in place.
- `loud_log()` — one stderr line per create / first-append / rewrite
  (path, mode, size, and both hashes for rewrites) so it lands in
  `run/collector.log`.
- Rewrite-event log (`rewrite_events.jsonl`) and the last-recorded-hash store
  (`verify_hashes.json`) the verifier cross-checks against (Phase 3).
"""
from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.ship_to_drive import sha256_of

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TICKS_DIR = ROOT / "run" / "ticks"

DAY_RE = re.compile(r"^ticks_(\d{4}-\d{2}-\d{2})\.jsonl(?:\.gz)?$")
BACKUP_DIRNAME = "backup"
REWRITE_EVENTS_NAME = "rewrite_events.jsonl"
HASH_STORE_NAME = "verify_hashes.json"


class TickRewriteRefused(RuntimeError):
    """A truncating rewrite of an existing day file was refused (no flag)."""


@dataclass
class RewriteNotice:
    """What guard_day_write did to an existing generation before a rewrite."""

    path: Path
    old_sha256: str
    backup_path: Path


def is_day_file(name: str) -> bool:
    """True when `name` follows the raw day-file convention."""
    return bool(DAY_RE.match(name))


def day_file_path(out_dir: Path, day_key: str, gzip: bool) -> Path:
    """The day-file Path for a day key — mirrors write_snap's naming."""
    return Path(out_dir) / f"ticks_{day_key}{'.jsonl.gz' if gzip else '.jsonl'}"


def backup_dir(out_dir: Path) -> Path:
    """Directory holding superseded day-file generations."""
    return Path(out_dir) / BACKUP_DIRNAME


def rewrite_events_path(out_dir: Path) -> Path:
    """The rewrite-event log the verifier cross-checks against."""
    return Path(out_dir) / REWRITE_EVENTS_NAME


def hash_store_path(out_dir: Path) -> Path:
    """The last-recorded-hash store (day filename → sha256)."""
    return Path(out_dir) / HASH_STORE_NAME


def guard_day_write(
    path: Path, mode: str, *, allow_rewrite: bool = False
) -> RewriteNotice | None:
    """The refuse/backup gate for a day-file write.

    mode is the caller's intent: "create", "append", or "rewrite" (truncate /
    replace an existing generation). Reads and appends never raise. A rewrite
    of an existing file raises TickRewriteRefused unless allow_rewrite is set;
    with the flag, the existing generation is moved (never destroyed) to
    `<ticks_dir>/backup/<name>.<old-sha8>` and a RewriteNotice is returned so
    the caller can log both hashes and record the rewrite event after the new
    bytes land.
    """
    path = Path(path)
    if mode != "rewrite" or not path.exists():
        return None
    old_sha = sha256_of(path)
    if not allow_rewrite:
        raise TickRewriteRefused(
            f"refusing to rewrite existing day file {path} without --allow-rewrite "
            f"(size={path.stat().st_size}, sha256={old_sha[:12]}...)"
        )
    bdir = backup_dir(path.parent)
    bdir.mkdir(parents=True, exist_ok=True)
    backup = bdir / f"{path.name}.{old_sha[:8]}"
    if backup.exists():  # same content rewritten twice: keep both generations
        backup = bdir / f"{path.name}.{old_sha[:8]}.{int(time.time())}"
    path.replace(backup)
    return RewriteNotice(path=path, old_sha256=old_sha, backup_path=backup)


def loud_log(
    path: Path,
    mode: str,
    *,
    size: int | None = None,
    old_sha256: str | None = None,
    new_sha256: str | None = None,
) -> None:
    """One loud stderr line per day-file create/append/rewrite (→ collector.log)."""
    path = Path(path)
    if size is None and path.exists():
        size = path.stat().st_size
    parts = [f"[tick-safety] {mode.upper()}: {path}", f"size={size if size is not None else '?'}"]
    if old_sha256:
        parts.append(f"old_sha256={old_sha256}")
    if new_sha256:
        parts.append(f"new_sha256={new_sha256}")
    if mode == "rewrite":
        parts.append("(rewrite was explicitly allowed via --allow-rewrite)")
    print(" ".join(parts), file=sys.stderr, flush=True)


def record_rewrite_event(
    out_dir: Path,
    day_file: str,
    old_sha256: str,
    new_sha256: str,
    backup_path: Path | str,
) -> None:
    """Append one rewrite event to rewrite_events.jsonl (best-effort loud)."""
    try:
        event = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "day_file": day_file,
            "old_sha256": old_sha256,
            "new_sha256": new_sha256,
            "backup": str(backup_path),
        }
        with open(rewrite_events_path(out_dir), "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
    except OSError as e:
        print(f"[tick-safety] WARN: failed to record rewrite event: {e}",
              file=sys.stderr, flush=True)


def read_rewrite_events(out_dir: Path) -> list[dict[str, Any]]:
    """All recorded rewrite events (empty list when the log does not exist)."""
    p = rewrite_events_path(out_dir)
    if not p.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                events.append(obj)
        except ValueError:
            continue  # a torn tail line must not hide the rest of the log
    return events


def read_hash_store(out_dir: Path) -> dict[str, str]:
    """Last recorded sha256 per day filename (empty map when absent/corrupt)."""
    p = hash_store_path(out_dir)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def update_hash_store(out_dir: Path, day_file: str, sha256: str) -> None:
    """Record the current hash of a day file in verify_hashes.json."""
    store = read_hash_store(out_dir)
    store[day_file] = sha256
    p = hash_store_path(out_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp")
    tmp.write_text(json.dumps(store, sort_keys=True, indent=1), encoding="utf-8")
    tmp.replace(p)
