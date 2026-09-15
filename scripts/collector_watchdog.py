"""Keep the tick collector alive unattended.

The collector is the only irreplaceable process here: a crash at 03:00 costs the
whole night's capture and nothing else notices. This restarts it and records
every event, so the morning's first question -- "did it actually run all night?"
-- has a written answer instead of being inferred from a file size.

Two failure modes, not one:

  * the process is gone -- start a new one;
  * the process is alive but wedged -- the collector rewrites
    ``run/ticks/manifest.json`` roughly every 10s, so a manifest that has not
    moved in minutes means a bare liveness check would report "healthy" while
    nothing is being captured. Kill it, then restart, so two processes never
    append to the same tick file.

Run detached:

    python -m scripts.collector_watchdog
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "run" / "watchdog.log"
MANIFEST = ROOT / "run" / "ticks" / "manifest.json"

# Windows process-creation flags: no console, own process group, so the
# collector survives the shell that started the watchdog.
DETACHED = 0x00000008 | 0x00000200


def log(msg: str) -> None:
    """Append one timestamped line to the watchdog log and to stdout."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}\n"
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line)
    print(line, end="", flush=True)


def collector_pids() -> list[int] | None:
    """PIDs of running `collect_ticks` processes, or None if unknowable.

    The distinction is the whole point. This used to return `[]` both when
    there was genuinely no collector and when the probe itself failed, and the
    caller treated `[]` as "start another one". On 2026-09-15 the machine slept,
    the probe returned a nonsense timeout, and the watchdog started a second
    collector against a live one. Two processes appended to the same tick file
    and produced 2,799 per-cid timestamp regressions -- the exact corruption
    `build_cache` now refuses to load.

    An unknown state is not a dead state. The caller does nothing on None.
    """
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Where-Object { $_.CommandLine -like '*collect_ticks*' } | "
             "ForEach-Object { $_.ProcessId }"],
            capture_output=True, text=True, timeout=30)
    except Exception as e:  # a failed probe must not kill the watchdog
        log(f"pid probe failed ({e}); state unknown, taking no action")
        return None
    if out.returncode != 0:
        log(f"pid probe rc={out.returncode}; state unknown, taking no action")
        return None
    return [int(x) for x in out.stdout.split() if x.strip().isdigit()]


def start_collector() -> int | None:
    """Spawn a detached collector; returns its PID, or None if it failed.

    Detached so the collector outlives the shell -- and the watchdog -- that
    started it. stdout/stderr append rather than truncate, so a restart never
    erases the previous process's last words.
    """
    out = ROOT / "run" / "collector.out.log"
    err = ROOT / "run" / "collector.log"
    try:
        with open(out, "ab") as fo, open(err, "ab") as fe:
            p = subprocess.Popen(
                [sys.executable, "-m", "scripts.collect_ticks"],
                cwd=str(ROOT), stdout=fo, stderr=fe,
                creationflags=DETACHED if os.name == "nt" else 0,
                close_fds=True)
        log(f"STARTED collector pid={p.pid}")
        return p.pid
    except Exception as e:
        log(f"START FAILED: {e}")
        return None


def kill(pid: int) -> None:
    """Force-terminate a collector so a restart cannot double-append ticks."""
    subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                   capture_output=True, text=True)


def manifest_age() -> float | None:
    """Seconds since the collector last wrote its manifest, None if absent."""
    try:
        return time.time() - MANIFEST.stat().st_mtime
    except OSError:
        return None


def main(argv: list[str]) -> int:
    """Check liveness forever, restarting a dead or wedged collector."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--check-seconds", type=float, default=60.0)
    ap.add_argument("--stale-seconds", type=float, default=180.0,
                    help="manifest silence that counts as wedged")
    ap.add_argument("--once", action="store_true",
                    help="run a single check and exit (for tests)")
    a = ap.parse_args(argv)

    log(f"watchdog up (check={a.check_seconds:.0f}s stale={a.stale_seconds:.0f}s)")
    restarts = 0
    while True:
        try:
            pids = collector_pids()
            if pids is None:
                pass  # probe failed; never act on a state we could not read
            elif len(pids) > 1:
                # Two writers append interleaved lines to one tick file and
                # corrupt it. Keep the newest and stop the rest immediately.
                keep = pids[-1]
                log(f"DUPLICATES: {pids}; keeping {keep}")
                for pid in pids[:-1]:
                    kill(pid)
            elif not pids:
                log("DOWN: no collect_ticks process")
                start_collector()
                restarts += 1
                log(f"restart count = {restarts}")
            else:
                age = manifest_age()
                if age is not None and age > a.stale_seconds:
                    log(f"WEDGED: manifest {age:.0f}s stale, killing {pids}")
                    for pid in pids:
                        kill(pid)
                    time.sleep(3)
                    start_collector()
                    restarts += 1
                    log(f"restart count = {restarts}")
        except Exception as e:
            # A watchdog that dies on a transient error is worse than none.
            log(f"watchdog error: {type(e).__name__}: {e}")
        if a.once:
            return 0
        time.sleep(a.check_seconds)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
