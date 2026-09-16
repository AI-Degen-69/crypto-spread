"""Integration tests for the 4-tab dashboard SPA and FastAPI API endpoints."""
import json
import subprocess
import time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
import server.osc_dash as osc_dash
from backtest.engine import BacktestParams
from server.osc_dash import app

client = TestClient(app)


def _make_fake_tick(ts: float, cid: str, slug: str, series: str, mid: float, tape: list | None = None) -> dict:
    """Build minimal tick dictionary for backtest simulation testing."""
    up_tok = f"{cid}_up"
    dn_tok = f"{cid}_dn"
    return {
        "ts": ts,
        "iso": "2026-08-31T00:00:00+00:00",
        "series": series,
        "duration": 300,
        "label": "BTC 5m",
        "cid": cid,
        "slug": slug,
        "start_ts": ts - 10,
        "end_ts": ts + 290,
        "t_rem": 290,
        "up_token": up_tok,
        "down_token": dn_tok,
        "up_book": {"token_id": up_tok, "bids": {"0.48": 10}, "asks": {}, "best_bid": mid - 0.005, "best_ask": mid + 0.005, "malformed": 0},
        # DOWN pinned at the complement of `mid`, so the two-sided mid the entry
        # anchor reads is `mid` too (issue #225). A fixed 0.485/0.495 described a
        # book no real binary pair produces.
        "down_book": {"token_id": dn_tok, "bids": {"0.48": 10}, "asks": {},
                      "best_bid": round((1.0 - mid) - 0.005, 4),
                      "best_ask": round((1.0 - mid) + 0.005, 4), "malformed": 0},
        "tape_delta": tape or [],
        "mid": mid,
        "touch_pair": 0.99,
        "resting_pair": 0.96,
        "queue_up": 10,
        "queue_down": 10,
        "err": None,
    }


def test_root_returns_4tab_spa():
    """Verify that root endpoint serves the full 4-tab SPA HTML with all containers."""
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text
    assert "app-sidebar" in html
    assert "sidebarToggleBtn" in html
    assert "tab-btn-cockpit" in html
    assert "tab-btn-live" in html
    assert "tab-btn-backtest" in html
    assert "tab-btn-summary" in html
    assert "tab-btn-ticks" in html
    assert "tab-live" in html
    assert "tab-backtest" in html
    assert "tab-summary" in html
    assert "tab-ticks" in html
    assert "collectorBadge" in html
    assert "tapeBadge" in html
    assert "switchTab" in html
    assert "loadManifest" in html
    assert "uploadFileStream" in html
    assert "chip-token-BTC" in html
    assert "chip-token-ETH" in html
    assert "btnDur5m" in html
    assert "cockpitActiveMarketsBadge" in html
    assert "toggleCockpitToken" in html
    assert "cockpitPositionsTable" in html
    assert "cockpitPositionsBody" in html
    assert "cockpitPositionsCount" in html
    assert 'rel="icon"' in html
    assert 'rel="alternate icon"' in html


def test_favicon_served():
    """Verify that /favicon.ico returns 200 OK with SVG content and correct media type."""
    response = client.get("/favicon.ico")
    assert response.status_code == 200
    assert "image/svg+xml" in response.headers["content-type"]
    assert "<svg" in response.text
    assert "#33c9b5" in response.text
    assert "#f0684d" in response.text


def test_root_ltr_layout_and_attributes():
    """Verify that the dashboard root HTML uses LTR direction and English language with left-aligned headers."""
    import re
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    # Parse and directly verify root html element attributes
    html_tag_match = re.search(r"<html\s+([^>]+)>", html)
    assert html_tag_match is not None, "<html> root tag not found in dashboard"
    attrs = html_tag_match.group(1)
    assert 'lang="en"' in attrs
    assert 'dir="ltr"' in attrs
    assert 'dir="rtl"' not in attrs
    assert 'lang="he"' not in attrs

    # Verify that required selectors explicitly set left text alignment
    assert re.search(r"\.tbl\s+th\s*\{[^}]*text-align:\s*left", html) is not None
    assert re.search(r"\.form-group\s+label\s*\{[^}]*text-align:\s*left", html) is not None


def test_sidebar_css_architecture():
    """Verify that CSS defines sidebar variables, layout classes, and transition rules."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert "--sidebar-w-collapsed:48px" in html or "--sidebar-w-collapsed: 48px" in html
    assert "--sidebar-w-expanded:220px" in html or "--sidebar-w-expanded: 220px" in html
    assert ".cui-sidebar" in html
    assert ".sidebar-header" in html
    assert ".sidebar-toggle-btn" in html
    assert ".sidebar-brand-text" in html
    assert ".sidebar-nav" in html
    assert ".sidebar-tab-btn" in html
    assert ".sidebar-link-btn" in html
    assert ".nav-icon" in html
    assert ".nav-label" in html
    assert ".sidebar-divider" in html
    assert ".sidebar-footer" in html
    assert ".sidebar-status-pill" in html
    assert ".status-indicator-dot" in html
    assert "sidebar-pinned" in html


def test_sidebar_dom_structure_and_header_streamlining():
    """Verify that <aside id="app-sidebar"> is present with SVG icons and header tabs are removed."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    # Sidebar element exists and has proper attributes
    assert 'id="app-sidebar"' in html
    assert 'class="cui-sidebar"' in html
    assert 'id="sidebarToggleBtn"' in html
    assert 'onclick="toggleSidebarPin()"' in html

    # All 5 navigation tab buttons exist with SVG icons and labels
    for tab_id in ["tab-btn-cockpit", "tab-btn-live", "tab-btn-backtest", "tab-btn-summary", "tab-btn-ticks"]:
        assert f'id="{tab_id}"' in html

    assert 'sidebarBotStatusPill' in html
    assert 'sidebarStatusDot' in html
    assert 'sidebarStatusText' in html

    # Verify that header no longer contains horizontal tab container
    assert '<div class="nav-tabs" id="main-nav">' not in html
    assert '<div class="nav-tabs">' not in html

    # Verify header still contains title, status badges, and polling buttons
    assert 'id="collectorBadge"' in html
    assert 'id="tapeBadge"' in html
    assert 'id="btnToggleCollector"' in html
    assert 'pollOnce()' in html


def test_sidebar_pinning_and_status_sync_scripts():
    """Verify that toggleSidebarPin, initSidebarState, and status dot sync scripts are present."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    assert "function toggleSidebarPin()" in html
    assert "cui_sidebar_pinned" in html
    assert "sidebar-pinned" in html
    assert "function initSidebarState()" in html
    assert "initSidebarState();" in html
    assert "sidebarStatusDot" in html
    assert "sidebarStatusText" in html


def test_no_hebrew_characters_in_dashboard():
    """Verify that all legacy Hebrew strings in server/osc_dash.py have been standardized to English."""
    import re
    from pathlib import Path
    server_file = Path(__file__).resolve().parent.parent / "server" / "osc_dash.py"
    with open(server_file, "r", encoding="utf-8") as f:
        hebrew_lines = [
            (idx, line.strip())
            for idx, line in enumerate(f, 1)
            if re.search(r"[\u0590-\u05ff]", line)
        ]
    assert len(hebrew_lines) == 0, f"Found {len(hebrew_lines)} lines with Hebrew in osc_dash.py: {hebrew_lines[:5]}"



def test_api_oscillation():
    """Verify oscillation payload returns summary, windows, live snapshots, and goals."""
    response = client.get("/api/oscillation")
    assert response.status_code == 200
    data = response.json()
    assert "summary" in data
    assert "windows" in data
    assert "live" in data
    assert "goals" in data


def test_api_ticks_manifest(tmp_path, monkeypatch):
    """Verify manifest endpoint returns file listings with byte sizes and line estimates."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    test_file = tmp_path / "test_ticks.jsonl"
    test_file.write_text('{"a": 1}\n{"a": 2}\n', encoding="utf-8")

    response = client.get("/api/ticks/manifest")
    assert response.status_code == 200
    data = response.json()
    assert "files" in data
    assert len(data["files"]) == 1
    assert data["files"][0]["name"] == "test_ticks.jsonl"
    assert data["files"][0]["lines"] == 2
    assert data["files"][0]["lines_estimated"] is False


def test_api_ticks_manifest_aggregate(tmp_path, monkeypatch):
    """Issue #109: /api/ticks/manifest exposes an additive `aggregate` rollup."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    f1 = tmp_path / "ticks_2026-09-07.jsonl"
    f1.write_text('{"a": 1}\n{"a": 2}\n', encoding="utf-8")
    f2 = tmp_path / "ticks_2026-09-08.jsonl"
    f2.write_text('{"a": 3}\n', encoding="utf-8")

    response = client.get("/api/ticks/manifest")
    assert response.status_code == 200
    data = response.json()
    agg = data["aggregate"]
    assert agg["total_files"] == 2
    assert agg["total_lines"] == sum(f["lines"] for f in data["files"])
    assert agg["total_bytes"] == sum(f["bytes"] for f in data["files"])
    assert agg["total_lines_estimated"] is False
    assert agg["tape_entries_total"] == 0
    assert agg["series_counts_source"] in ("none", "verify_cache", "scan_cache")


def test_api_ticks_manifest_aggregate_empty_dir(tmp_path, monkeypatch):
    """Issue #109: missing run/ticks/ yields a zeroed aggregate, not an error."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    response = client.get("/api/ticks/manifest")
    assert response.status_code == 200
    agg = response.json()["aggregate"]
    assert agg["total_files"] == 0
    assert agg["total_bytes"] == 0
    assert agg["total_lines"] == 0
    assert agg["series_counts"] == {}
    assert agg["series_counts_source"] == "none"


def test_api_ticks_manifest_aggregate_tape_from_manifest(tmp_path, monkeypatch):
    """Issue #109: collector manifest.json tape_entries_total is surfaced."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    f1 = tmp_path / "ticks_2026-09-08.jsonl"
    f1.write_text('{"a": 1}\n', encoding="utf-8")
    (tmp_path / "manifest.json").write_text(
        json.dumps({"tape_entries_total": 235, "lines": 500}),
        encoding="utf-8",
    )

    response = client.get("/api/ticks/manifest")
    assert response.status_code == 200
    agg = response.json()["aggregate"]
    assert agg["tape_entries_total"] == 235


def test_verify_writes_counts_cache_fed_to_manifest(tmp_path, monkeypatch):
    """Issue #109: /api/ticks/verify persists a counts sidecar that the
    manifest aggregate consumes (series_counts_source == verify_cache)."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    f1 = tmp_path / "ticks_2026-09-08.jsonl"
    f1.write_text(
        json.dumps({"series": "btc-up-or-down-5m", "duration": 300, "cid": "w1", "ts": 1.0, "tape_delta": [{"price": 0.5, "size": 1}]}) + "\n"
        + json.dumps({"series": "btc-up-or-down-5m", "duration": 300, "cid": "w1", "ts": 2.0, "tape_delta": []}) + "\n"
        + json.dumps({"series": "eth-up-or-down-5m", "duration": 300, "cid": "w2", "ts": 1.0, "tape_delta": []}) + "\n",
        encoding="utf-8",
    )

    res = client.get("/api/ticks/verify", params={"file": f1.name, "wait": 1})
    assert res.status_code == 200

    agg = client.get("/api/ticks/manifest").json()["aggregate"]
    assert agg["series_counts_source"] == "verify_cache"
    assert agg["series_counts"]["btc-up-or-down-5m"] == 2
    assert agg["series_counts"]["eth-up-or-down-5m"] == 1
    assert agg["total_windows"] == 2
    assert agg["windows_source"] == "cache"

    # Per-file market breakdown surfaces from the cache too.
    files = client.get("/api/ticks/manifest").json()["files"]
    assert files[0]["market_breakdown"][0]["series"] == "btc-up-or-down-5m"
    assert files[0]["market_breakdown"][0]["windows"] == 1
    assert files[0]["market_breakdown"][0]["trades"] == 1
    assert files[0]["market_breakdown"][0]["trades_per_window"] == 1.0


def test_prewarm_verify_cache_from_sidecars(tmp_path, monkeypatch):
    """Startup pre-warm loads fingerprint-matching sidecars into memory so the
    first /api/ticks/verify after a restart is instant."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    osc_dash._VERIFY_REPORT_CACHE.clear()
    f1 = tmp_path / "ticks_2026-09-08.jsonl"
    f1.write_text('{"series": "btc-up-or-down-5m", "cid": "w1", "ts": 1.0}' + "\n", encoding="utf-8")
    fp = osc_dash._file_fingerprint(f1)
    cache_dir = tmp_path / osc_dash._VERIFY_CACHE_DIRNAME
    cache_dir.mkdir()
    (cache_dir / "ticks_2026-09-08.jsonl.json").write_text(json.dumps({
        "file": f1.name, "status": "PASS", "valid_ticks": 1,
        "series_counts": {"btc-up-or-down-5m": 1},
        "fingerprint": fp, "ts": 12345.0,
    }), encoding="utf-8")

    osc_dash._prewarm_verify_cache()
    assert f1.name in osc_dash._VERIFY_REPORT_CACHE
    assert osc_dash._VERIFY_REPORT_CACHE[f1.name]["fingerprint"] == fp

    # Endpoint serves the pre-warmed report instantly (no PENDING, no wait).
    res = client.get("/api/ticks/verify", params={"file": f1.name})
    assert res.status_code == 200
    d = res.json()
    assert d["status"] == "PASS"
    assert d.get("cached") is True

    # Stale sidecar (fingerprint mismatch) is not pre-warmed.
    (cache_dir / "ticks_2026-09-08.jsonl.json").write_text(json.dumps({
        "file": f1.name, "status": "PASS", "fingerprint": "0:0", "ts": 1.0,
    }), encoding="utf-8")
    osc_dash._VERIFY_REPORT_CACHE.clear()
    osc_dash._prewarm_verify_cache()
    assert f1.name not in osc_dash._VERIFY_REPORT_CACHE
    osc_dash._VERIFY_REPORT_CACHE.clear()


def test_api_collector_lifecycle_and_status(monkeypatch, tmp_path):
    """Verify collector start, status, and stop workflow with mocked process."""
    # Isolated TICKS_DIR: a live external manifest in the real dir must not
    # make this lifecycle test flaky (Issue #151 start guard).
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    class DummyProc:
        def __init__(self):
            self.pid = 99999
            self._running = True

        def poll(self):
            return None if self._running else 0

        def terminate(self):
            self._running = False

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self._running = False

    monkeypatch.setattr(osc_dash.subprocess, "Popen", lambda *args, **kwargs: DummyProc())
    try:
        # Start collector
        res_start = client.post("/api/collector/start")
        assert res_start.status_code == 200
        assert res_start.json().get("running") is True
        assert res_start.json().get("pid") == 99999

        # Check status
        res_status = client.get("/api/collector/status")
        assert res_status.status_code == 200
        assert res_status.json().get("running") is True
        assert res_status.json().get("pid") == 99999

        # Stop collector
        res_stop = client.post("/api/collector/stop")
        assert res_stop.status_code == 200
        assert res_stop.json().get("running") is False
    finally:
        osc_dash._collector_proc = None


def test_api_collector_poll_once(monkeypatch):
    """Verify single poll collector endpoint invokes single collection pass via subprocess."""
    def _mock_run(*args, **kwargs):
        class DummyResult:
            returncode = 0
            stdout = "Collected 10 series successfully"
            stderr = ""
        return DummyResult()

    monkeypatch.setattr(subprocess, "run", _mock_run)
    response = client.post("/api/collector/poll-once")
    assert response.status_code == 200
    data = response.json()
    assert data.get("ok") is True
    assert "Collected 10 series" in data.get("output")


def test_api_collector_status_tape_metrics(tmp_path, monkeypatch):
    """Verify collector status endpoint surfaces tape empty rate and alert flag."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    
    # Without manifest
    res = client.get("/api/collector/status")
    assert res.status_code == 200
    assert res.json()["tape_empty_rate"] is None
    assert res.json()["tape_alert"] is False

    # With normal tape empty rate (e.g. 98.8%)
    mf = tmp_path / "manifest.json"
    mf.write_text(
        json.dumps({
            "lines": 500,
            "tape_empty_count": 494,
            "tape_non_empty_count": 6,
            "tape_empty_rate": 0.988,
            "tape_entries_total": 12,
        }),
        encoding="utf-8",
    )
    res = client.get("/api/collector/status")
    assert res.status_code == 200
    d = res.json()
    assert d["tape_empty_rate"] == 0.988
    assert d["tape_entries_total"] == 12
    assert d["tape_alert"] is False

    # With high tape silence (>99%)
    mf.write_text(
        json.dumps({
            "lines": 1000,
            "tape_empty_count": 995,
            "tape_non_empty_count": 5,
            "tape_empty_rate": 0.995,
            "tape_entries_total": 6,
        }),
        encoding="utf-8",
    )
    res = client.get("/api/collector/status")
    assert res.status_code == 200
    d = res.json()
    assert d["tape_empty_rate"] == 0.995
    assert d["tape_alert"] is True



def test_collector_status_large_tick_file_uses_size_estimate(tmp_path, monkeypatch):
    """Issue #200: large today's tick file uses size//950 estimate, not a full scan."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    monkeypatch.setattr(osc_dash, "_collector_proc", None)
    today = tmp_path / f"ticks_{time.strftime('%Y-%m-%d', time.gmtime())}.jsonl"
    # 20 MB + 1 byte would hold ~21052 lines at the 950-bytes/line heuristic.
    size = 20_000_001
    today.write_bytes(b"x" * size)
    called = {}
    orig = osc_dash._count_lines_fast
    def _boom(path):
        called["hit"] = True
        return orig(path)
    monkeypatch.setattr(osc_dash, "_count_lines_fast", _boom)
    res = client.get("/api/collector/status")
    assert res.status_code == 200
    assert "hit" not in called, "large tick file must not be fully scanned"
    assert res.json()["total_ticks_collected"] == int(size / 950)
    # exactly 20 MB is still estimated
    today.write_bytes(b"x" * 20_000_000)
    called.clear()
    res = client.get("/api/collector/status")
    assert "hit" not in called
    assert res.json()["total_ticks_collected"] == int(20_000_000 / 950)


def test_refresh_collector_status_no_empty_catch():
    """Issue #200: refreshCollectorStatus must not swallow failures with an empty catch."""
    html = client.get("/").text
    # The function must not contain an empty catch block
    import re
    m = re.search(r"async function refreshCollectorStatus\(\)\{.*?\n\}", html, re.DOTALL)
    assert m is not None
    block = m.group(0)
    assert "}catch{}" not in block and "} catch{}" not in block, "empty catch must be gone"
    assert "}catch {}" not in block and "} catch {}" not in block
    # It must log the failure visibly
    assert "console.warn" in block or "console.error" in block or "console.log" in block


def test_menu_collector_status_includes_failure_reason():
    """Issue #200: menu collector status failure must interpolate the real exception."""
    text = Path("scripts/crypto-spread-menu.ps1").read_text(encoding="utf-8")
    # Collector block must include $_ like the Trading Engine block does
    assert 'Csm-Warn "Collector: Could not query /api/collector/status ($_)"' in text
    # Static-only string without $_ must be gone (every occurrence carries ($_) )
    bare = text.count('Collector: Could not query /api/collector/status')
    interp = text.count('Collector: Could not query /api/collector/status ($_)')
    assert bare == interp and interp >= 1, "bare collector warning without $_ must be removed"
    # Timeout must be generous enough for a large-file estimate (not the old 3s)
    import re
    collector_timeout = re.search(
        r'Invoke-RestMethod -Uri "\$DashUrl/api/collector/status".*?TimeoutSec (\d+)', text, re.DOTALL)
    assert collector_timeout is not None
    assert int(collector_timeout.group(1)) >= 5


def test_collector_status_external_only(tmp_path, monkeypatch):
    """Issue #151: fresh manifest + no dashboard child => source external."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    monkeypatch.setattr(osc_dash, "_collector_proc", None)
    (tmp_path / "manifest.json").write_text(
        json.dumps({"ts": time.time(), "lines": 10}),
        encoding="utf-8",
    )
    res = client.get("/api/collector/status")
    assert res.status_code == 200
    d = res.json()
    assert d["source"] == "external"
    assert d["external"] is True
    assert d["running"] is False



def test_collector_start_refused_while_external_live(tmp_path, monkeypatch):
    """Issue #151: Start returns 409 with a reason and spawns nothing."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    monkeypatch.setattr(osc_dash, "_collector_proc", None)
    (tmp_path / "manifest.json").write_text(
        json.dumps({"ts": time.time(), "lines": 10}),
        encoding="utf-8",
    )
    def _boom(*args, **kwargs):
        raise AssertionError("must not spawn a second collector")
    monkeypatch.setattr(osc_dash.subprocess, "Popen", _boom)
    try:
        res = client.post("/api/collector/start")
    finally:
        osc_dash._collector_proc = None
    assert res.status_code == 409
    body = res.json()
    assert body.get("ok") is False
    assert body.get("source") == "external"
    assert "external" in body.get("error", "").lower()



def test_collector_status_source_matrix(tmp_path, monkeypatch):
    """Issue #151: status source covers none / child-only / stale-manifest."""
    class DummyProc:
        pid = 99999
        def poll(self):
            return None

    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    try:
        # none: no manifest, no child
        monkeypatch.setattr(osc_dash, "_collector_proc", None)
        d = client.get("/api/collector/status").json()
        assert d["source"] == "none"
        assert d["external"] is False

        # child-only: child running, no manifest
        monkeypatch.setattr(osc_dash, "_collector_proc", DummyProc())
        d = client.get("/api/collector/status").json()
        assert d["source"] == "child"
        assert d["external"] is False
        assert d["running"] is True

        # stale manifest + no child => none (not external)
        monkeypatch.setattr(osc_dash, "_collector_proc", None)
        (tmp_path / "manifest.json").write_text(
            json.dumps({"ts": time.time() - 3600, "lines": 10}),
            encoding="utf-8",
        )
        d = client.get("/api/collector/status").json()
        assert d["source"] == "none"
        assert d["external"] is False

        # corrupt manifest + no child => none (detection never raises)
        (tmp_path / "manifest.json").write_text("{not json", encoding="utf-8")
        d = client.get("/api/collector/status").json()
        assert d["source"] == "none"
        assert d["external"] is False

        # valid JSON but not an object (list) => none, never raises
        (tmp_path / "manifest.json").write_text("[1, 2, 3]", encoding="utf-8")
        d = client.get("/api/collector/status").json()
        assert d["source"] == "none"
        assert d["external"] is False

        # child wins: child running + fresh manifest => child, not external
        monkeypatch.setattr(osc_dash, "_collector_proc", DummyProc())
        (tmp_path / "manifest.json").write_text(
            json.dumps({"ts": time.time(), "lines": 10}),
            encoding="utf-8",
        )
        d = client.get("/api/collector/status").json()
        assert d["source"] == "child"
        assert d["external"] is False
    finally:
        osc_dash._collector_proc = None



def test_api_backtest_simulation(tmp_path, monkeypatch):
    """Verify backtest simulation on an isolated deterministic 4-window fixture."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    fake_file = tmp_path / "fake_round.jsonl"

    ticks = []
    for i in range(4):
        cid = f"0xCID_000{i}"
        slug = f"btc-updown-5m-100{i}"
        base_ts = 1000.0 + i * 500
        ticks.append(_make_fake_tick(base_ts, cid, slug, "btc-up-or-down-5m", 0.50, tape=[{"asset": f"{cid}_up", "price": 0.48, "size": 100}]))
        ticks.append(_make_fake_tick(base_ts + 1, cid, slug, "btc-up-or-down-5m", 0.48))
        ticks.append(_make_fake_tick(base_ts + 2, cid, slug, "btc-up-or-down-5m", 0.52, tape=[{"asset": f"{cid}_dn", "price": 0.46, "size": 100}]))
        ticks.append(_make_fake_tick(base_ts + 3, cid, slug, "btc-up-or-down-5m", 0.50))

    with open(fake_file, "w", encoding="utf-8") as f:
        for t in ticks:
            f.write(json.dumps(t) + "\n")

    url = "/api/backtest?file=fake_round.jsonl&offset=0.03&queue=75&pair_cost=0.98&exit_default_5m=0.15&fill_model=book&size=150&gas=0.02"
    response = client.get(url)
    assert response.status_code == 200
    data = response.json()
    assert "params_hash" in data
    assert "params" in data
    assert data["params"]["offset"] == 0.03
    assert data["params"]["queue"] == 75.0
    assert data["params"]["pair_cost"] == 0.98
    assert data["params"]["exit_default_5m"] == 0.15
    assert data["params"]["fill_model"] == "book"
    assert data["params"]["size"] == 150
    assert data["params"]["gas"] == 0.02
    assert "overall" in data
    assert "max_drawdown_cents" in data["overall"]
    assert "win_rate" in data["overall"]
    assert "per_series" in data
    assert len(data["per_series"]) == 10
    assert "equity_curve" in data
    assert len(data["equity_curve"]) == 4
    assert "trades_sample" in data
    assert len(data["trades_sample"]) == 4
    assert data["n_windows"] == 4
    # Drift-skip re-entry telemetry is present (0 here: no adverse-open skips in
    # this healthy fixture) at both the overall and per-series levels.
    assert "reentry_count" in data["overall"]
    assert data["overall"]["reentry_count"] == 0
    assert "reentry_pnl_cents" in data["overall"]
    assert data["overall"]["reentry_pnl_cents"] == 0.0
    btc_series = data["per_series"]["btc-up-or-down-5m"]
    assert "reentry_count" in btc_series
    assert "reentry_pnl_cents" in btc_series
    assert btc_series["reentry_count"] == 0
    assert btc_series["reentry_pnl_cents"] == 0.0

    # Test with fill_model=cross
    url_cross = "/api/backtest?file=fake_round.jsonl&offset=0.02&fill_model=cross"
    res_cross = client.get(url_cross)
    assert res_cross.status_code == 200
    assert res_cross.json()["params"]["fill_model"] == "cross"


def test_api_backtest_reentry_telemetry(tmp_path, monkeypatch):
    """A drift-skipped window that reverts reports recovered windows in the API."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    fake_file = tmp_path / "fake_reentry.jsonl"
    cid = "0xRE_000"
    # t=1000: adverse open (two-sided mid 0.35 -> drift 0.15 >= 0.05)
    # latches the drift gate. t=1011: mid back at 0.50 (21s in, inside the 30s
    # entry-timeout cutoff) with both legs filling at the 0.48 resting price.
    ticks = [
        _make_fake_tick(1000.0, cid, "btc-updown-5m-2000", "btc-up-or-down-5m", 0.35),
        _make_fake_tick(1011.0, cid, "btc-updown-5m-2000", "btc-up-or-down-5m", 0.50,
                        tape=[{"asset": f"{cid}_up", "price": 0.48, "size": 100},
                              {"asset": f"{cid}_dn", "price": 0.48, "size": 100}]),
    ]
    with open(fake_file, "w", encoding="utf-8") as f:
        for t in ticks:
            f.write(json.dumps(t) + "\n")

    data = client.get(
        "/api/backtest?file=fake_reentry.jsonl&fill_model=tape"
    ).json()
    assert data["n_windows"] == 1
    assert data["overall"]["reentry_count"] == 1
    assert data["overall"]["reentry_pnl_cents"] == pytest.approx(20.0, abs=1e-6)
    btc = data["per_series"]["btc-up-or-down-5m"]
    assert btc["windows"] == 1
    assert btc["reentry_count"] == 1
    assert btc["reentry_pnl_cents"] == pytest.approx(20.0, abs=1e-6)


def test_api_backtest_reentry_knob_a_b(tmp_path, monkeypatch):
    """The re-entry query knobs flip behavior: band 0 or a huge requote minimum disable."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    fake_file = tmp_path / "fake_reentry_knobs.jsonl"
    cid = "0xRE_001"
    ticks = [
        _make_fake_tick(1000.0, cid, "btc-updown-5m-2001", "btc-up-or-down-5m", 0.35),
        # Mid reverts to 0.499 (drift 0.001): inside the default 0.015 band but
        # outside band 0, so "re-entry off" is distinguishable from "on".
        _make_fake_tick(1011.0, cid, "btc-updown-5m-2001", "btc-up-or-down-5m", 0.499,
                        tape=[{"asset": f"{cid}_up", "price": 0.48, "size": 100},
                              {"asset": f"{cid}_dn", "price": 0.48, "size": 100}]),
    ]
    with open(fake_file, "w", encoding="utf-8") as f:
        for t in ticks:
            f.write(json.dumps(t) + "\n")

    base = "/api/backtest?file=fake_reentry_knobs.jsonl&fill_model=tape"

    # Default band 0.015 + 60s minimum: the skipped window is recovered.
    on = client.get(base).json()
    assert on["overall"]["reentry_count"] == 1
    assert on["params"]["reentry_drift_band"] == 0.015
    assert on["params"]["min_requote_remaining_sec"] == 300.0

    # Band 0 (re-entry off): the same window stays skipped.
    off = client.get(base + "&reentry_drift_band=0").json()
    assert off["overall"]["reentry_count"] == 0

    # The time gate is now the tighter of `min_requote_remaining_sec` and
    # `reentry_min_remaining_pct` of the window (issue #95), so raising the absolute
    # knob alone can no longer block a window that still has 30% of itself left --
    # which is the whole point of the change for 5m markets. Blocking by time is
    # covered against the engine directly in
    # tests/test_entry_timeout.py::test_backtest_no_reentry_below_min_requote_remaining_sec.
    still_on = client.get(base + "&min_requote_remaining_sec=300").json()
    assert still_on["overall"]["reentry_count"] == 1

    # Out-of-range band is clamped into 0..0.5 and echoed back.
    clamped = client.get(base + "&reentry_drift_band=0.9").json()
    assert clamped["params"]["reentry_drift_band"] == 0.5
    assert clamped["params"]["min_requote_remaining_sec"] == 300.0


def test_api_backtest_execution_prices_and_disaggregated_win_rate(tmp_path, monkeypatch):
    """Verify /api/backtest returns trade execution prices, unconstrained trades_sample, and disaggregated metrics."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    fake_file = tmp_path / "fake_prices.jsonl"
    cid = "0xPRICES_01"
    slug = "btc-updown-5m-5000"
    ticks = [
        _make_fake_tick(1000.0, cid, slug, "btc-up-or-down-5m", 0.50, tape=[
            {"asset": f"{cid}_up", "price": 0.48, "size": 10},
            {"asset": f"{cid}_dn", "price": 0.48, "size": 10},
        ]),
        _make_fake_tick(1001.0, cid, slug, "btc-up-or-down-5m", 0.50),
    ]
    with open(fake_file, "w", encoding="utf-8") as f:
        for t in ticks:
            f.write(json.dumps(t) + "\n")

    res = client.get(f"/api/backtest?file=fake_prices.jsonl&fill_model=tape")
    assert res.status_code == 200
    data = res.json()
    assert len(data["trades_sample"]) == 1
    trade = data["trades_sample"][0]
    assert "entry_up" in trade
    assert "entry_down" in trade
    assert "exit_price" in trade
    assert "exit_side" in trade
    assert trade["entry_up"] == 0.48
    assert trade["entry_down"] == 0.48
    assert trade["both_filled"] is True
    assert trade["exit_reason"] == "pair_merged"

    ov = data["overall"]
    assert "pair_rate" in ov
    assert "win_rate" in ov
    assert "profitable_windows" in ov
    assert "profitable_pairs" in ov
    assert "profitable_exits" in ov


def test_backtest_ui_pagination_and_tooltips_elements():
    """Verify that root SPA contains backtest log pagination controls, filters, and tooltips."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert "btEquityWarning" in html
    assert "btLogSearch" in html
    assert "btLogSeriesFilter" in html
    assert "btLogResultFilter" in html
    assert "btLogPageSize" in html
    assert "btLogBtnPrev" in html
    assert "btLogBtnNext" in html
    assert "btLogPageInfo" in html
    assert "Pair Capture Rate ℹ️" in html
    assert "Exit Stop Rate ℹ️" in html
    assert "Win Rate ℹ️" in html
    assert "renderBacktestTradesPage" in html


def test_api_upload_stream_ingest(tmp_path, monkeypatch):
    """Verify single direct stream upload of JSONL payload with index creation."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    monkeypatch.setattr(osc_dash, "RUN", tmp_path)

    content = '{"ts": 100}\n{"ts": 200}\n{"ts": 300}\n'.encode("utf-8")
    filename = "streamed_ticks.jsonl"

    res = client.post(
        f"/api/ticks/upload-stream?filename={filename}",
        content=content,
        headers={"Content-Type": "application/octet-stream"}
    )
    assert res.status_code == 200
    d = res.json()
    assert d.get("ok") is True
    assert d.get("filename") == filename
    assert d.get("lines") == 3
    assert (tmp_path / filename).exists()
    assert (tmp_path / f"{filename}.idx").exists()


def test_api_upload_chunk_and_delete(tmp_path, monkeypatch):
    """Verify chunked upload assembly, retries, bounds checking, and deletion."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    monkeypatch.setattr(osc_dash, "RUN", tmp_path)

    content = '{"ts": 1}\n{"ts": 2}\n'.encode("utf-8")
    part1 = content[:8]
    part2 = content[8:]
    upload_id = "up_test_upload_123"
    filename = "uploaded_ticks.jsonl"

    # Out of range bounds checks
    res_bad_idx = client.post(
        f"/api/ticks/upload-chunk?filename={filename}&uploadId={upload_id}&chunkIndex=5&totalChunks=2",
        content=part1,
        headers={"Content-Type": "application/octet-stream"}
    )
    assert res_bad_idx.status_code == 400

    res_bad_total = client.post(
        f"/api/ticks/upload-chunk?filename={filename}&uploadId={upload_id}&chunkIndex=0&totalChunks=20000",
        content=part1,
        headers={"Content-Type": "application/octet-stream"}
    )
    assert res_bad_total.status_code == 400

    # Send Chunk 0
    res0 = client.post(
        f"/api/ticks/upload-chunk?filename={filename}&uploadId={upload_id}&chunkIndex=0&totalChunks=2",
        content=part1,
        headers={"Content-Type": "application/octet-stream"}
    )
    assert res0.status_code == 200
    assert res0.json().get("ok") is True

    # Send Chunk 1 (final)
    res1 = client.post(
        f"/api/ticks/upload-chunk?filename={filename}&uploadId={upload_id}&chunkIndex=1&totalChunks=2",
        content=part2,
        headers={"Content-Type": "application/octet-stream"}
    )
    assert res1.status_code == 200
    d1 = res1.json()
    assert d1.get("ok") is True
    assert d1.get("lines") == 2
    assert (tmp_path / filename).exists()
    assert (tmp_path / f"{filename}.idx").exists()

    # Retry final chunk after upload_dir was removed
    res1_retry = client.post(
        f"/api/ticks/upload-chunk?filename={filename}&uploadId={upload_id}&chunkIndex=1&totalChunks=2",
        content=part2,
        headers={"Content-Type": "application/octet-stream"}
    )
    assert res1_retry.status_code == 200
    assert res1_retry.json().get("lines") == 2

    # Delete uploaded file and verify index cleanup
    res_del = client.delete(f"/api/ticks/file?filename={filename}")
    assert res_del.status_code == 200
    assert not (tmp_path / filename).exists()
    assert not (tmp_path / f"{filename}.idx").exists()


def test_api_security_origin_and_path_traversal():
    """Verify cross-origin, invalid port, and path traversal requests are rejected."""
    # Malicious external origin
    res = client.post(
        "/api/collector/start",
        headers={"Origin": "https://malicious-site.evil.com"}
    )
    assert res.status_code == 403

    # Invalid port on loopback origin
    res_port = client.post(
        "/api/collector/start",
        headers={"Origin": "http://127.0.0.1:9999"}
    )
    assert res_port.status_code == 403

    # Invalid uploadId format / path traversal
    res_traversal = client.post(
        "/api/ticks/upload-chunk?filename=test.jsonl&uploadId=../../etc&chunkIndex=0&totalChunks=1",
        content=b"test",
        headers={"Content-Type": "application/octet-stream"}
    )
    assert res_traversal.status_code == 400

    # Path traversal in stream upload
    res_bad_stream = client.post(
        "/api/ticks/upload-stream?filename=../bad.jsonl",
        content=b'{"a":1}',
        headers={"Content-Type": "application/octet-stream"}
    )
    assert res_bad_stream.status_code == 400


def test_api_backtest_with_max_start_delay_filter(tmp_path, monkeypatch):
    """Verify api_backtest filters late-started windows when max_start_delay or filter_partial is supplied."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    fake_file = tmp_path / "fake_partial_test.jsonl"

    # Window 1: delay = 10s (ts=1000, start_ts=990) -> partial
    # Window 2: delay = 1s (ts=2000, start_ts=1999) -> full
    ticks = []
    # w1 (late start delay 10s)
    t1 = _make_fake_tick(1000.0, "0xW1", "btc-updown-5m-1", "btc-up-or-down-5m", 0.50)
    t1["start_ts"] = 990.0
    ticks.append(t1)
    # w2 (early start delay 1s)
    t2 = _make_fake_tick(2000.0, "0xW2", "btc-updown-5m-2", "btc-up-or-down-5m", 0.50)
    t2["start_ts"] = 1999.0
    ticks.append(t2)

    fake_file.write_text("\n".join(json.dumps(t) for t in ticks) + "\n", encoding="utf-8")

    # Default (no filter) -> 2 windows
    res_all = client.get("/api/backtest?file=fake_partial_test.jsonl")
    assert res_all.status_code == 200
    d_all = res_all.json()
    assert d_all["n_windows"] == 2
    assert d_all["trades_sample"][0]["is_partial"] is True
    assert d_all["trades_sample"][0]["start_delay_sec"] == 10.0

    # Filter with max_start_delay=5.0 -> 1 window
    res_filtered = client.get("/api/backtest?file=fake_partial_test.jsonl&max_start_delay=5.0")
    assert res_filtered.status_code == 200
    d_filtered = res_filtered.json()
    assert d_filtered["n_windows"] == 1
    assert d_filtered["trades_sample"][0]["is_partial"] is False
    assert d_filtered["trades_sample"][0]["start_delay_sec"] == 1.0

    # Filter with filter_partial=true -> 1 window
    res_flag = client.get("/api/backtest?file=fake_partial_test.jsonl&filter_partial=true")
    assert res_flag.status_code == 200
    d_flag = res_flag.json()
    assert d_flag["n_windows"] == 1


def test_api_ticks_verify_endpoint(tmp_path, monkeypatch):
    """Verify /api/ticks/verify endpoint validates directories and individual files."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    clean_file = tmp_path / "ticks_2026-09-01.jsonl"
    t = _make_fake_tick(1725000000.0, "0xCID", "btc-5m-1", "btc-up-or-down-5m", 0.50)
    clean_file.write_text(json.dumps(t) + "\n", encoding="utf-8")

    # Directory verification
    res_dir = client.get("/api/ticks/verify")
    assert res_dir.status_code == 200
    d_dir = res_dir.json()
    assert "status" in d_dir
    assert d_dir["total_valid_ticks"] == 1
    assert d_dir["total_corrupt_lines"] == 0

    # Single file verification (wait=1: synchronous scan, cold cache would return PENDING)
    res_file = client.get("/api/ticks/verify?file=ticks_2026-09-01.jsonl&wait=1")
    assert res_file.status_code == 200
    d_file = res_file.json()
    assert d_file["valid_ticks"] == 1
    assert d_file["corrupt_lines"] == 0

    # Invalid file path traversal
    res_bad = client.get("/api/ticks/verify?file=../bad.jsonl")
    assert res_bad.status_code == 400

    # Missing file
    res_missing = client.get("/api/ticks/verify?file=nonexistent.jsonl")
    assert res_missing.status_code == 404


def test_api_live_cockpit_endpoints(monkeypatch):
    """Verify live trading cockpit endpoints for state, control, and config."""
    from strategy.live_trader import LiveTraderEngine
    engine = osc_dash.get_live_trader_engine()
    monkeypatch.setattr(LiveTraderEngine, "_poll_single_market", lambda self, slug: None)
    mock_acct = {
        "success": True,
        "net_value": 1234.56,
        "cash_balance": 1000.0,
        "positions_value": 234.56,
        "positions": [],
        "wallet_address": "0x1234567890abcdef1234567890abcdef12345678",
    }
    monkeypatch.setattr("server.osc_dash.fetch_polymarket_account_value", lambda *a, **kw: mock_acct)
    monkeypatch.setattr("strategy.live_trader.fetch_polymarket_account_value", lambda *a, **kw: mock_acct)
    try:
        # 1. GET state
        res_state = client.get("/api/live/state")
        assert res_state.status_code == 200
        d_state = res_state.json()
        assert "is_running" in d_state
        assert "portfolio_value" in d_state
        assert "markets" in d_state
        assert "open_positions" in d_state
        assert "positions" in d_state
        assert len(d_state["markets"]) == 5
        assert "timeline" in d_state
        for m in d_state["markets"].values():
            assert "fill_price_up" in m
            assert "fill_price_down" in m

        # 2. POST config
        res_cfg = client.post("/api/live/config", json={
            "offset": 0.025,
            "exit_thresh": 0.06,
            "shares": 8,
            "mode": "paper",
            "starting_balance": 1500.0,
        })
        assert res_cfg.status_code == 200
        d_cfg = res_cfg.json()
        assert d_cfg["params"]["offset"] == 0.025
        assert d_cfg["params"]["exit_thresh"] == 0.06
        assert d_cfg["params"]["shares"] == 8
        assert d_cfg["starting_balance"] == 1500.0

        # 3. GET /api/live/account
        res_acc = client.get("/api/live/account")
        assert res_acc.status_code == 200
        d_acc = res_acc.json()
        assert "net_value" in d_acc
        assert "cash_balance" in d_acc
        assert "positions_value" in d_acc
        assert "positions" in d_acc
        assert isinstance(d_acc["positions"], list)

        # 4. POST config in LIVE mode (locks starting balance)
        res_live_cfg = client.post("/api/live/config", json={
            "mode": "live",
            "starting_balance": 9999.0,
        })
        assert res_live_cfg.status_code == 200
        d_live = res_live_cfg.json()
        assert d_live["mode"] == "live"

        # 5. POST control start/stop/restart
        res_start = client.post("/api/live/control", json={"action": "start"})
        assert res_start.status_code == 200
        assert res_start.json()["is_running"] is True

        res_stop = client.post("/api/live/control", json={"action": "stop"})
        assert res_stop.status_code == 200
        assert res_stop.json()["is_running"] is False

        res_restart = client.post("/api/live/control", json={"action": "restart"})
        assert res_restart.status_code == 200
        assert res_restart.json()["is_running"] is True

        # Stop before seeding demo data
        client.post("/api/live/control", json={"action": "stop"})

        # 6. POST control demo_data
        res_demo = client.post("/api/live/control", json={"action": "demo_data"})
        assert res_demo.status_code == 200
        d_demo = res_demo.json()
        assert d_demo["total_trades"] == 7
        assert d_demo["pairs_merged"] == 6
        assert len(d_demo["trades"]) == 7
        assert len(d_demo["timeline"]) == 120
        assert len(d_demo["open_positions"]) > 0
        assert d_demo["markets"]["eth-up-or-down-5m"]["fill_price_up"] == 0.485

        # Switch back to paper mode for clean reset without CLOB network calls
        engine.mode = "paper"
        res_reset = client.post("/api/live/control", json={"action": "reset_pnl"})
        assert res_reset.status_code == 200
        assert res_reset.json()["total_trades"] == 0
    finally:
        # Restore default engine state
        client.post("/api/live/control", json={"action": "stop"})
        engine.mode = "paper"
        client.post("/api/live/config", json={
            "offset": 0.02,
            "exit_thresh": 0.05,
            "shares": 5,
            "mode": "paper",
            "starting_balance": 1000.0,
        })
        client.post("/api/live/control", json={"action": "reset_pnl"})


def test_reset_pnl_endpoint_refuses_while_live_running(monkeypatch):
    """Issue #93: reset_pnl on a live running engine → 409 + Stop-first message."""
    from unittest.mock import MagicMock
    import server.osc_dash as osc_dash
    from strategy.live_trader import LiveTraderEngine
    # Isolated engine: the endpoint resolves it via server.osc_dash, so the
    # process-global singleton is never mutated by this test.
    engine = LiveTraderEngine(load_persisted=False)
    monkeypatch.setattr(engine, "get_clob_client", lambda: MagicMock())
    monkeypatch.setattr(osc_dash, "get_live_trader_engine", lambda: engine)
    engine.mode = "live"
    engine.is_running = True
    m = engine.markets["btc-up-or-down-5m"]
    m.order_id_up = "endpoint_live_ord"
    m.order_status_up = "RESTING"
    res = client.post("/api/live/control", json={"action": "reset_pnl"})
    assert res.status_code == 409
    body = res.json()
    assert body.get("ok") is False
    assert "Stop" in body.get("error", "")
    assert m.order_id_up == "endpoint_live_ord"


def test_osc_dash_live_execution_endpoints(monkeypatch):
    """Verify live order execution endpoints: /orders, /cancel_all, /cancel_order, /test_order."""
    from unittest.mock import MagicMock
    from strategy.live_trader import get_live_trader_engine

    engine = get_live_trader_engine()
    fake_client = MagicMock()
    fake_client.create_and_post_order.return_value = {"orderID": "ord_mock_123", "status": "delayed"}
    fake_client.cancel.return_value = {"success": True}
    fake_client.cancel_all.return_value = {"success": True}
    fake_client.get_orders.return_value = [
        {"id": "ord_mock_123", "asset_id": "tok_test_up", "side": "BUY", "price": "0.05", "original_size": "1"}
    ]
    monkeypatch.setattr(engine, "_clob_client", fake_client)
    saved_markets = {
        slug: (
            m.order_id_up,
            m.order_id_down,
            m.next_order_id_up,
            m.next_order_id_down,
            m.order_status_up,
            m.order_status_down,
            m.next_quoted,
            m.status,
            m.last_action,
        )
        for slug, m in engine.markets.items()
    }
    saved_halted = engine.quoting_halted

    try:
        # 1. GET /api/live/orders
        res_orders = client.get("/api/live/orders")
        assert res_orders.status_code == 200
        orders_data = res_orders.json()
        assert "orders" in orders_data
        assert len(orders_data["orders"]) >= 1

        # 2. POST /api/live/test_order
        res_test_ord = client.post("/api/live/test_order", json={
            "token_id": "tok_test_up",
            "price": 0.05,
            "size": 1.0,
            "side": "BUY",
        })
        assert res_test_ord.status_code == 200
        ord_data = res_test_ord.json()
        assert ord_data["order_id"] == "ord_mock_123"

        # 2b. POST /api/live/test_order rejects invalid payloads before touching the CLOB
        fake_client.create_and_post_order.reset_mock()
        for payload in (
            {"token_id": "tok_test_up", "price": "invalid"},
            {"token_id": "tok_test_up", "size": "invalid"},
            {"token_id": "tok_test_up", "price": 0.0},
            {"token_id": "tok_test_up", "price": 1.0},
            {"token_id": "tok_test_up", "size": 11.0},
            {"token_id": "tok_test_up", "side": "INVALID"},
            {"token_id": "", "price": 0.05},
        ):
            res_reject = client.post("/api/live/test_order", json=payload)
            assert res_reject.status_code == 400, payload
        fake_client.create_and_post_order.assert_not_called()

        # 3. POST /api/live/cancel_order
        res_cancel_single = client.post("/api/live/cancel_order", json={"order_id": "ord_mock_123"})
        assert res_cancel_single.status_code == 200
        assert res_cancel_single.json()["ok"] is True

        # 4. POST /api/live/cancel_all
        res_cancel_all = client.post("/api/live/cancel_all")
        assert res_cancel_all.status_code == 200
        assert res_cancel_all.json()["ok"] is True
    finally:
        engine.quoting_halted = saved_halted
        for slug, saved in saved_markets.items():
            m = engine.markets.get(slug)
            if m:
                (
                    m.order_id_up,
                    m.order_id_down,
                    m.next_order_id_up,
                    m.next_order_id_down,
                    m.order_status_up,
                    m.order_status_down,
                    m.next_quoted,
                    m.status,
                    m.last_action,
                ) = saved


def test_api_live_config_market_selection():
    engine = osc_dash.get_live_trader_engine()
    # Reset engine to default 5m markets
    engine.update_config(selected_markets=["btc-up-or-down-5m", "eth-up-or-down-5m", "bnb-up-or-down-5m", "sol-up-or-down-5m", "xrp-up-or-down-5m"])

    # 1. Update selection via tokens and durations
    res = client.post("/api/live/config", json={"tokens": ["SOL"], "durations": [900]})
    assert res.status_code == 200
    data = res.json()
    assert set(data["markets"].keys()) == {"sol-up-or-down-15m"}

    # 2. Update selection via selected_markets directly
    res2 = client.post("/api/live/config", json={"selected_markets": ["btc-up-or-down-5m", "eth-up-or-down-15m"]})
    assert res2.status_code == 200
    data2 = res2.json()
    assert set(data2["markets"].keys()) == {"btc-up-or-down-5m", "eth-up-or-down-15m"}

    # Reset
    engine.update_config(selected_markets=["btc-up-or-down-5m", "eth-up-or-down-5m", "bnb-up-or-down-5m", "sol-up-or-down-5m", "xrp-up-or-down-5m"])


def test_api_live_config_invalid_selection_returns_400():
    # Invalid token
    res = client.post("/api/live/config", json={"tokens": ["DOGE"]})
    assert res.status_code == 400
    assert "error" in res.json()

    # Invalid duration
    res2 = client.post("/api/live/config", json={"durations": [12345]})
    assert res2.status_code == 400
    assert "error" in res2.json()


def test_api_live_config_open_position_deselection_rejection():
    engine = osc_dash.get_live_trader_engine()
    engine.update_config(selected_markets=["btc-up-or-down-5m", "eth-up-or-down-5m"])
    m_btc = engine.markets["btc-up-or-down-5m"]
    m_btc.filled_up = True
    m_btc.exit_taken = False

    try:
        # Deselecting btc while filled leg is unhedged and open must return 400
        res = client.post("/api/live/config", json={"selected_markets": ["eth-up-or-down-5m"]})
        assert res.status_code == 400
        assert "Cannot deselect active market" in res.json().get("error", "")
    finally:
        m_btc.filled_up = False
        engine.update_config(selected_markets=["btc-up-or-down-5m", "eth-up-or-down-5m", "bnb-up-or-down-5m", "sol-up-or-down-5m", "xrp-up-or-down-5m"])


def test_api_live_state_includes_series_metadata():
    res = client.get("/api/live/state")
    assert res.status_code == 200
    data = res.json()
    assert "available_series" in data
    assert len(data["available_series"]) == 10
    # Check shape of available_series entries
    first = data["available_series"][0]
    assert "slug" in first
    assert "token" in first
    assert "duration" in first
    assert "label" in first
    assert "color" in first
    assert "selected_series" in data


def test_api_live_config_rejects_changes_while_running():
    """Verify /api/live/config returns HTTP 400 if user tries to change markets or parameters while bot is running."""
    engine = osc_dash.get_live_trader_engine()
    engine.is_running = True
    try:
        # Market change while running rejected
        res = client.post("/api/live/config", json={"tokens": ["SOL"]})
        assert res.status_code == 400
        assert "Cannot change market selection while the trading bot is running" in res.json().get("error", "")

        # All scalar parameter changes while running must be rejected with HTTP 400
        for param_payload in [
            {"offset": 0.04},
            {"exit_thresh": 0.08},
            {"shares": 10},
            {"mode": "live"},
            {"wallet_address": "0x9999999999999999999999999999999999999999"},
            {"starting_balance": 5000.0},
        ]:
            res_param = client.post("/api/live/config", json=param_payload)
            assert res_param.status_code == 400, f"Expected 400 for payload {param_payload}"
            assert "Cannot change strategy parameters while the trading bot is running" in res_param.json().get("error", "")

        # Idempotent call with matching active parameters is accepted
        res_idempotent = client.post("/api/live/config", json={
            "offset": engine.offset,
            "exit_thresh": engine.exit_thresh,
            "shares": engine.shares,
            "mode": engine.mode,
            "wallet_address": engine.wallet_address,
            "starting_balance": engine.starting_balance,
        })
        assert res_idempotent.status_code == 200

        # Once stopped, updating parameters is accepted
        engine.is_running = False
        res3 = client.post("/api/live/config", json={"offset": 0.025, "shares": 6})
        assert res3.status_code == 200
        assert engine.offset == 0.025
        assert engine.shares == 6
    finally:
        engine.is_running = False
        engine.update_config(
            offset=0.02,
            exit_thresh=0.05,
            shares=5,
            mode="paper",
            wallet_address="",
            starting_balance=1000.0,
            selected_markets=["btc-up-or-down-5m", "eth-up-or-down-5m", "bnb-up-or-down-5m", "sol-up-or-down-5m", "xrp-up-or-down-5m"],
        )


def test_cockpit_ui_locks_market_filters_while_running():
    """Verify the cockpit page ships the client-side lock for market filters during a run."""
    res = client.get("/")
    assert res.status_code == 200
    html = res.text

    # Lock helpers exist and every filter handler consults the lock before mutating
    assert "function areCockpitFiltersLocked()" in html
    assert "function applyCockpitFilterLock(" in html
    assert "function syncCockpitFiltersFromState(" in html
    for handler in ("toggleCockpitToken", "setCockpitTokensAll", "setCockpitDuration", "applyCockpitConfig"):
        body_start = html.index(f"function {handler}(")
        assert "areCockpitFiltersLocked()" in html[body_start:body_start + 900], handler

    # Lock hint element and ids for the All/Clear buttons the lock disables
    assert 'id="cockpitFilterLockHint"' in html
    assert 'id="btnTokensAll"' in html
    assert 'id="btnTokensClear"' in html


def test_cockpit_ui_locks_strategy_parameters_while_running():
    """Verify the cockpit page ships the client-side lock and banner for strategy parameters during a run."""
    res = client.get("/")
    assert res.status_code == 200
    html = res.text

    # Verify presence of lock banner and parameter inputs
    assert 'id="cockpitParamsLockHint"' in html
    assert "LOCKED WHILE BOT IS RUNNING — STOP THE BOT TO CHANGE PARAMETERS" in html
    assert 'id="btnApplyParams"' in html
    assert 'id="cockpitOffset"' in html
    assert 'id="cockpitExit"' in html
    assert 'id="cockpitShares"' in html
    assert 'id="cockpitMode"' in html
    assert 'id="cockpitWallet"' in html
    assert 'id="cockpitStartBal"' in html

    # Verify parameter lock helper and client guard exist
    assert "function updateCockpitParamsLockUI(" in html
    apply_cfg_idx = html.index("async function applyCockpitConfig(")
    assert "cockpitState.is_running" in html[apply_cfg_idx:apply_cfg_idx + 400]


def test_api_live_config_selection_roundtrip_while_stopped():
    """Verify filters chosen in the UI while stopped drive the engine's active market set."""
    engine = osc_dash.get_live_trader_engine()
    assert not engine.is_running
    try:
        res = client.post("/api/live/config", json={"tokens": ["BTC", "ETH"], "durations": [900]})
        assert res.status_code == 200
        state = res.json()
        assert sorted(state["selected_series"]) == ["btc-up-or-down-15m", "eth-up-or-down-15m"]
        assert sorted(state["markets"].keys()) == ["btc-up-or-down-15m", "eth-up-or-down-15m"]
        assert sorted(engine.markets.keys()) == ["btc-up-or-down-15m", "eth-up-or-down-15m"]

        # Both durations for a single token
        res2 = client.post("/api/live/config", json={"tokens": ["SOL"], "durations": [300, 900]})
        assert res2.status_code == 200
        assert sorted(res2.json()["selected_series"]) == ["sol-up-or-down-15m", "sol-up-or-down-5m"]
    finally:
        engine.update_config(selected_markets=["btc-up-or-down-5m", "eth-up-or-down-5m", "bnb-up-or-down-5m", "sol-up-or-down-5m", "xrp-up-or-down-5m"])


def test_cockpit_ui_preserves_non_rectangular_selection():
    """Verify the cockpit resubmits an exact slug set it cannot express as token x duration."""
    res = client.get("/")
    assert res.status_code == 200
    html = res.text

    assert "let cockpitExactSelection = null;" in html
    assert "function cockpitFilterProductSlugs(" in html
    assert "body.selected_markets = cockpitExactSelection;" in html

    # Every explicit filter click drops back to the product representation
    for handler in ("toggleCockpitToken", "setCockpitTokensAll", "setCockpitDuration"):
        body_start = html.index(f"function {handler}(")
        assert "cockpitExactSelection = null;" in html[body_start:body_start + 400], handler


def test_api_live_config_accepts_non_rectangular_selection():
    """Verify a mixed-duration selection survives a parameter-only reapply."""
    engine = osc_dash.get_live_trader_engine()
    try:
        mixed = ["btc-up-or-down-5m", "eth-up-or-down-15m"]
        res = client.post("/api/live/config", json={"selected_markets": mixed})
        assert res.status_code == 200
        assert sorted(res.json()["selected_series"]) == sorted(mixed)

        # Resubmitting the exact set alongside parameters must not widen it
        res2 = client.post("/api/live/config", json={"offset": 0.03, "selected_markets": mixed})
        assert res2.status_code == 200
        assert sorted(res2.json()["selected_series"]) == sorted(mixed)
        assert sorted(engine.markets.keys()) == sorted(mixed)
    finally:
        engine.update_config(selected_markets=["btc-up-or-down-5m", "eth-up-or-down-5m", "bnb-up-or-down-5m", "sol-up-or-down-5m", "xrp-up-or-down-5m"])


def test_api_live_latency(monkeypatch):
    """Retired: LIVE STREAM TELEMETRY (RTDS vs CLOB) removed — /api/live/latency no longer exists."""
    res = client.get("/api/live/latency?series=btc-up-or-down-5m")
    assert res.status_code == 404


def test_api_live_latency_divergence_values_and_fallback(monkeypatch):
    """Retired: LIVE STREAM TELEMETRY removed — divergence endpoint no longer exists."""
    res = client.get("/api/live/latency?series=btc-up-or-down-5m")
    assert res.status_code == 404


def test_card_stream_telemetry_rendered_in_html():
    """Retired: LIVE STREAM TELEMETRY card removed — assert it is absent."""
    res = client.get("/")
    assert res.status_code == 200
    html = res.text
    assert 'id="card-stream-telemetry"' not in html
    assert 'LIVE STREAM TELEMETRY (RTDS vs CLOB)' not in html
    assert 'id="telActualPrice"' not in html
    assert 'id="telSpotPrice"' not in html
    assert 'function renderStreamTelemetry(' not in html
    assert 'async function fetchCockpitLatency()' not in html
    assert '/api/live/latency' not in html
    assert 'async function pollCockpit()' in html


def test_cockpit_input_placeholders_and_validation_script_in_html():
    """Invalid-input CSS and the validation function exist, without under-field hints.

    Issue #164 removed the numeric range placeholders these used to assert on.
    They restated bounds the registry now supplies as `min`/`max`, and a second
    copy of a bound is exactly what drifted between the two tabs — the Cockpit
    still read "5 – 10000" while the control had been given `min="1"`. The
    range is now asserted against the registry in
    `test_registry_bounds_match_what_the_live_payload_enforces`.
    """
    res = client.get("/")
    assert res.status_code == 200
    html = res.text
    assert 'placeholder="≥ 5.00"' in html
    assert '.form-group input.input-invalid' in html
    assert 'function validateCockpitInputs()' in html
    assert 'hintCockpitOffset' not in html
    assert 'hintCockpitExit' not in html
    assert 'hintCockpitShares' not in html
    assert 'hintCockpitStartBal' not in html


def test_api_live_config_shares_and_balance_minimums():
    """Verify shares < 5 or starting_balance < 5.0 return HTTP 422, while values >= 5 succeed."""
    engine = osc_dash.get_live_trader_engine()
    engine.is_running = False
    orig_shares = engine.shares
    orig_bal = engine.starting_balance

    try:
        # Shares < 5 rejected
        res_shares_err = client.post("/api/live/config", json={"shares": 4})
        assert res_shares_err.status_code == 422

        # Balance < 5.0 rejected
        res_bal_err = client.post("/api/live/config", json={"starting_balance": 4.99})
        assert res_bal_err.status_code == 422

        # Shares = 5 and Balance = 5.0 accepted
        res_ok = client.post("/api/live/config", json={"shares": 5, "starting_balance": 5.0})
        assert res_ok.status_code == 200
        data = res_ok.json()
        assert data["params"]["shares"] == 5
        assert data["starting_balance"] == 5.0
    finally:
        engine.update_config(shares=orig_shares, starting_balance=orig_bal)


def test_api_live_config_smart_cent_normalization():
    """Verify whole-number offset and exit_thresh entered as cents (e.g. 2, 5) normalize to decimals, while fractional entries are not converted."""
    engine = osc_dash.get_live_trader_engine()
    engine.is_running = False
    try:
        res = client.post("/api/live/config", json={"offset": 2, "exit_thresh": 5})
        assert res.status_code == 200
        data = res.json()
        assert abs(data["params"]["offset"] - 0.02) < 1e-4
        assert abs(data["params"]["exit_thresh"] - 0.05) < 1e-4

        # Non-integer values like 1.5 must NOT normalize to cents and should be rejected (> 0.49)
        res_frac = client.post("/api/live/config", json={"offset": 1.5})
        assert res_frac.status_code == 422
    finally:
        engine.update_config(offset=0.02, exit_thresh=0.05)


def test_api_live_config_validation_error_format():
    """Verify validation error responses return HTTP 422 with an explicit error string message."""
    res = client.post("/api/live/config", json={"offset": 99.0})
    assert res.status_code == 422
    data = res.json()
    assert "error" in data
    assert "Invalid configuration:" in data["error"]
    assert "detail" in data


def test_api_live_config_entry_timeout_pct():
    """Verify entry_timeout_pct is configurable and normalizes whole numbers (1-100)."""
    engine = osc_dash.get_live_trader_engine()
    engine.is_running = False
    orig_pct = engine.entry_timeout_pct
    # get_live_trader_engine() is a module-global singleton. Pin it to paper for the
    # duration so update_config cannot reach fetch_polymarket_account_value and make
    # a real Polymarket request if an earlier test left the singleton in live mode.
    orig_mode = engine.mode
    engine.mode = "paper"
    try:
        # Test whole number 10 -> 0.10
        res = client.post("/api/live/config", json={"entry_timeout_pct": 10})
        assert res.status_code == 200
        data = res.json()
        assert abs(data["params"]["entry_timeout_pct"] - 0.10) < 1e-4

        # Test full window 100 -> 1.0
        res_full = client.post("/api/live/config", json={"entry_timeout_pct": 100})
        assert res_full.status_code == 200
        data_full = res_full.json()
        assert abs(data_full["params"]["entry_timeout_pct"] - 1.0) < 1e-4

        # Test decimal 0.25 -> 0.25
        res_dec = client.post("/api/live/config", json={"entry_timeout_pct": 0.25})
        assert res_dec.status_code == 200
        data_dec = res_dec.json()
        assert abs(data_dec["params"]["entry_timeout_pct"] - 0.25) < 1e-4
    finally:
        engine.update_config(entry_timeout_pct=orig_pct)
        engine.mode = orig_mode


def test_api_live_config_reentry_drift_band():
    """Verify reentry_drift_band (issue #95) is exposed and settable at runtime."""
    engine = osc_dash.get_live_trader_engine()
    engine.is_running = False
    orig_band = engine.reentry_drift_band
    orig_mode = engine.mode
    engine.mode = "paper"
    try:
        state = client.get("/api/live/state").json()
        assert abs(state["params"]["reentry_drift_band"] - orig_band) < 1e-9
        assert state["params"]["min_requote_remaining_sec"] == engine.min_requote_remaining_sec
        assert state["params"]["max_reentries_per_window"] == engine.max_reentries_per_window

        res = client.post("/api/live/config", json={"reentry_drift_band": 0.02})
        assert res.status_code == 200
        assert abs(res.json()["params"]["reentry_drift_band"] - 0.02) < 1e-9
        assert abs(engine.reentry_drift_band - 0.02) < 1e-9

        # Out of range is rejected by the payload model, not silently clamped.
        assert client.post("/api/live/config", json={"reentry_drift_band": 0.9}).status_code == 422
    finally:
        engine.update_config(reentry_drift_band=orig_band)
        engine.mode = orig_mode


def test_api_live_config_exit_reversal():
    """Issue #111: exit_reversal is exposed in /api/live/config and settable.

    Cents-to-decimal normalization matches exit_thresh: whole numbers 1-50 are
    treated as cents (e.g. 2 -> 0.02); decimals pass through. Out of range is
    rejected by the payload model with 422.
    """
    engine = osc_dash.get_live_trader_engine()
    orig_running = engine.is_running
    engine.is_running = False
    orig_rev = engine.exit_reversal
    orig_mode = engine.mode
    engine.mode = "paper"
    try:
        state = client.get("/api/live/state").json()
        assert abs(state["params"]["exit_reversal"] - engine.exit_reversal) < 1e-9

        # Decimal pass-through
        res = client.post("/api/live/config", json={"exit_reversal": 0.03})
        assert res.status_code == 200
        assert abs(res.json()["params"]["exit_reversal"] - 0.03) < 1e-9
        assert abs(engine.exit_reversal - 0.03) < 1e-9

        # Cents normalization: 2 -> 0.02
        res_cents = client.post("/api/live/config", json={"exit_reversal": 2})
        assert res_cents.status_code == 200
        assert abs(res_cents.json()["params"]["exit_reversal"] - 0.02) < 1e-9

        # Out of range is rejected by the payload model, not silently clamped.
        assert client.post("/api/live/config", json={"exit_reversal": 0.9}).status_code == 422
    finally:
        engine.update_config(exit_reversal=orig_rev)
        engine.mode = orig_mode
        engine.is_running = orig_running


def test_api_live_config_naked_leg_knobs():
    """Issue #124: naked-leg risk knobs are exposed in /api/live/config.

    exit_thresh_naked normalizes whole cents like exit_thresh (3 -> 0.03);
    naked_leg_timeout_pct normalizes whole percentages above 1 to fractions
    (70 -> 0.70); reentry_require_pairable round-trips as a bool. All three
    appear in /api/live/state params.
    """
    engine = osc_dash.get_live_trader_engine()
    orig_running = engine.is_running
    engine.is_running = False
    orig_naked = engine.exit_thresh_naked
    orig_timeout = engine.naked_leg_timeout_pct
    orig_pairable = engine.reentry_require_pairable
    orig_mode = engine.mode
    engine.mode = "paper"
    try:
        # Decimals pass through and appear in state params.
        res = client.post("/api/live/config", json={
            "exit_thresh_naked": 0.04,
            "naked_leg_timeout_pct": 0.8,
            "reentry_require_pairable": False,
        })
        assert res.status_code == 200
        params = res.json()["params"]
        assert abs(params["exit_thresh_naked"] - 0.04) < 1e-9
        assert abs(params["naked_leg_timeout_pct"] - 0.8) < 1e-9
        assert params["reentry_require_pairable"] is False

        # Cents normalization: 3 -> 0.03; percentage: 70 -> 0.70.
        res_norm = client.post("/api/live/config", json={
            "exit_thresh_naked": 3,
            "naked_leg_timeout_pct": 70,
        })
        assert res_norm.status_code == 200
        params = res_norm.json()["params"]
        assert abs(params["exit_thresh_naked"] - 0.03) < 1e-9
        assert abs(params["naked_leg_timeout_pct"] - 0.70) < 1e-9

        # Out of range rejected by the payload model.
        assert client.post("/api/live/config", json={"exit_thresh_naked": 0.9}).status_code == 422
        # Above 100 percent: the before-validator only divides values <= 100,
        # so 150 stays out of the field's le=1.0 range and is rejected.
        assert client.post("/api/live/config", json={"naked_leg_timeout_pct": 150}).status_code == 422
    finally:
        engine.update_config(
            exit_thresh_naked=orig_naked,
            naked_leg_timeout_pct=orig_timeout,
            reentry_require_pairable=orig_pairable,
        )
        engine.mode = orig_mode
        engine.is_running = orig_running


def test_api_live_config_patient_band_preset():
    """Issue #137: patient_band_maker preset is selectable and echoed in state.

    Applies offset 0.03, band 0.04, delay 60s, no stop, chase cap 0.98, and
    the pilot universe; unknown presets are rejected with 400.
    """
    engine = osc_dash.get_live_trader_engine()
    orig_running = engine.is_running
    engine.is_running = False
    orig_mode = engine.mode
    engine.mode = "paper"
    orig_params = dict(engine.get_state()["params"])
    orig_markets = [s[0] for s in engine.selected_series]
    orig_active_preset = engine.active_preset
    try:
        res = client.post("/api/live/config", json={"preset": "patient_band_maker"})
        assert res.status_code == 200
        body = res.json()
        params = body["params"]
        assert abs(params["offset"] - 0.03) < 1e-9
        assert abs(params["entry_band"] - 0.04) < 1e-9
        assert abs(params["entry_delay_sec"] - 60.0) < 1e-9
        assert params["stop_loss_enabled"] is False
        assert abs(params["max_pair_cost"] - 0.98) < 1e-9
        assert body["active_preset"] == "patient_band_maker"
        assert set(body["selected_series"]) == {
            "xrp-up-or-down-15m", "bnb-up-or-down-15m", "eth-up-or-down-5m"}

        state = client.get("/api/live/state").json()
        assert state["active_preset"] == "patient_band_maker"

        # Individual knobs stay settable without a preset.
        res_knobs = client.post("/api/live/config", json={
            "entry_delay_sec": 30,
            "entry_band": 0.03,
            "stop_loss_enabled": True,
        })
        assert res_knobs.status_code == 200
        params = res_knobs.json()["params"]
        assert abs(params["entry_delay_sec"] - 30.0) < 1e-9
        assert abs(params["entry_band"] - 0.03) < 1e-9
        assert params["stop_loss_enabled"] is True

        # Unknown preset rejected, config untouched.
        res_bad = client.post("/api/live/config", json={"preset": "nope"})
        assert res_bad.status_code == 400
        # Absurd delay rejected at the boundary (would silently never quote).
        assert client.post(
            "/api/live/config", json={"entry_delay_sec": 99999}).status_code == 422
    finally:
        engine.update_config(
            offset=orig_params["offset"],
            exit_thresh=orig_params["exit_thresh"],
            shares=orig_params["shares"],
            entry_delay_sec=orig_params["entry_delay_sec"],
            entry_band=orig_params["entry_band"],
            stop_loss_enabled=orig_params["stop_loss_enabled"],
            max_pair_cost=orig_params["max_pair_cost"],
            selected_markets=orig_markets,
            # Restore a preset latch the manual knob posts above cleared.
            preset=orig_active_preset if orig_active_preset else None,
        )
        engine.mode = orig_mode
        engine.is_running = orig_running


# ============================================================================
# Issue #139: queue-telemetry endpoint
# ============================================================================

def _write_fills(path, rows):
    Path(path).write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _telemetry_rows():
    return [
        {"ts": 1.0, "slug": "s", "market_slug": "w1", "condition_id": "c1",
         "leg": "UP", "chased": False, "resting_price": 0.48, "fill_price": 0.48,
         "queue_ahead_at_rest": 120.0, "printed_size_at_price_since_rest": 12.0,
         "filled_size": 5, "fill_ratio": 0.1, "ratio_flagged": False,
         "window_elapsed_sec": 10.0, "mid_at_fill": 0.50, "resting_pair_cost": 0.96},
        {"ts": 2.0, "slug": "s", "market_slug": "w1", "condition_id": "c1",
         "leg": "DOWN", "chased": False, "resting_price": 0.48, "fill_price": 0.48,
         "queue_ahead_at_rest": 100.0, "printed_size_at_price_since_rest": 20.0,
         "filled_size": 5, "fill_ratio": 0.2, "ratio_flagged": False,
         "window_elapsed_sec": 12.0, "mid_at_fill": 0.50, "resting_pair_cost": 0.96},
        {"ts": 3.0, "slug": "s", "market_slug": "w2", "condition_id": "c2",
         "leg": "UP", "chased": False, "resting_price": 0.48, "fill_price": 0.48,
         "queue_ahead_at_rest": 100.0, "printed_size_at_price_since_rest": 60.0,
         "filled_size": 5, "fill_ratio": 0.6, "ratio_flagged": False,
         "window_elapsed_sec": 10.0, "mid_at_fill": 0.50, "resting_pair_cost": 0.96},
        {"ts": 4.0, "slug": "s", "market_slug": "w3", "condition_id": "c3",
         "leg": "UP", "chased": False, "resting_price": 0.48, "fill_price": 0.48,
         "queue_ahead_at_rest": 100.0, "printed_size_at_price_since_rest": 250.0,
         "filled_size": 5, "fill_ratio": 2.5, "ratio_flagged": False,
         "window_elapsed_sec": 10.0, "mid_at_fill": 0.50, "resting_pair_cost": 0.96},
        {"ts": 5.0, "slug": "s", "market_slug": "w1", "condition_id": "c1",
         "leg": "DOWN", "chased": True, "resting_price": 0.50, "fill_price": 0.50,
         "queue_ahead_at_rest": 0.0, "printed_size_at_price_since_rest": 5.0,
         "filled_size": 5, "fill_ratio": 0.05, "ratio_flagged": False,
         "window_elapsed_sec": 14.0, "mid_at_fill": 0.50, "resting_pair_cost": 0.98},
    ]


def _settle_rows():
    return [
        {"action": "WINDOW_SETTLE", "market_slug": "w1", "pnl_usd": 0.10},
        {"action": "WINDOW_SETTLE", "market_slug": "w2", "pnl_usd": -0.40},
        {"action": "WINDOW_SETTLE", "market_slug": "w3", "pnl_usd": 0.05},
    ]


def test_api_live_queue_telemetry_aggregation(tmp_path, monkeypatch):
    """Buckets, chased separation, and tape-like verdict from a fixture."""
    fills = tmp_path / "fills.jsonl"
    trades = tmp_path / "trades.jsonl"
    _write_fills(fills, _telemetry_rows())
    _write_fills(trades, _settle_rows())
    monkeypatch.setattr(osc_dash, "QUEUE_TELEMETRY_FILE", fills)
    monkeypatch.setattr(osc_dash, "QUEUE_TELEMETRY_TRADES_FILE", trades)
    monkeypatch.setattr(osc_dash, "_queue_telemetry_cache",
                        {"ts": 0.0, "payload": None})
    res = client.get("/api/live/queue_telemetry")
    assert res.status_code == 200
    body = res.json()
    assert body["empty"] is False
    assert body["total_fills"] == 5
    counts = [b["count"] for b in body["buckets"]]
    assert counts == [2, 0, 1, 1]
    means = [b["mean_settle_pnl_usd"] for b in body["buckets"]]
    assert abs(means[0] - 0.10) < 1e-9
    assert means[1] is None
    assert abs(means[2] - (-0.40)) < 1e-9
    assert abs(means[3] - 0.05) < 1e-9
    assert body["chased"] == {"count": 1, "mean_settle_pnl_usd": 0.10}
    assert body["verdict"] == "tape-like"
    # Issue #173: these fixture lines predate `tape_source`, so they are the
    # REST-measured sample they always were.
    assert body["tape_sources"] == {"rest": 5}
    assert body["tape_source_used"] == "rest"


def test_api_live_queue_telemetry_verdict_uses_one_tape_only(tmp_path, monkeypatch):
    """A mixed file must not produce a verdict blended across both tapes.

    Issue #173: the socket tape sees nearly every print and the REST data-api
    tape saw ~1.4% of them (#165), so `fill_ratio` — and the verdict computed
    from it — is comparable only within one tape. The socket-measured fills
    win: a smaller accurate sample beats a larger biased one.
    """
    fills = tmp_path / "fills.jsonl"
    trades = tmp_path / "trades.jsonl"
    rows = _telemetry_rows()
    for r in rows[:2]:
        r["tape_source"] = "ws"
    _write_fills(fills, rows)
    _write_fills(trades, _settle_rows())
    monkeypatch.setattr(osc_dash, "QUEUE_TELEMETRY_FILE", fills)
    monkeypatch.setattr(osc_dash, "QUEUE_TELEMETRY_TRADES_FILE", trades)
    monkeypatch.setattr(osc_dash, "_queue_telemetry_cache",
                        {"ts": 0.0, "payload": None})
    body = client.get("/api/live/queue_telemetry").json()
    # Every tape in the file is reported, but only one was aggregated.
    assert body["tape_sources"] == {"ws": 2, "rest": 3}
    assert body["tape_source_used"] == "ws"
    assert body["total_fills"] == 2, "the REST fills leaked into the verdict sample"
    assert sum(b["count"] for b in body["buckets"]) <= 2


def test_api_live_queue_telemetry_keeps_rest_when_no_socket_fills_exist(
        tmp_path, monkeypatch):
    """An all-REST file is internally comparable, so nothing is dropped."""
    fills = tmp_path / "fills.jsonl"
    trades = tmp_path / "trades.jsonl"
    _write_fills(fills, _telemetry_rows())
    _write_fills(trades, _settle_rows())
    monkeypatch.setattr(osc_dash, "QUEUE_TELEMETRY_FILE", fills)
    monkeypatch.setattr(osc_dash, "QUEUE_TELEMETRY_TRADES_FILE", trades)
    monkeypatch.setattr(osc_dash, "_queue_telemetry_cache",
                        {"ts": 0.0, "payload": None})
    body = client.get("/api/live/queue_telemetry").json()
    assert body["tape_source_used"] == "rest"
    assert body["total_fills"] == 5
    assert body["verdict"] == "tape-like"


def test_api_live_queue_telemetry_empty_payload_carries_tape_sources(tmp_path, monkeypatch):
    """The empty shape gains the key too, so readers need no `.get` guard."""
    fills = tmp_path / "fills.jsonl"
    fills.write_text("", encoding="utf-8")
    monkeypatch.setattr(osc_dash, "QUEUE_TELEMETRY_FILE", fills)
    monkeypatch.setattr(osc_dash, "QUEUE_TELEMETRY_TRADES_FILE", tmp_path / "nope.jsonl")
    monkeypatch.setattr(osc_dash, "_queue_telemetry_cache",
                        {"ts": 0.0, "payload": None})
    body = client.get("/api/live/queue_telemetry").json()
    assert body["empty"] is True
    assert body["tape_sources"] == {}
    assert body["tape_source_used"] is None


def test_api_live_queue_telemetry_verdict_transitions(tmp_path, monkeypatch):
    """queue-toxic and mixed/unclear verdicts follow the bucket means."""
    fills = tmp_path / "fills.jsonl"
    trades = tmp_path / "trades.jsonl"
    rows = [dict(r, market_slug="wx", fill_ratio=2.0,
                 printed_size_at_price_since_rest=200.0) for r in _telemetry_rows()]
    rows = [r for r in rows if not r["chased"]]
    _write_fills(fills, rows)
    _write_fills(trades, [{"action": "WINDOW_SETTLE", "market_slug": "wx", "pnl_usd": 0.30}])
    monkeypatch.setattr(osc_dash, "QUEUE_TELEMETRY_FILE", fills)
    monkeypatch.setattr(osc_dash, "QUEUE_TELEMETRY_TRADES_FILE", trades)
    monkeypatch.setattr(osc_dash, "_queue_telemetry_cache",
                        {"ts": 0.0, "payload": None})
    res = client.get("/api/live/queue_telemetry")
    assert res.status_code == 200
    body = res.json()
    assert body["verdict"] == "queue-toxic"

    # Mixed: high buckets positive but low buckets positive too.
    mixed_rows = [dict(r, market_slug="wm", fill_ratio=0.1,
                       printed_size_at_price_since_rest=10.0) for r in _telemetry_rows()]
    mixed_rows = [r for r in mixed_rows if not r["chased"]]
    mixed_rows.append(dict(_telemetry_rows()[3], market_slug="wm"))
    _write_fills(fills, mixed_rows)
    _write_fills(trades, [{"action": "WINDOW_SETTLE", "market_slug": "wm", "pnl_usd": 0.20}])
    monkeypatch.setattr(osc_dash, "_queue_telemetry_cache",
                        {"ts": 0.0, "payload": None})
    assert client.get("/api/live/queue_telemetry").json()["verdict"] == "mixed/unclear"


def test_api_live_queue_telemetry_empty_state(tmp_path, monkeypatch):
    """Missing file yields an explicit empty payload, HTTP 200."""
    monkeypatch.setattr(osc_dash, "QUEUE_TELEMETRY_FILE",
                        tmp_path / "absent.jsonl")
    monkeypatch.setattr(osc_dash, "_queue_telemetry_cache",
                        {"ts": 0.0, "payload": None})
    res = client.get("/api/live/queue_telemetry")
    assert res.status_code == 200
    body = res.json()
    assert body["empty"] is True
    assert body["total_fills"] == 0
    assert body["buckets"] == []
    assert body["chased"] == {"count": 0, "mean_settle_pnl_usd": None}
    assert body["verdict"] == "awaiting fills"


def test_cockpit_queue_and_pnl_panels_in_html():
    """Cockpit page has queuePanel and pnlHistPanel removed from DOM to prioritize orders & trades table height."""
    res = client.get("/")
    assert res.status_code == 200
    html = res.text
    assert 'id="queuePanel"' not in html
    assert 'id="pnlHistPanel"' not in html
    assert 'id="orders-trades-card"' in html
    assert "fetchQueueTelemetry" in html
    assert "renderQueuePanel" in html


def test_cockpit_pnl_histogram_render_hook_in_html():
    """Histogram math functions remain in page script while DOM panels are pruned."""
    res = client.get("/")
    assert res.status_code == 200
    html = res.text
    assert "renderPnlHistogram" in html
    assert "pnlBootstrapCiLo" in html
    assert "freedmanDiaconisBins" in html
    assert "session window" in html


def test_api_live_queue_telemetry_rejects_bad_ratios(tmp_path, monkeypatch):
    """NaN / inf / negative fill_ratio are excluded from totals and verdict."""
    fills = tmp_path / "fills.jsonl"
    trades = tmp_path / "trades.jsonl"
    rows = [dict(r) for r in _telemetry_rows() if not r["chased"]]
    rows[0]["fill_ratio"] = float("nan")
    rows[1]["fill_ratio"] = float("inf")
    rows[2]["fill_ratio"] = -0.5
    _write_fills(fills, rows)
    _write_fills(trades, _settle_rows())
    monkeypatch.setattr(osc_dash, "QUEUE_TELEMETRY_FILE", fills)
    monkeypatch.setattr(osc_dash, "QUEUE_TELEMETRY_TRADES_FILE", trades)
    monkeypatch.setattr(osc_dash, "_queue_telemetry_cache",
                        {"ts": 0.0, "payload": None})
    res = client.get("/api/live/queue_telemetry")
    assert res.status_code == 200
    body = res.json()
    # Only the 2.5-ratio row survives (finite, >= 0).
    assert body["empty"] is False
    assert body["total_fills"] == 1


def test_api_live_queue_telemetry_chased_without_settlement(tmp_path, monkeypatch):
    """Chased fill whose market never settled: 200, count kept, mean None."""
    fills = tmp_path / "fills.jsonl"
    trades = tmp_path / "trades.jsonl"
    rows = [dict(r) for r in _telemetry_rows() if r["chased"]]
    rows[0]["market_slug"] = "unsettled-xyz"
    _write_fills(fills, rows)
    _write_fills(trades, _settle_rows())
    monkeypatch.setattr(osc_dash, "QUEUE_TELEMETRY_FILE", fills)
    monkeypatch.setattr(osc_dash, "QUEUE_TELEMETRY_TRADES_FILE", trades)
    monkeypatch.setattr(osc_dash, "_queue_telemetry_cache",
                        {"ts": 0.0, "payload": None})
    res = client.get("/api/live/queue_telemetry")
    assert res.status_code == 200
    body = res.json()
    assert body["chased"] == {"count": 1, "mean_settle_pnl_usd": None}


def test_cockpit_panels_inside_cockpit_tab():
    """Orders & Trades card lives inside tab-cockpit, queue/pnl panels removed."""
    html = client.get("/").text
    tab = html.index('id="tab-cockpit"')
    toast = html.index('id="toastContainer"')
    assert tab < html.index('id="orders-trades-card"') < toast
    assert 'id="queuePanel"' not in html
    assert 'id="pnlHistPanel"' not in html


def _write_delay_fixture(tmp_path):
    """One 70-tick window, tape on ticks 0..59 only (issue #145)."""
    cid = "0xCID_DELAY"
    ticks = []
    for i in range(70):
        t = _make_fake_tick(1000.0 + i, cid, "btc-updown-5m-1000",
                            "btc-up-or-down-5m", 0.50,
                            tape=[{"asset": f"{cid}_up", "price": 0.48, "size": 100},
                                  {"asset": f"{cid}_dn", "price": 0.48, "size": 100}] if i < 60 else [])
        t["start_ts"] = 1000.0
        ticks.append(t)
    fake = tmp_path / "fake_delay.jsonl"
    with open(fake, "w", encoding="utf-8") as f:
        for t in ticks:
            f.write(json.dumps(t) + "\n")
    return fake


def test_api_backtest_entry_delay_band_passthrough(tmp_path, monkeypatch):
    """Delay=60 holds quotes past the only tape prints; echo carries knobs."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    _write_delay_fixture(tmp_path)
    base = client.get("/api/backtest?file=fake_delay.jsonl&offset=0.02&fill_model=tape").json()
    assert base["overall"]["pairs"] == 1
    delayed = client.get("/api/backtest?file=fake_delay.jsonl&offset=0.02&fill_model=tape"
                         "&entry_delay_sec=60&entry_band=0.04").json()
    assert delayed["overall"]["pairs"] == 0
    assert delayed["params"]["entry_delay_sec"] == 60.0
    assert delayed["params"]["entry_band"] == 0.04


def test_api_backtest_entry_delay_band_clamps(tmp_path, monkeypatch):
    """Out-of-range knobs clamp like the live config (3600 / 0.50)."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    _write_delay_fixture(tmp_path)
    d = client.get("/api/backtest?file=fake_delay.jsonl&entry_delay_sec=9999&entry_band=9").json()
    assert d["params"]["entry_delay_sec"] == 3600.0
    assert d["params"]["entry_band"] == 0.50


def test_api_backtest_entry_delay_band_nan_falls_back_off(tmp_path, monkeypatch):
    """Non-finite knobs fall back to off (0.0), never to the boundary."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    _write_delay_fixture(tmp_path)
    d = client.get("/api/backtest?file=fake_delay.jsonl&entry_delay_sec=nan&entry_band=inf").json()
    assert d["params"]["entry_delay_sec"] == 0.0
    assert d["params"]["entry_band"] == 0.0


def test_backtest_file_dropdown_label_is_dynamic():
    """Dropdown default label must use the live aggregate, not a hardcoded count."""
    html = client.get("/").text
    assert "2,820 Windows (Default)" not in html
    assert "All Files / ${defWinVal} Windows (Default)" in html


def test_backtest_delay_band_ui_elements():
    """Dashboard exposes delay/band inputs plus the winning-config preset."""
    html = client.get("/").text
    assert 'id="btEntryDelay"' in html
    assert 'id="btEntryBand"' in html
    assert 'id="btnWinningConfig"' in html
    assert "applyWinningConfig" in html


def test_run_backtest_aborts_previous_run():
    """runBacktest must abort the in-flight request before starting a new one."""
    html = client.get("/").text
    assert "window._btAbort" in html
    assert "new AbortController()" in html
    assert "{signal: ctl.signal}" in html
    assert "err.name === 'AbortError'" in html
    assert "window._btAbort === ctl" in html
    assert "window._btAbort !== ctl" in html


def _no_external_collector(monkeypatch, tmp_path):
    """Point the external-collector probe at an empty directory.

    `/api/rebuild` refuses with 409 while a collector is writing
    `run/ticks/`, and the probe reads that real path. So these tests passed or
    failed depending on whether a collector happened to be running on the
    machine — and, with one running, on where in its ~10-20s manifest write
    gap the assertion landed. Measured on master: 2 of 6 consecutive runs
    failed. Give the probe a directory with no manifest instead.
    """
    empty = tmp_path / "no_ticks"
    empty.mkdir()
    monkeypatch.setattr(osc_dash, "TICKS_DIR", empty)


def test_api_rebuild_windows(monkeypatch, tmp_path):
    """Verify rebuild endpoint runs the rebuild script and echoes ok/output."""
    _no_external_collector(monkeypatch, tmp_path)

    def _mock_run(*args, **kwargs):
        class DummyResult:
            returncode = 0
            stdout = "Wrote 10 windows to run/oscillation_windows.jsonl"
            stderr = ""
        return DummyResult()

    monkeypatch.setattr(subprocess, "run", _mock_run)
    response = client.post("/api/rebuild")
    assert response.status_code == 200
    data = response.json()
    assert data.get("ok") is True
    assert "Wrote 10 windows" in data.get("output")


def test_api_rebuild_windows_failure_and_busy(monkeypatch, tmp_path):
    """Verify rebuild surfaces subprocess failure output and serializes runs."""
    import server.osc_dash as osc_dash_mod
    _no_external_collector(monkeypatch, tmp_path)

    def _mock_fail(*args, **kwargs):
        class DummyResult:
            returncode = 1
            stdout = ""
            stderr = "traceback: boom"
        return DummyResult()

    monkeypatch.setattr(subprocess, "run", _mock_fail)
    data = client.post("/api/rebuild").json()
    assert data.get("ok") is False
    assert "boom" in data.get("output")

    def _mock_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="rebuild", timeout=300)

    monkeypatch.setattr(subprocess, "run", _mock_timeout)
    data = client.post("/api/rebuild").json()
    assert data.get("ok") is False
    assert "timed out" in data.get("output")

    # Lock held -> 409 busy without invoking subprocess
    osc_dash_mod._rebuild_lock.acquire()
    try:
        res = client.post("/api/rebuild")
        assert res.status_code == 409
        assert res.json().get("ok") is False
    finally:
        osc_dash_mod._rebuild_lock.release()


def test_api_rebuild_refused_while_collector_active(tmp_path, monkeypatch):
    """Rebuild returns 409 while own child or external collector is writing."""
    class DummyProc:
        def poll(self):
            return None

    def _boom(*args, **kwargs):
        raise AssertionError("rebuild must not run while collector active")

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(osc_dash, "_collector_proc", DummyProc())
    res = client.post("/api/rebuild")
    assert res.status_code == 409
    assert res.json().get("ok") is False

    monkeypatch.setattr(osc_dash, "_collector_proc", None)
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    (tmp_path / "manifest.json").write_text(
        json.dumps({"ts": time.time(), "lines": 10}), encoding="utf-8")
    res = client.post("/api/rebuild")
    assert res.status_code == 409
    assert "external" in res.json().get("output", "")


def test_api_rebuild_rejects_cross_origin():
    """Verify cross-origin rebuild requests are rejected."""
    res = client.post(
        "/api/rebuild",
        headers={"Origin": "https://malicious-site.evil.com"}
    )
    assert res.status_code == 403


def test_dashboard_rebuild_stats_button_present():
    """Dashboard exposes the Rebuild Stats button and provenance hook."""
    html = client.get("/").text
    assert 'id="btnRebuildStats"' in html
    assert "function rebuildStats()" in html
    assert 'id="provenanceLine"' in html


def test_api_oscillation_provenance_fields(tmp_path, monkeypatch):
    """Verify /api/oscillation exposes dataset source, freshness, and total count."""
    monkeypatch.setattr(osc_dash, "RUN", tmp_path)
    data = client.get("/api/oscillation").json()
    assert data["source"] == "oscillation_windows.jsonl"
    assert data["source_mtime"] is None
    assert data["total_windows"] == 0

    rec = {
        "series": "btc-up-or-down-5m", "label": "BTC 5m", "duration": 300,
        "cid": "0x1", "slug": "s", "start_ts": 1.0, "end_ts": 2.0,
        "closed_ts": 3.0, "snaps": 10, "start_mid": 0.5, "close_mid": 0.5,
        "max_up": 0.0, "max_down": 0.0, "min_mid": 0.5, "max_mid": 0.5,
        "class": "flat", "touch_pair_median": 1.0, "url": "",
    }
    (tmp_path / "oscillation_windows.jsonl").write_text(
        json.dumps(rec) + "\n", encoding="utf-8")
    data2 = client.get("/api/oscillation").json()
    assert data2["total_windows"] == 1
    assert isinstance(data2["source_mtime"], float)


# ---------------------------------------------------------------------------
# Collector badge: staleness threshold vs the collector's real write cadence
# ---------------------------------------------------------------------------

def test_external_collector_threshold_covers_the_real_manifest_cadence():
    """The dashboard must not call a healthy collector dead between writes.

    `scripts/collect_ticks.py` writes the manifest only when
    `int(time.time()) % 10 == 0`, and a ~1.36s round usually steps over that
    second entirely — so real write gaps run to ~20s, not 10s. A threshold at
    or below that gap flickers the badge AND re-opens the Start button, which
    is what stops a second writer from corrupting the same daily tick file.
    """
    step = 1.36                      # observed round + POLL_INTERVAL
    t, writes = 1000.0, []
    for _ in range(1500):
        if int(t) % 10 == 0:
            writes.append(t)
        t += step
    worst_gap = max(writes[i + 1] - writes[i] for i in range(len(writes) - 1))
    assert worst_gap > 10.0, "cadence model no longer reproduces the skipped-second gap"
    assert osc_dash.EXTERNAL_COLLECTOR_STALE_SEC > worst_gap, (
        f"threshold {osc_dash.EXTERNAL_COLLECTOR_STALE_SEC}s is inside the "
        f"collector's own {worst_gap:.1f}s write gap — the badge will flicker "
        "and Start will unlock mid-capture")


def test_live_external_collector_is_detected_across_a_write_gap(tmp_path, monkeypatch):
    """A manifest written 20s ago is a live collector, not a dead one."""
    ticks = tmp_path / "ticks"
    ticks.mkdir()
    now = 1_760_000_000.0
    (ticks / "manifest.json").write_text(json.dumps({"ts": now - 20.0}), encoding="utf-8")
    monkeypatch.setattr(osc_dash, "TICKS_DIR", ticks)
    ext = osc_dash._detect_external_collector(now=now)
    assert ext["live"] is True
    assert ext["manifest_age_sec"] == pytest.approx(20.0)


def test_genuinely_dead_collector_is_still_reported_dead(tmp_path, monkeypatch):
    """Raising the threshold must not make a stopped writer look alive."""
    ticks = tmp_path / "ticks"
    ticks.mkdir()
    now = 1_760_000_000.0
    (ticks / "manifest.json").write_text(json.dumps({"ts": now - 120.0}), encoding="utf-8")
    monkeypatch.setattr(osc_dash, "TICKS_DIR", ticks)
    assert osc_dash._detect_external_collector(now=now)["live"] is False


def test_collector_badge_reads_as_healthy_not_as_a_warning():
    """Amber read as danger for what is the normal way to run a long capture."""
    html = client.get("/").text
    assert "COLLECTING" in html
    # The old wording is gone: it put process ownership in the badge text.
    # The old wording is gone: it put process ownership in the badge text and
    # painted a normal standalone capture amber, which reads as a warning.
    assert "🟡 External live" not in html
    assert "(standalone writer — Start blocked)" not in html
    assert "⚪ Paused" not in html
    assert "Collector: 🟡 Start blocked" not in html


def test_start_button_is_locked_not_merely_relabelled():
    """A disabled control needs a style, or it just looks broken on hover."""
    html = client.get("/").text
    assert ".btn-locked{" in html
    assert "cursor:not-allowed" in html
    assert "'btn btn-locked'" in html
    assert "disabled = (src === 'external')" in html


# ===========================================================================
# Issue #164: both tabs render and validate from the one registry
# ===========================================================================

def _spec():
    from backtest.engine import BacktestParams
    return BacktestParams.param_spec()


def test_param_spec_endpoint_serves_the_registry():
    body = client.get("/api/params/spec").json()
    assert "groups" in body and "by_surface" in body
    off = body["groups"]["trading_knobs"]["offset"]
    assert off["label"] == "Spread Offset ($)"
    assert off["bounds"] == [0.001, 0.49]   # the bound live actually enforces
    assert "cockpit" in off["surfaces"] and "backtest" in off["surfaces"]


def _controls_by_surface(html):
    """Map each rendered `data-param` to the tabs that render it.

    Scoped by element id prefix — `bt*` is the Backtest tab, `cockpit*` the
    Cockpit. An earlier version searched the whole page for
    `data-param="<name>"`, so a knob marked for the Cockpit passed while only
    the Backtest rendered it. Verified by deleting the Cockpit's attribute and
    watching the test stay green.
    """
    import re

    tag_re = re.compile(r"<(?:input|select)[^>]*>")
    id_re = re.compile(r'id="([A-Za-z0-9_]+)"')
    param_re = re.compile(r'data-param="([a-z_]+)"')
    out: dict[str, set[str]] = {}
    for tag in tag_re.findall(html):
        m_id = id_re.search(tag)
        m_p = param_re.search(tag)
        if not (m_id and m_p):
            continue
        el_id = m_id.group(1)
        if el_id.startswith("cockpit"):
            surface = "cockpit"
        elif el_id.startswith("bt"):
            surface = "backtest"
        else:
            continue
        out.setdefault(m_p.group(1), set()).add(surface)
    return out


@pytest.mark.parametrize("surface", ["cockpit", "backtest"])
def test_every_knob_marked_for_a_surface_is_rendered_there(surface):
    """A knob marked for a tab that the tab never renders is a lie."""
    rendered = _controls_by_surface(client.get("/").text)
    missing = sorted(
        name
        for group in _spec().values()
        for name, v in group.items()
        if surface in v["surfaces"] and surface not in rendered.get(name, set())
    )
    # `exit_thresh_by_slug` is a dict fanned out into four per-series inputs
    # rather than one control, so it has no single `data-param` of its own.
    missing = [m for m in missing if m != "exit_thresh_by_slug"]
    assert missing == [], (
        f"registry marks these for the {surface} tab, which renders none of "
        f"them: {missing}")


def test_no_knob_is_rendered_on_a_surface_it_is_not_marked_for():
    """Execution assumptions must not leak into the live Cockpit."""
    rendered = _controls_by_surface(client.get("/").text)
    spec = {n: v for group in _spec().values() for n, v in group.items()}
    leaked = sorted(
        f"{name} on {surface}"
        for name, surfaces in rendered.items()
        if name in spec
        for surface in surfaces
        if surface not in spec[name]["surfaces"]
    )
    assert leaked == [], f"knobs rendered where the registry forbids them: {leaked}"


def test_the_two_knobs_that_define_the_winning_preset_are_settable_live():
    """The Cockpit had no input for either; they were reachable only by preset."""
    html = client.get("/").text
    assert 'id="cockpitEntryDelay"' in html
    assert 'id="cockpitEntryBand"' in html
    assert 'id="cockpitStopLossEnabled"' in html


def test_shared_labels_are_not_hard_coded_in_the_page():
    """Drift came from two hand-written copies of each label.

    The registry wording must appear in the served HTML only inside the JSON
    the page fetches — never typed into a `<label>`.
    """
    html = client.get("/").text
    for group in _spec().values():
        for name, v in group.items():
            assert f"<label>{v['label']}</label>" not in html, (
                f"{name}'s label is hard-coded in the page instead of coming "
                "from /api/params/spec")


def test_the_old_drifted_wordings_are_gone():
    """The exact strings the issue cited as evidence of drift."""
    html = client.get("/").text
    for stale in ("Offset from Mid ($0.02 = 0.02 spread)",
                  "Spread Offset (Rest @ 0.50 - offset)",
                  "Order Shares per Leg (Min 5)",
                  "Share Size (per leg)",
                  "Safety Exit Cap ($)"):
        assert stale not in html, f"drifted label still in the page: {stale!r}"


def test_backtest_sends_the_new_knobs():
    html = client.get("/").text
    for q in ("exit_reversal=", "entry_timeout_pct=", "exit_thresh_naked=",
              "naked_leg_timeout_pct=", "stop_loss_enabled=", "enable_leg_chase="):
        assert q in html, f"the Backtest run URL never sends {q}"


@pytest.mark.parametrize("field,over,clamped", [
    ("entry_band", 99.0, 0.50),
    ("entry_delay_sec", 999999.0, 3600.0),
    ("naked_leg_timeout_pct", 5.0, 1.0),
    ("exit_thresh_naked", 9.0, 0.50),
    ("reentry_drift_band", 9.0, 0.50),
])
def test_backtest_api_clamps_to_the_registry_bounds(field, over, clamped, tmp_path,
                                                    monkeypatch):
    """The API must refuse exactly what the engine refuses — no wider.

    `TICKS_DIR` is redirected because this call carries no `file=`: without it
    `/api/backtest` replays every tick file in the real `run/ticks/`. That was
    free when this test was written (#184) — the directory had just been
    archived — and became a multi-minute, multi-GB replay per parameter once a
    capture filled it. The clamp is what is under test; the replay is not.
    """
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    r = client.get("/api/backtest", params={field: over})
    assert r.status_code == 200, f"{field}={over} produced {r.status_code}"
    from server.osc_dash import _clamp_to_spec
    assert _clamp_to_spec(field, over) == pytest.approx(clamped)


def test_clamp_falls_back_to_the_default_on_non_finite_input():
    """NaN compares False against every bound, so min/max would pass it through."""
    from server.osc_dash import _clamp_to_spec
    assert _clamp_to_spec("entry_band", float("nan")) == 0.0
    assert _clamp_to_spec("entry_delay_sec", float("inf")) == 0.0
    assert _clamp_to_spec("entry_band", "not a number") == 0.0


def test_live_config_accepts_the_re_entry_time_gate():
    """`update_config()` always took it; the payload model never declared it."""
    from server.osc_dash import LiveConfigPayload
    assert "min_requote_remaining_sec" in LiveConfigPayload.model_fields
    p = LiveConfigPayload(min_requote_remaining_sec=120.0)
    assert p.min_requote_remaining_sec == pytest.approx(120.0)
    with pytest.raises(Exception):
        LiveConfigPayload(min_requote_remaining_sec=99999.0)


# Registry field -> the `LiveConfigPayload` field carrying the same knob.
# Names differ where live and research chose different words for one thing.
REGISTRY_TO_PAYLOAD = {
    "offset": "offset",
    "quote_shares": "shares",
    "pair_cost_gate": "max_pair_cost",
    "exit_reversal": "exit_reversal",
    "exit_thresh_naked": "exit_thresh_naked",
    "naked_leg_timeout_pct": "naked_leg_timeout_pct",
    "entry_timeout_pct": "entry_timeout_pct",
    "entry_delay_sec": "entry_delay_sec",
    "entry_band": "entry_band",
    "reentry_drift_band": "reentry_drift_band",
    "min_requote_remaining_sec": "min_requote_remaining_sec",
    "reentry_min_remaining_pct": "reentry_min_remaining_pct",
    "max_reentries_per_window": "max_reentries_per_window",
}


def _payload_bounds(payload_field):
    from server.osc_dash import LiveConfigPayload
    meta = LiveConfigPayload.model_fields[payload_field].metadata
    return (next((m.ge for m in meta if hasattr(m, "ge")), None),
            next((m.le for m in meta if hasattr(m, "le")), None))


def test_every_cockpit_knob_is_mapped_to_a_payload_field():
    """The mapping must be exhaustive, or a knob escapes the bounds check.

    The first version of the bounds test listed seven fields by hand. Three
    mismatches sat in the four it did not list — `exit_reversal`,
    `pair_cost_gate` and `exit_thresh_naked` — and a reviewer found them, not
    this suite. Derive the list from the registry so it cannot go stale.
    """
    from backtest.engine import BacktestParams
    cockpit = {
        name
        for group in BacktestParams.param_spec().values()
        for name, v in group.items()
        if "cockpit" in v["surfaces"]
    }
    # `exit_thresh_by_slug` is a dict fanned out per series, not one bounded
    # scalar; the two booleans have no numeric range to compare. Neither has a
    # payload field this check could be applied to.
    for no_range in ("exit_thresh_by_slug", "stop_loss_enabled", "enable_leg_chase"):
        cockpit.discard(no_range)
    unmapped = sorted(cockpit - set(REGISTRY_TO_PAYLOAD))
    assert unmapped == [], (
        f"these knobs are offered in the Cockpit but are not bounds-checked "
        f"against the live payload: {unmapped}")


@pytest.mark.parametrize("field,payload_field", sorted(REGISTRY_TO_PAYLOAD.items()))
def test_registry_bounds_match_what_the_live_payload_enforces(field, payload_field):
    """A registry bound looser than the live one makes the UI lie.

    Found in a live DOM read: the registry advertised `quote_shares` as
    (1, 100000), so the Cockpit rendered `min="1"` over an engine that clamps
    to 5 — the form accepted a value it would silently discard. Review then
    found three more the same way. `applyParamSpec()` overwrites each input's
    `min`/`max` from these bounds, so a loose bound is not cosmetic: it is a
    form that accepts what the request will reject.
    """
    from backtest.engine import BacktestParams

    # The Cockpit's own range, which is what `applyParamSpec()` renders there
    # and therefore what the operator's form will accept.
    low, high = BacktestParams.bounds_for(field, "cockpit")
    live_low, live_high = _payload_bounds(payload_field)
    if live_low is not None:
        assert low >= live_low, (
            f"{field}: registry allows {low}, live rejects below {live_low}")
    if live_high is not None:
        assert high <= live_high, (
            f"{field}: registry allows {high}, live rejects above {live_high}")


def test_every_knob_the_cockpit_posts_is_declared_on_the_payload():
    """Pydantic's `extra="ignore"` turns an undeclared field into a silent no-op.

    Found in review: the Cockpit posted `reentry_min_remaining_pct` and
    `max_reentries_per_window`, both dropped before the handler ran. The
    request returned 200 and the bot kept its old re-entry limits while the
    form showed the new ones.
    """
    import re
    from server.osc_dash import LiveConfigPayload

    html = client.get("/").text
    block = re.search(r"const numeric = \{(.*?)\};", html, re.S)
    assert block, "the Cockpit's numeric field map was not found in the page"
    posted = set(re.findall(r"^\s*([a-z_]+):", block.group(1), re.M))
    posted |= {"stop_loss_enabled", "enable_leg_chase"}
    undeclared = sorted(posted - set(LiveConfigPayload.model_fields))
    assert undeclared == [], (
        f"the Cockpit posts these and the payload silently drops them: {undeclared}")


def test_every_declared_payload_knob_reaches_the_engine():
    """A field declared but never forwarded fails just as silently."""
    import inspect
    import re
    from server.osc_dash import LiveConfigPayload
    import strategy.live_trader as lt

    src = inspect.getsource(__import__("server.osc_dash", fromlist=["api_live_config"]).api_live_config)
    forwarded = set(re.findall(r"(\w+)=payload\.\w+", src))
    accepted = set(inspect.signature(lt.LiveTraderEngine.update_config).parameters)
    for name in ("reentry_min_remaining_pct", "max_reentries_per_window",
                 "min_requote_remaining_sec"):
        assert name in LiveConfigPayload.model_fields, f"{name} not declared"
        assert name in accepted, f"update_config does not accept {name}"
        assert name in forwarded, (
            f"{name} is declared on the payload but never passed to "
            "update_config — the request succeeds and changes nothing")


def test_no_input_advertises_a_range_that_contradicts_the_registry():
    """A `placeholder="5 – 10000"` beside `min="5"` is one more copy to drift."""
    import re
    html = client.get("/").text
    stale = re.findall(r'placeholder="[\d.]+\s*[–-]\s*[\d.]+"', html)
    assert stale == [], (
        f"these inputs restate their range in a placeholder instead of "
        f"taking it from the registry: {stale}")


def test_param_spec_hands_out_a_copy_not_the_cache():
    """One caller mutating the spec must not rewrite it for every surface.

    `param_spec()` is cached, and the cached dict is what `/api/params/spec`
    serialises — the definition both tabs render from. Returning it by
    reference meant a single in-place edit anywhere could silently change every
    label and bound the operator sees.
    """
    first = BacktestParams.param_spec()
    first["trading_knobs"]["offset"]["label"] = "POISONED"
    first["trading_knobs"]["offset"]["bounds"] = (-99.0, 99.0)
    fresh = BacktestParams.param_spec()
    assert fresh["trading_knobs"]["offset"]["label"] == "Spread Offset ($)"
    assert fresh["trading_knobs"]["offset"]["bounds"] == (0.001, 0.49)


def test_spec_for_hands_out_a_copy_too():
    """The single-knob accessor is the one used per-request; same rule."""
    s = BacktestParams.spec_for("entry_band")
    s["label"] = "POISONED"
    s["surfaces"] = ()
    again = BacktestParams.spec_for("entry_band")
    assert again["label"] == "Entry Band ($ from 0.50)"
    assert "cockpit" in again["surfaces"]


def test_the_spec_is_actually_cached_not_rebuilt_each_call():
    """`spec_for` runs once per knob per request; rebuilding would be waste."""
    BacktestParams.param_spec()          # warm
    a = BacktestParams._param_spec_cached()
    b = BacktestParams._param_spec_cached()
    assert a is b, "the build is no longer cached"


def test_every_registry_control_has_an_id_the_surface_detection_understands():
    """`applyParamSpec()` picks a surface from the id prefix.

    A control whose id starts with neither `cockpit` nor `bt` falls through to
    the shared bounds, which for `pair_cost_gate` means a Cockpit-style control
    silently rendering the research range. Nothing today is misnamed; this
    fails the moment one is.
    """
    import re

    html = client.get("/").text
    stray = []
    for tag in re.findall(r"<(?:input|select)[^>]*>", html):
        if "data-param=" not in tag:
            continue
        m = re.search(r'id="([A-Za-z0-9_]+)"', tag)
        if not m:
            stray.append(tag[:60])
            continue
        el_id = m.group(1)
        if not (el_id.startswith("cockpit") or el_id.startswith("bt")):
            stray.append(el_id)
    assert stray == [], (
        "these registry-driven controls have ids applyParamSpec() cannot map "
        f"to a surface, so they get the shared bounds: {stray}")


# --- Issue #193: Stats Summary hero cards reflect live oscillation data ---

def test_summary_hero_literals_removed():
    """Verify the Stats Summary hero no longer states oscillation figures as literals."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    for stale in ("2,820+", "74% of Windows Are Oscillating", "73% oscillating", "80% oscillating"):
        assert stale not in html, f"stale hardcoded figure still served: {stale!r}"


def test_summary_hero_element_ids_present():
    """Verify the hero card ships the live slots, each with an em dash placeholder."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    for elem_id in ("oscHeroOverallPct", "oscHeroTotalWindows", "oscHeroPct5m", "oscHeroPct15m", "oscHeroAsOf"):
        assert f'id="{elem_id}"' in html, f"missing live slot: {elem_id}"
        # The static markup must ship a neutral placeholder, never a stale
        # numeral: read the element's text whatever other attributes it carries.
        tag_open = html.index(f'id="{elem_id}"')
        text_start = html.index(">", tag_open) + 1
        placeholder = html[text_start:html.index("<", text_start)]
        assert placeholder == "\u2014", (
            f"{elem_id} must ship an em dash placeholder, got {placeholder!r}"
        )
    # The wiring itself is exercised by test_render_summary_charts_feeds_the_hero;
    # pin the call site here so a silent rename is caught in the served markup too.
    assert "renderOscillationHero(d.summary);" in html

def test_stoploss_card_declares_static_provenance():
    """Verify the stop-loss card marks itself non-computed and names its provenance."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert 'id="stopLossProvenance"' in html
    start = html.index('id="stopLossProvenance"')
    note = html[start:start + 600]
    # It must say it is not computed from the live dataset...
    assert "Not computed" in note
    # ...and name the newest research, which reached the opposite conclusion.
    assert "ev-research-findings-2026-09-11" in note

def test_summary_hero_announces_async_updates():
    """Verify the hero card is a live region: its figures arrive after first paint."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    card_start = html.index('<h3>Research Conclusion') - 400
    card = html[card_start:html.index("oscHeroAsOf")]
    assert 'aria-live="polite"' in card, "async-populated hero figures need a live region"
    assert 'aria-atomic="true"' in card, "the sentence should be announced whole, not word by word"


# --- Issue #201: backtest stop-loss thresholds grouped under one on/off toggle ---

def test_stop_loss_thresholds_live_inside_the_toggled_group():
    """All four thresholds must sit inside the wrapper the toggle hides."""
    html = client.get("/").text
    start = html.index('<div id="btStopLossFields">')
    end = html.index('data-param-label="quote_shares"', start)
    group = html[start:end]
    for el_id in ("btExit5m", "btExit15m", "btExitBtc", "btExitSol"):
        assert f'id="{el_id}"' in group, (
            f"{el_id} is outside btStopLossFields, so the toggle cannot hide it")


def test_stop_loss_group_survives_the_form_grid():
    """The wrapper must not collapse the four fields into one grid cell.

    `.form-grid` is `repeat(4,1fr)`, so a plain wrapper becomes a single item.
    `display:contents` keeps them as direct grid children, and the `[hidden]`
    override is mandatory: the id selector outranks the UA `[hidden]` rule.
    """
    html = client.get("/").text
    assert "#btStopLossHead{grid-column:1/-1}" in html, (
        "without a full-width header the four fields wrap around it and the "
        "group reads as two unrelated halves")
    assert 'class="form-group" id="btStopLossHead"' in html
    assert "#btStopLossFields,#cockpitStopLossFields{display:contents}" in html
    assert ("#btStopLossFields[hidden],#cockpitStopLossFields[hidden]"
            "{display:none}") in html


def test_stop_loss_switch_is_a_checkbox_matching_the_pair_cost_toggle():
    """The control is the shared toggle widget, not the old dropdown."""
    html = client.get("/").text
    assert '<select id="btStopLossEnabled"' not in html, (
        "the standalone stop-loss dropdown should be gone")
    start = html.index('id="btStopLossToggleLabel"')
    block = html[start:start + 400]
    assert 'class="toggle-switch"' in block
    assert 'class="toggle-slider"' in block
    assert 'type="checkbox" id="btStopLossEnabled"' in block
    assert 'onchange="toggleStopLossInputs()"' in block
    assert "checked" in block, "stop loss defaults to On, as the old select did"
    # Kept so applyParamSpec() still resolves the label and the `bt` surface.
    assert 'data-param="stop_loss_enabled"' in block


def test_toggle_disables_as_well_as_hides_and_drives_the_request():
    """Off must disable the inputs and send stop_loss_enabled=0."""
    html = client.get("/").text
    fn_start = html.index("function toggleStopLossInputs()")
    fn = html[fn_start:html.index("function toggleBtSection", fn_start)]
    assert "wrap.hidden = !enabled" in fn, "Off must hide the group"
    assert "inp.disabled = !enabled" in fn, "Off must disable the inputs too"
    assert (
        "const stopLoss = ($('btStopLossEnabled') && "
        "!$('btStopLossEnabled').checked) ? '0' : '1';"
    ) in html, "runBacktest must read .checked, not .value, off a checkbox"
    assert "stop_loss_enabled=${stopLoss}" in html


def test_reset_restores_the_stop_loss_toggle():
    """resetBtParams must not leave the group hidden with default values."""
    html = client.get("/").text
    fn_start = html.index("function resetBtParams()")
    fn = html[fn_start:html.index("function applyWinningConfig", fn_start)]
    assert "$('btStopLossEnabled').checked = true;" in fn
    assert "toggleStopLossInputs();" in fn


def test_winning_config_states_hold_to_settle_instead_of_faking_it():
    """The preset must turn the stop loss off, not hide it behind 0.49 stops.

    `max_down`/`max_up` are drift from 0.50, so a 0.49 threshold is reachable in
    an extreme window — the preset would then take a stop it claims never to
    take. `stop_loss_enabled=0` is the exact statement.
    """
    html = client.get("/").text
    fn_start = html.index("function applyWinningConfig()")
    fn = html[fn_start:html.index("\n}", fn_start)]
    for stale in ('"0.49"', '"0.50"'):
        assert stale not in fn, (
            f"the preset still fakes hold-to-settle with a {stale} stop")
    assert "$('btStopLossEnabled').checked = false;" in fn
    assert "toggleStopLossInputs();" in fn
    assert "'btStopLossEnabled'" in fn.split("\n")[1], (
        "the toggle belongs in the required-ids guard, or the preset can half apply")


# --- Issue #201: the same grouping on the Live Cockpit ---

def test_cockpit_stop_loss_switch_mirrors_the_backtest_toggle():
    html = client.get("/").text
    assert '<select id="cockpitStopLossEnabled"' not in html, (
        "the cockpit stop-loss dropdown should be gone")
    start = html.index('id="cockpitStopLossToggleLabel"')
    block = html[start:start + 420]
    assert 'class="toggle-switch"' in block
    assert 'type="checkbox" id="cockpitStopLossEnabled"' in block
    assert 'onchange="toggleCockpitStopLossInputs()"' in block
    assert "checked" in block, "the cockpit stop loss defaults to On, as its select did"
    assert 'data-param="stop_loss_enabled"' in block


def test_cockpit_stop_threshold_lives_inside_the_toggled_group():
    html = client.get("/").text
    start = html.index('<div id="cockpitStopLossFields">')
    end = html.index("</div>\n        </div>", start)
    assert 'id="cockpitExit"' in html[start:end], (
        "cockpitExit is outside cockpitStopLossFields, so the toggle cannot hide it")


def test_cockpit_payload_reads_the_checkbox_not_a_select_value():
    """`checkbox.value` is the string "on", which is truthy for both states."""
    html = client.get("/").text
    assert "body.stop_loss_enabled = stopEl.checked;" in html
    assert "stopEl.value === 'true'" not in html


def test_cockpit_toggle_survives_unlocking_the_params():
    """Stopping the bot re-enables every param id; the toggle must be re-applied."""
    html = client.get("/").text
    fn_start = html.index("function updateCockpitParamsLockUI(locked)")
    fn = html[fn_start:html.index("async function", fn_start)]
    assert "toggleCockpitStopLossInputs();" in fn, (
        "unlocking would otherwise re-enable a stop field the toggle turned off")
    tog_start = html.index("function toggleCockpitStopLossInputs()")
    tog = html[tog_start:html.index("function toggleBtSection", tog_start)]
    assert "cockpitParamsLockHint" in tog, (
        "the toggle must not re-enable the input while the bot holds the lock")


def test_both_stop_loss_toggles_are_labelled_for_assistive_tech():
    """The visible text is just ON/OFF, so the switch needs a real name."""
    html = client.get("/").text
    for el_id in ("btStopLossEnabled", "cockpitStopLossEnabled"):
        start = html.index(f'id="{el_id}"')
        assert 'aria-label="Stop loss enabled"' in html[start:start + 200], (
            f"{el_id} would be announced as just 'ON'")
        assert f'<label for="{el_id}"' in html, (
            f"the {el_id} caption should be a click target for its switch")


def test_cockpit_stop_loss_switch_is_locked_while_the_bot_runs():
    """applyCockpitConfig() returns early when running, so the switch must lock.

    Left interactive it would hide the stop group and flip the caption without
    ever reaching /api/live/config — the UI would claim a stop-loss state the
    engine never received.
    """
    html = client.get("/").text
    fn_start = html.index("function updateCockpitParamsLockUI(locked)")
    ids = html[fn_start:html.index("];", fn_start)]
    assert "'cockpitStopLossEnabled'" in ids


def test_dash_script_contains_no_phantom_up_down_mid_keys():
    """Issue #216: Neither up_mid nor down_mid exists in any engine payload.

    The dashboard client script must not reference these phantom names in its
    fallback ternary expressions.
    """
    html = client.get("/").text
    assert "m.up_mid" not in html, "m.up_mid is a phantom key never emitted by live_trader"
    assert "m.down_mid" not in html, "m.down_mid is a phantom key never emitted by live_trader"


def test_dash_resting_price_helper_respects_custom_offset_and_mid():
    """Issue #216: When resting prices are null, the helper must derive quotes from

    the live offset and real mid, and must not hardcode 0.48 or offset 0.02.
    """
    import shutil
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js not installed")

    html = client.get("/").text
    start = html.find("<script>")
    end = html.rfind("</script>")
    assert start != -1 and end != -1
    script = html[start + len("<script>"):end]

    dom_prelude = """
    const setInterval = () => 0;
    const clearInterval = () => {};
    const setTimeout = () => 0;
    const clearTimeout = () => {};
    const fetch = () => Promise.resolve({ ok: true, json: async () => ({}) });
    const EventSource = class { constructor() {} addEventListener() {} close() {} };
    const WebSocket = class { constructor() {} addEventListener() {} send() {} close() {} };
    const makeElem = () => ({
      style: {},
      textContent: '',
      innerHTML: '',
      appendChild: () => {},
      classList: { add: () => {}, remove: () => {}, toggle: () => {} },
      addEventListener: () => {},
      querySelectorAll: () => [],
      value: ''
    });
    const window = { selectedBacktestFile: '', addEventListener: () => {}, location: { search: '' } };
    globalThis.window = window;
    const document = { getElementById: makeElem, querySelectorAll: () => [] };
    globalThis.document = document;
    const localStorage = {
      _data: {},
      getItem(k) { return this._data[k] || null; },
      setItem(k, v) { this._data[k] = String(v); }
    };
    globalThis.localStorage = localStorage;
    """

    test_js = """
    if (typeof cockpitRestingPrice !== 'function') {
      throw new Error('cockpitRestingPrice function is not defined');
    }
    if (typeof cockpitLegPrice !== 'function') {
      throw new Error('cockpitLegPrice function is not defined');
    }

    // 1. When resting quotes are absent and mid is absent, offset=0.03 must yield 0.47, NEVER 0.48
    const emptyMarket = {};
    const upDef = cockpitRestingPrice(emptyMarket, 'up', 0.03);
    const downDef = cockpitRestingPrice(emptyMarket, 'down', 0.03);
    if (upDef !== 0.47) throw new Error(`expected upDef 0.47 with offset 0.03, got ${upDef}`);
    if (downDef !== 0.47) throw new Error(`expected downDef 0.47 with offset 0.03, got ${downDef}`);

    // 2. When mid is present (0.60), anchored quote with offset 0.03
    const anchoredMarket = { mid: 0.60 };
    const upMid = cockpitRestingPrice(anchoredMarket, 'up', 0.03);
    const downMid = cockpitRestingPrice(anchoredMarket, 'down', 0.03);
    if (Math.abs(upMid - 0.57) > 1e-4) throw new Error(`expected upMid 0.57, got ${upMid}`);
    if (Math.abs(downMid - 0.37) > 1e-4) throw new Error(`expected downMid 0.37, got ${downMid}`);

    // 3. When resting quotes are populated, use them directly
    const restingMarket = { resting_up: 0.52, resting_down: 0.44 };
    if (cockpitRestingPrice(restingMarket, 'up', 0.03) !== 0.52) throw new Error('expected resting_up 0.52');
    if (cockpitRestingPrice(restingMarket, 'down', 0.03) !== 0.44) throw new Error('expected resting_down 0.44');

    // 4. cockpitLegPrice prefers fill price over resting price
    const filledMarket = { fill_price_up: 0.55, resting_up: 0.52 };
    if (cockpitLegPrice(filledMarket, 'up', 0.03) !== 0.55) throw new Error('expected fill_price_up 0.55');

    console.log('DASH_RESTING_PRICE_HELPER_TESTS_PASSED');
    process.exit(0);
    """

    res = subprocess.run(
        [node_bin],
        input=dom_prelude + "\n" + script + "\n" + test_js,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert res.returncode == 0, f"Node script failed: {res.stderr}\n{res.stdout}"
    assert "DASH_RESTING_PRICE_HELPER_TESTS_PASSED" in res.stdout


def test_dash_no_book_status_labels_and_cockpit_styling():
    """Issue #207: Dashboard includes NO_BOOK and NO_BOOK_SKIPPED badges and styling."""
    html = client.get("/").text
    assert "'NO_BOOK': 'No Book'" in html
    assert "'NO_BOOK_SKIPPED': 'No Book Skipped'" in html
    assert "NOT QUOTED (UNPRICEABLE BOOK)" in html
    assert "WAITING FOR BOOK" in html
    assert "Skipped — unpriceable book" in html

