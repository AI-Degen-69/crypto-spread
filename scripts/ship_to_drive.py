"""Ship closed tick-day files to Google Drive via rclone (issue #285).

The trial host's disk is small (500MB); Drive is the file store. Drive is an
upload target, not a filesystem — the live day file is still being appended
by `collect_ticks.write_snap` every ~1.4s, so only CLOSED days (day key older
than today) are ever shipped.

Auth is env-only: rclone reads `RCLONE_CONFIG_GDRIVE_*` from the environment
(the operator pastes one OAuth refresh token). No credential ever touches
this repo.

Nothing here raises into the caller: every file is attempted independently,
failures are logged and returned in the summary, and a stuck shipper must
never kill capture.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

DAY_RE = re.compile(r"^ticks_(\d{4}-\d{2}-\d{2})\.jsonl(\.gz)?$")

STATE_FILE = "shipped.json"


def closed_day_files(out_dir: Path, today: str) -> list[Path]:
    """Day files whose day is fully past, oldest first.

    `today` is a `YYYY-MM-DD` day key (normally `now_day_key()`); the live
    day file is excluded because the collector is still appending to it.
    """
    found = []
    for p in out_dir.glob("ticks_*.jsonl*"):
        m = DAY_RE.match(p.name)
        if m and m.group(1) != today and p.is_file():
            found.append(p)
    return sorted(found)


def sha256_of(path: Path) -> str:
    """Hex sha256 of a file, streamed (day files are gigabytes)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_state(out_dir: Path) -> dict:
    """Previously shipped files (`shipped.json`), {} when never shipped."""
    try:
        return json.loads((out_dir / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(out_dir: Path, state: dict) -> None:
    (out_dir / STATE_FILE).write_text(json.dumps(state, indent=2), encoding="utf-8")


def rclone_copyto(local: Path, dest: str, timeout: float = 600.0) -> bool:
    """Upload one file via `rclone copyto`; False (never raise) on failure."""
    try:
        r = subprocess.run(
            ["rclone", "copyto", str(local), dest],
            capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        print(f"ship: rclone failed for {local.name} ({e})", flush=True)
        return False
    if r.returncode != 0:
        print(f"ship: rclone rc={r.returncode} for {local.name}: {r.stderr.strip()[:200]}",
              flush=True)
        return False
    return True


def ship_file(path: Path, remote: str, manifest_src: Path | None = None) -> bool:
    """Upload one closed day + `.sha256` sidecar + manifest snapshot.

    Returns True only when all three land. The sidecar uses `sha256sum`
    format (`<hex>  <name>`) so a plain `sha256sum -c` verifies a pull.
    """
    digest = sha256_of(path)
    sidecar = path.with_name(path.name + ".sha256")
    sidecar.write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    ok = rclone_copyto(path, f"{remote}:{path.name}")
    ok = rclone_copyto(sidecar, f"{remote}:{sidecar.name}") and ok
    snap = path.with_name(path.name + ".manifest.json")
    if manifest_src is not None and manifest_src.is_file():
        snap.write_bytes(manifest_src.read_bytes())
    else:
        snap.write_text(json.dumps({"shipped_ts": time.time(), "day_file": path.name}),
                        encoding="utf-8")
    ok = rclone_copyto(snap, f"{remote}:{snap.name}") and ok
    return ok


def ship_all(out_dir: Path, remote: str, today: str,
             manifest_src: Path | None = None) -> dict:
    """Ship every unshipped closed day. Returns {shipped, failed, skipped}."""
    state = load_state(out_dir)
    summary: dict[str, list[str]] = {"shipped": [], "failed": [], "skipped": []}
    for path in closed_day_files(out_dir, today):
        entry = state.get(path.name, {})
        if entry.get("sha256") == sha256_of(path):
            summary["skipped"].append(path.name)
            continue
        if ship_file(path, remote, manifest_src):
            state[path.name] = {"sha256": sha256_of(path),
                                "remote": f"{remote}:{path.name}",
                                "shipped_ts": time.time()}
            save_state(out_dir, state)
            summary["shipped"].append(path.name)
        else:
            summary["failed"].append(path.name)
    return summary


def main(argv: list[str] | None = None) -> int:
    import os
    ap = argparse.ArgumentParser(description="Ship closed tick days to Drive.")
    ap.add_argument("--out", type=Path, default=Path("run/ticks"))
    ap.add_argument("--remote", default=os.environ.get("DRIVE_REMOTE", ""),
                    help="rclone destination, e.g. gdrive:crypto-ticks")
    ap.add_argument("--today", default=None,
                    help="override day key (tests); default: current UTC day")
    a = ap.parse_args(argv)
    if not a.remote:
        print("ship: no --remote and DRIVE_REMOTE unset; nothing to do")
        return 2
    from scripts.collect_ticks import now_day_key
    today = a.today or now_day_key()
    out_dir: Path = a.out
    summary = ship_all(out_dir, a.remote, today, out_dir / "manifest.json")
    print(f"ship: {summary}", flush=True)
    return 0 if not summary["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
