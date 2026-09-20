"""Ship closed tick-day files to Google Drive via rclone (issue #285).

The trial host's disk is small (500MB); Drive is the file store. Drive is an
upload target, not a filesystem — the live day file is still being appended
by `collect_ticks.write_snap` every ~1.4s, so only CLOSED, write-stable days
are ever shipped. Shipped days are pruned locally: without that the 500MB
buffer would fill by day 3 no matter how fast the shipper runs.

Auth is env-only: rclone reads `RCLONE_CONFIG_GDRIVE_*` from the environment
(the operator pastes one OAuth refresh token). No credential ever touches
this repo.

Per-file failures never escape: each file is attempted independently and a
stuck shipper must never kill capture. A bad `remote` is a config error and
raises `ValueError` (contained by the watchdog's `maybe_ship`, exit-2 in CLI).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

DAY_RE = re.compile(r"^ticks_(\d{4}-\d{2}-\d{2})\.jsonl(\.gz)?$")

STATE_FILE = "shipped.json"

# A day that just rolled over may still be flushed by the collector; only
# ship files untouched for this long.
MIN_STABLE_AGE_S = 300.0


def closed_day_files(out_dir: Path, today: str) -> list[Path]:
    """Day files strictly older than `today`, oldest first.

    `today` is a `YYYY-MM-DD` day key (normally `now_day_key()`); the live
    day file is excluded because the collector is still appending to it.
    The comparison is strict (`<`, not `!=`): if the host clock ever steps
    back across midnight, a future-named file must not ship — uploading and
    pruning it would split one capture day across two files. ISO day keys
    compare chronologically as plain strings.
    """
    found: list[Path] = []
    for p in out_dir.glob("ticks_*.jsonl*"):
        m = DAY_RE.match(p.name)
        if m and m.group(1) < today and p.is_file():
            found.append(p)
    return sorted(found)


def is_stable(path: Path, min_age_s: float = MIN_STABLE_AGE_S) -> bool:
    """True when the file has not been written for `min_age_s`."""
    try:
        return time.time() - path.stat().st_mtime >= min_age_s
    except OSError:
        return False


def sha256_of(path: Path) -> str:
    """Hex sha256 of a file, streamed (day files are gigabytes)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_state(out_dir: Path) -> dict[str, Any]:
    """Previously shipped files, {} when absent, corrupt, or not a dict."""
    try:
        state = json.loads((out_dir / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def save_state(out_dir: Path, state: dict[str, Any]) -> None:
    """Atomic state write: a torn `shipped.json` must never strand a day."""
    tmp = out_dir / (STATE_FILE + ".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, out_dir / STATE_FILE)


def dest(remote: str, name: str) -> str:
    """Join an rclone remote and a file name with `/`, never `:`.

    `gdrive:crypto-ticks` + `ticks_D.jsonl.gz` must become
    `gdrive:crypto-ticks/ticks_D.jsonl.gz` — a second colon is an invalid
    address. A leading-dash remote would be parsed as rclone flags.
    """
    remote = remote.strip()
    if not remote or remote.startswith("-"):
        raise ValueError(f"refusing bad DRIVE_REMOTE {remote!r}")
    return remote.rstrip("/") + "/" + name


def rclone_copyto(local: Path, remote_dest: str, timeout: float = 600.0) -> bool:
    """Upload one file via `rclone copyto`; False (never raise) on failure.

    Failure logs carry only the return code and the file name — never
    stderr, which can echo auth config (CWE-532).
    """
    try:
        r = subprocess.run(
            ["rclone", "copyto", str(local), remote_dest],
            capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        print(f"ship: rclone failed for {local.name} ({type(e).__name__})", flush=True)
        return False
    if r.returncode != 0:
        print(f"ship: rclone rc={r.returncode} for {local.name}", flush=True)
        return False
    return True


def ship_file(path: Path, remote: str, digest: str,
              manifest_src: Path | None = None) -> bool:
    """Upload one closed day + `.sha256` sidecar + ship-manifest snapshot.

    Returns True only when all three land. The sidecar uses `sha256sum`
    format (`<hex><two spaces><name>`) so a plain `sha256sum -c` verifies a
    pull. The snapshot records collector state AT SHIP TIME (not the day's
    content) for #281's provenance audit.
    """
    sidecar = path.with_name(path.name + ".sha256")
    sidecar.write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    snap = path.with_name(path.name + ".ship-manifest.json")
    manifest: Any = None
    if manifest_src is not None and manifest_src.is_file():
        try:
            manifest = json.loads(manifest_src.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            manifest = None
    snap.write_text(json.dumps({"shipped_ts": time.time(), "day_file": path.name,
                                "collector_manifest": manifest}), encoding="utf-8")
    ok = rclone_copyto(path, dest(remote, path.name))
    ok = rclone_copyto(sidecar, dest(remote, sidecar.name)) and ok
    ok = rclone_copyto(snap, dest(remote, snap.name)) and ok
    return ok


def prune_local(path: Path) -> None:
    """Delete a shipped day and its sidecars (best effort)."""
    for p in (path,
              path.with_name(path.name + ".sha256"),
              path.with_name(path.name + ".ship-manifest.json")):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


def ship_all(out_dir: Path, remote: str, today: str,
             manifest_src: Path | None = None,
             min_age_s: float = MIN_STABLE_AGE_S,
             prune: bool = True) -> dict[str, list[str]]:
    """Ship every unshipped closed day. Returns {shipped, failed, skipped}.

    One digest per file per pass (day files are gigabytes — never re-read).
    Freshly-rolled files wait for `min_age_s` of silence. Successfully
    shipped days are pruned locally so the small trial disk never fills.
    A skip also prunes: if the process died between state-save and prune,
    the next pass must finish the cleanup or files accumulate forever.
    """
    dest(remote, "probe")  # fail fast on config, before touching files
    state = load_state(out_dir)
    summary: dict[str, list[str]] = {"shipped": [], "failed": [], "skipped": []}
    for path in closed_day_files(out_dir, today):
        try:
            if not is_stable(path, min_age_s):
                continue  # rolled moments ago; next pass will take it
            digest = sha256_of(path)
            entry = state.get(path.name)
            if (isinstance(entry, dict) and entry.get("sha256") == digest
                    and entry.get("remote") == dest(remote, path.name)):
                if prune:
                    prune_local(path)  # retry the cleanup a crash skipped
                summary["skipped"].append(path.name)
                continue
            if ship_file(path, remote, digest, manifest_src):
                state[path.name] = {"sha256": digest,
                                    "remote": dest(remote, path.name),
                                    "shipped_ts": time.time()}
                save_state(out_dir, state)
                summary["shipped"].append(path.name)
                if prune:
                    prune_local(path)
            else:
                summary["failed"].append(path.name)
        except Exception as e:
            # One bad file (unreadable, vanishing, unwritable sidecar) must
            # not abort the later files in the same pass.
            print(f"ship: {path.name} failed ({type(e).__name__}: {e})", flush=True)
            summary["failed"].append(path.name)
    return summary


def main(argv: list[str] | None = None) -> int:
    """Run one Drive shipping pass from the command line."""
    ap = argparse.ArgumentParser(description="Ship closed tick days to Drive.")
    ap.add_argument("--out", type=Path, default=None,
                    help="tick dir; default follows COLLECT_OUT like the watchdog")
    ap.add_argument("--remote", default=os.environ.get("DRIVE_REMOTE", "").strip(),
                    help="rclone destination, e.g. gdrive:crypto-ticks")
    ap.add_argument("--today", default=None,
                    help="override day key (tests); default: current UTC day")
    a = ap.parse_args(argv)
    if not a.remote or a.remote.startswith("-"):
        print("ship: bad or missing remote (use --remote or DRIVE_REMOTE)")
        return 2
    from scripts.collect_ticks import now_day_key
    from scripts.collector_watchdog import collect_out_dir
    today = a.today or now_day_key()
    out_dir: Path = a.out or collect_out_dir()
    try:
        summary = ship_all(out_dir, a.remote, today, out_dir / "manifest.json")
    except ValueError as e:
        print(f"ship: {e}")
        return 2
    print(f"ship: {summary}", flush=True)
    return 0 if not summary["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
