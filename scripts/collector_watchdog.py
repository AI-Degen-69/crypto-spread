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
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "run" / "watchdog.log"
MANIFEST = ROOT / "run" / "ticks" / "manifest.json"


def collect_out_dir() -> Path:
    """Tick output dir the guarded collector writes to (issue #283).

    Follows `COLLECT_OUT` when set (managed host with a mounted disk);
    otherwise the local default. Stripped, so a whitespace-only value
    cannot become `--out " "`. A relative value resolves against ROOT so
    the watchdog and the collector (spawned with `cwd=ROOT`) read the
    same directory.
    """
    out = os.environ.get("COLLECT_OUT", "").strip()
    if not out:
        return ROOT / "run" / "ticks"
    p = Path(out)
    return p if p.is_absolute() else ROOT / p


def manifest_path() -> Path:
    """Manifest the watchdog must watch: the one inside the output dir.

    The collector writes `manifest.json` into its own `out_dir`
    (`collect_ticks.update_manifest`), so watching the fixed default while
    the collector was redirected elsewhere would read a stale file forever
    and declare the live process wedged.
    """
    if os.environ.get("COLLECT_OUT", "").strip():
        return collect_out_dir() / "manifest.json"
    return MANIFEST

# Windows process-creation flags: no console, own process group, so the
# collector survives the shell that started the watchdog.
DETACHED = 0x00000008 | 0x00000200

# Kill signal for the POSIX path. SIGKILL exists on every managed Linux host;
# the getattr fallback is for the Windows dev machine, where this branch only
# ever runs under tests.
_KILL_SIG = getattr(signal, "SIGKILL", signal.SIGTERM)


def log(msg: str) -> None:
    """Append one timestamped line to the watchdog log and to stdout."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}\n"
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line)
    print(line, end="", flush=True)


def collector_pids() -> list[int] | None:
    """PIDs of running `collect_ticks` processes, or None if unknowable.

    Order is whatever the OS query returns; it is NOT sorted by start time, so
    callers must not read any element as "the newest".

    The distinction is the whole point. This used to return `[]` both when
    there was genuinely no collector and when the probe itself failed, and the
    caller treated `[]` as "start another one". On 2026-09-15 the machine slept,
    the probe returned a nonsense timeout, and the watchdog started a second
    collector against a live one. Two processes appended to the same tick file
    and produced 2,799 per-cid timestamp regressions -- the exact corruption
    `build_cache` now refuses to load.

    An unknown state is not a dead state. The caller does nothing on None.
    """
    if os.name == "nt":
        return _collector_pids_windows()
    return _collector_pids_posix()


def _collector_pids_windows() -> list[int] | None:
    """Windows probe via the process command lines (unchanged legacy path)."""
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


# Full module path, not the bare stem: `pgrep -f collect_ticks` also matches
# editors, test runners, and one-shot `--once` proof runs whose command line
# merely contains the substring. The residual `--once` overlap is handled by
# refusing `--once` in COLLECT_EXTRA_ARGS (see collector_cmd) and by the
# runbook rule: never run a manual `--once` on the host while the worker
# is up — it can read as a duplicate and get killed.
_PGREP_PATTERN = "scripts.collect_ticks"


def _collector_pids_posix() -> list[int] | None:
    """POSIX probe via `pgrep -f` (managed Linux host path, issue #283).

    Same contract as the Windows probe: a list of PIDs, `[]` only when the
    probe ran clean and matched nothing, None when the state is unknowable.
    `pgrep` exits 1 on "no match", which is the healthy empty case — any
    other failure (or a missing `pgrep` binary) is unknown, never empty.
    """
    try:
        out = subprocess.run(
            ["pgrep", "-f", _PGREP_PATTERN],
            capture_output=True, text=True, timeout=30)
    except Exception as e:  # includes FileNotFoundError: no pgrep installed
        log(f"pid probe failed ({e}); state unknown, taking no action")
        return None
    if out.returncode == 1 and not out.stdout.strip():
        return []
    if out.returncode != 0:
        log(f"pid probe rc={out.returncode}; state unknown, taking no action")
        return None
    toks = out.stdout.split()
    pids = [int(x) for x in toks if x.strip().isdigit()]
    if len(pids) != len(toks):
        log(f"pid probe rc=0 but unparsable output {out.stdout!r}; treating as unknown")
        return None
    return pids


def collector_cmd() -> list[str]:
    """Build the collector command, with managed-host overrides (issue #283).

    `COLLECT_OUT` redirects the tick output dir (e.g. at a mounted disk);
    `COLLECT_EXTRA_ARGS` appends flags such as `--gzip`. Unset means the
    local defaults, so Windows behavior is unchanged.

    `--once` is refused: under the watchdog it would exit instantly and be
    restarted in a tight loop, and on the host its command line trips the
    duplicate-collector path. `--out` is refused too: argparse takes the
    last `--out`, so a smuggled one would run the collector elsewhere while
    `manifest_path()` watches the redirect — a permanent false `WEDGED`
    loop. Use `COLLECT_OUT` for the output dir. Simple whitespace split —
    quoted values with spaces are not supported; keep host flags to tokens.
    """
    cmd = [sys.executable, "-m", "scripts.collect_ticks"]
    out = os.environ.get("COLLECT_OUT", "").strip()
    if out:
        cmd += ["--out", out]
    # Issue #302: watchdog respawns are explicit rewrite-allowing restarts —
    # the collector appends to today's file, so the guard never fires in the
    # normal path; the flag keeps an automated restart loud instead of
    # silently blocked if a future write path ever becomes truncating.
    cmd += ["--allow-rewrite"]
    extra = os.environ.get("COLLECT_EXTRA_ARGS", "").split()
    cleaned: list[str] = []
    skip_next = False
    refused = False
    for a in extra:
        if skip_next:
            skip_next = False
            continue
        if a == "--once" or a == "--out":
            refused = True
            if a == "--out":
                skip_next = True  # drop its value token as well
            continue
        if a.startswith("--out="):
            refused = True
            continue
        cleaned.append(a)
    if refused:
        log("COLLECT_EXTRA_ARGS contained --once/--out; refused (use COLLECT_OUT for the output dir)")
    return cmd + cleaned


def start_collector() -> int | None:
    """Spawn a detached collector; returns its PID, or None if it failed.

    Detached so the collector outlives the shell -- and the watchdog -- that
    started it. stdout/stderr append rather than truncate, so a restart never
    erases the previous process's last words.
    """
    out = ROOT / "run" / "collector.out.log"
    err = ROOT / "run" / "collector.log"
    try:
        # DETACHED is a Windows-only flag; on POSIX the child simply
        # inherits the watchdog's session, which is what a managed host
        # (Render/Fly) expects of its start command.
        popen_kw: dict[str, object] = {"close_fds": True}
        if os.name == "nt":
            popen_kw["creationflags"] = DETACHED
        with open(out, "ab") as fo, open(err, "ab") as fe:
            p = subprocess.Popen(
                collector_cmd(),
                cwd=str(ROOT), stdout=fo, stderr=fe, **popen_kw)
        log(f"STARTED collector pid={p.pid}")
        return p.pid
    except Exception as e:
        log(f"START FAILED: {e}")
        return None


def kill(pid: int) -> None:
    """Force-terminate a collector so a restart cannot double-append ticks."""
    if os.name == "nt":
        r = subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            log(f"taskkill pid={pid} rc={r.returncode}; leaving it alone")
        return
    try:
        os.kill(pid, _KILL_SIG)
    except ProcessLookupError:
        pass  # already gone — the desired end state
    except PermissionError as e:
        log(f"kill pid={pid} not permitted ({e}); leaving it alone")


def manifest_age() -> float | None:
    """Seconds since the collector last wrote its manifest, None if absent."""
    try:
        return time.time() - manifest_path().stat().st_mtime
    except OSError:
        return None


_ship_thread: threading.Thread | None = None


def _ship_pass(remote: str) -> None:
    """Run one Drive-shipper pass to completion (worker thread body)."""
    try:
        from scripts.collect_ticks import now_day_key
        from scripts.ship_to_drive import ship_all
        out_dir = collect_out_dir()
        summary = ship_all(out_dir, remote, now_day_key(), out_dir / "manifest.json")
        if summary["shipped"] or summary["failed"]:
            log(f"ship: {summary}")
    except Exception as e:
        log(f"ship pass failed ({type(e).__name__}: {e}); capture continues")


def maybe_ship() -> None:
    """Kick off a Drive-shipper pass, only when the host asked for it (#285).

    Gated on `DRIVE_REMOTE`: unset means local/Windows runs behave exactly
    as before. The pass runs on a daemon worker thread, never inline: one
    backlogged file can cost three 600s rclone timeouts, and the liveness
    loop must keep watching the collector during that window. A second pass
    never overlaps the first — it simply waits for the next loop.
    """
    global _ship_thread
    remote = os.environ.get("DRIVE_REMOTE", "").strip()
    if not remote:
        return
    if _ship_thread is not None and _ship_thread.is_alive():
        return
    _ship_thread = threading.Thread(target=_ship_pass, args=(remote,),
                                    daemon=True, name="drive-ship")
    _ship_thread.start()


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
                # corrupt it, so all but one must go. Which one survives does
                # not matter for the data -- the file is already interleaved by
                # the time this is noticed, and every survivor writes the same
                # stream from here on. `collector_pids()` does not order by
                # start time, so this keeps one arbitrarily rather than
                # claiming to keep the newest.
                keep = pids[0]
                log(f"DUPLICATES: {pids}; keeping {keep}, stopping the rest")
                for pid in pids[1:]:
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
        maybe_ship()
        if a.once:
            return 0
        time.sleep(a.check_seconds)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
