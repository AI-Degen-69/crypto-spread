"""Regression tests for the collector watchdog (`scripts.collector_watchdog`).

The watchdog exists to keep an irreplaceable capture running unattended. It has
already caused one incident by getting this wrong: a failed liveness probe was
read as "no collector", a second collector was started against a live one, and
two processes appended to the same tick file — 2,799 per-cid timestamp
regressions, which `build_cache` then refuses to load.

Every branch of that decision is exercised here, because the failure mode is
silent corruption of data that cannot be recollected.
"""
import os
import threading
import time
from unittest import mock

import pytest

from scripts import collector_watchdog as wd


@pytest.fixture(autouse=True)
def _quiet(monkeypatch, tmp_path):
    """Keep test runs out of the real `run/watchdog.log`.

    Pins the Windows probe path (CI runs Linux — without the pin, the
    legacy probe tests below would take the POSIX branch) and clears the
    host env overrides so one export cannot leak across tests.
    """
    monkeypatch.setattr(wd, "LOG", tmp_path / "watchdog.log")
    monkeypatch.setattr(wd, "MANIFEST", tmp_path / "manifest.json")
    monkeypatch.setattr(wd.os, "name", "nt")
    monkeypatch.delenv("COLLECT_OUT", raising=False)
    monkeypatch.delenv("COLLECT_EXTRA_ARGS", raising=False)


def _run_once(pids, *, manifest_age=1.0):
    """One watchdog pass against a given probe result. Returns (started, killed)."""
    started, killed = [], []
    with mock.patch.object(wd, "collector_pids", return_value=pids), \
            mock.patch.object(wd, "manifest_age", return_value=manifest_age), \
            mock.patch.object(wd, "start_collector",
                              side_effect=lambda: started.append(True)), \
            mock.patch.object(wd, "kill", side_effect=killed.append), \
            mock.patch.object(wd.time, "sleep", lambda *_a: None):
        wd.main(["--once", "--stale-seconds", "180"])
    return started, killed


def test_a_failed_probe_starts_nothing():
    """The incident. An unknown state is not a dead state.

    `collector_pids()` used to return `[]` both when there was genuinely no
    collector and when the probe itself failed; the caller read that as "start
    another one" and corrupted a live capture.
    """
    started, killed = _run_once(None)
    assert started == [], "a failed probe spawned a collector"
    assert killed == []


def test_no_collector_starts_one():
    """The case the watchdog is actually for."""
    started, killed = _run_once([])
    assert len(started) == 1
    assert killed == []


def test_a_healthy_collector_is_left_alone():
    started, killed = _run_once([1234], manifest_age=5.0)
    assert started == [] and killed == []


def test_a_wedged_collector_is_killed_and_restarted():
    """Alive but no longer writing. A bare liveness check calls this healthy,
    which is why the manifest age is checked at all."""
    started, killed = _run_once([1234], manifest_age=999.0)
    assert killed == [1234]
    assert len(started) == 1


def test_duplicates_are_reduced_to_one_and_nothing_is_started():
    """Two writers interleave lines into one tick file and corrupt it."""
    started, killed = _run_once([111, 222, 333])
    assert len(killed) == 2, f"expected two of three stopped, killed={killed}"
    assert len(set(killed)) == 2
    assert set(killed) < {111, 222, 333}
    assert started == [], "a duplicate cleanup must not also spawn a new one"


def test_a_missing_manifest_is_not_treated_as_wedged():
    """Before the first write there is no manifest; that is not a stall."""
    started, killed = _run_once([1234], manifest_age=None)
    assert started == [] and killed == []


def test_the_probe_reports_unknown_rather_than_raising(monkeypatch):
    """A probe that throws must not take the watchdog down with it."""
    monkeypatch.setattr(wd.subprocess, "run",
                        mock.Mock(side_effect=OSError("boom")))
    assert wd.collector_pids() is None


def test_a_nonzero_probe_exit_is_unknown_not_empty(monkeypatch):
    """A shell that fails prints nothing — indistinguishable from "no
    collector" unless the return code is checked."""
    monkeypatch.setattr(wd.subprocess, "run",
                        mock.Mock(return_value=mock.Mock(returncode=1, stdout="")))
    assert wd.collector_pids() is None


def test_a_successful_probe_parses_the_pids(monkeypatch):
    monkeypatch.setattr(wd.subprocess, "run",
                        mock.Mock(return_value=mock.Mock(
                            returncode=0, stdout="111\r\n222\r\n")))
    assert wd.collector_pids() == [111, 222]


def test_an_empty_successful_probe_really_means_no_collector(monkeypatch):
    """The distinction only works if a clean empty result still reads as []."""
    monkeypatch.setattr(wd.subprocess, "run",
                        mock.Mock(return_value=mock.Mock(returncode=0, stdout="")))
    assert wd.collector_pids() == []


def _as_posix(monkeypatch):
    """Run the following probe/kill through the managed-host (Linux) path."""
    monkeypatch.setattr(wd.os, "name", "posix")


def test_posix_probe_parses_pids(monkeypatch):
    _as_posix(monkeypatch)
    monkeypatch.setattr(wd.subprocess, "run",
                        mock.Mock(return_value=mock.Mock(
                            returncode=0, stdout="111\n222\n")))
    assert wd.collector_pids() == [111, 222]


def test_posix_probe_no_match_is_empty_not_unknown(monkeypatch):
    """`pgrep` exits 1 when nothing matches — that is the healthy empty case."""
    _as_posix(monkeypatch)
    monkeypatch.setattr(wd.subprocess, "run",
                        mock.Mock(return_value=mock.Mock(returncode=1, stdout="")))
    assert wd.collector_pids() == []


def test_posix_probe_other_failure_is_unknown(monkeypatch):
    _as_posix(monkeypatch)
    monkeypatch.setattr(wd.subprocess, "run",
                        mock.Mock(return_value=mock.Mock(returncode=2, stdout="")))
    assert wd.collector_pids() is None


def test_posix_probe_missing_pgrep_is_unknown(monkeypatch):
    """No `pgrep` binary at all must read as unknown, never as "no collector"."""
    _as_posix(monkeypatch)
    monkeypatch.setattr(wd.subprocess, "run",
                        mock.Mock(side_effect=FileNotFoundError("pgrep")))
    assert wd.collector_pids() is None


def test_posix_kill_sends_sigkill(monkeypatch):
    _as_posix(monkeypatch)
    sent = []
    monkeypatch.setattr(wd.os, "kill",
                        lambda pid, sig: sent.append((pid, sig)))
    wd.kill(1234)
    assert sent == [(1234, wd._KILL_SIG)]


def test_posix_kill_of_a_gone_pid_is_silent(monkeypatch):
    """A pid that already exited is the desired end state, not an error."""
    _as_posix(monkeypatch)
    monkeypatch.setattr(wd.os, "kill",
                        mock.Mock(side_effect=ProcessLookupError))
    wd.kill(1234)  # must not raise


def test_collector_cmd_defaults_to_bare_module(monkeypatch):
    monkeypatch.delenv("COLLECT_OUT", raising=False)
    monkeypatch.delenv("COLLECT_EXTRA_ARGS", raising=False)
    # Issue #302: the watchdog restarts the collector with an explicit
    # --allow-rewrite so an automated respawn is loud, never silently blocked.
    assert wd.collector_cmd() == [
        wd.sys.executable, "-m", "scripts.collect_ticks", "--allow-rewrite"]


def test_collector_cmd_honours_host_overrides(monkeypatch):
    monkeypatch.setenv("COLLECT_OUT", "/data")
    monkeypatch.setenv("COLLECT_EXTRA_ARGS", "--gzip")
    assert wd.collector_cmd() == [
        wd.sys.executable, "-m", "scripts.collect_ticks",
        "--out", "/data", "--allow-rewrite", "--gzip"]


def test_windows_kill_still_uses_taskkill(monkeypatch):
    """The legacy Windows path is byte-identical: taskkill /F."""
    run = mock.Mock(return_value=mock.Mock(returncode=0))
    monkeypatch.setattr(wd.subprocess, "run", run)
    wd.kill(1234)
    run.assert_called_once_with(["taskkill", "/PID", "1234", "/F"],
                                capture_output=True, text=True)


def test_kill_signal_is_sigkill_where_it_exists():
    """The POSIX branch must really send SIGKILL, whatever its number."""
    if not hasattr(wd.signal, "SIGKILL"):
        pytest.skip("no SIGKILL on this platform")
    assert wd._KILL_SIG == wd.signal.SIGKILL


def test_posix_kill_without_permission_is_logged_not_raised(monkeypatch, tmp_path):
    """A kill we may not perform must not take the watchdog down."""
    _as_posix(monkeypatch)
    monkeypatch.setattr(wd.os, "kill",
                        mock.Mock(side_effect=PermissionError("denied")))
    wd.kill(1234)  # must not raise
    assert "not permitted" in (tmp_path / "watchdog.log").read_text()


def test_posix_probe_garbage_output_is_unknown(monkeypatch):
    """rc=0 with unparsable text is not an empty healthy result."""
    _as_posix(monkeypatch)
    monkeypatch.setattr(wd.subprocess, "run",
                        mock.Mock(return_value=mock.Mock(returncode=0, stdout="???")))
    assert wd.collector_pids() is None


def test_collector_cmd_refuses_once(monkeypatch, tmp_path):
    """`--once` under the watchdog means an instant exit + restart churn."""
    monkeypatch.setenv("COLLECT_EXTRA_ARGS", "--gzip --once")
    assert wd.collector_cmd() == [
        wd.sys.executable, "-m", "scripts.collect_ticks",
        "--allow-rewrite", "--gzip"]
    assert "--once" in (tmp_path / "watchdog.log").read_text()


def test_manifest_follows_collect_out(monkeypatch, tmp_path):
    """With COLLECT_OUT set, the watchdog watches the redirected manifest —
    otherwise it would read a stale default file and declare a live
    collector wedged."""
    monkeypatch.setenv("COLLECT_OUT", str(tmp_path / "data"))
    assert wd.manifest_path() == tmp_path / "data" / "manifest.json"
    assert wd.collect_out_dir() == tmp_path / "data"


def test_manifest_defaults_locally(monkeypatch, tmp_path):
    assert wd.manifest_path() == tmp_path / "manifest.json"


def test_shipper_off_by_default(monkeypatch):
    """No DRIVE_REMOTE: the loop is exactly the legacy watchdog."""
    monkeypatch.delenv("DRIVE_REMOTE", raising=False)
    with mock.patch.object(wd, "collector_pids", return_value=[1234]), \
            mock.patch.object(wd, "manifest_age", return_value=1.0), \
            mock.patch.object(wd, "start_collector"), \
            mock.patch.object(wd, "kill"), \
            mock.patch("scripts.ship_to_drive.ship_all") as ship_all, \
            mock.patch.object(wd.time, "sleep", lambda *_a: None):
        wd.main(["--once", "--stale-seconds", "180"])
    ship_all.assert_not_called()


def test_shipper_pass_runs_when_remote_set(monkeypatch, tmp_path):
    """DRIVE_REMOTE set: one shipper pass per loop, failures contained."""
    from scripts import ship_to_drive as sh
    monkeypatch.setenv("DRIVE_REMOTE", "gdrive:ticks")
    monkeypatch.setattr(wd, "MANIFEST", tmp_path / "manifest.json")
    day = tmp_path / "ticks_2026-09-19.jsonl.gz"
    day.write_bytes(b"v1")
    old = time.time() - 3600
    os.utime(day, (old, old))  # closed AND write-stable
    with mock.patch.object(wd, "collector_pids", return_value=[1234]), \
            mock.patch.object(wd, "manifest_age", return_value=1.0), \
            mock.patch.object(wd, "start_collector"), \
            mock.patch.object(wd, "kill"), \
            mock.patch.object(sh.subprocess, "run",
                              mock.Mock(return_value=mock.Mock(
                                  returncode=0, stderr=""))), \
            mock.patch.object(wd.time, "sleep", lambda *_a: None), \
            mock.patch("scripts.collect_ticks.now_day_key",
                       return_value="2026-09-20"):
            with mock.patch.dict(wd.os.environ, {"COLLECT_OUT": str(tmp_path)}):
                wd.main(["--once", "--stale-seconds", "180"])
                if wd._ship_thread is not None:
                    # join under the mocks: the pass runs off-loop
                    wd._ship_thread.join(timeout=30)
    assert (tmp_path / "shipped.json").is_file()


def test_shipper_pass_never_overlaps_itself(monkeypatch):
    """A slow pass blocks the next kick: shipping never piles up threads."""
    started = threading.Event()
    release = threading.Event()

    def slow(remote):
        started.set()
        release.wait(timeout=30)

    monkeypatch.setenv("DRIVE_REMOTE", "gdrive:ticks")
    monkeypatch.setattr(wd, "_ship_pass", slow)
    wd._ship_thread = None
    try:
        wd.maybe_ship()
        assert started.wait(timeout=10)
        first = wd._ship_thread
        assert first is not None and first.is_alive()
        wd.maybe_ship()  # must not spawn a second worker
        assert wd._ship_thread is first
    finally:
        release.set()
        if wd._ship_thread is not None:
            wd._ship_thread.join(timeout=10)
        wd._ship_thread = None


def test_whitespace_collect_out_stays_on_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("COLLECT_OUT", "   ")
    assert wd.collect_out_dir() == wd.ROOT / "run" / "ticks"
    assert wd.manifest_path() == tmp_path / "manifest.json"


def test_relative_collect_out_resolves_against_root(monkeypatch):
    monkeypatch.setenv("COLLECT_OUT", "data")
    assert wd.collect_out_dir() == wd.ROOT / "data"


def test_extra_out_is_refused_with_its_value(monkeypatch):
    monkeypatch.setenv("COLLECT_OUT", "/data")
    monkeypatch.setenv("COLLECT_EXTRA_ARGS", "--gzip --out /evil --days 1")
    assert wd.collector_cmd() == [
        wd.sys.executable, "-m", "scripts.collect_ticks",
        "--out", "/data", "--allow-rewrite", "--gzip", "--days", "1"]


def test_extra_out_equals_form_is_refused(monkeypatch):
    monkeypatch.setenv("COLLECT_EXTRA_ARGS", "--out=/evil --gzip")
    assert wd.collector_cmd() == [
        wd.sys.executable, "-m", "scripts.collect_ticks",
        "--allow-rewrite", "--gzip"]


def test_partial_garbage_probe_is_unknown(monkeypatch):
    """One bad token poisons the whole probe — a partial pid list is not
    a state to act on."""
    _as_posix(monkeypatch)
    monkeypatch.setattr(wd.subprocess, "run",
                        mock.Mock(return_value=mock.Mock(returncode=0, stdout="123 ???")))
    assert wd.collector_pids() is None
