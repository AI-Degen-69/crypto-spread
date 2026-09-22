"""Boundary prewarm + startup alignment tests for scripts/collect_ticks.py.

No live network: gamma, books and tape are stubbed like
tests/test_collect_ticks_smoke.py. Targets:

* prewarm: the NEXT market is parked pre-open; promotion into the live cache
  costs no HTTP; the boundary round therefore fires zero gamma lookups.
* prewarm leads are staggered so the prewarm itself never bursts.
* alignment: a late join skips in-flight windows and records from the next
  quarter-hour opens; --no-align / cleared cutoff record immediately.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import scripts.collect_ticks as ct


def _market(cid: str, start_ts: float, end_ts: float, series: str) -> dict:
    """Minimal resolved-market dict shaped like fetch_live_for_series output."""
    return {
        "conditionId": cid, "slug": f"slug-{cid}",
        "start_ts": start_ts, "end_ts": end_ts,
        "up_token": f"{cid}-UP", "down_token": f"{cid}-DOWN",
        "series": series,
    }


def _book(host, tok):
    """A stub book good enough for poll_once to record a snap."""
    return {"bids": {}, "asks": {}, "best_bid": 0.49, "best_ask": 0.51,
            "malformed": 0, "token_id": tok}


@pytest.fixture
def env(monkeypatch, tmp_path):
    """A three-series collector with gamma/books/tape stubbed out."""
    ct.reset_gamma_cache()
    ct.reset_prewarm()
    ct.windows.clear()
    ct._join_cutoff = None
    series = [("a-5m", 300, "A"), ("b-5m", 300, "B"), ("c-5m", 300, "C")]
    monkeypatch.setattr(ct, "SERIES", series)
    monkeypatch.setattr(ct, "recent_trades", lambda cid, seen, limit=200, **_kw: {})
    yield ct
    ct.windows.clear()
    ct.reset_gamma_cache()
    ct.reset_prewarm()
    ct._join_cutoff = None


def _snaps(out_dir: Path) -> list[dict]:
    rows: list[dict] = []
    for f in sorted(out_dir.glob("ticks_*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


# --- boundary prewarm ------------------------------------------------------

def test_prewarm_parks_the_next_market_before_the_roll(env, monkeypatch):
    """A live window inside its lead gets its successor fetched and parked."""
    now = time.time()
    live = _market("0xLIVE", now - 270.0, now + 20.0, "a-5m")     # T-20
    nxt = _market("0xNEXT", now + 300.0, now + 600.0, "a-5m")
    calls: list[str] = []

    def fake_next(slug, now=None):
        calls.append(slug)
        return nxt, None

    monkeypatch.setattr(env, "fetch_next_market_for_series", fake_next)
    env._prewarm["a-5m"] = live
    env.prewarm_round(now, [env.SeriesFetch("a-5m", 300, "A", info=live)])
    assert calls == ["a-5m"]
    assert env._prewarm["a-5m"]["conditionId"] == "0xNEXT"


def test_prewarm_skips_windows_far_from_the_roll(env, monkeypatch):
    """A live window with plenty of time left pays no prewarm call."""
    now = time.time()
    live = _market("0xLIVE", now - 10.0, now + 200.0, "a-5m")     # T-200
    calls: list[str] = []
    monkeypatch.setattr(env, "fetch_next_market_for_series",
                        lambda slug, now=None: calls.append(slug))
    env.prewarm_round(now, [env.SeriesFetch("a-5m", 300, "A", info=live)])
    assert calls == []
    assert "a-5m" not in env._prewarm


def test_promotion_serves_the_prewarmed_market_with_no_http(env, monkeypatch):
    """After the roll, resolve_series_market promotes the parked market — no fetch."""
    now = time.time()
    nxt = _market("0xNEXT", now - 1.0, now + 299.0, "a-5m")
    env._prewarm["a-5m"] = nxt

    def fail(slug):
        raise AssertionError("gamma must not be called: promotion should serve")

    monkeypatch.setattr(env, "fetch_live_for_series", fail)
    info, err = env.resolve_series_market("a-5m", now)
    assert err is None
    assert info["conditionId"] == "0xNEXT"
    assert "a-5m" not in env._prewarm          # consumed
    assert env._gamma_cache["a-5m"][1]["conditionId"] == "0xNEXT"


def test_prewarm_leads_are_staggered_across_series(env):
    """Each series gets a different lead, so the prewarm itself never bursts."""
    now = time.time()
    # All three live windows are equally close to their roll; with one shared
    # lead every one of them would qualify for the same round.
    fetches = [env.SeriesFetch(s, 300, "A", info=_market(f"0x{i}", now - 200.0,
                                                         now + 45.0, s))
               for i, s in enumerate(("a-5m", "b-5m", "c-5m"))]
    leads = [env.PREWARM_LEAD_SEC + (i % env.PREWARM_STAGGER_STEPS) * env.PREWARM_STAGGER_SEC
             for i in range(len(fetches))]
    assert len(set(leads)) == min(len(fetches), env.PREWARM_STAGGER_STEPS)
    assert len(set(leads)) > 1


def test_prewarm_never_serves_the_next_market_while_live_is_open(env, monkeypatch):
    """A parked successor must not be handed out before its start."""
    now = time.time()
    live = _market("0xLIVE", now - 100.0, now + 200.0, "a-5m")
    nxt = _market("0xNEXT", now + 300.0, now + 600.0, "a-5m")
    env._prewarm["a-5m"] = nxt
    env._gamma_cache["a-5m"] = (now - 5.0, live)   # warm live resolution

    def fail(slug):
        raise AssertionError("live fetch would be shadowed by the prewarm")

    monkeypatch.setattr(env, "fetch_live_for_series", fail)
    info, err = env.resolve_series_market("a-5m", now)
    assert err is None
    assert info["conditionId"] == "0xLIVE"     # the open market, not the parked one
    assert "a-5m" in env._prewarm              # still parked, promotion not due yet


def test_prewarm_discards_a_missed_promotion(env, monkeypatch):
    """A parked market that blew past its start unpromoted is dropped, not served."""
    now = time.time()
    stale = _market("0xGONE", now - 600.0, now - 300.0, "a-5m")
    env._prewarm["a-5m"] = stale
    fresh = _market("0xFRESH", now - 10.0, now + 290.0, "a-5m")
    monkeypatch.setattr(env, "fetch_live_for_series", lambda slug: (fresh, None))
    info, err = env.resolve_series_market("a-5m", now)
    assert info["conditionId"] == "0xFRESH"
    assert "a-5m" not in env._prewarm


def test_poll_once_boundary_uses_prewarm_not_a_gamma_burst(env, monkeypatch, tmp_path):
    """Across a real roll, promotion serves every series with zero live fetches.

    The old windows end at a fixed `roll_at` inside the prewarm lead, so the
    successors get parked pre-open; after the roll, resolve must come from the
    parked markets, never from fetch_live_for_series again.
    """
    roll_at = time.time() + 12.0                    # inside every staggered lead
    live_calls: list[str] = []
    next_calls: list[str] = []

    def live_fn(slug, now=None):
        """The current window per series; after the roll this must not run."""
        now = now if now is not None else time.time()
        tag = slug[0]
        suffix = "1" if now < roll_at else "2"
        live_calls.append(f"{slug}:{suffix}")
        if suffix == "1":
            return _market(f"0x{tag.upper()}1", now - 100.0, roll_at, slug), None
        return _market(f"0x{tag.upper()}2", roll_at, roll_at + 300.0, slug), None

    def next_fn(slug, now=None):
        """The successor each series would prewarm, recorded per slug."""
        next_calls.append(slug)
        tag = slug[0]
        return _market(f"0x{tag.upper()}2", roll_at, roll_at + 300.0, slug), None

    monkeypatch.setattr(env, "fetch_live_for_series", live_fn)
    monkeypatch.setattr(env, "fetch_next_market_for_series", next_fn)
    monkeypatch.setattr(env, "full_book", _book)
    stats: dict = {}
    env.poll_once(tmp_path, False, stats)           # round 1: cold resolve x3
    before_live = len(live_calls)

    # Drive poll_once across the roll; prewarm parks successors, promotion
    # serves them after it.
    while time.time() < roll_at + 2.0:
        env.poll_once(tmp_path, False, stats)
        time.sleep(0.2)

    # Every series was prewarmed exactly once, before its roll.
    assert sorted(next_calls) == ["a-5m", "b-5m", "c-5m"]
    # After the roll, no live fetch at all: promotion served every series.
    post_roll_live = [c for c in live_calls[before_live:] if c.endswith(":2")]
    assert post_roll_live == [], f"boundary burst back: {post_roll_live}"
    # And the new windows are actually being recorded.
    cids = {s["cid"] for s in _snaps(tmp_path)}
    assert {"0xA2", "0xB2", "0xC2"} <= cids


# --- startup alignment ------------------------------------------------------

def _per_slug(results: dict):
    """A fetch_live_for_series stub keyed by slug: slug -> (info, err)."""
    def _fetch(slug, now=None):
        return results.get(slug, (None, "no live"))
    return _fetch


def test_next_fresh_boundary_is_a_quarter_hour(env):
    now = time.time()
    target = env.next_fresh_boundary(now)
    assert target > now
    assert int(target) % 900 == 0


def test_late_join_skips_in_flight_windows_and_records_from_the_open(
        env, monkeypatch, tmp_path):
    """poll_once with a join cutoff drops windows that opened before it."""
    now = time.time()
    monkeypatch.setattr(env, "full_book", _book)
    env._join_cutoff = now + 2.0                    # fresh opens 2s from now
    # The in-flight window ends before the fresh one opens, like a real roll.
    live = _market("0xMID", now - 120.0, now + 1.0, "a-5m")
    monkeypatch.setattr(env, "fetch_live_for_series",
                        _per_slug({"a-5m": (live, None)}))
    monkeypatch.setattr(env, "fetch_next_market_for_series",
                        lambda slug, now=None: (None, "no upcoming"))
    stats: dict = {}
    env.poll_once(tmp_path, False, stats)           # mid-flight window: skipped
    assert [s["cid"] for s in _snaps(tmp_path)] == []

    time.sleep(1.2)                                 # past the old window's end
    fresh = _market("0xFRESH", now + 2.0, now + 302.0, "a-5m")
    monkeypatch.setattr(env, "fetch_live_for_series",
                        _per_slug({"a-5m": (fresh, None)}))
    env.poll_once(tmp_path, False, stats)           # fresh window: recorded

    cids = [s["cid"] for s in _snaps(tmp_path)]
    assert "0xMID" not in cids
    assert cids == ["0xFRESH"]
    # Still armed: the fresh boundary itself has not been processed yet.
    assert env._join_cutoff is not None


def test_no_cutoff_records_immediately(env, monkeypatch, tmp_path):
    """Without alignment, a mid-flight window is recorded as before."""
    now = time.time()
    monkeypatch.setattr(env, "full_book", _book)
    live = _market("0xMID", now - 120.0, now + 180.0, "a-5m")
    monkeypatch.setattr(env, "fetch_live_for_series",
                        _per_slug({"a-5m": (live, None)}))
    stats: dict = {}
    env.poll_once(tmp_path, False, stats)
    assert [s["cid"] for s in _snaps(tmp_path)] == ["0xMID"]


def test_join_cutoff_applies_per_series_not_globally_sticky(env, monkeypatch, tmp_path):
    """A series that misses the fresh open (gamma lag) still records later cids."""
    now = time.time()
    monkeypatch.setattr(env, "full_book", _book)
    env._join_cutoff = now + 2.0
    late_open = _market("0xLATE", now + 5.0, now + 305.0, "a-5m")   # opens after cutoff
    monkeypatch.setattr(env, "fetch_live_for_series",
                        _per_slug({"a-5m": (late_open, None)}))
    stats: dict = {}
    env.poll_once(tmp_path, False, stats)           # recorded: opened after the cutoff
    assert [s["cid"] for s in _snaps(tmp_path)] == ["0xLATE"]


def test_cli_help_advertises_no_align():
    """--no-align must be discoverable for operators who want the old behaviour."""
    import subprocess, sys
    r = subprocess.run([sys.executable, "-m", "scripts.collect_ticks", "--help"],
                       cwd=str(Path(__file__).resolve().parent.parent),
                       capture_output=True, text=True, timeout=10)
    assert r.returncode == 0
    assert "--no-align" in r.stdout
