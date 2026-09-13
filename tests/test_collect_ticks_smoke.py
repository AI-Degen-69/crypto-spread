"""Smoke tests for scripts/collect_ticks.py — no live network.

Verifies the file/manifest schema, slate, and CLI arg parsing.
"""
from __future__ import annotations
import gzip
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "collect_ticks.py"


def _build_snap() -> dict:
    return {
        "ts": 1700000000.0, "iso": "2023-11-14T22:13:20+00:00",
        "series": "btc-up-or-down-5m", "duration": 300, "label": "BTC 5m",
        "cid": "0xDEAD", "slug": "btc-updown-5m-1700000000",
        "start_ts": 1700000000.0, "end_ts": 1700000300.0, "t_rem": 300.0,
        "up_book": {"bids": {"0.49": 100.0}, "asks": {"0.51": 100.0},
                    "best_bid": 0.49, "best_ask": 0.51, "malformed": 0,
                    "token_id": "0xA"},
        "down_book": {"bids": {"0.49": 100.0}, "asks": {"0.51": 100.0},
                      "best_bid": 0.49, "best_ask": 0.51, "malformed": 0,
                      "token_id": "0xB"},
        "tape_delta": [],
        "mid": 0.50, "touch_pair": 1.02, "resting_pair": 0.96,
        "queue_up": 0.0, "queue_down": 0.0, "err": None,
    }


def test_ticks_file_roundtrip(tmp_path: Path):
    """Write a synthetic tick file and read it back via iter_ticks."""
    from backtest import iter_ticks
    f = tmp_path / "ticks_2026-08-29.jsonl"
    line = json.dumps(_build_snap()) + "\n"
    f.write_text(line, encoding="utf-8")
    out = list(iter_ticks(f))
    assert len(out) == 1
    assert out[0]["cid"] == "0xDEAD"
    assert out[0]["up_book"]["bids"]["0.49"] == 100.0

def test_gzip_roundtrip(tmp_path: Path):
    from backtest import iter_ticks
    f = tmp_path / "ticks_2026-08-29.jsonl.gz"
    with gzip.open(f, "wt", encoding="utf-8") as gz:
        gz.write(json.dumps(_build_snap()) + "\n")
    out = list(iter_ticks(f))
    assert len(out) == 1

def test_module_imports_cleanly():
    import importlib
    mod = importlib.import_module("scripts.collect_ticks")
    assert hasattr(mod, "poll_once")
    assert hasattr(mod, "SERIES") or mod.SERIES is not None
    # SERIES comes from strategy.series via the module's import
    from strategy.series import SERIES as S
    assert mod.SERIES == S

def test_cli_help_runs():
    """--help must exit 0 even if no API is reachable."""
    r = subprocess.run(
        [sys.executable, "-m", "scripts.collect_ticks", "--help"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=10,
    )
    assert r.returncode == 0
    assert "out" in r.stdout or "ticks" in r.stdout.lower()


def test_cli_exposes_no_ws_flag():
    """--no-ws must be advertised so operators can revert to pure REST polling."""
    r = subprocess.run(
        [sys.executable, "-m", "scripts.collect_ticks", "--help"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=10,
    )
    assert r.returncode == 0
    assert "--no-ws" in r.stdout


def test_manifest_includes_ws_telemetry(tmp_path: Path):
    """Socket health fields survive into manifest.json for the dashboard."""
    import scripts.collect_ticks as ct

    ct.update_manifest(tmp_path, {
        "lines": 10, "ws_enabled": True, "ws_connected": False,
        "ws_reconnects": 3, "tape_captured_ws": 120, "tape_captured_rest": 7,
    })
    data = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert data["ws_enabled"] is True
    assert data["ws_connected"] is False
    assert data["ws_reconnects"] == 3
    assert data["tape_captured_ws"] == 120
    assert data["tape_captured_rest"] == 7


def test_manifest_tape_empty_rate_tracking(tmp_path: Path):
    """Verify update_manifest writes public tape stats and strips internal keys."""
    import scripts.collect_ticks as ct

    stats = {
        "lines": 100,
        "series_seen": ["btc-up-or-down-5m"],
        "day": "2026-09-01",
        "tape_empty_count": 98,
        "tape_non_empty_count": 2,
        "tape_empty_rate": 0.98,
        "tape_recent_empty_rate": 0.98,
        "tape_alert": False,
        "tape_entries_total": 5,
        "_tape_window": [{"ts": 1000.0, "empty": True}],
    }
    ct.update_manifest(tmp_path, stats)
    mf = tmp_path / "manifest.json"
    assert mf.exists()
    data = json.loads(mf.read_text(encoding="utf-8"))
    assert data["tape_empty_rate"] == 0.98
    assert data["tape_recent_empty_rate"] == 0.98
    assert data["tape_alert"] is False
    assert data["tape_empty_count"] == 98
    assert data["tape_entries_total"] == 5
    assert data["day"] == "2026-09-01"
    assert "_tape_window" not in data


def test_record_tape_sample_startup_silence():
    """Startup silence under 5 minutes must not trigger tape_alert."""
    import scripts.collect_ticks as ct

    stats = {}
    base_ts = 1000.0
    # 60 polls (1s apart) all empty
    for i in range(60):
        ct.record_tape_sample(stats, base_ts + i, has_trades=False, num_entries=0)

    assert stats["tape_empty_count"] == 60
    assert stats["tape_empty_rate"] == 1.0
    assert stats["tape_recent_empty_rate"] == 1.0
    # Alert must be False because duration is only 59s (< 290s)
    assert stats["tape_alert"] is False


def test_record_tape_sample_sustained_silence_alerts():
    """Sustained silence >= 5 minutes must trigger tape_alert."""
    import scripts.collect_ticks as ct

    stats = {}
    base_ts = 1000.0
    # 300 polls (1s apart) all empty
    for i in range(301):
        ct.record_tape_sample(stats, base_ts + i, has_trades=False, num_entries=0)

    assert stats["tape_empty_rate"] == 1.0
    assert stats["tape_recent_empty_rate"] == 1.0
    assert stats["tape_alert"] is True


def test_record_tape_sample_healthy_then_silent_transition():
    """A healthy collector with trades that later becomes silent triggers alert after 5m."""
    import scripts.collect_ticks as ct

    stats = {}
    base_ts = 1000.0
    # 300s of healthy activity (50% non-empty)
    for i in range(300):
        has_trades = (i % 2 == 0)
        ct.record_tape_sample(stats, base_ts + i, has_trades=has_trades, num_entries=1 if has_trades else 0)

    assert stats["tape_alert"] is False
    assert stats["tape_empty_rate"] == 0.5

    # Next 300s has 100% empty trades
    for i in range(300, 601):
        ct.record_tape_sample(stats, base_ts + i, has_trades=False, num_entries=0)

    # Rolling window only sees the last 300s (all empty) -> alert triggers
    assert stats["tape_recent_empty_rate"] == 1.0
    assert stats["tape_alert"] is True
    # Lifetime rate is still only ~75%, but time-bounded rolling detector caught the outage
    assert stats["tape_empty_rate"] < 0.90


def _build_window_rec(**over):
    from strategy.windows import finalize_window
    base = {
        "series": "btc-up-or-down-5m", "label": "BTC 5m", "duration": 300,
        "cid": "0xC10", "slug": "btc-updown-5m-1",
        "start_ts": 1700000000.0, "end_ts": 1700000300.0,
        "closed_ts": 1700000301.0, "snaps": 300,
    }
    base.update(over)
    return finalize_window([0.51, 0.53, 0.49], [1.01, 1.02], base)


def test_write_window_appends_one_json_line(tmp_path: Path):
    """write_window appends exactly one JSON line per closed window."""
    import scripts.collect_ticks as ct

    ct.write_window(_build_window_rec(), tmp_path)
    ct.write_window(_build_window_rec(cid="0xC11"), tmp_path)
    lines = (tmp_path / "oscillation_windows.jsonl").read_text(
        encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["cid"] == "0xC10"
    assert json.loads(lines[1])["cid"] == "0xC11"


def test_refresh_summary_writes_ts_and_per_series(tmp_path: Path):
    """refresh_summary rebuilds oscillation_summary.json with ts + per_series."""
    import scripts.collect_ticks as ct

    ct.write_window(_build_window_rec(), tmp_path)
    ct.refresh_summary(tmp_path)
    data = json.loads((tmp_path / "oscillation_summary.json").read_text(
        encoding="utf-8"))
    assert data["ts"] > 0
    assert data["per_series"]["btc-up-or-down-5m"]["windows"] == 1
    assert data["per_series"]["eth-up-or-down-5m"]["windows"] == 0


def test_refresh_summary_on_empty_dir(tmp_path: Path):
    """refresh_summary on a dir without a windows file writes a zeroed summary."""
    import scripts.collect_ticks as ct

    ct.refresh_summary(tmp_path)
    data = json.loads((tmp_path / "oscillation_summary.json").read_text(
        encoding="utf-8"))
    assert data["ts"] > 0
    assert data["per_series"]["btc-up-or-down-5m"]["windows"] == 0


def test_run_dir_for_ticks_layout(tmp_path: Path):
    """run_dir_for maps <run>/ticks -> <run> and keeps custom out dirs."""
    import scripts.collect_ticks as ct

    assert ct.run_dir_for(tmp_path / "ticks") == tmp_path
    assert ct.run_dir_for(tmp_path / "custom") == tmp_path / "custom"


# --- Issue #167 T1: gamma market resolution cache -------------------------

def _market(cid: str = "0xC1", end_ts: float = 2000.0) -> dict:
    """Minimal resolved-market dict shaped like fetch_live_for_series output."""
    return {
        "conditionId": cid, "slug": f"slug-{cid}",
        "start_ts": end_ts - 300.0, "end_ts": end_ts,
        "up_token": f"{cid}-UP", "down_token": f"{cid}-DOWN",
        "series": "btc-up-or-down-5m",
    }


@pytest.fixture
def gamma(monkeypatch):
    """Count fetch_live_for_series calls with a scripted queue of results."""
    import scripts.collect_ticks as ct

    ct.reset_gamma_cache()
    # `seen` is a list, not a counter: poll_once resolves off-thread and `+= 1`
    # is not atomic, while list.append is.
    state = {"seen": [], "queue": [(_market(), None)]}

    def fake_fetch(series_slug: str):
        """Pop the next scripted result, repeating the last one forever."""
        state["seen"].append(series_slug)
        if len(state["queue"]) > 1:
            return state["queue"].pop(0)
        return state["queue"][0]

    monkeypatch.setattr(ct, "fetch_live_for_series", fake_fetch)
    yield state
    ct.reset_gamma_cache()


def test_gamma_cache_serves_within_window(gamma):
    """A second resolve inside the same window issues no second HTTP fetch."""
    import scripts.collect_ticks as ct

    first, err1 = ct.resolve_series_market("btc-up-or-down-5m", now=1000.0)
    second, err2 = ct.resolve_series_market("btc-up-or-down-5m", now=1001.0)
    assert err1 is None and err2 is None
    assert first["conditionId"] == second["conditionId"] == "0xC1"
    assert len(gamma["seen"]) == 1


def test_gamma_cache_reresolves_after_end_ts(gamma):
    """Once the window rolls the cached market must not be served again."""
    import scripts.collect_ticks as ct

    gamma["queue"] = [(_market("0xOLD", end_ts=2000.0), None),
                      (_market("0xNEW", end_ts=2300.0), None)]
    assert ct.resolve_series_market("btc-up-or-down-5m", now=1900.0)[0]["conditionId"] == "0xOLD"
    got, _ = ct.resolve_series_market("btc-up-or-down-5m", now=2001.0)
    assert got["conditionId"] == "0xNEW"
    assert len(gamma["seen"]) == 2


def test_gamma_cache_reresolves_after_max_age(gamma):
    """A bounded max age forces a re-resolve even while the window is open."""
    import scripts.collect_ticks as ct

    far = _market("0xC1", end_ts=1_000_000.0)
    gamma["queue"] = [(far, None)]
    ct.resolve_series_market("btc-up-or-down-5m", now=1000.0)
    ct.resolve_series_market("btc-up-or-down-5m", now=1000.0 + ct.GAMMA_CACHE_MAX_AGE - 1.0)
    assert len(gamma["seen"]) == 1
    ct.resolve_series_market("btc-up-or-down-5m", now=1000.0 + ct.GAMMA_CACHE_MAX_AGE + 1.0)
    assert len(gamma["seen"]) == 2


def test_gamma_cache_does_not_cache_failure(gamma):
    """A gamma error is never stored, so the next tick retries the lookup."""
    import scripts.collect_ticks as ct

    gamma["queue"] = [(None, "gamma err boom"), (_market("0xOK"), None)]
    info, err = ct.resolve_series_market("btc-up-or-down-5m", now=1000.0)
    assert info is None and err == "gamma err boom"
    info, err = ct.resolve_series_market("btc-up-or-down-5m", now=1000.5)
    assert err is None and info["conditionId"] == "0xOK"
    assert len(gamma["seen"]) == 2


def test_gamma_cache_picks_up_replaced_market(gamma):
    """A market swapped mid-window is adopted once the cache entry ages out."""
    import scripts.collect_ticks as ct

    gamma["queue"] = [(_market("0xA", end_ts=1_000_000.0), None),
                      (_market("0xB", end_ts=1_000_000.0), None)]
    assert ct.resolve_series_market("btc-up-or-down-5m", now=1000.0)[0]["conditionId"] == "0xA"
    later = 1000.0 + ct.GAMMA_CACHE_MAX_AGE + 1.0
    assert ct.resolve_series_market("btc-up-or-down-5m", now=later)[0]["conditionId"] == "0xB"


def test_poll_once_uses_the_gamma_cache(monkeypatch, tmp_path):
    """poll_once must resolve through the cache, not call the fetcher per tick."""
    import scripts.collect_ticks as ct

    ct.reset_gamma_cache()
    ct.windows.clear()
    calls: list[str] = []

    def fake_fetch(series_slug: str):
        """Return a live market for the first series only, recording each call."""
        calls.append(series_slug)
        if series_slug != ct.SERIES[0][0]:
            return None, "no live"
        return _market("0xPOLL", end_ts=time.time() + 300.0), None

    monkeypatch.setattr(ct, "fetch_live_for_series", fake_fetch)
    monkeypatch.setattr(ct, "full_book", lambda host, tok: {
        "bids": {}, "asks": {}, "best_bid": 0.49, "best_ask": 0.51,
        "malformed": 0, "token_id": tok})
    monkeypatch.setattr(ct, "recent_trades", lambda cid, seen, limit=200, **_kw: {})

    stats: dict = {}
    ct.poll_once(tmp_path, False, stats)
    after_first = len(calls)
    ct.poll_once(tmp_path, False, stats)
    # Only the nine failing series re-resolve; the cached live one does not.
    assert len(calls) - after_first == len(ct.SERIES) - 1
    ct.reset_gamma_cache()
    ct.windows.clear()


# --- Issue #167 T3: bounded concurrent fan-out ----------------------------

@pytest.fixture
def slate(monkeypatch):
    """A three-series collector whose gamma/book/tape calls are stubbed out."""
    import scripts.collect_ticks as ct

    ct.reset_gamma_cache()
    ct.windows.clear()
    series = [("a-5m", 300, "A"), ("b-5m", 300, "B"), ("c-5m", 300, "C")]
    monkeypatch.setattr(ct, "SERIES", series)

    def fake_fetch(slug: str):
        """One live market per series, keyed so cid and tokens stay distinct."""
        tag = slug[0]
        return {
            "conditionId": f"0x{tag.upper()}", "slug": f"slug-{tag}",
            "start_ts": time.time() - 10.0, "end_ts": time.time() + 300.0,
            "up_token": f"{tag}_up", "down_token": f"{tag}_dn", "series": slug,
        }, None

    monkeypatch.setattr(ct, "fetch_live_for_series", fake_fetch)
    monkeypatch.setattr(ct, "recent_trades", lambda cid, seen, limit=200, **_kw: {})
    yield ct
    ct.windows.clear()
    ct.reset_gamma_cache()


def _snaps(out_dir: Path) -> list[dict]:
    rows: list[dict] = []
    for f in sorted(out_dir.glob("ticks_*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def test_snaps_are_written_in_series_order_despite_completion_order(slate, tmp_path):
    """Workers finish out of order; the tick file must still follow SERIES."""
    finished: list[str] = []

    def slow_book(host, tok):
        """Invert completion order, and record when each series actually ends."""
        time.sleep({"a": 0.25, "b": 0.1}.get(tok[0], 0.0))
        if tok.endswith("_dn"):
            finished.append(tok[0])
        return {"bids": {}, "asks": {}, "best_bid": 0.49, "best_ask": 0.51,
                "malformed": 0, "token_id": tok}

    slate.full_book = slow_book
    slate.poll_once(tmp_path, False, {})

    # The point of the test is that these two lists disagree: completion order
    # is c, b, a, while the file must still read a, b, c. Without the inversion
    # actually happening the ordering assertion would prove nothing.
    assert finished == ["c", "b", "a"], f"fan-out did not invert: {finished}"
    assert [s["series"] for s in _snaps(tmp_path)] == ["a-5m", "b-5m", "c-5m"]


def test_the_slate_is_actually_concurrent(slate, tmp_path):
    """Three 250ms series must cost about one series, not three."""
    def slow_book(host, tok):
        """Every book takes 400ms, so a sequential round would take 2.4s."""
        time.sleep(0.4)
        return {"bids": {}, "asks": {}, "best_bid": 0.49, "best_ask": 0.51,
                "malformed": 0, "token_id": tok}

    slate.full_book = slow_book
    t0 = time.perf_counter()
    slate.poll_once(tmp_path, False, {})
    elapsed = time.perf_counter() - t0

    # Sequential would be 6 books x 400ms = 2.4s; concurrent is 2 x 400ms plus
    # the stagger ramp, so ~0.85s. The 1.5s threshold sits 1.8x above the
    # concurrent path and 1.6x below the sequential one, leaving margin on a
    # loaded CI box in both directions.
    assert elapsed < 1.5, f"round took {elapsed:.2f}s, fan-out is not concurrent"


def test_one_failing_series_leaves_the_others_writing(slate, tmp_path):
    """A worker raising must isolate to its own snap, not abort the round."""
    def flaky_book(host, tok):
        """The B series blows up on both legs; A and C are healthy."""
        if tok.startswith("b"):
            raise RuntimeError("clob exploded")
        return {"bids": {}, "asks": {}, "best_bid": 0.49, "best_ask": 0.51,
                "malformed": 0, "token_id": tok}

    slate.full_book = flaky_book
    slate.poll_once(tmp_path, False, {})

    snaps = _snaps(tmp_path)
    assert [s["series"] for s in snaps] == ["a-5m", "b-5m", "c-5m"]
    by_series = {s["series"]: s for s in snaps}
    assert by_series["a-5m"]["err"] is None
    assert by_series["c-5m"]["err"] is None
    assert "clob exploded" in by_series["b-5m"]["err"]


def test_a_gamma_failure_still_skips_only_that_series(slate, tmp_path):
    """A series with no live market is reported and skipped, not fatal."""
    def partial_gamma(slug: str):
        """B has no live market this tick."""
        if slug == "b-5m":
            return None, "no live"
        tag = slug[0]
        return {
            "conditionId": f"0x{tag.upper()}", "slug": f"slug-{tag}",
            "start_ts": time.time() - 10.0, "end_ts": time.time() + 300.0,
            "up_token": f"{tag}_up", "down_token": f"{tag}_dn", "series": slug,
        }, None

    slate.fetch_live_for_series = partial_gamma
    slate.full_book = lambda host, tok: {
        "bids": {}, "asks": {}, "best_bid": 0.49, "best_ask": 0.51,
        "malformed": 0, "token_id": tok}
    _closed, errs = slate.poll_once(tmp_path, False, {})

    assert [s["series"] for s in _snaps(tmp_path)] == ["a-5m", "c-5m"]
    assert errs == ["b-5m:no live"]


def test_poll_executor_is_bounded_and_reused():
    """One process-wide pool, sized to the slate — never rebuilt per tick."""
    import scripts.collect_ticks as ct

    ct.shutdown_poll_executor()
    first = ct.get_poll_executor()
    assert first is ct.get_poll_executor()
    assert first._max_workers == ct.MAX_POLL_WORKERS
    # Derived from the slate, not a parallel literal: adding an 11th series
    # must not silently serialize it behind the other ten.
    from strategy.series import SERIES as REAL_SERIES
    assert ct.MAX_POLL_WORKERS == len(REAL_SERIES)
    ct.shutdown_poll_executor()
    assert ct.get_poll_executor() is not first
    ct.shutdown_poll_executor()


def test_workers_are_staggered_to_keep_the_anti_burst_property(slate, tmp_path):
    """Fan-out must ramp its first requests, not fire the slate as one burst."""
    starts: dict[str, float] = {}

    def timed_book(host, tok):
        """Record when each series' first request lands."""
        if tok.endswith("_up"):
            starts[tok[0]] = time.perf_counter()
        return {"bids": {}, "asks": {}, "best_bid": 0.49, "best_ask": 0.51,
                "malformed": 0, "token_id": tok}

    slate.full_book = timed_book
    slate.poll_once(tmp_path, False, {})

    # Assert the property that matters — later series start later — rather than
    # an absolute gap, which is provable from the stagger formula alone and has
    # no margin against the ~15ms sleep granularity on Windows.
    assert list(starts) == ["a", "b", "c"]
    assert starts["a"] < starts["b"] < starts["c"]


def test_every_series_including_the_first_is_jittered(slate, tmp_path,
                                                      monkeypatch):
    """Worker 0 must jitter too, or one series never de-synchronizes."""
    draws: list[tuple[float, float]] = []
    real_uniform = slate.random.uniform

    def spy_uniform(a, b):
        """Record each jitter draw the workers make."""
        draws.append((a, b))
        return real_uniform(a, b)

    monkeypatch.setattr(slate.random, "uniform", spy_uniform)
    slate.full_book = lambda host, tok: {
        "bids": {}, "asks": {}, "best_bid": 0.49, "best_ask": 0.51,
        "malformed": 0, "token_id": tok}
    slate.poll_once(tmp_path, False, {})

    jitter_draws = [d for d in draws if d == (0.0, slate.JITTER_SEC)]
    assert len(jitter_draws) >= len(slate.SERIES)


# --- Issue #167 T4: cadence telemetry and a truthful budget ---------------

def test_a_healthy_round_reports_no_slow_tick(slate, tmp_path):
    """slow_tick must be silent on a round that is comfortably in budget."""
    slate.full_book = lambda host, tok: {
        "bids": {}, "asks": {}, "best_bid": 0.49, "best_ask": 0.51,
        "malformed": 0, "token_id": tok}
    stats: dict = {}
    slate.poll_once(tmp_path, False, stats)          # warm-up round
    _closed, errs = slate.poll_once(tmp_path, False, stats)

    assert [e for e in errs if e.startswith("slow_tick")] == []


def test_a_genuinely_slow_round_still_reports_slow_tick(slate, tmp_path,
                                                        monkeypatch):
    """The budget check must keep its teeth: a real degradation is reported."""
    slate.full_book = lambda host, tok: {
        "bids": {}, "asks": {}, "best_bid": 0.49, "best_ask": 0.51,
        "malformed": 0, "token_id": tok}
    stats: dict = {}
    slate.poll_once(tmp_path, False, stats)          # warm-up round

    monkeypatch.setattr(slate, "TICK_BUDGET_MS", 0.0)
    _closed, errs = slate.poll_once(tmp_path, False, stats)

    assert any(e.startswith("slow_tick:") for e in errs)


def test_the_opening_round_is_recorded_but_not_budgeted(slate, tmp_path,
                                                        monkeypatch):
    """The first round pays for a cold cache and pool; judging it is noise."""
    slate.full_book = lambda host, tok: {
        "bids": {}, "asks": {}, "best_bid": 0.49, "best_ask": 0.51,
        "malformed": 0, "token_id": tok}
    monkeypatch.setattr(slate, "TICK_BUDGET_MS", 0.0)
    stats: dict = {}
    _closed, errs = slate.poll_once(tmp_path, False, stats)

    assert [e for e in errs if e.startswith("slow_tick")] == []
    assert stats["tick_ms_first"] > 0.0
    assert "tick_ms_max" not in stats


def test_manifest_publishes_the_real_sampling_interval(slate, tmp_path):
    """Consumers of run/ticks must be able to read the true granularity."""
    slate.full_book = lambda host, tok: {
        "bids": {}, "asks": {}, "best_bid": 0.49, "best_ask": 0.51,
        "malformed": 0, "token_id": tok}
    stats: dict = {}
    slate.poll_once(tmp_path, False, stats)
    slate.poll_once(tmp_path, False, stats)
    slate.update_manifest(tmp_path, stats)

    data = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert isinstance(data["tick_ms_last"], float)
    assert isinstance(data["tick_ms_max"], float)
    assert isinstance(data["tick_ms_first"], float)
    # Measured from the gap between the two rounds' own timestamps, not
    # computed from POLL_INTERVAL, so it reflects what a reader really sees.
    assert data["sampling_interval_s"] > 0.0
    assert "_last_tick_ts" not in data, "internal cursor must stay out of the manifest"


def test_the_sampling_interval_is_measured_not_assumed(slate, tmp_path,
                                                       monkeypatch):
    """It must track the real gap between rounds, including work after a poll."""
    slate.full_book = lambda host, tok: {
        "bids": {}, "asks": {}, "best_bid": 0.49, "best_ask": 0.51,
        "malformed": 0, "token_id": tok}
    stats: dict = {}
    slate.poll_once(tmp_path, False, stats)
    # The first round has no predecessor, so it keeps the computed estimate.
    assert stats["sampling_interval_s"] == pytest.approx(
        stats["tick_ms_last"] / 1000.0 + slate.POLL_INTERVAL, abs=0.05)

    # Stand in for main()'s between-round work plus an overshooting sleep. The
    # old computed form could not see any of this.
    time.sleep(0.5)
    slate.poll_once(tmp_path, False, stats)

    assert stats["sampling_interval_s"] >= 0.5
    assert stats["sampling_interval_s"] > stats["tick_ms_last"] / 1000.0


# --- Issue #167 review follow-ups: rollover, cold budget, telemetry --------

def test_a_rolled_window_closes_in_the_same_tick_its_replacement_opens(
        slate, tmp_path, monkeypatch):
    """Cache expiry, new cid and old-window close all land in one round."""
    swapped = {"on": False}

    def rolling_gamma(slug: str):
        """Series A rolls to a new market once `swapped` flips."""
        if slug != "a-5m":
            return None, "no live"
        now = time.time()
        if swapped["on"]:
            return {
                "conditionId": "0xNEW", "slug": "slug-new",
                "start_ts": now - 1.0, "end_ts": now + 300.0,
                "up_token": "a_up2", "down_token": "a_dn2", "series": slug,
            }, None
        # Already past its end: the cache must not serve it next tick.
        return {
            "conditionId": "0xOLD", "slug": "slug-old",
            "start_ts": now - 300.0, "end_ts": now - 1.0,
            "up_token": "a_up", "down_token": "a_dn", "series": slug,
        }, None

    slate.fetch_live_for_series = rolling_gamma
    slate.full_book = lambda host, tok: {
        "bids": {}, "asks": {}, "best_bid": 0.49, "best_ask": 0.51,
        "malformed": 0, "token_id": tok}

    slate.poll_once(tmp_path, False, {})
    assert "0xOLD" not in slate.windows, "an already-expired window must close"

    swapped["on"] = True
    slate.poll_once(tmp_path, False, {})

    assert "0xNEW" in slate.windows
    assert "0xOLD" not in slate.windows
    assert [s["cid"] for s in _snaps(tmp_path)] == ["0xOLD", "0xNEW"]


def test_an_expired_market_is_never_served_from_the_cache(slate, tmp_path):
    """poll_once must re-resolve past end_ts, not reuse the cached market."""
    seen: list[str] = []

    def expired_gamma(slug: str):
        """Always hands back a market whose window has already ended."""
        seen.append(slug)
        if slug != "a-5m":
            return None, "no live"
        now = time.time()
        return {
            "conditionId": "0xEXP", "slug": "slug-exp",
            "start_ts": now - 300.0, "end_ts": now - 1.0,
            "up_token": "a_up", "down_token": "a_dn", "series": slug,
        }, None

    slate.fetch_live_for_series = expired_gamma
    slate.full_book = lambda host, tok: {
        "bids": {}, "asks": {}, "best_bid": 0.49, "best_ask": 0.51,
        "malformed": 0, "token_id": tok}

    slate.poll_once(tmp_path, False, {})
    first = seen.count("a-5m")
    slate.poll_once(tmp_path, False, {})

    assert seen.count("a-5m") == first + 1, "expired entry was served from cache"


def test_a_wedged_cold_start_is_still_reported(slate, tmp_path, monkeypatch):
    """The opening round is exempt from TICK_BUDGET_MS, not from all limits."""
    slate.full_book = lambda host, tok: {
        "bids": {}, "asks": {}, "best_bid": 0.49, "best_ask": 0.51,
        "malformed": 0, "token_id": tok}
    monkeypatch.setattr(slate, "COLD_TICK_BUDGET_MS", 0.0)
    stats: dict = {}
    _closed, errs = slate.poll_once(tmp_path, False, stats)

    assert any(e.startswith("slow_first_tick:") for e in errs)
    # Still not a steady-state degradation: the two signals stay distinct.
    assert [e for e in errs if e.startswith("slow_tick:")] == []


def test_manifest_carries_the_tape_source_split(slate, tmp_path):
    """An operator must be able to see which source actually fed the tape."""
    slate.full_book = lambda host, tok: {
        "bids": {}, "asks": {}, "best_bid": 0.49, "best_ask": 0.51,
        "malformed": 0, "token_id": tok}
    stats = {"tape_captured_ws": 12, "tape_captured_rest": 3,
             "tape_rest_skipped": 7}
    slate.poll_once(tmp_path, False, stats)
    slate.update_manifest(tmp_path, stats)

    data = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert data["tape_captured_ws"] == 12
    assert data["tape_captured_rest"] == 3
    assert data["tape_rest_skipped"] == 7
