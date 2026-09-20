"""Regression tests for the collector watchdog (`scripts.collector_watchdog`).

The watchdog exists to keep an irreplaceable capture running unattended. It has
already caused one incident by getting this wrong: a failed liveness probe was
read as "no collector", a second collector was started against a live one, and
two processes appended to the same tick file — 2,799 per-cid timestamp
regressions, which `build_cache` then refuses to load.

Every branch of that decision is exercised here, because the failure mode is
silent corruption of data that cannot be recollected.
"""
from unittest import mock

import pytest

from scripts import collector_watchdog as wd


@pytest.fixture(autouse=True)
def _quiet(monkeypatch, tmp_path):
    """Keep test runs out of the real `run/watchdog.log`."""
    monkeypatch.setattr(wd, "LOG", tmp_path / "watchdog.log")
    monkeypatch.setattr(wd, "MANIFEST", tmp_path / "manifest.json")


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
    assert wd.collector_cmd() == [wd.sys.executable, "-m", "scripts.collect_ticks"]


def test_collector_cmd_honours_host_overrides(monkeypatch):
    monkeypatch.setenv("COLLECT_OUT", "/data")
    monkeypatch.setenv("COLLECT_EXTRA_ARGS", "--gzip")
    assert wd.collector_cmd() == [
        wd.sys.executable, "-m", "scripts.collect_ticks", "--out", "/data", "--gzip"]


def test_windows_kill_still_uses_taskkill(monkeypatch):
    """The legacy Windows path is byte-identical: taskkill /F."""
    monkeypatch.setattr(wd.os, "name", "nt")
    run = mock.Mock(return_value=mock.Mock(returncode=0))
    monkeypatch.setattr(wd.subprocess, "run", run)
    wd.kill(1234)
    run.assert_called_once_with(["taskkill", "/PID", "1234", "/F"],
                                capture_output=True, text=True)
