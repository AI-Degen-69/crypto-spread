"""Integration tests for the dashboard SPA and FastAPI API endpoints."""
import json
import os
import re
import shutil
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


def test_root_returns_dashboard_spa():
    """Verify that root endpoint serves the dashboard SPA with all containers."""
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text
    assert "app-sidebar" in html
    assert "sidebarToggleBtn" in html
    assert "tab-btn-cockpit" in html
    assert "tab-btn-marketdata" in html
    assert "tab-btn-backtest" in html
    assert "tab-btn-jungleking" in html
    assert "tab-btn-summary" in html
    assert "tab-btn-ticks" in html
    assert "tab-marketdata" in html
    assert "tab-backtest" in html
    assert "tab-summary" in html
    assert "tab-ticks" in html
    assert 'id="tab-jungleking" class="tab-content" aria-labelledby="tab-btn-jungleking"' in html
    assert "collectorBadge" in html
    assert "tapeBadge" in html
    assert "switchTab" in html
    assert "loadManifest" in html
    assert "uploadFileStream" in html
    assert "chip-token-BTC" in html
    assert "chip-token-ETH" in html
    # Backtester market/timeframe chip multi-select (mirrors cockpit chips).
    assert 'id="btToken-BTC"' in html
    assert 'id="btToken-XRP"' in html
    assert 'id="btTokensAll"' in html
    assert 'id="btTokensClear"' in html
    assert 'id="btDur5m"' in html
    assert 'id="btDur15m"' in html
    assert 'id="btDurBoth"' in html
    assert 'toggleBtToken' in html
    assert 'setBtDuration' in html
    assert 'btMarketSelect' not in html
    assert 'btTimeframeSelect' not in html
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

    # All navigation tab buttons exist with SVG icons and labels.
    for tab_id in ["tab-btn-cockpit", "tab-btn-marketdata", "tab-btn-backtest", "tab-btn-jungleking", "tab-btn-summary", "tab-btn-ticks"]:
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
    assert data["files"][0]["windows_5m"] is None
    assert data["files"][0]["windows_15m"] is None
    assert data["aggregate"]["total_windows_5m"] == 0
    assert data["aggregate"]["total_windows_15m"] == 0


def test_api_ticks_manifest_aggregate(tmp_path, monkeypatch):
    """Issue #109: /api/ticks/manifest exposes an additive `aggregate` rollup."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    f1 = tmp_path / "ticks_2026-09-07.jsonl"
    f1.write_text('{"a": 1}\n{"a": 2}\n', encoding="utf-8")
    f2 = tmp_path / "ticks_2026-09-08.jsonl"
    f2.write_text('{"a": 3}\n', encoding="utf-8")
    _write_verify_sidecar(
        tmp_path,
        f1.name,
        windows=5,
        market_breakdown=[
            {"series": "btc-up-or-down-5m", "duration": 300, "windows": 2, "trades": 0},
            {"series": "eth-up-or-down-15m", "duration": 900, "windows": 3, "trades": 0},
        ],
    )

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
    assert agg["total_windows_5m"] == 2
    assert agg["total_windows_15m"] == 3
    assert agg["windows_source"] == "partial"
    files_by_name = {entry["name"]: entry for entry in data["files"]}
    assert files_by_name[f1.name]["windows_5m"] == 2
    assert files_by_name[f1.name]["windows_15m"] == 3
    assert files_by_name[f2.name]["windows_5m"] is None
    assert files_by_name[f2.name]["windows_15m"] is None


def test_manifest_large_file_uses_cached_raw_lines(tmp_path, monkeypatch):
    """Large files show exact cached raw_lines, not the size/950 estimate."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    big = tmp_path / "ticks_2026-09-14.jsonl"
    big.write_bytes(b"x" * 20_000_001)  # >= 20 MB forces the estimate path
    _write_verify_sidecar(tmp_path, big.name, raw_lines=429545)

    data = client.get("/api/ticks/manifest").json()
    entry = {f["name"]: f for f in data["files"]}[big.name]
    assert entry["lines"] == 429545
    assert entry["lines_estimated"] is False
    assert data["aggregate"]["total_lines"] == 429545
    assert data["aggregate"]["total_lines_estimated"] is False


def test_manifest_large_file_without_cache_still_estimates(tmp_path, monkeypatch):
    """Large files with no verify sidecar keep the old size/950 estimate."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    big = tmp_path / "ticks_2026-09-14.jsonl"
    big.write_bytes(b"x" * 20_000_001)

    data = client.get("/api/ticks/manifest").json()
    entry = {f["name"]: f for f in data["files"]}[big.name]
    assert entry["lines"] == int(20_000_001 / 950)
    assert entry["lines_estimated"] is True
    assert data["aggregate"]["total_lines_estimated"] is True


def test_tick_files_render_readiness_vocabulary():
    html = client.get("/").text
    tick_files = html[html.index("async function loadManifest()"):html.index("// Sequential verify queue")]
    tick_verify = html[html.index("async function verifyTickData"):html.index("// Expand/collapse the inline verify accordion")]
    aggregate_labels = re.search(r"const tiles = document.createElement\('div'\);[\s\S]*?tiles.innerHTML =([\s\S]*?)aggWrap.appendChild\(tiles\);", tick_files)
    assert aggregate_labels is not None
    table_headers = re.search(r"thead.innerHTML = '([^']+)'", tick_files)
    assert table_headers is not None
    assert "Tick Snapshots" in html
    assert "Market Windows" in html
    assert "Research Readiness" in html
    assert "Valid Tick Snapshots" in html
    assert "Tape Entries / Window" in html
    assert "COMPLETE CAPTURE" in html
    assert "PARTIAL CAPTURE" in html
    assert "CORRUPTED DATA" in html
    assert "tick-progress" in html
    assert "EXPLORATORY " in html
    assert "RESEARCH READY " in html
    assert "toggleReadinessTooltip" in html
    assert "The targets tell us whether this file contains enough varied data" in html
    assert "claim_note" not in html
    assert "Tick Snapshots" not in aggregate_labels.group(1)
    assert "Tape Entries'" not in aggregate_labels.group(1)
    assert "lineFmt" not in tick_files
    assert "linesVal" not in tick_files
    assert "linesHtml" not in tick_files
    assert "Tick Snapshots</th>" not in tick_files
    assert "Tape Entries / Window" in html
    assert "valid JSONL rows, not trades" not in tick_files
    assert "unique (series, cid) intervals" in tick_files
    assert "snapshots with empty tape_delta; lower is better" in tick_files
    assert "tape entries divided by market windows" not in tick_files
    assert re.findall(r"<th>(.*?)</th>", table_headers.group(1)) == [
        "Last Modified", "File Name", "5m / 15m", "Integrity",
        "Research Readiness", "Actions", "Size",
    ]
    assert "5m means 300-second windows" in tick_files
    assert "15m means 900-second windows" in tick_files
    assert "Counts come from verification reports" in tick_files
    assert "≥ means some files are not verified yet" in tick_files
    assert "${windows5m} × 5m" in tick_files
    assert "${windows15m} × 15m" in tick_files
    assert "formatFileWindows" in tick_files
    assert "value == null ? '—'" in tick_files
    assert "windows-cell" in tick_files
    assert "windowsCell.textContent" in tick_verify
    assert "countWindows(300)" in tick_verify
    assert "countWindows(900)" in tick_verify
    assert "Not verified" in tick_files
    assert "readiness-cell" in tick_files
    assert 'colspan="7"' in tick_files
    assert 'td:nth-child(6)' not in tick_files
    assert "padStart(2, '0')" in html
    assert "data-scale-max" in html
    assert "tick-progress-marker exploratory" in html
    assert "tick-progress-marker research" in html
    assert 'class="tick-progress-targets"' not in html
    assert "tick-progress-targets-row" in html
    assert "tick-progress-grid" in html
    assert "tick-progress-measured-readout" in html
    assert "tick-progress-measured-row" in html
    assert "MEASURED · ${measuredText}" in html
    assert "height:18px" in html
    assert "Next milestone: ${milestoneText" in html
    assert "manifestAggregateWrap" in html
    assert "total_windows_5m" in tick_files
    assert "total_windows_15m" in tick_files
    assert "tick-progress-value" not in html
    assert html.count('class=\"tick-progress-track\"') == 1
    assert "aria-label=\"${esc(label)} measured" in html
    assert "EXPLORATORY · ${exploratoryText}" in html
    assert "RESEARCH READY · ${researchText}" in html


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
    assert agg["total_windows_5m"] is None
    assert agg["total_windows_15m"] is None


def test_manifest_unverified_split_totals_are_partial_subtotals(tmp_path, monkeypatch):
    """Partial coverage reports a zero known subtotal when no file is verified."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    (tmp_path / "ticks_2026-09-08.jsonl").write_text('{"a": 1}\n', encoding="utf-8")

    aggregate = client.get("/api/ticks/manifest").json()["aggregate"]
    assert aggregate["windows_source"] == "partial"
    assert aggregate["total_windows_5m"] == 0
    assert aggregate["total_windows_15m"] == 0


def test_manifest_verified_zero_split_counts(tmp_path, monkeypatch):
    """A matching verify sidecar with no windows means exact zero, not unknown."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    filename = "ticks_2026-09-08.jsonl"
    _write_verify_sidecar(tmp_path, filename, windows=0, market_breakdown=[])

    data = client.get("/api/ticks/manifest").json()
    assert data["files"][0]["windows_5m"] == 0
    assert data["files"][0]["windows_15m"] == 0
    assert data["aggregate"]["total_windows_5m"] == 0
    assert data["aggregate"]["total_windows_15m"] == 0
    assert data["aggregate"]["windows_source"] == "cache"


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


def test_manifest_aggregate_counts_duplicated_tiers_once(tmp_path, monkeypatch):
    """Issue #281 (CodeRabbit round 1): golden/pristine hold copies of the same
    source day — both tiers stay listed as files, but the All Files aggregate
    counts each source day once (no doubled windows/totals)."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    body = (
        json.dumps({"series": "btc-up-or-down-5m", "duration": 300, "cid": "w1",
                    "ts": 1.0, "tape_delta": []}) + "\n"
        + json.dumps({"series": "eth-up-or-down-5m", "duration": 300, "cid": "w2",
                      "ts": 2.0, "tape_delta": []}) + "\n"
    )
    name = "ticks_2026-09-13.jsonl"
    (tmp_path / name).write_text(body, encoding="utf-8")
    (tmp_path / "pristine").mkdir(exist_ok=True)
    (tmp_path / "pristine" / name).write_text(body, encoding="utf-8")
    (tmp_path / "golden").mkdir(exist_ok=True)
    (tmp_path / "golden" / name).write_text(body, encoding="utf-8")
    breakdown = [
        {"series": "btc-up-or-down-5m", "duration": 300, "windows": 1, "trades": 0},
        {"series": "eth-up-or-down-5m", "duration": 300, "windows": 1, "trades": 0},
        {"series": "sol-up-or-down-15m", "duration": 900, "windows": 1, "trades": 0},
    ]
    for name in ("ticks_2026-09-13.jsonl", "pristine/ticks_2026-09-13.jsonl", "golden/ticks_2026-09-13.jsonl"):
        _write_verify_sidecar(tmp_path, name, windows=3, market_breakdown=breakdown)

    data = client.get("/api/ticks/manifest").json()
    names = [f["name"] for f in data["files"]]
    assert "ticks_2026-09-13.jsonl" in names
    assert "pristine/ticks_2026-09-13.jsonl" in names
    assert "golden/ticks_2026-09-13.jsonl" in names
    # 3 rows listed, but only ONE counted per tier-copy in the aggregate.
    assert data["aggregate"]["total_files"] == 3  # all rows remain files
    agg_lines = data["aggregate"]["total_lines"]
    assert agg_lines == 2  # 2 rows in one day, not 6 across copies
    assert data["aggregate"]["total_windows_5m"] == 2
    assert data["aggregate"]["total_windows_15m"] == 1


def test_verify_writes_counts_cache_fed_to_manifest(tmp_path, monkeypatch):
    """Issue #109: /api/ticks/verify persists a counts sidecar that the
    manifest aggregate consumes (series_counts_source == verify_cache)."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    f1 = tmp_path / "ticks_2026-09-08.jsonl"
    f1.write_text(
        json.dumps({"series": "btc-up-or-down-5m", "duration": 300, "cid": "w1", "ts": 1.0, "tape_delta": [{"price": 0.5, "size": 1}]}) + "\n"
        + json.dumps({"series": "btc-up-or-down-5m", "duration": 300, "cid": "w1", "ts": 2.0, "tape_delta": []}) + "\n"
        + json.dumps({"series": "eth-up-or-down-5m", "duration": 300, "cid": "w2", "ts": 1.0, "tape_delta": []}) + "\n"
        + json.dumps({"series": "sol-up-or-down-15m", "duration": 900, "cid": "w3", "ts": 1.0, "tape_delta": []}) + "\n",
        encoding="utf-8",
    )

    res = client.get("/api/ticks/verify", params={"file": f1.name, "wait": 1})
    assert res.status_code == 200
    assert res.json()["readiness"]["level"] == "INSUFFICIENT"
    assert res.json()["readiness"]["targets"]["exploratory"]["min_valid_ticks"] == 1_000
    assert res.json()["capture_state"]["label"] == "CORRUPTED DATA"

    agg = client.get("/api/ticks/manifest").json()["aggregate"]
    assert agg["series_counts_source"] == "verify_cache"
    assert agg["series_counts"]["btc-up-or-down-5m"] == 2
    assert agg["series_counts"]["eth-up-or-down-5m"] == 1
    assert agg["total_windows"] == 3
    assert agg["total_windows_5m"] == 2
    assert agg["total_windows_15m"] == 1
    assert agg["windows_source"] == "cache"

    # Issue #279/#294: CORRUPTED-data file is ineligible for tier 1 but
    # tier 2 picks it as least-bad (it's the only file).
    manifest = client.get("/api/ticks/manifest").json()
    assert manifest["preferred_file"] == f1.name
    assert manifest["preferred_tier"] == 2

    # Per-file market breakdown surfaces from the cache too.
    files = client.get("/api/ticks/manifest").json()["files"]
    assert files[0]["market_breakdown"][0]["series"] == "btc-up-or-down-5m"
    assert files[0]["market_breakdown"][0]["windows"] == 1
    assert files[0]["market_breakdown"][0]["trades"] == 1
    assert files[0]["market_breakdown"][0]["trades_per_window"] == 1.0
    assert files[0]["windows_5m"] == 2
    assert files[0]["windows_15m"] == 1
    assert "readiness" in files[0]


def test_manifest_hides_stale_policy_readiness(tmp_path, monkeypatch):
    """Manifest entries must not expose readiness from an old policy sidecar."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    f1 = tmp_path / "ticks_2026-09-08.jsonl"
    f1.write_text('{"a": 1}\n', encoding="utf-8")
    cache_dir = tmp_path / osc_dash._VERIFY_CACHE_DIRNAME
    cache_dir.mkdir()
    (cache_dir / f"{f1.name}.json").write_text(json.dumps({
        "file": f1.name,
        "status": "PASS",
        "readiness": {"level": "RESEARCH_READY", "policy_version": "old-policy"},
        "windows_count": 3,
        "market_breakdown": [
            {"series": "btc-up-or-down-5m", "duration": 300, "windows": 2, "trades": 0},
            {"series": "eth-up-or-down-15m", "duration": 900, "windows": 1, "trades": 0},
        ],
        "series_counts": {},
        "fingerprint": osc_dash._file_fingerprint(f1),
    }), encoding="utf-8")

    entry = client.get("/api/ticks/manifest").json()["files"][0]
    assert entry["readiness"] is None
    assert entry["readiness_targets"] is None
    assert entry["windows_5m"] == 2
    assert entry["windows_15m"] == 1


def _write_verify_sidecar(tmp_path, name, *, status="PASS", capture_label="COMPLETE CAPTURE",
                          level="RESEARCH_READY", windows=50, policy=None, market_breakdown=None,
                          raw_lines=None, late_starts=0, early_cutoffs=0, problems=None):
    """Write a fingerprint-matched verify sidecar for `name` (Issue #279 helper).

    Issue #295: `name` may be a subpath (e.g. pristine/ticks_2026-09-13.jsonl) —
    the tick file is created under that subdirectory and the sidecar at the
    mirrored .verify_cache/ subpath, exactly as the server resolves them.
    """
    from scripts.verify_tick_data import READINESS_POLICY_VERSION

    target = tmp_path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text('{"a": 1}\n', encoding="utf-8")
    cache_dir = tmp_path / osc_dash._VERIFY_CACHE_DIRNAME
    (cache_dir / name).parent.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(exist_ok=True)
    payload = {
        "file": name,
        "status": status,
        "capture_state": {"label": capture_label},
        "readiness": {"level": level,
                      "policy_version": READINESS_POLICY_VERSION if policy is None else policy},
        "windows_count": windows,
        "market_breakdown": market_breakdown if market_breakdown is not None else [],
        "fingerprint": osc_dash._file_fingerprint(target),
        "series_counts": {},
    }
    if raw_lines is not None:
        payload["raw_lines"] = raw_lines
    # Window-quality inputs. Absent by default, matching the verifier's shape for
    # a clean capture, so `problems` can inject any one of them for the
    # "is this file trustworthy" assertions.
    payload["late_starts_count"] = late_starts
    payload["early_cutoffs_count"] = early_cutoffs
    for key, value in (problems or {}).items():
        payload[key] = value
    (cache_dir / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_manifest_window_quality_reports_windows_not_lines(tmp_path, monkeypatch):
    """The dataset picker reports research-grade windows, not line counts.

    A line is an artefact of how the collector wrote the file; a window is the
    unit the engine replays and the unit a robustness claim rests on. Three
    numbers, each stricter than the last: full windows (captured start to
    close), clean windows (no integrity or continuity problem anywhere in the
    capture), and research windows (clean *and* broad enough to generalise).
    """
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    # Clean, broad, research-ready.
    _write_verify_sidecar(tmp_path, "ready.jsonl", windows=820, late_starts=10,
                          early_cutoffs=5, level="RESEARCH_READY")
    # Clean but only one day, so not research-ready by policy (needs 3+ blocks).
    _write_verify_sidecar(tmp_path, "exploratory.jsonl", windows=1470, level="EXPLORATORY")
    # Structural damage anywhere in the capture disqualifies the clean count.
    _write_verify_sidecar(tmp_path, "gappy.jsonl", windows=400, level="RESEARCH_READY",
                          problems={"sampling_gaps_count": 12})
    # Never verified: we do not know, and must not print a zero.
    (tmp_path / "unverified.jsonl").write_text('{"a": 1}\n', encoding="utf-8")

    files = {f["name"]: f for f in client.get("/api/ticks/manifest").json()["files"]}

    ready = files["ready.jsonl"]["window_quality"]
    assert ready["full_windows"] == 820 - 10 - 5, "late starts and cutoffs are not full windows"
    assert ready["clean_windows"] == 805
    assert ready["research_windows"] == 805
    assert ready["readiness_level"] == "RESEARCH_READY"

    exploratory = files["exploratory.jsonl"]["window_quality"]
    assert exploratory["full_windows"] == 1470
    assert exploratory["clean_windows"] == 1470
    assert exploratory["research_windows"] is None, (
        "a single day cannot clear the 3+ time-block research bar")

    gappy = files["gappy.jsonl"]["window_quality"]
    assert gappy["full_windows"] == 400, "the count is still reported"
    assert gappy["clean_windows"] is None, "sampling gaps mean no window is verifiably clean"
    assert gappy["research_windows"] is None

    assert files["unverified.jsonl"]["window_quality"] is None, (
        "an unverified file must report unknown, not a verified zero")


def test_manifest_window_quality_honours_the_readiness_policy(tmp_path, monkeypatch):
    """A sidecar written under a superseded policy contributes no window counts.

    Its `windows_count` was measured against thresholds that no longer apply, so
    letting it into the picker would rank a file by a standard that has moved.
    """
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    _write_verify_sidecar(tmp_path, "stale.jsonl", windows=9999, policy="2020-01-01.old")

    entry = {f["name"]: f
             for f in client.get("/api/ticks/manifest").json()["files"]}["stale.jsonl"]
    assert entry["window_quality"] is None
    assert entry.get("windows_count") is None, (
        "a stale sidecar's window count must not be ranked or displayed")


def test_manifest_window_quality_never_goes_negative(tmp_path, monkeypatch):
    """More broken windows than windows yields 0, not a negative count."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    _write_verify_sidecar(tmp_path, "bad.jsonl", windows=3, late_starts=5,
                          early_cutoffs=4, level="INSUFFICIENT")

    wq = {f["name"]: f["window_quality"]
          for f in client.get("/api/ticks/manifest").json()["files"]}["bad.jsonl"]
    assert wq["full_windows"] == 0


def test_manifest_preferred_file_picks_healthy_winner(tmp_path, monkeypatch):
    """Issue #279: eligible = PASS + COMPLETE CAPTURE; the most-ready file wins."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    _write_verify_sidecar(tmp_path, "ticks_2026-09-07.jsonl", status="WARN",
                          capture_label="PARTIAL CAPTURE", level="RESEARCH_READY", windows=999)
    _write_verify_sidecar(tmp_path, "ticks_2026-09-08.jsonl", level="EXPLORATORY", windows=900)
    _write_verify_sidecar(tmp_path, "ticks_2026-09-09.jsonl", level="RESEARCH_READY", windows=50)

    data = client.get("/api/ticks/manifest").json()
    assert data["preferred_file"] == "ticks_2026-09-09.jsonl"
    assert data["preferred_tier"] == 1
    flagged = [f["name"] for f in data["files"] if f["is_preferred"]]
    assert flagged == ["ticks_2026-09-09.jsonl"]


def test_manifest_preferred_file_windows_tie_break(tmp_path, monkeypatch):
    """Issue #279: equal readiness ranks by windows_count desc, then mtime desc."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    a = tmp_path / "ticks_2026-09-07.jsonl"
    a.write_text('{"a": 1}\n', encoding="utf-8")
    b = tmp_path / "ticks_2026-09-08.jsonl"
    b.write_text('{"a": 1}\n', encoding="utf-8")
    c = tmp_path / "ticks_2026-09-09.jsonl"
    c.write_text('{"a": 1}\n', encoding="utf-8")
    _write_verify_sidecar(tmp_path, a.name, level="EXPLORATORY", windows=100)
    _write_verify_sidecar(tmp_path, b.name, level="EXPLORATORY", windows=300)
    _write_verify_sidecar(tmp_path, c.name, level="EXPLORATORY", windows=100)
    older = time.time() - 500
    os.utime(a, (older, older))
    os.utime(c, (older + 100, older + 100))  # c newer than a, same windows

    data = client.get("/api/ticks/manifest").json()
    # b wins on windows outright; among the 100-window ties the newer mtime wins.
    assert data["preferred_file"] == "ticks_2026-09-08.jsonl"
    assert data["preferred_tier"] == 1


def test_pick_preferred_pure_ranking():
    """Issue #279/#294: pure ranking — tier 1 level → windows → mtime; tier 2 fallback."""
    def mk(name, **kw):
        return {"name": name, "mtime": 0, "windows_count": 0, **kw}

    files = [
        mk("warn.jsonl", integrity_status="WARN", capture_state={"label": "PARTIAL CAPTURE"},
           readiness={"level": "RESEARCH_READY"}, windows_count=999, mtime=3),
        mk("research.jsonl", integrity_status="PASS", capture_state={"label": "COMPLETE CAPTURE"},
           readiness={"level": "RESEARCH_READY"}, windows_count=40, mtime=2),
        mk("explor.jsonl", integrity_status="PASS", capture_state={"label": "COMPLETE CAPTURE"},
           readiness={"level": "EXPLORATORY"}, windows_count=500, mtime=3),
    ]
    # Tier-1: WARN file is not tier-1 eligible; research wins on level despite fewer windows.
    winner, tier = osc_dash.pick_preferred(files)
    assert winner["name"] == "research.jsonl"
    assert tier == 1
    assert osc_dash.pick_preferred([]) == (None, None)

    # Tier-2: only non-eligible files → tier 2 with the total order.
    winner2, tier2 = osc_dash.pick_preferred([files[0]])
    assert winner2["name"] == "warn.jsonl"
    assert tier2 == 2

    # windows_count beats mtime at equal level (tier 1).
    tie = [
        mk("older.jsonl", integrity_status="PASS", capture_state={"label": "COMPLETE CAPTURE"},
           readiness={"level": "EXPLORATORY"}, windows_count=100, mtime=1),
        mk("newer.jsonl", integrity_status="PASS", capture_state={"label": "COMPLETE CAPTURE"},
           readiness={"level": "EXPLORATORY"}, windows_count=99, mtime=99),
    ]
    assert osc_dash.pick_preferred(tie)[0]["name"] == "older.jsonl"

    # Fully equal keys resolve stably by the caller's (name-sorted) order.
    same = [
        mk("a.jsonl", integrity_status="PASS", capture_state={"label": "COMPLETE CAPTURE"},
           readiness={"level": "EXPLORATORY"}),
        mk("b.jsonl", integrity_status="PASS", capture_state={"label": "COMPLETE CAPTURE"},
           readiness={"level": "EXPLORATORY"}),
    ]
    assert osc_dash.pick_preferred(same)[0]["name"] == "a.jsonl"


# --- Issue #295: pristine/ subpath datasets ---------------------------------


def test_manifest_lists_pristine_files_as_distinct_datasets(tmp_path, monkeypatch):
    """Issue #295: a same-named pristine file and day file both appear with
    distinct names, and the pristine entry carries its own verify verdicts."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    _write_verify_sidecar(tmp_path, "ticks_2026-09-13.jsonl",
                          status="WARN", capture_label="PARTIAL CAPTURE",
                          level="EXPLORATORY", windows=10)
    _write_verify_sidecar(tmp_path, "pristine/ticks_2026-09-13.jsonl",
                          status="PASS", capture_label="COMPLETE CAPTURE",
                          level="RESEARCH_READY", windows=77)

    data = client.get("/api/ticks/manifest").json()
    by_name = {f["name"]: f for f in data["files"]}
    assert set(by_name) == {
        "ticks_2026-09-13.jsonl",
        "pristine/ticks_2026-09-13.jsonl",
    }
    pristine = by_name["pristine/ticks_2026-09-13.jsonl"]
    assert pristine["is_pristine"] is True
    assert pristine["integrity_status"] == "PASS"
    assert pristine["readiness"]["level"] == "RESEARCH_READY"
    assert pristine["windows_count"] == 77
    # The day file keeps its own (different) cached verdicts — no collision.
    assert by_name["ticks_2026-09-13.jsonl"]["integrity_status"] == "WARN"


def test_manifest_preferred_file_can_be_pristine(tmp_path, monkeypatch):
    """Issue #295: a pristine entry wins preferred ranking under the same
    eligibility rules and is starred by its exact subpath name."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    _write_verify_sidecar(tmp_path, "ticks_2026-09-13.jsonl",
                          status="WARN", capture_label="PARTIAL CAPTURE",
                          level="RESEARCH_READY", windows=999)
    _write_verify_sidecar(tmp_path, "pristine/ticks_2026-09-13.jsonl",
                          status="PASS", capture_label="COMPLETE CAPTURE",
                          level="RESEARCH_READY", windows=50)

    data = client.get("/api/ticks/manifest").json()
    assert data["preferred_file"] == "pristine/ticks_2026-09-13.jsonl"
    assert data["preferred_tier"] == 1
    flagged = [f["name"] for f in data["files"] if f["is_preferred"]]
    assert flagged == ["pristine/ticks_2026-09-13.jsonl"]


def test_backtest_and_sweep_resolve_pristine_subpath(tmp_path, monkeypatch):
    """Issue #295: file=pristine/<basename> replays the pristine file, not the
    same-named day file."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    cid = "0xPRISTINE_01"
    ticks = [
        _make_fake_tick(1000.0 + i, cid, "btc-updown-5m-1000", "btc-up-or-down-5m", m)
        for i, m in enumerate((0.50, 0.48, 0.52, 0.50))
    ]
    pristine_dir = tmp_path / "pristine"
    pristine_dir.mkdir()
    pristine_file = pristine_dir / "fake_pristine.jsonl"
    pristine_file.write_text("".join(json.dumps(t) + "\n" for t in ticks), encoding="utf-8")
    # A decoy day file with the same basename must NOT be replayed.
    (tmp_path / "fake_pristine.jsonl").write_text("not-a-tick-file\n", encoding="utf-8")

    res = client.get("/api/backtest?file=pristine%2Ffake_pristine.jsonl&offset=0.02")
    assert res.status_code == 200
    assert "error" not in res.json()

    sweep = client.get("/api/backtest/sweep?axis=queue&file=pristine%2Ffake_pristine.jsonl")
    assert sweep.status_code == 200
    assert sweep.json()["axis"] == "queue"


def test_endpoints_reject_traversal_and_unlisted_subdirs(tmp_path, monkeypatch):
    """Issue #295: the shared resolver still blocks `..`, backslashes and
    non-allow-listed subdirectories on every tick-file endpoint."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    bad_values = (
        "..%2Fsecrets.jsonl",
        "pristine%2F..%2Fsecrets.jsonl",
        "quarantine%2Fticks_2026-09-13.jsonl",  # subdir exists but not allow-listed
        "pristine%5Cticks_2026-09-13.jsonl",  # backslash, not a slash
    )
    for bad in bad_values:
        res = client.get(f"/api/backtest?file={bad}")
        assert res.status_code == 200
        assert res.json().get("error") == "invalid file param", bad
        sweep = client.get(f"/api/backtest/sweep?file={bad}")
        assert sweep.status_code == 400, bad
        verify = client.get(f"/api/ticks/verify?file={bad}&wait=1")
        assert verify.status_code == 400, bad

    missing = client.get("/api/ticks/verify?file=pristine%2Fnope.jsonl&wait=1")
    assert missing.status_code == 404


# --- Issue #292: golden dataset certification card ---------------------------


def _write_golden_manifest(tmp_path, *, policy="old-policy", days=None):
    """Write a golden_manifest.json the way certification would."""
    golden_dir = tmp_path / "golden"
    golden_dir.mkdir(exist_ok=True)
    (golden_dir / "golden_manifest.json").write_text(json.dumps({
        "policy_version": policy,
        "days": days or [],
    }), encoding="utf-8")


def test_golden_endpoint_absent_state(tmp_path, monkeypatch):
    """Issue #292: no golden dir → explicit absent state, 200 OK, checklist
    rendered unchecked (the binding contract from the issue)."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    res = client.get("/api/ticks/golden")
    assert res.status_code == 200
    data = res.json()
    assert data["state"] == "absent"
    assert data["reason"] == "no golden dataset yet"
    assert data["charter"] == "docs/golden-tick-dataset.md"
    assert data["days"] == []
    names = {c["name"] for c in data["checks"]}
    assert {"windows_total", "windows_per_market_pair", "time_blocks",
            "valid_ticks", "sampling_gap_rate", "all_10_series_present",
            "every_golden_day_passes", "policy_version_current"} <= names
    assert all(c["ok"] is False for c in data["checks"])


def test_golden_endpoint_stale_policy_is_not_certified(tmp_path, monkeypatch):
    """Issue #292: a manifest citing an old policy version is never certified."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    _write_golden_manifest(tmp_path, policy="2020-01-01.old")
    _write_verify_sidecar(tmp_path, "golden/ticks_2026-09-13.jsonl",
                          status="PASS", capture_label="COMPLETE CAPTURE",
                          level="RESEARCH_READY", windows=600)
    data = client.get("/api/ticks/golden").json()
    assert data["state"] == "present"
    assert data["policy_version"] == "2020-01-01.old"
    currency = {c["name"]: c for c in data["checks"]}
    assert currency["policy_version_current"]["ok"] is False
    # Everything else can pass, but the stale policy alone blocks certification.
    assert any(c["ok"] for c in data["checks"])


def _golden_certified_fixture(tmp_path, *, market_breakdown):
    """Two golden days with strong metrics, current policy, fresh .idx files."""
    from scripts.verify_tick_data import READINESS_POLICY_VERSION

    _write_golden_manifest(tmp_path, policy=READINESS_POLICY_VERSION)
    for day in ("ticks_2026-09-13.jsonl", "ticks_2026-09-14.jsonl"):
        target = tmp_path / "golden" / day
        target.parent.mkdir(exist_ok=True)
        target.write_text('{"a": 1}\n', encoding="utf-8")
        sidecar = tmp_path / osc_dash._VERIFY_CACHE_DIRNAME / "golden" / f"{day}.json"
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps({
            "file": f"golden/{day}",
            "status": "PASS",
            "capture_state": {"label": "COMPLETE CAPTURE"},
            "readiness": {"level": "RESEARCH_READY",
                          "policy_version": READINESS_POLICY_VERSION},
            "windows_count": 300,
            "valid_ticks": 40_000,
            "corrupt_lines": 0,
            "collector_errors": 0,
            "time_reversals": 0,
            "sampling_gaps_count": 0,
            "time_blocks": ["2026-09-13", "2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17"],
            "market_breakdown": market_breakdown,
            "fingerprint": osc_dash._file_fingerprint(target),
            "series_counts": {},
        }), encoding="utf-8")
        (target.with_name(target.name + ".idx")).write_text("{}", encoding="utf-8")


def test_golden_endpoint_certified_state(tmp_path, monkeypatch):
    """Issue #292: full coverage + healthy days + current policy → certified."""
    from strategy.series import SERIES

    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    _golden_certified_fixture(tmp_path, market_breakdown=[
        {"series": s, "duration": dur, "windows": 60, "trades": 10}
        for s, dur, _label in SERIES
    ])
    data = client.get("/api/ticks/golden").json()
    assert data["state"] == "certified"
    by_name = {c["name"]: c for c in data["checks"]}
    assert by_name["windows_total"]["n"] == 600
    assert by_name["windows_total"]["ok"] is True
    assert by_name["windows_per_market_pair"]["n"] == 120
    assert by_name["windows_per_market_pair"]["ok"] is True
    assert by_name["time_blocks"]["ok"] is True
    assert by_name["valid_ticks"]["n"] == 80_000
    assert by_name["zero_corrupt_rows"]["ok"] is True
    assert by_name["zero_time_reversals"]["ok"] is True
    assert by_name["policy_version_current"]["ok"] is True
    assert by_name["all_10_series_present"]["ok"] is True
    assert all(c["ok"] for c in data["checks"])


def test_golden_endpoint_incomplete_series_coverage(tmp_path, monkeypatch):
    """Issue #292: two series only → the 10-series coverage gate stays unchecked."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    _golden_certified_fixture(tmp_path, market_breakdown=[
        {"series": s, "duration": 300, "windows": 60, "trades": 10}
        for s in ("btc-up-or-down-5m", "eth-up-or-down-5m")
    ])
    data = client.get("/api/ticks/golden").json()
    assert data["state"] == "present"
    by_name = {c["name"]: c for c in data["checks"]}
    assert by_name["all_10_series_present"]["ok"] is False
    assert by_name["all_10_series_present"]["n"] == 2


def test_golden_card_frontend_invariants():
    """Issue #292: the SPA ships the golden card container, loader and badge."""
    html = client.get("/").text
    assert "goldenCardWrap" in html
    assert "loadGoldenCard" in html
    assert "/api/ticks/golden" in html
    assert "Golden Dataset" in html
    # Issue #292 review follow-up: the absent state must render the checklist
    # (the guard checks checks[] directly, not just the state name).
    assert "d.state !== 'absent' || (d.checks || []).length" in html


def test_manifest_preferred_file_absent_when_nothing_qualifies(tmp_path, monkeypatch):
    """Issue #279/#294: empty dir → preferred_file is None; otherwise tier 2 picks least-bad."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    data = client.get("/api/ticks/manifest").json()
    assert data["preferred_file"] is None
    assert data["preferred_tier"] is None
    assert data["files"] == []

    # Uncached file: tier-2 picks it as least-bad (all rank fields zero, but it exists).
    (tmp_path / "ticks_2026-09-08.jsonl").write_text('{"a": 1}\n', encoding="utf-8")
    data = client.get("/api/ticks/manifest").json()
    assert data["preferred_file"] == "ticks_2026-09-08.jsonl"
    assert data["preferred_tier"] == 2
    assert sum(1 for f in data["files"] if f["is_preferred"]) == 1

    # WARN file: still tier-2 eligible — better than no star at all.
    _write_verify_sidecar(tmp_path, "ticks_2026-09-08.jsonl", status="WARN",
                          capture_label="PARTIAL CAPTURE")
    data = client.get("/api/ticks/manifest").json()
    assert data["preferred_file"] == "ticks_2026-09-08.jsonl"
    assert data["preferred_tier"] == 2


def test_manifest_preferred_file_rejects_stale_policy_cache(tmp_path, monkeypatch):
    """Issue #279/#294: a PASS+COMPLETE sidecar under an old readiness policy is stale —
    every eligibility field stays null, but tier 2 still picks it as least-bad."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    _write_verify_sidecar(tmp_path, "ticks_2026-09-08.jsonl", policy="stale-policy-0")

    data = client.get("/api/ticks/manifest").json()
    # Tier-2 picks the only file (all rank axes = 0, but it exists).
    assert data["preferred_file"] == "ticks_2026-09-08.jsonl"
    assert data["preferred_tier"] == 2
    assert len(data["files"]) == 1
    entry = data["files"][0]
    assert entry["integrity_status"] is None
    assert entry["capture_state"] is None
    assert entry["readiness"] is None
    assert entry["is_preferred"] is True


def test_manifest_tier2_all_partial(tmp_path, monkeypatch):
    """Issue #294: only PARTIAL CAPTURE files → tier-2 least-bad winner."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    _write_verify_sidecar(tmp_path, "ticks_2026-09-07.jsonl", status="WARN",
                          capture_label="PARTIAL CAPTURE", level="EXPLORATORY", windows=30)
    _write_verify_sidecar(tmp_path, "ticks_2026-09-08.jsonl", status="PASS",
                          capture_label="PARTIAL CAPTURE", level="RESEARCH_READY", windows=80)
    _write_verify_sidecar(tmp_path, "ticks_2026-09-09.jsonl", status="PASS",
                          capture_label="PARTIAL CAPTURE", level="EXPLORATORY", windows=120)

    data = client.get("/api/ticks/manifest").json()
    # Tier-2: 09-08 wins — PASS > WARN for integrity; PARTIAL vs PARTIAL tie; then
    # RESEARCH_READY (2) > EXPLORATORY (1) beats 09-09's higher windows.
    assert data["preferred_file"] == "ticks_2026-09-08.jsonl"
    assert data["preferred_tier"] == 2
    assert sum(1 for f in data["files"] if f["is_preferred"]) == 1


def test_manifest_tier1_outranks_tier2(tmp_path, monkeypatch):
    """Issue #294: a single PASS+COMPLETE file always wins tier-1 over many PARTIALs."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    _write_verify_sidecar(tmp_path, "ticks_2026-09-07.jsonl", status="PASS",
                          capture_label="PARTIAL CAPTURE", level="RESEARCH_READY", windows=999)
    _write_verify_sidecar(tmp_path, "ticks_2026-09-08.jsonl", status="PASS",
                          capture_label="COMPLETE CAPTURE", level="EXPLORATORY", windows=10)

    data = client.get("/api/ticks/manifest").json()
    assert data["preferred_file"] == "ticks_2026-09-08.jsonl"
    assert data["preferred_tier"] == 1


def test_pick_preferred_tier2_total_order():
    """Issue #294: tier-2 total order — integrity > capture > readiness > windows > mtime."""
    def mk(name, **kw):
        return {"name": name, "mtime": 0, "windows_count": 0, **kw}

    # integrity_status decides: PASS > WARN even with worse capture/windows.
    files = [
        mk("warn_complete.jsonl", integrity_status="WARN",
           capture_state={"label": "COMPLETE CAPTURE"},
           readiness={"level": "RESEARCH_READY"}, windows_count=999),
        mk("pass_partial.jsonl", integrity_status="PASS",
           capture_state={"label": "PARTIAL CAPTURE"},
           readiness={"level": "EXPLORATORY"}, windows_count=1),
    ]
    # Neither is tier-1 eligible (WARN excludes first, PARTIAL excludes second).
    winner, tier = osc_dash.pick_preferred(files)
    assert tier == 2
    assert winner["name"] == "pass_partial.jsonl"  # PASS (2) > WARN (1)

    # capture_state decides at equal integrity.
    files2 = [
        mk("partial.jsonl", integrity_status="PASS",
           capture_state={"label": "PARTIAL CAPTURE"},
           readiness={"level": "RESEARCH_READY"}, windows_count=999),
        mk("complete.jsonl", integrity_status="PASS",
           capture_state={"label": "COMPLETE CAPTURE"},
           readiness={"level": "EXPLORATORY"}, windows_count=1),
    ]
    # Both PASS — but complete.jsonl is tier-1 eligible, so tier 1 wins.
    w2, t2 = osc_dash.pick_preferred(files2)
    assert t2 == 1
    assert w2["name"] == "complete.jsonl"

    # All-PARTIAL same integrity: readiness > windows > mtime.
    files3 = [
        mk("low.jsonl", integrity_status="WARN",
           capture_state={"label": "PARTIAL CAPTURE"},
           readiness={"level": "RESEARCH_READY"}, windows_count=500, mtime=99),
        mk("high.jsonl", integrity_status="WARN",
           capture_state={"label": "PARTIAL CAPTURE"},
           readiness={"level": "RESEARCH_READY"}, windows_count=501, mtime=1),
    ]
    w3, t3 = osc_dash.pick_preferred(files3)
    assert t3 == 2
    assert w3["name"] == "high.jsonl"  # equal integrity/capture/readiness; windows wins


def test_manifest_stale_sidecar_windows_not_ranked(tmp_path, monkeypatch):
    """Issue #294 review: a stale-policy sidecar must not leak windows_count
    into ranking — it scores zero on every axis, like an uncached file."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    stale = tmp_path / "ticks_2026-09-07.jsonl"
    _write_verify_sidecar(tmp_path, stale.name, status="PASS",
                          capture_label="PARTIAL CAPTURE", level="RESEARCH_READY",
                          windows=9999, policy="old-policy")
    uncached = tmp_path / "ticks_2026-09-08.jsonl"
    uncached.write_text('{"a": 1}\n', encoding="utf-8")
    old = time.time() - 500
    os.utime(stale, (old, old))  # stale file older; uncached file newer

    data = client.get("/api/ticks/manifest").json()
    # Stale sidecar contributes nothing: the uncached file wins tier 2 on mtime.
    assert data["preferred_file"] == "ticks_2026-09-08.jsonl"
    assert data["preferred_tier"] == 2
    stale_entry = next(f for f in data["files"] if f["name"] == stale.name)
    assert "windows_count" not in stale_entry


def test_manifest_missing_dir_includes_preferred_tier(tmp_path, monkeypatch):
    """Issue #294 review: the early no-TICKS_DIR response carries preferred_tier=None."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path / "nope")
    data = client.get("/api/ticks/manifest").json()
    assert data["files"] == []
    assert data["preferred_file"] is None
    assert data["preferred_tier"] is None



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
        "file": f1.name, "status": "PASS", "valid_ticks": 1,        "series_counts": {"btc-up-or-down-5m": 1},
            "readiness": {"level": "EXPLORATORY", "policy_version": "2026-09-20.v2"},
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

    # A sidecar from an older readiness policy must not be pre-warmed.
    (cache_dir / "ticks_2026-09-08.jsonl.json").write_text(json.dumps({
        "file": f1.name, "status": "PASS", "valid_ticks": 1,
        "readiness": {"level": "EXPLORATORY", "policy_version": "old-policy"},
        "fingerprint": fp, "ts": 12345.0,
    }), encoding="utf-8")
    osc_dash._VERIFY_REPORT_CACHE.clear()
    osc_dash._prewarm_verify_cache()
    assert f1.name not in osc_dash._VERIFY_REPORT_CACHE

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


def test_api_collector_status_book_shadow(tmp_path, monkeypatch):
    """Issue #349: /api/collector/status surfaces the #174 Phase 1 book_shadow summary.

    Present block -> flat summary with rate, count and tolerance; absent block or
    malformed manifest -> null; the response change stays additive either way.
    """
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    monkeypatch.setattr(osc_dash, "_collector_proc", None)

    # No manifest at all -> null, endpoint healthy.
    res = client.get("/api/collector/status")
    assert res.status_code == 200
    assert res.json()["book_shadow"] is None

    # Present block -> flat ready-to-read summary; tolerance supplied by server.
    mf = tmp_path / "manifest.json"
    mf.write_text(
        json.dumps({
            "tape_empty_rate": 0.1,
            "book_shadow": {
                "comparisons": 39414,
                "divergent": 11737,
                "divergence_rate": 0.2977,
                "mean_abs_bb_delta": 0.0064,
                "mean_abs_ba_delta": 0.0064,
                "max_bb": 0.32,
                "max_ba": 0.32,
                "per_series": {
                    "sol-up-or-down-5m": {"comparisons": 3002, "divergent": 1246},
                    "btc-up-or-down-15m": {"comparisons": 3065, "divergent": 448},
                },
            },
        }),
        encoding="utf-8",
    )
    res = client.get("/api/collector/status")
    assert res.status_code == 200
    d = res.json()
    bs = d["book_shadow"]
    assert bs["comparisons"] == 39414
    assert bs["divergent"] == 11737
    assert bs["divergence_rate"] == 0.2977
    assert bs["tolerance"] == 0.001
    assert bs["max_bb"] == 0.32
    # per_series stays in the payload for the badge tooltip.
    assert bs["per_series"]["sol-up-or-down-5m"]["divergent"] == 1246

    # Malformed manifest -> null, endpoint still answers (same tolerance as tape).
    mf.write_text("{not json", encoding="utf-8")
    res = client.get("/api/collector/status")
    assert res.status_code == 200
    assert res.json()["book_shadow"] is None


def test_shadow_badge_format(tmp_path, monkeypatch):
    """Issue #349: the badge formats honestly — never a rate without its sample size.

    With comparisons: rate + count together. Zero/absent comparisons: 'not enough
    data yet', never '0%'. The server renders badge_text into the payload and the
    UI displays it verbatim — this test owns the format.
    """
    assert osc_dash._shadow_badge_text({
        "comparisons": 39414, "divergent": 11737, "divergence_rate": 0.2977,
        "tolerance": 0.001, "per_series": {},
    }) == "Book Δ: 29.8% (39,414)"
    assert "not enough data" in osc_dash._shadow_badge_text(None).lower()
    assert "not enough data" in osc_dash._shadow_badge_text({
        "comparisons": 0, "divergent": 0, "divergence_rate": None,
        "tolerance": 0.001, "per_series": {},
    }).lower()
    # The status payload carries the server-rendered text for the UI.
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    monkeypatch.setattr(osc_dash, "_collector_proc", None)
    (tmp_path / "manifest.json").write_text(json.dumps({
        "book_shadow": {"comparisons": 100, "divergent": 20,
                        "divergence_rate": 0.2, "per_series": {}},
    }), encoding="utf-8")
    d = client.get("/api/collector/status").json()
    assert d["book_shadow"]["badge_text"] == "Book Δ: 20.0% (100)"


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

    url = "/api/backtest?file=fake_round.jsonl&offset=0.03&queue=75&pair_cost=0.98&exit_default_5m=0.15&size=150"
    response = client.get(url)
    assert response.status_code == 200
    data = response.json()
    assert "params_hash" in data
    assert "params" in data
    assert data["params"]["offset"] == 0.03
    assert data["params"]["queue"] == 75.0
    assert data["params"]["pair_cost"] == 0.98
    assert data["params"]["exit_default_5m"] == 0.15
    assert data["params"]["size"] == 150
    # `gas` is gone from the echoed params: Polymarket sponsors the merge, so
    # there is no cost to report and nothing for a caller to set.
    assert "gas" not in data["params"]
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
    # Issue #136: per-window return distribution histogram
    assert "pnl_histogram" in data
    hist = data["pnl_histogram"]
    assert "bucket_width_cents" in hist
    assert "buckets" in hist
    assert hist["n"] == 4
    assert sum(b["count"] for b in hist["buckets"]) == 4
    assert "mean_cents" in hist
    assert "median_cents" in hist
    # Issue #228: the re-entry mechanism is deleted, so the summary carries
    # no re-entry telemetry at either level.
    assert "reentry_count" not in data["overall"]
    assert "reentry_pnl_cents" not in data["overall"]
    btc_series = data["per_series"]["btc-up-or-down-5m"]
    assert "reentry_count" not in btc_series
    assert "reentry_pnl_cents" not in btc_series

    # There is no fill model to select (issue #226). An old bookmark that
    # still carries one is ignored rather than rejected, and runs the one rule.
    res_legacy = client.get(
        "/api/backtest?file=fake_round.jsonl&offset=0.02&fill_model=cross")
    assert res_legacy.status_code == 200
    assert "fill_model" not in res_legacy.json()["params"]


def _selection_fixture(tmp_path):
    """Two windows: BTC 5m + ETH 15m, under a monkeypatched TICKS_DIR."""
    f = tmp_path / "fake_sel.jsonl"
    rows = []
    for i, (cid, series, dur) in enumerate([
        ("0xSEL_BTC", "btc-up-or-down-5m", 300),
        ("0xSEL_ETH", "eth-up-or-down-15m", 900),
    ]):
        base = 2000.0 + i * 1000
        for j in range(2):
            t = _make_fake_tick(base + j, cid, f"{cid}-{j}", series, 0.50)
            t["duration"] = dur
            t["start_ts"] = base
            rows.append(t)
    f.write_text("\n".join(json.dumps(t) for t in rows) + "\n", encoding="utf-8")
    return f


def test_api_backtest_series_and_durations_selection(tmp_path, monkeypatch):
    """Issue #308: series/durations params filter exactly like the CLI flags."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    _selection_fixture(tmp_path)

    full = client.get("/api/backtest?file=fake_sel.jsonl").json()
    assert full["n_windows"] == 2
    assert full["coverage"]["filtered"] is False
    assert set(full["per_duration"]) == {"300", "900"}
    assert full["per_duration"]["300"]["windows"] == 1
    assert full["per_duration"]["900"]["windows"] == 1

    btc = client.get("/api/backtest?file=fake_sel.jsonl&series=btc").json()
    assert btc["n_windows"] == 1
    assert btc["coverage"]["filtered"] is True
    assert btc["coverage"]["selection"] == {"series": ["btc"], "durations": []}
    assert btc["per_duration"]["300"]["windows"] == 1
    assert btc["per_duration"]["900"]["windows"] == 0

    m15 = client.get("/api/backtest?file=fake_sel.jsonl&durations=900").json()
    assert m15["n_windows"] == 1
    assert m15["overall"]["windows"] == 1

    both = client.get(
        "/api/backtest?file=fake_sel.jsonl&series=eth&durations=900").json()
    assert both["n_windows"] == 1

    # CLI/API parity: same totals on the same file as the CLI replay path.
    from backtest import iter_ticks as _it, replay as _replay
    from backtest.engine import BacktestParams as _BP
    snaps = [s for s in _it(tmp_path / "fake_sel.jsonl")
             if "btc" in s.get("series", "")]
    cli_out = _replay(snaps, _BP(offset=0.02, queue_gate=0.0))
    assert btc["n_windows"] == cli_out["n_windows"]
    assert btc["overall"]["total_pnl_cents"] == round(
        cli_out["aggregate"]["overall"]["total_pnl_cents"] * 5, 2)


def test_api_backtest_bad_selection_is_400_not_silent_zero(tmp_path, monkeypatch):
    """Issue #308: a typo'd series token fails loudly instead of empty."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    _selection_fixture(tmp_path)
    bad = client.get("/api/backtest?file=fake_sel.jsonl&series=bcc")
    assert bad.status_code == 400
    assert "bcc" in bad.json()["error"]
    bad_dur = client.get("/api/backtest?file=fake_sel.jsonl&durations=60")
    assert bad_dur.status_code == 400


def test_api_backtest_sweep_contract_and_validation(tmp_path, monkeypatch):
    """Verify the one-axis sweep response, canonical labels, and file validation."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    fake_file = tmp_path / "fake_sweep.jsonl"
    cid = "0xSWEEP_01"
    ticks = [
        _make_fake_tick(1000.0, cid, "btc-updown-5m-1000", "btc-up-or-down-5m", 0.50),
        _make_fake_tick(1001.0, cid, "btc-updown-5m-1000", "btc-up-or-down-5m", 0.48),
        _make_fake_tick(1002.0, cid, "btc-updown-5m-1000", "btc-up-or-down-5m", 0.52),
        _make_fake_tick(1003.0, cid, "btc-updown-5m-1000", "btc-up-or-down-5m", 0.50),
    ]
    fake_file.write_text("".join(json.dumps(t) + "\\n" for t in ticks), encoding="utf-8")

    response = client.get("/api/backtest/sweep?axis=queue&file=fake_sweep.jsonl")
    assert response.status_code == 200
    data = response.json()
    assert data["axis"] == "queue"
    assert data["series_order"][:2] == ["btc-up-or-down-5m", "eth-up-or-down-5m"]
    assert len(data["series_order"]) == 10
    assert data["series_labels"]["btc-up-or-down-5m"] == "05m BTC"
    assert data["series_labels"]["btc-up-or-down-15m"] == "15m BTC"
    assert isinstance(data["n_windows"], int)
    assert isinstance(data["n_snaps"], int)
    assert len(data["points"]) == len(osc_dash.SWEEP_AXES["queue"])
    assert all(isinstance(point["value"], (int, float)) for point in data["points"])
    assert all({"label", "value", "overall", "per_series"} <= point.keys() for point in data["points"])
    assert set(data["points"][0]["per_series"]) == set(data["series_order"])
    assert data["best_overall"] is None or {"value", "label", "total_pnl_cents"} <= data["best_overall"].keys()
    assert data["best_market"] is None or {"series", "label", "value", "point_label", "total_pnl_cents"} <= data["best_market"].keys()

    tied_points = [
        {
            "value": 1.0,
            "label": "one",
            "overall": {"windows": 1, "total_pnl_cents": 0.0},
            "per_series": {"later": 1.0, "earlier": 1.0},
            "series_present": ["later", "earlier"],
        },
    ]
    _, tied_market = osc_dash._select_sweep_bests(
        tied_points,
        ["earlier", "later"],
        {"earlier": "Earlier", "later": "Later"},
    )
    assert tied_market["series"] == "earlier"

    shared_base = BacktestParams(exit_thresh_by_slug={
        "default_5m": 0.05, "default_15m": 0.07,
        "btc-up-or-down-5m": 0.05, "btc-up-or-down-15m": 0.07,
        "sol-up-or-down-5m": 0.05, "sol-up-or-down-15m": 0.07,
    })
    default_stop, label = osc_dash._sweep_params_for_value(
        shared_base, "exit_stop_default", 0.12)
    assert label == "stop_default=0.12"
    assert default_stop.exit_thresh_by_slug["default_5m"] == 0.12
    assert default_stop.exit_thresh_by_slug["default_15m"] == 0.12
    btc_stop, label = osc_dash._sweep_params_for_value(shared_base, "exit_stop_btc", 0.12)
    assert label == "stop_btc=0.12"
    assert btc_stop.exit_thresh_by_slug["btc-up-or-down-5m"] == 0.12
    assert btc_stop.exit_thresh_by_slug["btc-up-or-down-15m"] == 0.12
    assert btc_stop.exit_thresh_by_slug["default_5m"] == 0.05, \
        "the BTC axis must leave the default thresholds alone"
    sol_stop, label = osc_dash._sweep_params_for_value(shared_base, "exit_stop_sol", 0.12)
    assert label == "stop_sol=0.12"
    assert sol_stop.exit_thresh_by_slug["sol-up-or-down-5m"] == 0.12
    assert sol_stop.exit_thresh_by_slug["sol-up-or-down-15m"] == 0.12
    assert sol_stop.exit_thresh_by_slug["default_15m"] == 0.07, \
        "the SOL axis must leave the default thresholds alone"

    unknown = client.get("/api/backtest/sweep?axis=not-an-axis")
    assert unknown.status_code == 400
    assert unknown.json()["valid"] == sorted(osc_dash.SWEEP_AXES)
    assert unknown.json()["valid"] == [
        "exit_rev", "exit_stop_btc", "exit_stop_default", "exit_stop_sol",
        "late_entry", "offset", "queue", "quote_range"]

    unsafe = client.get("/api/backtest/sweep?file=../secrets.jsonl")
    assert unsafe.status_code == 400
    missing = client.get("/api/backtest/sweep?file=missing.jsonl")
    assert missing.status_code == 404


def test_sweep_empty_result_has_neutral_best_metadata(tmp_path, monkeypatch):
    """Verify an empty sweep response does not invent a best result."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    response = client.get("/api/backtest/sweep?axis=exit_stop_default")
    assert response.status_code == 200
    data = response.json()
    assert len(data["points"]) == len(osc_dash.SWEEP_AXES["exit_stop_default"])
    assert all(point["overall"]["windows"] == 0 for point in data["points"])
    assert data["best_overall"] is None
    assert data["best_market"] is None


def test_api_backtest_sweep_concurrency_releases_guard(tmp_path, monkeypatch):
    """Verify sweep returns 429 while busy and accepts a later request."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    fake_file = tmp_path / "fake_sweep_busy.jsonl"
    fake_file.write_text('{"cid": "0x1", "series": "btc-up-or-down-5m", "ts": 1000.0, "start_ts": 1000.0, "mid": 0.50, "bids": [], "asks": []}\\n', encoding="utf-8")

    import concurrent.futures
    import threading

    mock_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(osc_dash, "get_backtest_pool", lambda: mock_pool)
    started_event = threading.Event()
    release_event = threading.Event()

    def blocking_worker(*args, **kwargs):
        started_event.set()
        release_event.wait(timeout=5.0)
        return {
            "axis": "queue",
            "points": [],
            "series_order": [],
            "series_labels": {},
            "n_snaps": 0,
            "n_windows": 0,
        }

    monkeypatch.setattr(osc_dash, "_run_sweep_worker", blocking_worker)
    responses = {}

    def run_first():
        responses["first"] = client.get("/api/backtest/sweep?file=fake_sweep_busy.jsonl")

    thread = threading.Thread(target=run_first)
    thread.start()
    assert started_event.wait(timeout=3.0), "First sweep did not start in time"

    second = client.get("/api/backtest/sweep?file=fake_sweep_busy.jsonl")
    assert second.status_code == 429

    release_event.set()
    thread.join(timeout=5.0)
    mock_pool.shutdown(wait=True)
    assert responses["first"].status_code == 200
    assert not osc_dash._BACKTEST_RUNNING


def _sweep_fixture(tmp_path, slugs, name="sweep_base.jsonl"):
    """Ticks that actually fill, so P&L is a real discriminator between bases."""
    rows = []
    for slug in slugs:
        for w in range(4):
            cid = f"0x_{slug}_{w}"
            for i in range(30):
                mid = 0.50 + 0.03 * ((i + w) % 4 - 1)
                tape = [{"price": 0.48, "size": 20}] if i == 5 else []
                rows.append(_make_fake_tick(
                    1000.0 + w * 400 + i, cid, f"{slug}-w", slug, mid, tape
                ))
    path = tmp_path / name
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


# A second fixture with one genuine 15m series. `_make_fake_tick` pins every
# window's duration at 300, and the engine keys the stop threshold on that
# duration (`default_{'5m' if duration == 300 else '15m'}`), so a 5m-only
# fixture can never exercise the `default_15m` stop the `exit_stop_default` sweep
# replaces. `_make_fake_tick_15m` is the same shape with the 15m clock and
# duration, so the parity tests below prove the sweep and the backtest agree
# on the 15m thresholds too, not just on `default_5m`.
def _make_fake_tick_15m(ts: float, cid: str, slug: str, series: str, mid: float,
                        tape: list | None = None) -> dict:
    """`_make_fake_tick` with a 15m duration and a matching window clock."""
    tick = _make_fake_tick(ts, cid, slug, series, mid, tape)
    tick["duration"] = 900
    tick["label"] = "BTC 15m"
    tick["start_ts"] = ts - 30
    tick["end_ts"] = ts + 870
    tick["t_rem"] = 870
    return tick


def _sweep_fixture_15m(tmp_path, slugs, name="sweep_base.jsonl"):
    """Fixture mixing 5m windows with one real 15m series that actually fills."""
    path = _sweep_fixture(tmp_path, slugs, name=name)
    rows = []
    for w in range(4):
        cid = f"0x_btc-up-or-down-15m_{w}"
        for i in range(30):
            mid = 0.50 + 0.03 * ((i + w) % 4 - 1)
            tape = [{"price": 0.48, "size": 20}] if i == 5 else []
            rows.append(_make_fake_tick_15m(
                2000.0 + w * 1200 + i, cid, "btc-15m-w", "btc-up-or-down-15m",
                mid, tape
            ))
    with open(path, "a", encoding="utf-8") as f:
        f.write("".join(json.dumps(r) + "\n" for r in rows))
    return path


# Every knob the Backtester exposes, with values chosen to differ from the
# engine defaults so a dropped parameter cannot hide behind an equal default.
_NON_DEFAULT = {
    "offset": 0.02, "queue": 50, "pair_cost": 0.95,
    "exit_default_5m": 0.06, "exit_default_15m": 0.07,
    "exit_btc_5m": 0.08, "exit_sol_5m": 0.09, "exit_reversal": 0.03,
    "size": 5, "quote_lo": 0.20, "quote_hi": 0.80,
    "entry_delay_pct": 4.0, "dead_zone_pct": 12.0,
    "naked_leg_at_expiry": "hold", "enable_leg_chase": True,
}


def test_sweep_base_point_equals_a_backtest_with_the_same_settings(tmp_path, monkeypatch):
    """A sweep point on the axis must reproduce the backtest shown beside it.

    The sweep used to hand-roll its own BacktestParams from six knobs, so every
    later control (pair cost, quote range, dead zone, entry delay, naked-leg,
    leg-chase) silently fell back to an engine default while the page showed the
    operator's value. This pins the two endpoints to one base.
    """
    import concurrent.futures

    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    _sweep_fixture(tmp_path, ["btc-up-or-down-5m", "eth-up-or-down-5m",
                              "sol-up-or-down-5m", "xrp-up-or-down-5m",
                              "bnb-up-or-down-5m"])
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(osc_dash, "get_backtest_pool", lambda: pool)
    try:
        back = client.get("/api/backtest", params={"file": "sweep_base.jsonl", **_NON_DEFAULT})
        assert back.status_code == 200
        b = back.json()
        # The fixture must actually trade, or this test would pass vacuously.
        assert b["overall"]["pairs"] > 0, "fixture did not fill; test would be vacuous"

        sweep = client.get("/api/backtest/sweep", params={
            "axis": "queue", "file": "sweep_base.jsonl", **_NON_DEFAULT})
        assert sweep.status_code == 200
        at_base = [p for p in sweep.json()["points"] if p["value"] == _NON_DEFAULT["queue"]]
        assert len(at_base) == 1
        point = at_base[0]

        assert point["overall"]["total_pnl_cents"] == b["overall"]["total_pnl_cents"]
        assert point["overall"]["pairs"] == b["overall"]["pairs"]
        assert point["overall"]["windows"] == b["n_windows"]

        # And the old default-parameter base really did differ, so this test
        # has teeth: a sweep that ignored the page is detectably wrong.
        stale = client.get("/api/backtest/sweep", params={"axis": "queue", "file": "sweep_base.jsonl"})
        stale_point = [p for p in stale.json()["points"] if p["value"] == _NON_DEFAULT["queue"]][0]
        assert stale_point["overall"]["total_pnl_cents"] != point["overall"]["total_pnl_cents"]
    finally:
        pool.shutdown(wait=True)


def test_every_backtest_knob_reaches_the_sweep_base():
    """The sweep endpoint must accept the same parameter surface as the backtest.

    Guards against the original regression: each knob was added to the backtest
    endpoint only, and the sweep kept a stale private copy of the list.
    """
    import inspect

    backtest_params = set(inspect.signature(osc_dash.api_backtest).parameters)
    sweep_params = set(inspect.signature(osc_dash.api_backtest_sweep).parameters)
    missing = backtest_params - sweep_params
    assert missing == set(), (
        "sweep endpoint is missing knobs the backtest accepts: " f"{sorted(missing)}"
    )
    assert {"axis", "limit_windows"} <= sweep_params
    for venue in ("taker_fee_rate", "tick_size", "min_quote_shares", "gas"):
        assert venue not in sweep_params, f"{venue} is a pinned venue fact, not a control"


def test_sweep_axis_moves_only_its_own_parameter():
    """Each axis varies one field and holds the rest of the operator's config."""
    from dataclasses import asdict

    params, _echo = osc_dash._build_backtest_params(
        offset=0.03, queue=77, pair_cost=0.95, exit_default_5m=0.06,
        exit_default_15m=0.08, exit_btc_5m=0.09, exit_sol_5m=0.11,
        exit_reversal=0.03, size=9, quote_lo=0.20, quote_hi=0.80,
        entry_delay_sec=0.0, entry_delay_pct=4.0, dead_zone_val=0.1,
        dead_zone_pct=5.0, dead_zone_unit="pct",
        naked_leg_at_expiry="hold", enable_leg_chase=True,
    )
    expected = {
        "queue": {"queue_gate"},
        "offset": {"offset"},
        "exit_stop_default": {"exit_thresh_by_slug"},
        "exit_stop_btc": {"exit_thresh_by_slug"},
        "exit_stop_sol": {"exit_thresh_by_slug"},
        "exit_rev": {"exit_reversal"},
        "late_entry": {"entry_delay_pct"},
        "quote_range": {"quote_range"},
    }
    for axis, value in [("queue", 25.0), ("offset", 0.04),
                        ("exit_stop_default", 0.15), ("exit_stop_btc", 0.15),
                        ("exit_stop_sol", 0.15), ("exit_rev", 0.02),
                        ("late_entry", 10.0), ("quote_range", 0.15)]:
        variant, _label = osc_dash._sweep_params_for_value(params, axis, value)
        before, after = asdict(params), asdict(variant)
        changed = {k for k in before if before[k] != after[k]}
        assert changed == expected[axis], f"{axis} changed {sorted(changed)}"


def test_sweep_honours_market_and_duration_selection(tmp_path, monkeypatch):
    """The market/timeframe chips must narrow a sweep, as they narrow a backtest."""
    import concurrent.futures

    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    _sweep_fixture(tmp_path, ["btc-up-or-down-5m", "eth-up-or-down-5m",
                              "sol-up-or-down-5m", "xrp-up-or-down-5m",
                              "bnb-up-or-down-5m"])
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(osc_dash, "get_backtest_pool", lambda: pool)
    try:
        def run(**extra):
            r = client.get("/api/backtest/sweep", params={
                "axis": "queue", "file": "sweep_base.jsonl", **_NON_DEFAULT, **extra})
            assert r.status_code == 200
            return r.json()

        every = run()
        assert len(every["points"][0]["series_present"]) == 5

        two = run(series="btc,eth")
        assert two["points"][0]["series_present"] == ["btc-up-or-down-5m", "eth-up-or-down-5m"]
        assert two["n_windows"] < every["n_windows"]

        # A typo must fail loudly, never silently replay zero windows.
        assert client.get("/api/backtest/sweep", params={
            "axis": "queue", "file": "sweep_base.jsonl", "series": "bcc"}).status_code == 400
        assert client.get("/api/backtest/sweep", params={
            "axis": "queue", "file": "sweep_base.jsonl", "durations": "7"}).status_code == 400
    finally:
        pool.shutdown(wait=True)


def test_sweep_button_sends_every_control_to_both_endpoints():
    """The page must build one query for both runs, so they cannot disagree."""
    html = osc_dash.FULL_APP_HTML
    assert "function btControlValues(" in html
    assert "function btControlQuery(" in html
    for knob in ("pair_cost", "exit_btc_5m", "exit_sol_5m", "quote_lo", "quote_hi",
                 "entry_delay_pct", "dead_zone_pct", "naked_leg_at_expiry",
                 "enable_leg_chase", "max_start_delay", "series", "durations"):
        assert knob in html, f"{knob} never reaches the request"
    # Both endpoints consume the shared builder, not a private copy.        # Issue #331: the backtest reader consumes /api/backtest/stream; the
        # sweep now consumes its stream too (#344). Both via btControlQuery.
        assert "/api/backtest/stream?${btControlQuery(v)}" in html
        assert "/api/backtest/sweep/stream?axis=${encodeURIComponent(axis)}&${btControlQuery(v)}" in html
    # The sweep reader must not re-read controls behind the helper's back.
    assert "const offset = $('btOffset')" not in html
    assert "const queue = $('btQueue')" not in html
    # Issue #355: the chips have exactly one reader, shared by the request and
    # the sweep card's Markets grid. Two readers is how the grid came to disagree
    # with the run.
    assert "function btSelection(" in html
    assert "function btSelectedSeriesSlugs(" in html
    for fname in ("btControlQuery", "btSelectedSeriesSlugs"):
        body = html[html.index(f"function {fname}("):]
        body = body[:body.index("\n}")]
        assert "selectedBtTokens" not in body, f"{fname} reads the chips directly"
        assert "selectedBtDuration" not in body, f"{fname} reads the chips directly"
    bt_query = html[html.index("function btControlQuery("):]
    bt_query = bt_query[:bt_query.index("\n}")]
    assert "btSelection()" in bt_query


def test_sweep_selection_snapshot_reaches_every_render_path():
    """Issue #355: the run-start selection reaches the pending card, the progress
    views and the final render, and a live `rows_total: null` is worded as
    "replayed" rather than "row 0/0"."""
    html = osc_dash.FULL_APP_HTML
    # Snapshot taken once, at run start, next to `v`.
    runner = html[html.index("async function runSweepVisual("):]
    runner = runner[:runner.index("\nasync function") if "\nasync function" in runner[10:] else len(runner)]
    assert "const selectedSeries = btSelectedSeriesSlugs(sel);" in runner
    assert "selected_series: selectedSeries," in runner          # pending card
    assert "buildSweepProgressView(axis, v, ev, selectedSeries)" in runner
    assert "ev.result.selected_series = selectedSeries;" in runner  # final render
    # Progress view adapter carries it and preserves an unknown total.
    view = html[html.index("function buildSweepProgressView("):]
    view = view[:view.index("\n}")]
    assert "selected_series: selectedSeries," in view
    assert "ev.rows_total === null" in view
    # The idle card reads the live chips instead of a frozen snapshot.
    idle = html[html.index("function renderSweepIdle("):]
    idle = idle[:idle.index("\n}")]
    assert "selected_series: btSelectedSeriesSlugs()," in idle
    # Both card renderers route the grid through the shared builder.
    for fname in ("sweepCard", "sweepCardTail"):
        body = html[html.index(f"function {fname}("):]
        body = body[:body.index("\n}")]
        assert "sweepMarketsGridHtml(data.points, data.selected_series)" in body, fname
    # Chip handlers keep an idle card aligned without ever starting a run.
    # CodeRabbit round 1 (#356): the guard uses the explicit in-flight flag —
    # the abort controller only exists after the wait loop and was never
    # cleared on failure, so it both missed the race and stuck afterwards.
    assert "function refreshSweepIdleCard(" in html
    guard = html[html.index("function refreshSweepIdleCard("):]
    guard = guard[:guard.index("\n}")]
    assert "window._btSweepInFlight" in guard
    assert "idle" in guard
    # The flag is set at run start (next to the selection snapshot) and
    # cleared in the `finally` block, so idle refreshes resume after success
    # or failure.
    runner_head = runner[:runner.index("const v = btControlValues();")]
    assert "window._btSweepInFlight = true;" in runner_head
    fin = runner[runner.index("}finally{"):]
    fin = fin[:fin.index("\n  }")]
    assert "window._btSweepInFlight = false;" in fin
    for fname in ("toggleBtToken", "setBtTokensAll", "setBtDuration"):
        body = html[html.index(f"function {fname}("):]
        body = body[:body.index("\n}")]
        assert "refreshSweepIdleCard" in body, fname
        assert "runSweepVisual()" not in body, f"{fname} must not start a run"


def test_sweep_progress_text_three_states():
    """Issue #355: `rows_total` is three-valued — undefined (nothing reported),
    null (streaming, total unknown) and a number (converged)."""
    import shutil
    import subprocess

    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js not installed")

    html = client.get("/").text
    found = re.search(r"function sweepProgressText\(.*?\n\}", html, re.DOTALL)
    assert found is not None, "sweepProgressText is no longer a top-level function"
    test_js = found.group(0) + """
    const assert = (cond, msg) => { if (!cond) throw new Error(msg); };
    assert(sweepProgressText({rows_done: 7, rows_total: null}) === '7 windows replayed…',
           sweepProgressText({rows_done: 7, rows_total: null}));
    assert(sweepProgressText({rows_done: 0, rows_total: null}) === '0 windows replayed…');
    assert(sweepProgressText({rows_done: 0, rows_total: undefined}) === 'starting…');
    assert(sweepProgressText({rows_done: 40, rows_total: 40}) === 'row 40/40');
    // A total of zero is a real, finished, empty run — not "unknown".
    assert(sweepProgressText({rows_done: 0, rows_total: 0}) === 'row 0/0');
    console.log('SWEEP_PROGRESS_TEXT_TESTS_PASSED');
    process.exit(0);
    """
    res = subprocess.run([node_bin, "-e", test_js], capture_output=True, text=True)
    assert res.returncode == 0, f"Node script failed: {res.stderr}\n{res.stdout}"
    assert "SWEEP_PROGRESS_TEXT_TESTS_PASSED" in res.stdout


def test_sweep_markets_grid_has_three_states():
    """Issue #355: the Markets grid distinguishes participated / selected-with-no-
    windows / not-selected, and every chip says which it is."""
    import shutil
    import subprocess

    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js not installed")

    html = client.get("/").text
    parts = []
    for name in ("sweepMarketsGridHtml",):
        found = re.search(rf"function {name}\(.*?\n\}}", html, re.DOTALL)
        assert found is not None, f"{name} is no longer a top-level function"
        parts.append(found.group(0))
    parts.append("const BT_ALL_TOKENS = ['BTC', 'ETH', 'BNB', 'SOL', 'XRP'];")

    test_js = "\n".join(parts) + """
    const assert = (cond, msg) => { if (!cond) throw new Error(msg); };
    const tokens = ['BTC', 'ETH', 'BNB', 'SOL', 'XRP'];
    const ALL = tokens.flatMap(t => [`${t.toLowerCase()}-up-or-down-5m`,
                                    `${t.toLowerCase()}-up-or-down-15m`]);
    const pts = present => [{ label: 'q=0', value: 0, overall: {}, per_series: {},
                              series_present: present }];

    // 1. The reported bug: everything selected, nothing replayed yet. The grid
    //    must be all `pending` and contain no `off` chip at all.
    let html1 = sweepMarketsGridHtml(pts([]), ALL);
    assert(html1.includes('sweep-mkt pending'), html1);
    assert(!html1.includes('sweep-mkt off'), html1);
    assert(html1.includes('title="05m BTC — selected — no windows yet"'), html1);
    tokens.forEach(t => {
      assert(html1.includes(`>05m ${t}</span>`), html1);
      assert(html1.includes(`>15m ${t}</span>`), html1);
    });

    // 2. Partial selection: the unselected tokens go `off`, the selected stay
    //    `pending`. This is the case that used to render fully grey.
    const btcOnly = ['btc-up-or-down-5m', 'btc-up-or-down-15m'];
    let html2 = sweepMarketsGridHtml(pts([]), btcOnly);
    assert(html2.includes('sweep-mkt pending" title="05m BTC'), html2);
    assert(html2.includes('sweep-mkt off" title="05m ETH — not selected'), html2);
    assert(html2.includes('sweep-mkt off" title="15m XRP — not selected'), html2);

    // 3. A participant wins over `pending` and over `off` — a market that
    //    replayed is never described as absent, even if not selected.
    let html3 = sweepMarketsGridHtml(pts(['sol-up-or-down-5m']), btcOnly);
    assert(html3.includes('class="sweep-mkt" title="05m SOL — replayed in this sweep"'), html3);
    assert(!html3.includes('05m SOL — not selected'), html3);
    assert(!html3.includes('05m SOL — selected'), html3);
    assert(html3.includes('sweep-mkt pending" title="05m BTC'), html3);
    assert(html3.includes('sweep-mkt pending" title="15m BTC'), html3);
    assert(html3.includes('sweep-mkt off" title="15m ETH — not selected'), html3);

    // 4. Backward compatibility: no selection supplied = the two-state contract.
    let html4 = sweepMarketsGridHtml(pts(['eth-up-or-down-5m']), []);
    assert(html4.includes('>05m ETH<'), html4);
    assert(!html4.includes('sweep-mkt pending'), html4);
    assert(html4.includes('sweep-mkt off" title="15m BTC'), html4);
    // No points at all, no selection: the whole grid is `off` (today's idle
    // fallback when the caller supplies no selection).
    let html5 = sweepMarketsGridHtml([], []);
    assert(!html5.includes('sweep-mkt pending'), html5);
    tokens.forEach(t => {
      assert(html5.includes(`sweep-mkt off" title="05m ${t} —`), html5);
    });

    console.log('SWEEP_MARKETS_GRID_TESTS_PASSED');
    process.exit(0);
    """
    res = subprocess.run([node_bin, "-e", test_js], capture_output=True, text=True)
    assert res.returncode == 0, f"Node script failed: {res.stderr}\n{res.stdout}"
    assert "SWEEP_MARKETS_GRID_TESTS_PASSED" in res.stdout


def test_sweep_markets_grid_pending_style_uses_theme_tokens():
    """Issue #355: `pending` is its own style, built from theme variables only —
    no hardcoded colour (tests/test_theme_tokens.py is the repo's colour gate)."""
    html = osc_dash.FULL_APP_HTML
    assert "#btSweepMeta .sweep-mkt.pending{" in html
    pending_rule = html[html.index("#btSweepMeta .sweep-mkt.pending{"):]
    pending_rule = pending_rule[:pending_rule.index("}")]
    assert "var(--" in pending_rule
    # No hardcoded colour: strip the selector, then no '#' literal may remain.
    body = pending_rule.split("{", 1)[1]
    assert "#" not in body, body
    # It must read differently from both neighbours.
    assert "border-style:solid" in pending_rule
    off_rule = html[html.index("#btSweepMeta .sweep-mkt.off{"):]
    off_rule = off_rule[:off_rule.index("}")]
    assert off_rule != pending_rule


def test_bt_selected_series_slugs_follow_the_chips():
    """Issue #355: the selected (token, timeframe) pairs expand to exactly the
    slugs the sweep reports in `series_present`, so a chip toggle lights the
    matching grid cells and nothing else."""
    import re as _re
    html = osc_dash.FULL_APP_HTML
    parts = []
    for name in ("btSelection", "btSelectedSeriesSlugs"):
        found = _re.search(rf"function {name}\(.*?\n\}}", html, _re.DOTALL)
        assert found is not None, f"{name} is no longer a top-level function"
        parts.append(found.group(0))
    test_js = "\n".join(parts) + """
    const assert = (cond, msg) => { if (!cond) throw new Error(msg); };
    let selectedBtTokens = new Set(['BTC', 'ETH', 'BNB', 'SOL', 'XRP']);
    let selectedBtDuration = 'both';
    // All five tokens + both frames = the whole universe, ten slugs.
    assert(btSelectedSeriesSlugs().length === 10, btSelectedSeriesSlugs().join(','));
    assert(btSelectedSeriesSlugs().includes('btc-up-or-down-5m'));
    assert(btSelectedSeriesSlugs().includes('xrp-up-or-down-15m'));
    // One token, both frames = exactly its two slugs.
    selectedBtTokens = new Set(['SOL']);
    let slugs = btSelectedSeriesSlugs();
    assert(slugs.length === 2, slugs.join(','));
    assert(slugs.includes('sol-up-or-down-5m') && slugs.includes('sol-up-or-down-15m'), slugs.join(','));
    // One timeframe narrows to five slugs.
    selectedBtTokens = new Set(['BTC', 'ETH', 'BNB', 'SOL', 'XRP']);
    selectedBtDuration = '15m';
    slugs = btSelectedSeriesSlugs();
    assert(slugs.length === 5, slugs.join(','));
    assert(slugs.every(s => s.endsWith('-up-or-down-15m')), slugs.join(','));
    // The helper defaults to the live chips when no snapshot is passed.
    assert(btSelection().duration === '15m');
    assert(btSelection().tokens.length === 5);
    // An explicit snapshot wins over the live chips (run-start stability).
    const snap = { tokens: ['BTC'], duration: '5m' };
    assert(btSelectedSeriesSlugs(snap).join(',') === 'btc-up-or-down-5m');
    """
    import shutil
    import subprocess
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js not installed")
    proc = subprocess.run([node_bin, "-e", test_js], capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, proc.stderr


# ── Issue #335: every axis holds the operator's configuration ────────────────
# `test_sweep_base_point_equals_a_backtest_with_the_same_settings` above proves
# the base is shared for one axis and one point. These extend the guarantee to
# every axis and every value an operator can actually click, plus the one axis
# (`offset`) whose numbers come from a different simulation path.

# Which Backtester control each axis replaces. Each stop axis maps to the
# input(s) whose thresholds it overwrites, so the backtest built from them is
# the same parameter set the swept point writes: `exit_stop_default` takes the
# two default inputs (its per-market overrides stay at the submitted values —
# that is the distinction #338 exists to make), and the BTC/SOL axes replace
# exactly their one override input, which covers both durations.
_AXIS_CONTROLS = {
    "queue": ("queue",),
    "offset": ("offset",),
    "exit_stop_default": ("exit_default_5m", "exit_default_15m"),
    "exit_stop_btc": ("exit_btc_5m",),
    "exit_stop_sol": ("exit_sol_5m",),
    "exit_rev": ("exit_reversal",),
    "late_entry": ("entry_delay_pct",),
    "quote_range": ("quote_lo", "quote_hi"),
}


def _axis_point_query(axis: str, value: float) -> dict:
    """`_NON_DEFAULT` with every control of `axis` set to one point value.

    Sent verbatim to both endpoints, so the backtest and the sweep point are
    the same request by construction and any difference belongs to the
    endpoint, not to the test.

    The two per-market stop axes are the exception by design: one input covers
    both durations (`_build_backtest_params` writes `exit_btc_5m` into
    `btc-up-or-down-5m` *and* `btc-up-or-down-15m`), so setting that one input
    to the point value already builds the exact parameter set the axis writes.
    """
    query = dict(_NON_DEFAULT)
    if axis == "quote_range":
        query["quote_lo"] = value
        query["quote_hi"] = round(1.0 - value, 2)
    else:
        for control in _AXIS_CONTROLS[axis]:
            query[control] = value
    return query


def _sweep_point_at(data: dict, value: float) -> dict:
    """The single sweep point tested at `value`, or a failing assertion."""
    matches = [p for p in data["points"]
               if abs(float(p["value"]) - float(value)) < 1e-9]
    assert len(matches) == 1, f"expected one point at {value}, got {len(matches)}"
    return matches[0]


@pytest.mark.parametrize("axis", sorted(osc_dash.SWEEP_AXES))
def test_every_sweep_point_reproduces_a_backtest_at_the_same_settings(
        tmp_path, monkeypatch, axis):
    """Each axis point must equal a plain backtest whose axis control matches.

    The sweep is sold as "the backtest on this page, with one thing varied".
    That is only true if *every* point, not just the one that happens to sit at
    the operator's value, equals the backtest run with that axis value typed
    in. A knob the sweep drops shows up as a P&L difference on some point.
    """
    import concurrent.futures

    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    _sweep_fixture_15m(tmp_path, ["btc-up-or-down-5m", "eth-up-or-down-5m",
                                  "sol-up-or-down-5m", "xrp-up-or-down-5m",
                                  "bnb-up-or-down-5m"])
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(osc_dash, "get_backtest_pool", lambda: pool)
    try:
        sweep = client.get("/api/backtest/sweep", params={
            "axis": axis, "file": "sweep_base.jsonl",
            **_axis_point_query(axis, osc_dash.SWEEP_AXES[axis][0])})
        assert sweep.status_code == 200
        data = sweep.json()
        assert len(data["points"]) == len(osc_dash.SWEEP_AXES[axis])

        filled: list = []
        filled_15m: list = []
        for value in osc_dash.SWEEP_AXES[axis]:
            back = client.get("/api/backtest", params={
                "file": "sweep_base.jsonl", **_axis_point_query(axis, value)})
            assert back.status_code == 200
            b = back.json()
            assert b["n_windows"] > 0, f"{axis}={value} replayed no windows"
            # The fixture must contain the 15m slice for every run (its windows
            # are simulated), but a far point legitimately fills nothing — the
            # equality above still pins that zero to the backtest's zero.
            assert b["per_duration"]["900"]["windows"] > 0, \
                f"{axis}={value}: the 15m windows were not simulated"
            if b["per_duration"]["900"]["pairs"] > 0:
                filled_15m.append(value)

            point = _sweep_point_at(data, value)
            assert point["overall"]["total_pnl_cents"] == b["overall"]["total_pnl_cents"], \
                f"{axis}={value}: sweep and backtest disagree on P&L"
            assert point["overall"]["pairs"] == b["overall"]["pairs"], \
                f"{axis}={value}: sweep and backtest disagree on pairs"
            assert point["overall"]["windows"] == b["n_windows"], \
                f"{axis}={value}: sweep and backtest disagree on windows"
            if b["overall"]["pairs"] > 0:
                filled.append(value)

        # Non-vacuous, but per axis rather than per point: at the far end of the
        # offset axis the resting quote is legitimately never reached, so zero
        # fills there is a real result the two endpoints must still agree on.
        # If no point of an axis ever fills, the equalities above compare
        # nothing but zeros, and that is the case this guard exists to catch.
        assert filled, f"{axis} filled no pairs at any point; comparison is vacuous"
        # Non-vacuous for the 15m half too: at least one point of every axis
        # must trade a 15m window, or the `default_15m` stop the `exit_stop`
        # sweep replaces would never be read by either endpoint.
        assert filled_15m, (f"{axis}: no point traded a 15m window; the 15m "
                            "thresholds are untested")
    finally:
        pool.shutdown(wait=True)


def test_offset_sweep_matches_a_plain_backtest_without_the_queue_memo(
        tmp_path, monkeypatch):
    """The memo-off path must be an optimisation, not a different simulation.

    Only the `offset` axis runs with `queue_memo=None` (the `reuse` branch at
    `server/osc_dash.py:2012-2015`), because sweeping the offset moves the
    resting price the memo is keyed on. That makes it the one axis produced by a
    path the other three never exercise: if the memo ever stopped being
    equivalent to a cold computation, the offset chart would drift while the
    other three stayed correct — and nothing would notice.
    """
    import concurrent.futures

    import backtest.engine as bt_engine

    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    _sweep_fixture_15m(tmp_path, ["btc-up-or-down-5m", "eth-up-or-down-5m",
                                  "sol-up-or-down-5m", "xrp-up-or-down-5m",
                                  "bnb-up-or-down-5m"])
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(osc_dash, "get_backtest_pool", lambda: pool)

    seen: list = []
    real_simulate = bt_engine._simulate_window

    def spy(window_snaps, params, queue_memo=None):
        seen.append(queue_memo)
        return real_simulate(window_snaps, params, queue_memo=queue_memo)

    monkeypatch.setattr(bt_engine, "_simulate_window", spy)
    try:
        value = 0.025
        offset_query = _axis_point_query("offset", value)
        sweep = client.get("/api/backtest/sweep", params={
            "axis": "offset", "file": "sweep_base.jsonl", **offset_query})
        assert sweep.status_code == 200
        assert seen, "the offset sweep simulated nothing"
        assert all(m is None for m in seen), \
            "offset axis reused a queue memo; its resting price changes per point"

        point = _sweep_point_at(sweep.json(), value)
        seen.clear()

        back = client.get("/api/backtest", params={
            "file": "sweep_base.jsonl", **offset_query})
        assert back.status_code == 200
        b = back.json()
        assert b["overall"]["pairs"] > 0, "fixture did not fill; test would be vacuous"
        # The plain backtest passes no memo at all, so this is the cold path the
        # memo is supposed to be equivalent to.
        assert all(m is None for m in seen)

        assert point["overall"]["total_pnl_cents"] == b["overall"]["total_pnl_cents"]
        assert point["overall"]["pairs"] == b["overall"]["pairs"]
        assert point["overall"]["windows"] == b["n_windows"]
        assert b["per_duration"]["900"]["pairs"] > 0, \
            "the 15m windows did not trade; the memo-off parity is 5m-only"

        # The other half of the proof: the shared memo really is shared when it
        # is used. Without this, "all memo is None" would only prove the memo
        # feature had been switched off everywhere.
        seen.clear()
        queue = client.get("/api/backtest/sweep", params={
            "axis": "queue", "file": "sweep_base.jsonl", **_NON_DEFAULT})
        assert queue.status_code == 200
        assert any(isinstance(m, dict) for m in seen), \
            "queue axis did not use the shared queue memo"
    finally:
        pool.shutdown(wait=True)


def test_sweep_axis_select_offers_every_sweep_axis():
    """An axis added to SWEEP_AXES must be reachable from the page.

    Issue #344: the selector lives inside the sweep card's title (rendered by
    `sweepCard` from the AXIS_LABELS list), not as a static dropdown row. A new
    axis also needs its Backtester controls in `_AXIS_CONTROLS`, or the parity
    test would silently compare a sweep against a backtest of something else.
    """
    html = osc_dash.FULL_APP_HTML
    select_start = html.index('<select id="btSweepAxis"')
    select = html[select_start:html.index("</select>", select_start)]
    assert "axisOpts" in select  # options are generated from the axis list
    # The generator list must cover every axis SWEEP_AXES defines.
    gen_start = html.index("const axisOpts = ")
    gen = html[gen_start:html.index("]", gen_start)]
    for axis in osc_dash.SWEEP_AXES:
        assert f"'{axis}'" in gen, f"{axis} is not offered in the title selector"
    assert set(_AXIS_CONTROLS) == set(osc_dash.SWEEP_AXES), \
        "the parity tests and the sweep axes have drifted apart"


def test_sweep_override_note_is_wired_into_the_meta_block():
    """The submitted snapshot must reach the renderer that writes the note.

    The note is only honest if it is built from the values captured before the
    request, which is what `runSweepVisual` already holds in `v`. Re-reading the
    controls inside the renderer would let a mid-flight edit describe a
    configuration that never ran.
    """
    html = osc_dash.FULL_APP_HTML
    assert "function sweepOverrideNote(" in html
    # Issue #344: the final render call carries the isProgress flag; progress
    # events route through renderSweepVisual(view, v, true). Issue #355: the
    # run-start selection snapshot rides along on that call.
    assert "renderSweepVisual(ev.result, v);" in html
    assert "renderSweepVisual(buildSweepProgressView(axis, v, ev, selectedSeries), v, true);" in html
    assert "function renderSweepVisual(data, submitted, isProgress)" in html
    assert "function renderSweepVisual(" in html
    # The card owns the verdict now — it calls the note helper with the same
    # pre-request snapshot and the response's point values.
    assert "sweepOverrideNote(data.axis, v, (data.points || []).map(p => Number(p.value)))" in html
    assert "sweepCard(submitted, data, statsHtml)" in html
    assert "function sweepCard(" in html
    # The renderer must not go behind the snapshot's back and read the page.
    renderer = html[html.index("function renderSweepVisual("):]
    renderer = renderer[:renderer.index("\nfunction ")]
    assert "$('btOffset')" not in renderer
    assert "$('btQueue')" not in renderer
    # The display is the card alone: the grey stat sentence is gone, the
    # subject is the axis selector inside the card's title (#344),
    # best-overall/best-market fold into a stat row inside it.
    assert 'class="sweep-card"' in html
    assert '<select id="btSweepAxis"' in html
    assert 'sweep-stats' in html
    assert 'Best overall' in html
    assert 'Best market' in html
    assert 'Parameters held' in html
    assert 'Designed constraints / rules' in html
    assert 'sweep-cols' in html


def test_sweep_override_note_wording_node():
    """The note must name the swept field, one input per stop axis.

    The three stop axes each replace exactly the input their name names — the
    per-market split (issue #338) removed the global axis that forced the
    mixed/uniform special case, so every axis note is now the single-field
    shape and can always claim its bar when the operator's input is on it.
    """
    import shutil
    import subprocess

    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js not installed")

    html = client.get("/").text
    # Harness what the card needs: the pure note helper (verdict) plus the
    # card builder, so the tests assert on the exact HTML the meta block
    # receives.
    parts = []
    # Issue #355: sweepCard delegates the Markets grid to sweepMarketsGridHtml,
    # so both must be in the harness or the grid renders as `undefined`.
    for name in ("sweepOverrideNote", "sweepMarketsGridHtml", "sweepCard",
                 "formatSweepTickValue"):
        found = re.search(rf"function {name}\(.*?\n\}}", html, re.DOTALL)
        assert found is not None, f"{name} is no longer a top-level function"
        parts.append(found.group(0))
    parts.append("const BT_ALL_TOKENS = ['BTC', 'ETH', 'BNB', 'SOL', 'XRP'];")

    test_js = "\n".join(parts) + """
    const assert = (cond, msg) => { if (!cond) throw new Error(msg); };
    const v = {
      offset: 0.02, queue: 50, pairCost: 0.95,
      exit5m: 0.06, exit15m: 0.07, exitBtc: 0.08, exitSol: 0.09,
      exitReversal: 0.03, size: 5, maxStartDelay: 0,
      quoteLo: 0.20, quoteHi: 0.80, entryDelayPct: 4,
      deadZonePct: 12, nakedLegAtExpiry: 'hold', legChase: '1'
    };
    const data = {
      axis: 'queue',
      points: [{label:'0', value:0},{label:'10', value:10},{label:'25', value:25},
               {label:'50', value:50},{label:'100', value:100},{label:'200', value:200}]
               .map(p => ({ ...p, series_present: ['eth-up-or-down-5m'] })),
      series_order: ['eth-up-or-down-5m'],
      series_labels: {'eth-up-or-down-5m': '5m ETH'}
    };

    // Card skeleton: title above the card, then the three labeled columns.
    // Issue #344: the title IS the axis selector (the selected option carries
    // the subject label); no separate sweep-title-main span anymore.
    const card = sweepCard(v, data);
    assert(card.includes('sweep-card'), card);
    assert(card.includes('<select id="btSweepAxis"'), card);
    assert(card.includes('value="queue" selected'), card);
    assert(card.includes('testing 0, 10, 25, 50, 100, 200'), card);
    assert(card.includes('Markets'), card);
    assert(card.includes('sweep-mkt'), card);
    // The full universe is always shown: 05m row above the 15m row, one
    // column per token, participants solid and absentees grayed out.
    const tokens = ['BTC', 'ETH', 'BNB', 'SOL', 'XRP'];
    tokens.forEach(t => {
      assert(card.includes(`05m ${t}</span>`), card);
      assert(card.includes(`15m ${t}</span>`), card);
    });
    assert(card.includes('sweep-mkt-grid'), card);
    // eth-up-or-down-5m participated: its chip solid; every 15m absent: dashed.
    assert(card.includes('>05m ETH<'), card);
    assert(!card.includes('sweep-mkt off" title="05m ETH'), card);
    assert(card.includes('sweep-mkt off" title="15m BTC'), card);
    assert(card.includes('sweep-mkt off'), card);
    assert(card.includes('Parameters held'), card);
    assert(card.includes('Designed constraints / rules'), card);
    // Stats fold into the card when the renderer passes them.
    const withStats = sweepCard(v, data, '<span class="sweep-stats">S</span>');
    assert(withStats.includes('sweep-stats'), withStats);

    // Every held parameter shows the operator's submitted value.
    assert(card.includes('Spread Offset ($)'), card);
    assert(card.includes('0.020'), card);
    assert(card.includes('Queue Depth Filter'), card);
    assert(card.includes('>50<'), card);
    assert(card.includes('Late Entry (% window)'), card);
    assert(card.includes('4%'), card);
    assert(card.includes('Exit Stop 5m ($)'), card);
    assert(card.includes('0.06'), card);
    assert(card.includes('Exit Stop 15m ($)'), card);
    assert(card.includes('0.07'), card);
    assert(card.includes('Reversal Buffer ($)'), card);
    assert(card.includes('0.030'), card);
    assert(card.includes('Leg Chase'), card);
    assert(card.includes('Enabled'), card);

    // Constraints read from the same snapshot.
    assert(card.includes('Quotable Range ($)'), card);
    assert(card.includes('[0.20, 0.80]'), card);
    assert(card.includes('Dead Zone (% window)'), card);
    assert(card.includes('12%'), card);
    assert(card.includes('Naked Leg at Expiry'), card);
    assert(card.includes('Hold'), card);

    // The subject row is marked, exactly one per axis.
    assert(card.includes('← subject'), card);
    assert(card.includes('sweep-row subject'), card);
    assert(card.split('← subject').length - 1 === 1, card);

    // The verdict survives in the card: on-axis value claims the bar (gold).
    assert(card.includes('that bar is your setting'), card);
    assert(card.includes('sweep-verdict yours'), card);

    // Off-axis value: honest "no bar" verdict in the warning color.
    const offCard = sweepCard({ ...v, queue: 77 }, data);
    assert(offCard.includes('>77<'), offCard);
    assert(offCard.includes('no bar equals it'), offCard);
    assert(offCard.includes('sweep-verdict none'), offCard);

    // Stop-axis subjects: the note names the input it replaces — each axis
    // is honest about exactly one thing, full stop. The card renders the
    // verdict; the head/items live on the note helper itself.
    const points6 = [0.06, 0.08, 0.10, 0.12, 0.14, 0.16];
    const defNote = sweepOverrideNote('exit_stop_default', v, points6);
    assert(defNote.head === 'sweeps the default 5m + 15m stop — replaces the two submitted default stops', defNote.head);
    assert(defNote.submittedLabel === 'submitted', defNote.head);
    assert(defNote.items.length === 2, defNote.head);
    assert(defNote.verdict.cls === 'none', defNote.head);
    assert(defNote.verdict.text === 'no bar equals your values', defNote.head);

    const v6 = { ...v, exit5m: 0.10, exit15m: 0.10, exitBtc: 0.10, exitSol: 0.10 };
    const defYours = sweepOverrideNote('exit_stop_default', v6, points6);
    assert(defYours.verdict.cls === 'yours', defYours.head);
    assert(defYours.verdict.text === 'the bar at 0.1 is your setting', defYours.head);

    // Both defaults on the axis but different from each other: no single bar
    // carries both submitted values, so the note must refuse the claim —
    // even though an unrelated BTC/SOL override sits off the axis.
    const mixed = sweepOverrideNote('exit_stop_default',
                                    { ...v, exit5m: 0.06, exit15m: 0.08 }, points6);
    assert(mixed.verdict.cls === 'none', mixed.head);
    assert(mixed.verdict.text === 'no bar equals your values', mixed.head);

    const btcNote = sweepOverrideNote('exit_stop_btc', v, points6);
    assert(btcNote.head === 'sweeps the BTC stop — replaces the submitted BTC 5m Stop Loss', btcNote.head);
    assert(btcNote.items.length === 0, btcNote.head);
    // v.exitBtc is 0.08, an axis point — the note must claim it, even though
    // the operator's other stop inputs differ. That is the point of #338.
    assert(btcNote.verdict.cls === 'yours', btcNote.head);
    assert(btcNote.verdict.text === 'that bar is your setting', btcNote.head);

    const btcOff = sweepOverrideNote('exit_stop_btc', { ...v, exitBtc: 0.07 }, points6);
    assert(btcOff.verdict.cls === 'none', btcOff.head);
    assert(btcOff.verdict.text === 'no bar equals it', btcOff.head);

    const solNote = sweepOverrideNote('exit_stop_sol', v, points6);
    assert(solNote.head === 'sweeps the SOL stop — replaces the submitted SOL 5m Stop Loss', solNote.head);
    assert(solNote.verdict.text === 'no bar equals it', solNote.head);

    // Card level: the axis selector IS the title (#344) — the selected option
    // carries the subject label; subject markers, verdict text.
    const stopCard = sweepCard(v, { ...data, axis: 'exit_stop_default' });
    assert(stopCard.includes('value="exit_stop_default" selected'), stopCard);
    assert(stopCard.split('← subject').length - 1 === 2, stopCard);
    assert(stopCard.includes('no bar equals your values'), stopCard);

    // BTC/SOL sweeps mark their own submitted stop as the subject — not the
    // Exit Stop 5m row the sweep holds unchanged.
    const btcCard = sweepCard(v, { ...data, axis: 'exit_stop_btc' });
    assert(btcCard.includes('value="exit_stop_btc" selected'), btcCard);
    assert(btcCard.includes('>BTC 5m Stop ($) <span class="sweep-tag">← subject</span>'), btcCard);
    assert(btcCard.includes('>0.08<'), btcCard);
    assert(btcCard.split('← subject').length - 1 === 1, btcCard);
    assert(!btcCard.includes('Exit Stop 5m ($) <span class="sweep-tag"'), btcCard);
    assert(btcCard.includes('no bar equals it'), btcCard);

    const solCard = sweepCard(v, { ...data, axis: 'exit_stop_sol' });
    assert(solCard.includes('value="exit_stop_sol" selected'), solCard);
    assert(solCard.includes('>SOL 5m Stop ($) <span class="sweep-tag">← subject</span>'), solCard);
    assert(solCard.includes('>0.09<'), solCard);
    assert(solCard.split('← subject').length - 1 === 1, solCard);

    // late_entry and quote_range subjects and formatters
    const lateCard = sweepCard(v, { ...data, axis: 'late_entry' });
    assert(lateCard.includes('value="late_entry" selected'), lateCard);
    assert(lateCard.includes('>Late Entry (% window) <span class="sweep-tag">← subject</span>'), lateCard);
    assert(lateCard.split('← subject').length - 1 === 1, lateCard);

    const qrCard = sweepCard(v, { ...data, axis: 'quote_range' });
    assert(qrCard.includes('value="quote_range" selected'), qrCard);
    assert(qrCard.includes('>Quotable Range ($) <span class="sweep-tag">← subject</span>'), qrCard);
    assert(qrCard.split('← subject').length - 1 === 1, qrCard);

    const qrPoints = [0.00, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30];
    const qrNote = sweepOverrideNote('quote_range', v, qrPoints);
    assert(qrNote.head.includes('Quotable Range'), qrNote.head);
    assert(qrNote.verdict.cls === 'yours', qrNote.head);

    assert(formatSweepTickValue('late_entry', 15) === '15%');
    assert(formatSweepTickValue('quote_range', 0.1) === '[0.10, 0.90]');

    // Unknown axis: no claim, card still renders the held knobs.
    const unk = sweepCard(v, { ...data, axis: 'not-an-axis' });
    assert(!unk.includes('← subject'), unk);
    assert(unk.includes('Parameters held'), unk);

    // Markets stay permanent even when the response has no points: with no
    // selection supplied the whole grid falls back to the two-state contract,
    // every chip grayed out, layout unchanged (issue #355).
    const allMkts = sweepCard(v, { ...data, points: [] });
    tokens.forEach(t => {
      assert(allMkts.includes(`sweep-mkt off" title="05m ${t} —`), allMkts);
      assert(allMkts.includes(`sweep-mkt off" title="15m ${t} —`), allMkts);
    });

    console.log('SWEEP_OVERRIDE_NOTE_TESTS_PASSED');
    process.exit(0);
    """

    res = subprocess.run(
        [node_bin, "-e", test_js],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    assert res.returncode == 0, f"Node script failed: {res.stderr}\n{res.stdout}"
    assert "SWEEP_OVERRIDE_NOTE_TESTS_PASSED" in res.stdout


def test_sweep_stream_progress_before_final_and_parity(tmp_path, monkeypatch):
    """Issue #344: the streamed sweep emits progress per settled row, its final
    event equals the blocking sweep's payload, and running per-point totals
    converge on the final points."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    _make_backtest_ticks_file(tmp_path)
    mock_pool = _install_stream_test_harness(monkeypatch, tmp_path)

    url = "/api/backtest/sweep/stream?axis=queue&file=fake_stream.jsonl&offset=0.02"
    with client.stream("GET", url) as res:
        assert res.status_code == 200
        body = "".join(chunk for chunk in res.iter_text())
    events = _parse_sse_events(body)
    types = [e["type"] for e in events]
    assert types.count("final") == 1
    assert "progress" in types
    final = events[-1]
    assert final["type"] == "final"

    blocking = client.get(
        "/api/backtest/sweep?axis=queue&file=fake_stream.jsonl&offset=0.02").json()
    assert json.dumps(final["result"], sort_keys=True) == json.dumps(blocking, sort_keys=True)

    # Running totals converge: the last progress snapshot per point matches
    # the final point's overall totals.
    last_progress = next(e for e in reversed(events) if e["type"] == "progress")
    assert len(last_progress["points"]) == len(blocking["points"])
    for lp, fp in zip(last_progress["points"], blocking["points"]):
        assert lp["value"] == fp["value"]
        assert lp["overall"]["windows"] == fp["overall"]["windows"]
        assert lp["overall"]["total_pnl_cents"] == fp["overall"]["total_pnl_cents"]

    mock_pool.shutdown(wait=True)


def test_sweep_stream_validation_and_busy(tmp_path, monkeypatch):
    """Issue #344: unknown axis 400s, and the single-run guard 429s."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    _make_backtest_ticks_file(tmp_path)
    mock_pool = _install_stream_test_harness(monkeypatch, tmp_path)

    bad = client.get("/api/backtest/sweep/stream?axis=not-an-axis")
    assert bad.status_code == 400
    assert "unknown axis" in bad.json()["error"]

    import threading
    started = threading.Event()
    release = threading.Event()

    def blocking_worker(*args, **kwargs):
        started.set()
        release.wait(timeout=5.0)
        return {"axis": "queue", "points": [], "series_order": [],
                "series_labels": {}, "best_overall": None, "best_market": None,
                "n_snaps": 0, "n_windows": 0}

    monkeypatch.setattr(osc_dash, "_run_sweep_worker", blocking_worker)
    t = threading.Thread(target=lambda: client.get("/api/backtest/sweep?axis=queue&file=fake_stream.jsonl"))
    t.start()
    assert started.wait(timeout=3.0)
    res = client.get("/api/backtest/sweep/stream?axis=queue&file=fake_stream.jsonl")
    assert res.status_code == 429
    release.set()
    t.join(timeout=5.0)
    mock_pool.shutdown(wait=True)


def test_sweep_frontend_contract(tmp_path):
    """Issue #344: the SPA consumes the sweep stream, writes the card on run
    click, and the axis selector lives in the card title — no dropdown row."""
    html = osc_dash.FULL_APP_HTML
    assert "/api/backtest/sweep/stream?axis=" in html
    assert "buildSweepProgressView" in html
    assert "sweepCardTail" in html
    # The immediate card on run click (submitted values before results return).
    assert "meta.innerHTML = sweepCard(v, {" in html
    # The axis selector now renders inside the card title; changing it aborts
    # any in-flight sweep stream before starting the new axis (review #345).
    assert 'onchange="onSweepAxisChange()"' in html
    assert "function onSweepAxisChange()" in html


def test_sweep_worker_progress_points_converge(tmp_path, monkeypatch):
    """Issue #344: the sweep worker's progress queue carries per-point running
    totals whose final snapshot equals the returned points. Issue #355: the
    FIRST message arrives from inside the read loop (unknown total, non-empty
    `series_present`) and the last one has converged."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    _make_backtest_ticks_file(tmp_path)
    q = _fake_queue_factory()
    from dataclasses import asdict
    from backtest import BacktestParams
    result = osc_dash._run_sweep_worker(
        str(tmp_path), str(tmp_path / "fake_stream.jsonl"),
        asdict(BacktestParams(offset=0.02)), "queue", 5, 0.0, 0, "", "",
        progress_queue=q,
    )
    messages = []
    while True:
        try:
            messages.append(q.get_nowait())
        except Exception:
            break
    assert messages, "no sweep progress emitted"
    # Issue #355: the first event is a live preview from the read loop — the
    # total is not known yet, and it already names the markets it replayed. That
    # is what stops the card sitting all-grey for the whole run.
    first = messages[0]
    assert first["rows_total"] is None, first
    assert first["rows_done"] >= 1
    assert first["points"][0]["series_present"], first
    last = messages[-1]
    assert last["rows_done"] == last["rows_total"]
    assert len(last["points"]) == len(result["points"])
    for lp, fp in zip(last["points"], result["points"]):
        assert lp["overall"] == fp["overall"]
        assert lp["per_series"] == fp["per_series"]
        assert lp["series_present"] == fp["series_present"]


def test_sweep_worker_progress_emission_is_rate_limited(tmp_path, monkeypatch):
    """Issue #355: progress emission is gated on wall-clock, so the event count
    is bounded by the run duration and not by the window count. A huge interval
    leaves exactly the first-window event plus the converged one."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    _make_backtest_ticks_file(tmp_path, windows=8)
    monkeypatch.setattr(osc_dash, "SWEEP_PROGRESS_MIN_INTERVAL_SEC", 10_000.0)
    q = _fake_queue_factory()
    from dataclasses import asdict
    from backtest import BacktestParams
    result = osc_dash._run_sweep_worker(
        str(tmp_path), str(tmp_path / "fake_stream.jsonl"),
        asdict(BacktestParams(offset=0.02)), "queue", 5, 0.0, 0, "", "",
        progress_queue=q,
    )
    messages = []
    while True:
        try:
            messages.append(q.get_nowait())
        except Exception:
            break
    # 8 windows produced 1 live event + 1 converged event, not 8+1.
    assert len(messages) == 2, [m["rows_done"] for m in messages]
    assert messages[0]["rows_total"] is None
    assert messages[-1]["rows_done"] == result["n_windows"]


def test_sweep_worker_progress_failure_does_not_change_result(tmp_path, monkeypatch):
    """Issue #355: a broken progress queue disables emission only; the returned
    result is byte-identical to a run with no queue at all."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    _make_backtest_ticks_file(tmp_path)

    class ExplodingQueue:
        def put_nowait(self, msg):
            raise RuntimeError("queue broken")

    from dataclasses import asdict
    from backtest import BacktestParams
    params_dict = asdict(BacktestParams(offset=0.02))
    with_queue = osc_dash._run_sweep_worker(
        str(tmp_path), str(tmp_path / "fake_stream.jsonl"),
        params_dict, "queue", 5, 0.0, 0, "", "", progress_queue=ExplodingQueue(),
    )
    without = osc_dash._run_sweep_worker(
        str(tmp_path), str(tmp_path / "fake_stream.jsonl"),
        params_dict, "queue", 5, 0.0, 0, "", "",
    )
    assert json.dumps(with_queue, sort_keys=True) == json.dumps(without, sort_keys=True)


def test_api_backtest_concurrency_capping_429(tmp_path, monkeypatch):
    """Verify /api/backtest rejects concurrent simulation runs with HTTP 429 when already in flight (Issue #259)."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    fake_file = tmp_path / "fake_round.jsonl"
    fake_file.write_text('{"cid": "0x1", "slug": "btc-updown-5m", "ts": 1000.0, "start_ts": 1000.0, "mid": 0.50, "bids": [], "asks": []}\n', encoding="utf-8")

    import concurrent.futures
    import threading

    mock_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(osc_dash, "get_backtest_pool", lambda: mock_pool)

    started_event = threading.Event()
    release_event = threading.Event()

    def blocking_worker(*args, **kwargs):
        started_event.set()
        release_event.wait(timeout=5.0)
        return {
            "params_hash": "test",
            "params": {},
            "params_groups": {},
            "overall": {
                "windows": 0,
                "entered_windows": 0,
                "pairs": 0,
                "pair_rate": 0.0,
                "exits": 0,
                "exit_rate": 0.0,
                "total_pnl_cents": 0.0,
                "avg_pnl_cents": 0.0,
                "max_drawdown_cents": 0.0,
                "win_rate": 0.0,
            },
            "per_series": {},
            "equity_curve": [],
            "trades_sample": [],
            "pnl_histogram": dict(osc_dash.EMPTY_PNL_HISTOGRAM),
        }

    monkeypatch.setattr(osc_dash, "_run_backtest_simulation_worker", blocking_worker)

    responses = {}

    def run_first():
        responses["first"] = client.get(f"/api/backtest?file={fake_file.name}")

    t1 = threading.Thread(target=run_first)
    t1.start()

    # Wait until the first request acquires the lock and enters the worker
    assert started_event.wait(timeout=3.0), "First backtest request did not start in time"

    # Concurrently issue a second request while first is in flight
    res_second = client.get(f"/api/backtest?file={fake_file.name}")
    assert res_second.status_code == 429
    data_second = res_second.json()
    assert "error" in data_second
    assert "already in progress" in data_second["error"].lower()

    # Release worker and wait for first request to complete
    release_event.set()
    t1.join(timeout=5.0)
    mock_pool.shutdown(wait=True)
    assert responses["first"].status_code == 200

    # Verify that concurrency guards are fully released
    assert not osc_dash._BACKTEST_RUNNING


def test_backtest_stream_frontend_contract():
    """Issue #331: the SPA reads the stream, draws a provisional curve, and
    renders the final result through the extracted renderBacktestResult."""
    html = osc_dash.FULL_APP_HTML
    assert "/api/backtest/stream?${btControlQuery(v)}" in html
    assert "getReader()" in html
    assert "Cumulative PnL ($) — provisional" in html
    assert "function renderBacktestResult(" in html
    assert "consumeBacktestStream" in html
    # The old blocking fetch must not drive the backtest run anymore.
    assert "/api/backtest?${btControlQuery(v)}" not in html


def test_sweep_stream_reader_honors_sweep_controller():
    """Dead Run Sweep Visual: the shared SSE reader must not cancel a sweep
    run by comparing its controller against the regular backtest guard only."""
    html = osc_dash.FULL_APP_HTML
    start = html.index("async function consumeBacktestStream(")
    body = html[start:start + 2600]  # guard moved past the read await (#371 watchdog)
    # Full guard line: polarity (&&, not ||) and both disjuncts matter — a
    # substring check on the tokens alone would pass an inverted guard.
    assert "if (ctl.signal.aborted || (window._btAbort !== ctl && window._btSweepAbort !== ctl))" in body


def test_worker_progress_message_carries_card_counters_and_hist_sample(tmp_path, monkeypatch):
    """IIIB feedback: progress messages carry pairs/exits/wins/max-drawdown and
    a provisional pnl sample, so every dashboard visualization can react."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    fake_file = _make_backtest_ticks_file(tmp_path)
    q = _fake_queue_factory()
    from dataclasses import asdict
    from backtest import BacktestParams
    params_dict = asdict(BacktestParams(offset=0.02))
    result = osc_dash._run_backtest_simulation_worker(
        str(tmp_path), str(fake_file), params_dict, 5, 0.0, 0, {}, {},
        "", "", progress_queue=q, progress_batch_windows=2,
    )
    messages = []
    while True:
        try:
            messages.append(q.get_nowait())
        except Exception:
            break
    assert messages
    last = messages[-1]
    for key in ("pairs", "exits", "wins", "max_drawdown_cents", "pnl_sample_cents"):
        assert key in last, f"progress message missing {key}"
    sample = last["pnl_sample_cents"]
    assert len(sample) == len(result["equity_curve"])
    # Same per-window values the final histogram is computed from.
    final_pnls = [e["pnl_cents"] for e in result["equity_curve"]]
    assert sorted(sample) == sorted(final_pnls)
    # Counter semantics match the final overall block.
    ov = result["overall"]
    assert last["pairs"] == ov["pairs"]
    assert last["exits"] == ov["exits"]
    assert last["wins"] == ov["wins"]
    assert last["max_drawdown_cents"] == ov["max_drawdown_cents"]


def test_backtest_stream_html_has_provisional_cards_and_histogram():
    """IIIB feedback: the provisional phase updates metric cards and a
    provisional histogram chart, not just the equity curve."""
    html = osc_dash.FULL_APP_HTML
    assert "btBeginProvisionalHist" in html
    assert "btUpdateProvisionalHist" in html
    assert "pnl_sample_cents" in html
    assert "Windows — provisional" in html
    # The provisional card updates write the same metric fields the final
    # render writes, so a mid-run glance reads the real numbers.
    for sel in ("btTotalPnl", "btPairRate", "btExitRate", "btMaxDd", "btWinRate"):
        assert sel in html


def test_shutdown_backtest_pool():
    """Verify shutdown_backtest_pool cleanly terminates pool and resets singleton (Issue #259)."""
    pool = osc_dash.get_backtest_pool()
    assert pool is not None
    osc_dash.shutdown_backtest_pool()
    assert osc_dash._BACKTEST_POOL is None


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

    res = client.get(f"/api/backtest?file=fake_prices.jsonl")
    assert res.status_code == 200
    data = res.json()
    assert len(data["trades_sample"]) == 1
    trade = data["trades_sample"][0]
    assert "entry_up" in trade
    assert "entry_down" in trade
    assert "exit_price" in trade
    assert "exit_side" in trade
    assert trade["entry_up"] == 0.48
    assert "both_filled" in trade
    assert trade["both_filled"] is True
    assert trade["exit_reason"] == "pair_merged"
    assert "is_dead_zone" in trade
    assert "settle_source" in trade

    ov = data["overall"]
    assert "pair_rate" in ov
    assert "win_rate" in ov
    assert "profitable_windows" in ov
    assert "profitable_pairs" in ov
    assert "profitable_exits" in ov
    assert "pairs_above_settle" in ov


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
    assert "btCardPairCost" in html
    assert "Peak to trough from $0.00 start" in html
    assert "Exit Stop Rate ℹ️" in html
    assert "Win Rate ℹ️" in html
    assert "Elapsed Time ℹ️" in html
    assert "btElapsedTime" in html
    assert "renderBacktestTradesPage" in html
    assert "bt-master-tbl" in html
    assert "for profit" not in html


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


def test_api_live_config_dead_zone_knobs():
    """Issue #229: the dead-zone trio is configurable via /api/live/config.

    `dead_zone_val` accepts a pct fraction (0.10) or absolute seconds under the
    matching unit; `naked_leg_at_expiry` is the close/hold switch that replaced
    `stop_loss_enabled`. All three appear in /api/live/state params, and
    out-of-domain values are rejected with 422 rather than clamped.
    """
    engine = osc_dash.get_live_trader_engine()
    engine.is_running = False
    orig_val = engine.dead_zone_val
    orig_unit = engine.dead_zone_unit
    orig_expiry = engine.naked_leg_at_expiry
    # get_live_trader_engine() is a module-global singleton. Pin it to paper for the
    # duration so update_config cannot reach fetch_polymarket_account_value and make
    # a real Polymarket request if an earlier test left the singleton in live mode.
    orig_mode = engine.mode
    engine.mode = "paper"
    try:
        # Pct fraction pass-through (the 10% default spelled explicitly).
        res = client.post("/api/live/config", json={"dead_zone_val": 0.10})
        assert res.status_code == 200
        assert abs(res.json()["params"]["dead_zone_val"] - 0.10) < 1e-9

        # Unit switch to seconds carries an absolute value.
        res_sec = client.post("/api/live/config", json={
            "dead_zone_val": 30.0, "dead_zone_unit": "sec"})
        assert res_sec.status_code == 200
        params = res_sec.json()["params"]
        assert abs(params["dead_zone_val"] - 30.0) < 1e-9
        assert params["dead_zone_unit"] == "sec"

        # The expiry policy switch.
        res_hold = client.post("/api/live/config", json={"naked_leg_at_expiry": "hold"})
        assert res_hold.status_code == 200
        assert res_hold.json()["params"]["naked_leg_at_expiry"] == "hold"

        # Out of domain: pct above 1.0 passes the payload's union bound (sec
        # allows absolute seconds) but is rejected by the engine's unit-aware
        # validation and surfaced as 400; an unknown policy is a 422 at the
        # payload model. Neither is silently clamped.
        res_bad = client.post(
            "/api/live/config", json={"dead_zone_val": 1.5, "dead_zone_unit": "pct"})
        assert res_bad.status_code == 400
        assert "dead_zone_val" in res_bad.json()["error"]
        assert client.post(
            "/api/live/config", json={"naked_leg_at_expiry": "keep"}).status_code == 422
    finally:
        engine.update_config(
            dead_zone_val=orig_val,
            dead_zone_unit=orig_unit,
            naked_leg_at_expiry=orig_expiry,
        )
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


def test_api_live_config_ws_book_authority():
    """Issue #353: the socket-isolation switch is reachable from the dashboard.

    Round-trips through /api/live/config into engine state; a non-coercible
    value is rejected by the payload model with 422.
    """
    engine = osc_dash.get_live_trader_engine()
    orig_running = engine.is_running
    engine.is_running = False
    orig_auth = engine.ws_book_authority
    orig_mode = engine.mode
    engine.mode = "paper"
    try:
        state = client.get("/api/live/state").json()
        assert state["params"]["ws_book_authority"] is engine.ws_book_authority

        res = client.post("/api/live/config", json={"ws_book_authority": True})
        assert res.status_code == 200
        assert res.json()["params"]["ws_book_authority"] is True
        assert engine.ws_book_authority is True

        res_off = client.post("/api/live/config", json={"ws_book_authority": False})
        assert res_off.status_code == 200
        assert engine.ws_book_authority is False

        assert client.post("/api/live/config", json={"ws_book_authority": [1]}).status_code == 422
        assert engine.ws_book_authority is False
    finally:
        engine.update_config(ws_book_authority=orig_auth)
        engine.mode = orig_mode
        engine.is_running = orig_running


def test_api_live_config_exit_thresh_naked_deleted():
    """Issue #230: exit_thresh_naked is deleted across both engines and API.

    Posting `exit_thresh_naked` is not accepted as an active param, and the
    field no longer appears in state params.
    """
    engine = osc_dash.get_live_trader_engine()
    assert not hasattr(engine, "exit_thresh_naked")
    state = client.get("/api/live/state").json()
    assert "exit_thresh_naked" not in state["params"]
    from server.osc_dash import LiveConfigPayload
    assert "exit_thresh_naked" not in LiveConfigPayload.model_fields


def test_api_live_config_quote_range():
    """Issue #228: the Cockpit sets the quotable range; inverted pairs get 400."""
    engine = osc_dash.get_live_trader_engine()
    orig_running = engine.is_running
    engine.is_running = False
    orig_range = tuple(engine.quote_range)
    orig_mode = engine.mode
    engine.mode = "paper"
    try:
        res = client.post("/api/live/config", json={"quote_range": [0.20, 0.80]})
        assert res.status_code == 200
        assert res.json()["params"]["quote_range"] == [0.20, 0.80]
        assert engine.quote_range == (0.20, 0.80)

        # Each end clamps to the price domain.
        res = client.post("/api/live/config", json={"quote_range": [-1.0, 99.0]})
        assert res.status_code == 200
        assert res.json()["params"]["quote_range"] == [0.0, 1.0]

        # Inverted is refused, not silently reordered.
        assert client.post("/api/live/config", json={"quote_range": [0.80, 0.20]}).status_code == 400
    finally:
        engine.update_config(quote_range=list(orig_range))
        engine.mode = orig_mode
        engine.is_running = orig_running


def test_api_live_config_patient_band_preset_deleted():
    """Issue #228: patient_band_maker preset was deleted with the band gate;
    requesting it returns 400.
    """
    engine = osc_dash.get_live_trader_engine()
    orig_running = engine.is_running
    engine.is_running = False
    orig_mode = engine.mode
    engine.mode = "paper"
    orig_delay = engine.entry_delay_sec
    orig_range = engine.quote_range
    orig_expiry = engine.naked_leg_at_expiry
    try:
        res = client.post("/api/live/config", json={"preset": "patient_band_maker"})
        assert res.status_code == 400
        assert "Unknown preset 'patient_band_maker'" in (res.json().get("error") or res.json().get("detail", ""))

        # Individual knobs stay settable without a preset.
        res_knobs = client.post("/api/live/config", json={
            "entry_delay_sec": 30,
            "quote_range": [0.20, 0.80],
            "naked_leg_at_expiry": "hold",
        })
        assert res_knobs.status_code == 200
        params = res_knobs.json()["params"]
        assert abs(params["entry_delay_sec"] - 30.0) < 1e-9
        assert params["quote_range"] == [0.20, 0.80]
        assert params["naked_leg_at_expiry"] == "hold"

        # Unknown preset rejected, config untouched.
        res_bad = client.post("/api/live/config", json={"preset": "nope"})
        assert res_bad.status_code == 400
        # Absurd delay rejected at the boundary (would silently never quote).
        assert client.post(
            "/api/live/config", json={"entry_delay_sec": 99999}).status_code == 422
    finally:
        engine.entry_delay_sec = orig_delay
        engine.quote_range = orig_range
        engine.naked_leg_at_expiry = orig_expiry
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


def test_api_backtest_entry_delay_range_passthrough(tmp_path, monkeypatch):
    """Delay=60 holds quotes past the only tape prints; echo carries knobs."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    _write_delay_fixture(tmp_path)
    base = client.get("/api/backtest?file=fake_delay.jsonl&offset=0.02").json()
    assert base["overall"]["pairs"] == 1
    assert base["params"]["quote_lo"] == 0.10
    assert base["params"]["quote_hi"] == 0.90
    delayed = client.get("/api/backtest?file=fake_delay.jsonl&offset=0.02"
                         "&entry_delay_sec=60&quote_lo=0.20&quote_hi=0.80").json()
    assert delayed["overall"]["pairs"] == 0
    assert delayed["params"]["entry_delay_sec"] == 60.0
    assert delayed["params"]["quote_lo"] == 0.20
    assert delayed["params"]["quote_hi"] == 0.80


def test_api_backtest_quote_range_clamps(tmp_path, monkeypatch):
    """Each range end clamps to the price domain; an inverted pair falls back."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    _write_delay_fixture(tmp_path)
    d = client.get("/api/backtest?file=fake_delay.jsonl&quote_lo=-1&quote_hi=99").json()
    assert d["params"]["quote_lo"] == 0.0
    assert d["params"]["quote_hi"] == 1.0
    inv = client.get("/api/backtest?file=fake_delay.jsonl&quote_lo=0.80&quote_hi=0.20").json()
    assert inv["params"]["quote_lo"] == 0.10
    assert inv["params"]["quote_hi"] == 0.90


def test_api_backtest_quote_range_nan_falls_back(tmp_path, monkeypatch):
    """Non-finite ends fall back to the default range, never to a boundary."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    _write_delay_fixture(tmp_path)
    d = client.get("/api/backtest?file=fake_delay.jsonl&quote_lo=nan&quote_hi=inf").json()
    assert d["params"]["quote_lo"] == 0.10
    assert d["params"]["quote_hi"] == 0.90


def test_api_backtest_ignores_retired_gate_keys(tmp_path, monkeypatch):
    """Old bookmarks carrying the deleted gates run the default range (issue #226 precedent)."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    _write_delay_fixture(tmp_path)
    d = client.get("/api/backtest?file=fake_delay.jsonl&entry_band=0.04"
                   "&reentry_drift_band=0&min_requote_remaining_sec=0").json()
    assert d["params"]["quote_lo"] == 0.10
    assert d["params"]["quote_hi"] == 0.90


def test_backtest_file_dropdown_label_is_dynamic():
    """Dropdown default label must use the live aggregate, not a hardcoded count."""
    html = client.get("/").text
    assert "2,820 Windows (Default)" not in html
    assert "All Files / ${defWinVal} Windows (Default)" in html


def test_backtest_delay_range_ui_elements():
    """Dashboard exposes delay plus the two quotable-range inputs."""
    html = client.get("/").text
    assert 'id="btEntryDelay"' in html
    assert 'id="btQuoteLo"' in html
    assert 'id="btQuoteHi"' in html
    assert 'id="btEntryBand"' not in html
    assert "applyWinningConfig" not in html


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
    # These registry entries are compatibility fields or are rendered as
    # multiple controls rather than one data-param element.
    missing = [m for m in missing if m not in {"exit_thresh_by_slug", "dead_zone_unit", "dead_zone_val", "entry_delay_sec"}]

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


def test_the_quotable_range_is_settable_on_both_tabs():
    """Issue #228: each tab renders the two range inputs bound to one knob."""
    html = client.get("/").text
    assert 'id="cockpitQuoteLo"' in html
    assert 'id="cockpitQuoteHi"' in html
    assert 'id="btQuoteLo"' in html
    assert 'id="btQuoteHi"' in html
    assert 'id="cockpitEntryBand"' not in html
    assert 'id="cockpitStopLossEnabled"' not in html, (
        "Issue #229: the stop-loss toggle is gone — naked_leg_at_expiry governs "
        "the unpaired leg now")


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


# ===========================================================================
# Issue #233: structural limits render apart from tuning knobs
# ===========================================================================

STRUCTURAL_FIELDS = ("max_pair_cost", "quote_range", "dead_zone_val",
                     "dead_zone_unit", "naked_leg_at_expiry")


def test_params_spec_serves_param_class_for_every_knob():
    body = client.get("/api/params/spec").json()
    for group, entries in body["groups"].items():
        for name, v in entries.items():
            assert v["param_class"] in ("tuning", "structural", "assumption"), (
                f"{group}.{name} has no valid param_class: {v.get('param_class')!r}")
    assert body["groups"]["trading_knobs"]["max_pair_cost"]["param_class"] == "structural"
    assert body["groups"]["trading_knobs"]["offset"]["param_class"] == "tuning"


def test_backtest_tab_renders_a_dedicated_structural_limits_section():
    """The Backtest tab separates risk limits into their own card/section."""
    html = client.get("/").text
    assert "btSecStructural" in html, "no dedicated Risk Limits section on the Backtest tab"
    assert "Risk Limits" in html
    # No explainer paragraphs: headers alone carry the meaning.
    assert "bt-section-desc" not in html


def test_backtest_structural_section_contains_all_structural_controls():
    """Every structural control's markup sits inside the structural section."""
    html = client.get("/").text
    sec_start = html.index("btSecStructural")
    sec_end = html.index("id=\"btSecGeometry\"")
    section = html[sec_start:sec_end]
    for frag in ('data-param="max_pair_cost"', 'id="btQuoteLo"', 'id="btQuoteHi"',                     'data-param="dead_zone_pct"',
                     'data-param="naked_leg_at_expiry"'):

        assert frag in section, f"structural control {frag!r} not inside btSecStructural"
    # And the tuning controls stayed behind in the operator section.
    op_start = html.index("btSecOperatorBody")
    operator = html[op_start:sec_start]
    assert 'data-param="offset"' in operator
    assert 'data-param="max_pair_cost"' not in operator


def test_backtest_operator_section_is_labelled_quote_placement():
    """The operator section says what it is: quote placement, not all controls."""
    html = client.get("/").text
    assert "Quote Placement" in html


def test_backtest_execution_section_shows_only_tick_size():
    """The Fill Model section is gone entirely; so is the Tick Size control.

    Taker fee rate, min quote shares, merge gas and tick size are all venue
    facts, not operator choices, so none of them renders on any tab. Tick size
    is pinned at the venue's own increment of 0.001 and is no longer even a
    query parameter on `/api/backtest` — widening it fabricates fills, so it
    must not be reachable from the UI or the URL bar.
    """
    html = client.get("/").text
    assert "Fill Model" not in html
    assert "Tick Size" not in html
    for gone in ('id="btTakerFee"', 'id="btMinShares"', 'id="btGas"',
                 'id="btTickSize"', 'data-param="tick_size"',
                 "btSecExecutionBody"):
        assert gone not in html, f"{gone} is a venue constant and should not render"


def test_api_backtest_ignores_a_venue_constant_even_if_sent():
    """No venue constant is settable by hand-editing the URL.

    `/api/backtest` does not declare these parameters, so FastAPI drops them and
    the engine keeps its own value. This is the guard against someone pasting
    `&tick_size=0.01` into the address bar and reading the inflated pair rate as
    a real result, or `&gas=5` and quietly deleting the merge economics.
    """
    import inspect
    from server.osc_dash import api_backtest
    names = set(inspect.signature(api_backtest).parameters)
    for dropped in ("taker_fee_rate", "tick_size", "min_quote_shares", "gas"):
        assert dropped not in names, f"{dropped} is still settable via the API"


def test_backtest_geometry_lives_inside_parameters():
    """Geometry preview is the last group inside Backtest Setup & Run."""
    html = client.get("/").text
    params = html.index("btSecParameters")
    overall = html.index("btSecOverall")
    segment = html[params:overall]
    for frag in ("btSecScope", "btSecOperator", "btSecStructural", "btSecGeometry"):
        assert frag in segment, f"{frag} not inside Backtest Setup"
    assert "Strategy Geometry Preview" in segment


def test_backtest_scope_holds_universe_controls():
    """Dataset, markets, timeframe, and window filter live in Backtest Scope."""
    html = client.get("/").text
    scope_start = html.index("btSecScope")
    scope_end = html.index("btSecOperator")
    scope = html[scope_start:scope_end]
    for frag in ('id="btFileSelect"', 'id="btTokenChips"', 'id="btDurBoth"',
                  'id="btMaxStartDelay"'):
        assert frag in scope, f"scope control {frag!r} not inside btSecScope"
    # And they left Quote Placement: only quote-level dials remain there.
    op_start = html.index("btSecOperatorBody")
    op_end = html.index("btSecStructural")
    operator = html[op_start:op_end]
    for frag in ('id="btFileSelect"', 'id="btTokenChips"', 'id="btDurBoth"',
                  'id="btMaxStartDelay"'):
        assert frag not in operator, f"scope control {frag!r} still in Quote Placement"
    assert 'data-param="offset"' in operator


def test_backtest_run_buttons_sit_at_setup_top_level():
    """Run Sweep / Reset live directly under Setup, not inside Geometry."""
    html = client.get("/").text
    params = html.index('id="btSecParametersBody"')
    accordion = html.index('class="bt-accordion"')
    geo = html.index('id="btSecGeometry"')
    for frag in ('id="btnRunSweep"', 'id="btnResetParams"', 'id="btLastRunTime"'):
        pos = html.index(frag)
        assert params < pos < accordion, f"{frag} not at setup top level"
        assert pos < geo, f"{frag} still inside Geometry"


def test_issue_270_backtest_peer_sections_and_accessible_chart_dialog():
    """Issue #270: peer sections and a view-only accessible chart dialog exist."""
    html = client.get("/").text
    sections = {
        "btSecParametersBody": "Backtest Setup",
        "btSecGeometryBody": "Strategy Geometry Preview",
        "btSecOverallBody": "Overall Execution Results",
        "btSecSweepBody": "Sweep Visual",
        "btSecSeriesBody": "Per-Series Performance",
        "btSecLogBody": "Executed Windows Log",
    }
    for body_id, heading in sections.items():
        assert f'aria-controls="{body_id}"' in html
        assert heading in html
    assert html.count("<span>📝 Executed Windows Log</span>") == 1
    assert html.count("<span>🔬 Sweep Visual</span>") == 1
    assert html.count('class="bt-section-head"') >= 9  # six peers + parameter groups
    assert 'id="btChartDialog"' in html
    assert 'role="dialog"' in html
    assert 'aria-labelledby="btChartDialogTitle"' in html
    assert 'id="btChartDialogClose"' in html
    assert "function openBtChartDetail" in html
    assert "function closeBtChartDetail" in html
    assert "event.key === 'Escape'" in html
    assert "window._btRunning = false" in html
    assert "window._btSweepVisualData" in html


def test_issue_270_backtest_labels_are_duration_first_and_filter_values_stay_slugs():
    """Issue #270: result rendering canonicalizes labels without changing slug values."""
    html = client.get("/").text
    for label in (
        "05m BTC", "05m ETH", "05m BNB", "05m SOL", "05m XRP",
        "15m BTC", "15m ETH", "15m BNB", "15m SOL", "15m XRP",
    ):
        assert label in html or "canonicalMarketName" in html
    assert "function canonicalMarketName" in html
    assert "seriesLabels.set(t.series" in html
    assert "const currentVal = $('btLogSeriesFilter').value;" in html
    assert "opts += `<option value=\"${esc(slug)}\"" in html


def test_cockpit_renders_a_structural_limits_grouping_with_badges():
    """The Cockpit demarcates structural limits with a badged sub-group."""
    html = client.get("/").text
    assert 'id="cockpitStructuralGroup"' in html
    assert "Structural" in html   # badged header text
    grp_start = html.index("cockpitStructuralGroup")
    grp_end = html.index('id="cockpitWallet"')
    group = html[grp_start:grp_end]
    for frag in ('id="cockpitQuoteLo"', 'id="cockpitQuoteHi"',
                 'data-param="max_pair_cost"', 'data-param="dead_zone_val"',
                 'data-param="naked_leg_at_expiry"'):
        assert frag in group, f"structural control {frag!r} not inside cockpitStructuralGroup"
    # Structural controls carry a badge marker class.
    assert "param-structural" in html


def test_js_helper_badges_structural_inputs_via_the_registry():
    """applyParamSpec tags structural controls from the served param_class."""
    html = client.get("/").text
    assert "param_class" in html, "page never reads param_class from the spec"
    assert "classList.add('param-structural')" in html


def test_backtest_sends_the_new_knobs():
    html = client.get("/").text
    for q in ("exit_reversal=", "dead_zone_pct=", "entry_delay_pct=",
              "naked_leg_at_expiry=",
              "enable_leg_chase=", "quote_lo=", "quote_hi="):
        assert q in html, f"the Backtest run URL never sends {q}"
    for stale in ("entry_timeout_pct=", "naked_leg_timeout_pct=",
                  "stop_loss_enabled=", "max_start_elapsed_pct=",
                  "exit_thresh_naked="):
        assert stale not in html, f"the Backtest run URL still sends deleted knob {stale}"


@pytest.mark.parametrize("field,over,clamped", [
    ("entry_delay_sec", 999999.0, 3600.0),
    ("dead_zone_val", 9999.0, 3600.0),
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


@pytest.mark.parametrize("sent,clamped", [
    (1.05, 1.00),   # the pre-#227 default, still arriving from old bookmarks
    (2.0, 1.00),
    (0.0, 0.50),    # the pre-#227 "off" sentinel
    (0.49, 0.50),
    (0.99, 0.99),
])
def test_the_pair_cost_query_alias_clamps_to_the_engine_range(sent, clamped,
                                                              tmp_path, monkeypatch):
    """`pair_cost` is the query spelling of `max_pair_cost` (issue #227).

    The alias is the reason this needs its own case: the parametrized clamp
    test above sends the registry's own field name, and `pair_cost` is the one
    knob whose query key and field name differ — so it would sail past that
    test untouched. A cap above 1.00 authorises paying more for a pair than a
    pair can return, and 0.0 used to mean "gate off", so both must land inside
    [0.50, 1.00] rather than error or pass through.

    `TICKS_DIR` is redirected for the same reason as the test above: this call
    carries no `file=`.
    """
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    r = client.get("/api/backtest", params={"pair_cost": sent})
    assert r.status_code == 200, f"pair_cost={sent} produced {r.status_code}"
    assert r.json()["params"]["pair_cost"] == pytest.approx(clamped)


def test_clamp_falls_back_to_the_default_on_non_finite_input():
    """NaN compares False against every bound, so min/max would pass it through."""
    from server.osc_dash import _clamp_to_spec
    assert _clamp_to_spec("entry_delay_sec", float("inf")) == 0.0
    # Unregistered names pass through untouched — the retired band gate is no
    # longer clamped anywhere, so a stale caller gets its own value back.
    assert _clamp_to_spec("entry_band", 0.04) == 0.04


def test_live_config_accepts_the_quotable_range():
    """Issue #228: `quote_range` is declared on the payload and reaches the engine."""
    from server.osc_dash import LiveConfigPayload
    assert "quote_range" in LiveConfigPayload.model_fields
    p = LiveConfigPayload(quote_range=[0.20, 0.80])
    assert p.quote_range == [0.20, 0.80]
    assert "min_requote_remaining_sec" not in LiveConfigPayload.model_fields
    assert "reentry_drift_band" not in LiveConfigPayload.model_fields
    assert "entry_band" not in LiveConfigPayload.model_fields


# Registry field -> the `LiveConfigPayload` field carrying the same knob.
# Names differ where live and research chose different words for one thing.
REGISTRY_TO_PAYLOAD = {
    "offset": "offset",
    "quote_shares": "shares",
    "max_pair_cost": "max_pair_cost",
    "exit_reversal": "exit_reversal",
    "dead_zone_val": "dead_zone_val",
    "entry_delay_sec": "entry_delay_sec",
    "quote_range": "quote_range",
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
    `max_pair_cost` and `exit_thresh_naked` — and a reviewer found them, not
    this suite. Derive the list from the registry so it cannot go stale.
    """
    from backtest.engine import BacktestParams
    cockpit = {
        name
        for group in BacktestParams.param_spec().values()
        for name, v in group.items()
        if "cockpit" in v["surfaces"]
    }
    # `exit_thresh_by_slug` is a dict fanned out per series, and the remaining
    # unbounded knobs have no numeric range this check could compare.
    for no_range in ("exit_thresh_by_slug", "enable_leg_chase",
                     "dead_zone_unit", "naked_leg_at_expiry"):
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
    posted |= {"enable_leg_chase"}
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
    for name in ("offset", "entry_delay_sec", "quote_range",
                 "dead_zone_val", "dead_zone_unit",
                 "naked_leg_at_expiry"):
        assert name in LiveConfigPayload.model_fields, f"{name} not declared"
        assert name in accepted, f"update_config does not accept {name}"
        assert name in forwarded, (
            f"{name} is declared on the payload but never passed to "
            "update_config — the request succeeds and changes nothing")
    # Issues #228/#229/#230: the retired gates must not be declared anymore —
    # pydantic's `extra="ignore"` would turn a Cockpit post into a silent no-op.
    for name in ("entry_band", "reentry_drift_band",
                 "min_requote_remaining_sec", "reentry_min_remaining_pct",
                 "max_reentries_per_window", "reentry_require_pairable",
                 "entry_timeout_pct", "naked_leg_timeout_pct", "stop_loss_enabled",
                 "exit_thresh_naked"):
        assert name not in LiveConfigPayload.model_fields, (
            f"{name} is still declared after its mechanism was deleted")


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
    s = BacktestParams.spec_for("quote_range")
    s["label"] = "POISONED"
    s["surfaces"] = ()
    again = BacktestParams.spec_for("quote_range")
    assert again["label"] == "Quotable Range (mid lo/hi)"
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
    the shared bounds. No knob declares a per-surface override today (issue
    #227 unified the one that did), so nothing is misrendered right now — this
    fails the moment a control is misnamed and a future override appears.
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


# --- Issue #201, carried through #229: backtest stop-loss thresholds stay one
# --- grid group; the on/off switch itself is gone (naked_leg_at_expiry, #229).

def test_stop_loss_thresholds_live_inside_one_grid_group():
    """All four thresholds must sit inside the wrapper that keeps them together."""
    html = client.get("/").text
    start = html.index('<div id="btStopLossFields">')
    end = html.index('data-param-label="quote_shares"', start)
    group = html[start:end]
    for el_id in ("btExit5m", "btExit15m", "btExitBtc", "btExitSol"):
        assert f'id="{el_id}"' in group, (
            f"{el_id} is outside btStopLossFields, so the group reads as four "
            "unrelated inputs")


def test_stop_loss_group_survives_the_form_grid():
    """The wrapper must not collapse the four fields into one grid cell.

    `.form-grid` is `repeat(4,1fr)`, so a plain wrapper becomes a single item.
    `display:contents` keeps them as direct grid children, and the `[hidden]`
    override is mandatory: the id selector outranks the UA `[hidden]` rule.
    """
    html = client.get("/").text
    assert "#btStopLossFields{display:contents}" in html
    assert "#btStopLossFields[hidden]{display:none}" in html
    assert "cockpitStopLossFields" not in html, (
        "Issue #229: the Cockpit mirror of the group went with the toggle")


def test_both_tabs_render_the_dead_zone_and_expiry_switches():
    """Issue #229: close/hold and pct/sec are selects on both tabs."""
    html = client.get("/").text
    for sel_id in ("btNakedLegAtExpiry", "cockpitNakedLegAtExpiry"):
        start = html.index(f'id="{sel_id}"')
        block = html[start:start + 300]
        assert 'data-param="naked_leg_at_expiry"' in block
        assert '<option value="close"' in block
        assert '<option value="hold"' in block
    start = html.index('id="cockpitDeadZoneUnit"')
    block = html[start:start + 300]
    assert 'data-param="dead_zone_unit"' in block
    assert '<option value="pct"' in block
    assert '<option value="sec"' in block


def test_the_deleted_knobs_have_no_inputs_left_on_either_tab():
    """Issue #229: a control for a deleted knob is a form that posts a no-op."""
    html = client.get("/").text
    for stale in ("entry_timeout_pct", "naked_leg_timeout_pct",
                  "max_start_elapsed_pct", "stop_loss_enabled",
                  "btStopLossEnabled", "cockpitStopLossEnabled"):
        assert stale not in html, f"deleted knob {stale} is still in the page"


def test_reset_restores_the_stop_loss_group_defaults():
    """resetBtParams must restore the four thresholds without a toggle left."""
    html = client.get("/").text
    fn_start = html.index("function resetBtParams()")
    fn = html[fn_start:html.index("\n}", fn_start)]
    assert "toggleStopLossInputs" not in fn, (
        "Issue #229: the toggle function is deleted, nothing may call it")
    assert "$('btQuoteLo').value = \"0.10\";" in fn
    assert "$('btQuoteHi').value = \"0.90\";" in fn


def test_cockpit_payload_sends_the_dead_zone_trio():
    """The Cockpit posts dead_zone_val/unit and naked_leg_at_expiry explicitly."""
    html = client.get("/").text
    assert "dead_zone_val" in html
    assert "dead_zone_unit" in html
    assert "naked_leg_at_expiry" in html
    assert "body.stop_loss_enabled" not in html


def test_cockpit_dead_zone_inputs_are_locked_while_the_bot_runs():
    """applyCockpitConfig() returns early when running, so the new inputs must lock.

    Left interactive they would show values the engine never received.
    """
    html = client.get("/").text
    fn_start = html.index("function updateCockpitParamsLockUI(locked)")
    ids = html[fn_start:html.index("];", fn_start)]
    for el_id in ("cockpitDeadZoneVal", "cockpitDeadZoneUnit", "cockpitNakedLegAtExpiry"):
        assert f"'{el_id}'" in ids


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


def test_loadmanifest_preselects_preferred_file():
    """Issue #279: first load pre-selects the ★-marked preferred file; a stored
    manual choice (including All Files) survives later loadManifest() calls."""
    import shutil
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not available")
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
    // Property (not const) so the harness can swap the payload per scenario.
    globalThis.fetch = () => Promise.resolve({ ok: true, json: async () => ({}) });
    const EventSource = class { constructor() {} addEventListener() {} close() {} };
    const WebSocket = class { constructor() {} addEventListener() {} send() {} close() {} };
    const makeElem = () => ({
      style: {}, textContent: '', innerHTML: '', appendChild: () => {},
      classList: { add: () => {}, remove: () => {}, toggle: () => {} },
      addEventListener: () => {}, querySelectorAll: () => [], value: ''
    });
    const makeSel = (initialValue) => ({
      value: initialValue,
      innerHTML: '',
      options: [],
      appendChild(opt) { this.options.push(opt); },
    });
    let sel;
    const window = { selectedBacktestFile: '', _btFileChosen: false, addEventListener: () => {}, location: { search: '' } };
    globalThis.window = window;
    const document = { getElementById: (id) => id === 'btFileSelect' ? sel : makeElem(), createElement: () => ({ textContent: '', value: '' }), querySelectorAll: () => [] };
    globalThis.document = document;
    const localStorage = { _data: {}, getItem(k) { return this._data[k] || null; }, setItem(k, v) { this._data[k] = String(v); } };
    globalThis.localStorage = localStorage;
    """

    test_js = """
    (async () => {
    // Build a dropdown state from a manifest payload, then report the outcome.
    globalThis.__runLoad = async (initialValue, stored, chosen, payload) => {
      sel = makeSel(initialValue);
      window.selectedBacktestFile = stored;
      window._btFileChosen = chosen;
      const files = payload.files.map(f => ({
        name: f[0], lines: 10, lines_estimated: false,
        is_preferred: f[1],
        // Windows, not lines, drive the label (see loadManifest). The default
        // here is a verified EXPLORATORY day: clean, 1,470 full windows, not
        // research-ready (a single day can never hold 3+ time blocks).
        window_quality: f[2] === undefined ? {
          full_windows: 1470, clean_windows: 1470, research_windows: null,
          readiness_level: 'EXPLORATORY', status: 'PASS',
        } : f[2],
      }));
      await loadManifest.__withPayload({ files, preferred_file: payload.preferred });
      return { selValue: sel.value, stored: window.selectedBacktestFile,
               labels: sel.options.map(o => o.textContent) };
    };

    // 1. First load with a preferred file: it is ★-marked and pre-selected.
    const first = await __runLoad('', '', false, {
      preferred: 'ticks_b.jsonl',
      files: [['ticks_a.jsonl', false], ['ticks_b.jsonl', true]],
    });
    if (first.labels.length !== 3) throw new Error('dropdown not built; options=' + JSON.stringify(first.labels));
    if (first.selValue !== 'ticks_b.jsonl') throw new Error('expected preferred pre-select, got ' + first.selValue);
    if (first.stored !== 'ticks_b.jsonl') throw new Error('window.selectedBacktestFile must mirror the pre-select, got ' + first.stored);
    if (first.labels[2] !== '★ ticks_b.jsonl (1,470 exploratory windows)') throw new Error('expected star-marked label, got ' + first.labels[2]);
    if (first.labels[0].startsWith('★') || first.labels[1].startsWith('★')) throw new Error('non-preferred options must not carry the star');

    // 1b. The label reports windows, never lines. A line count is a property of
    // how the collector wrote the file and tells an operator nothing about how
    // much research the file supports.
    if (/lines/.test(first.labels.join(' '))) throw new Error('labels must not report line counts, got ' + JSON.stringify(first.labels));

    // 1c. The readiness tier the file actually earned is stated, so two files
    // are comparable at a glance. A research-ready file says so; an
    // insufficient one says so rather than implying a usable dataset.
    const researchReady = await __runLoad('', '', false, {
      preferred: 'ticks_r.jsonl',
      files: [['ticks_r.jsonl', true, {
        full_windows: 820, clean_windows: 820, research_windows: 820,
        readiness_level: 'RESEARCH_READY', status: 'PASS',
      }]],
    });
    if (researchReady.labels[1] !== '★ ticks_r.jsonl (820 research windows)') throw new Error('research-ready label wrong, got ' + researchReady.labels[1]);

    const insufficient = await __runLoad('', '', false, {
      preferred: 'ticks_i.jsonl',
      files: [['ticks_i.jsonl', true, {
        full_windows: 10, clean_windows: null, research_windows: null,
        readiness_level: 'INSUFFICIENT', status: 'WARN',
      }]],
    });
    if (insufficient.labels[1] !== '★ ticks_i.jsonl (10 insufficient windows)') throw new Error('insufficient label wrong, got ' + insufficient.labels[1]);

    // 1d. No verify report means we do not know — never print a misleading zero.
    const unverified = await __runLoad('', '', false, {
      preferred: 'ticks_u.jsonl',
      files: [['ticks_u.jsonl', true, null]],
    });
    if (unverified.labels[1] !== '★ ticks_u.jsonl (windows unknown — not verified)') throw new Error('unverified label wrong, got ' + unverified.labels[1]);

    // 2. Stored selection (manual file pick) survives a manifest refresh.
    const kept = await __runLoad('ticks_a.jsonl', 'ticks_a.jsonl', true, {
      preferred: 'ticks_b.jsonl',
      files: [['ticks_a.jsonl', false], ['ticks_b.jsonl', true]],
    });
    if (kept.selValue !== 'ticks_a.jsonl') throw new Error('manual pick must survive refresh, got ' + kept.selValue);

    // 3. Manual All Files pick (empty value, empty stored) must not be
    //    overridden by the preferred pre-select — _btFileChosen tells it apart
    //    from a genuine first load.
    const allFiles = await __runLoad('', '', true, {
      preferred: 'ticks_b.jsonl',
      files: [['ticks_a.jsonl', false], ['ticks_b.jsonl', true]],
    });
    if (allFiles.selValue !== '') throw new Error('manual All Files pick must survive, got ' + allFiles.selValue);
    if (allFiles.stored !== '') throw new Error('stored must stay empty after a manual All Files pick');

    // 4. No preferred file: default stays All Files.
    const none = await __runLoad('', '', false, {
      preferred: null,
      files: [['ticks_a.jsonl', false]],
    });
    if (none.selValue !== '') throw new Error('no winner must keep All Files default, got ' + none.selValue);
    if (none.stored !== '') throw new Error('stored must stay empty when nothing qualifies');
    if (none.labels.some(l => l.startsWith('★'))) throw new Error('no star without a preferred file');

    console.log('LOADMANIFEST_PRESELECT_TESTS_PASSED');
    })().catch(e => { console.error(e); process.exit(1); });
    """

    # Make the fetch-driven loadManifest() run against a canned payload.
    harness = """
    // One-shot payload override: the next fetch consumes it once, every other
    // fetch (the app's own init loadManifest calls) gets the standing payload.
    const __standing = { files: null, preferred_file: null };
    let __next;
    loadManifest.__withPayload = (p) => {
      __next = p;
      return loadManifest();
    };
    globalThis.fetch = () => {
      const p = __next !== undefined ? __next : __standing;
      __next = undefined;
      return Promise.resolve({ ok: true, json: async () => p });
    };
    """

    res = subprocess.run(
        [node_bin],
        input=dom_prelude + "\n" + harness + "\n" + script + "\n" + test_js,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    assert res.returncode == 0, f"Node script failed: {res.stderr}\n{res.stdout}"
    assert "LOADMANIFEST_PRESELECT_TESTS_PASSED" in res.stdout


def test_api_backtest_pnl_histogram_invariant_and_edge_cases():
    """Issue #136: Verify pnl_histogram computation, edge cases, and bucket count invariant."""
    from types import SimpleNamespace
    from server.osc_dash import _compute_pnl_histogram, EMPTY_PNL_HISTOGRAM

    # 1. Empty windows list returns well-formed empty structure
    empty_res = _compute_pnl_histogram([], size=10)
    assert empty_res == EMPTY_PNL_HISTOGRAM
    assert empty_res["buckets"] == []
    assert empty_res["n"] == 0

    # 2. Degenerate zero-variance case (all windows zero or identical)
    flat_windows = [SimpleNamespace(pnl_cents=0.0) for _ in range(10)]
    flat_res = _compute_pnl_histogram(flat_windows, size=5)
    assert flat_res["n"] == 10
    assert flat_res["mean_cents"] == 0.0
    assert flat_res["median_cents"] == 0.0
    assert len(flat_res["buckets"]) == 1
    assert flat_res["buckets"][0]["count"] == 10
    assert sum(b["count"] for b in flat_res["buckets"]) == 10

    # 3. Multiple windows with variance: invariant sum(count) == n and 0.0 edge alignment
    mixed_windows = [
        SimpleNamespace(pnl_cents=-0.05),
        SimpleNamespace(pnl_cents=-0.02),
        SimpleNamespace(pnl_cents=0.0),
        SimpleNamespace(pnl_cents=0.03),
        SimpleNamespace(pnl_cents=0.08),
        SimpleNamespace(pnl_cents=0.15),
    ]
    mixed_res = _compute_pnl_histogram(mixed_windows, size=100)
    assert mixed_res["n"] == 6
    assert sum(b["count"] for b in mixed_res["buckets"]) == 6
    assert mixed_res["bucket_width_cents"] > 0
    # Edges should be aligned so 0.0 is an exact boundary
    zero_edges = [b["lo"] for b in mixed_res["buckets"]] + [mixed_res["buckets"][-1]["hi"]]
    assert any(abs(edge) < 1e-6 for edge in zero_edges)


def test_backtest_pnl_histogram_in_html():
    """Issue #136: Dashboard backtest tab includes histogram canvas, stats header, and JS render logic."""
    res = client.get("/")
    assert res.status_code == 200
    html = res.text
    assert 'id="chartPnlHist"' in html
    assert 'id="btPnlHistStats"' in html
    assert 'id="btPnlHistWarning"' in html
    assert "Per-Window P&amp;L Distribution (Histogram)" in html
    assert "destroyChartInstance('chartPnlHist')" in html
    assert "pnlHistChartInstance = new Chart" in html


def test_backtest_param_preview_grid_in_html():
    """Issue #198: Dashboard backtest tab includes 2D parameter preview grid container, SVG, and reactive updater."""
    res = client.get("/")
    assert res.status_code == 200
    html = res.text

    # Container & SVG elements
    assert 'id="btParamPreviewWrap"' in html
    assert 'id="btParamPreviewSvg"' in html
    # The pills strip and the "2D price" sub-label were removed; the chart
    # stands alone.
    assert 'id="btPreviewMetricsPills"' not in html
    assert "2D price" not in html
    assert "Strategy Geometry Preview" in html
    # Single visible instance: the collapsible section header is the only
    # heading; the inner duplicate title and the legend were removed.
    assert html.count("<span>📐 Strategy Geometry Preview</span>") == 1
    assert 'id="btParamPreviewLegend"' not in html
    assert "Quotable corridor" not in html
    assert "bt-preview-legend-item" not in html

    # CSS styles and responsive SVG contract
    assert "#btParamPreviewWrap" in html
    assert 'viewBox="0 0 900 420"' in html
    assert 'preserveAspectRatio="xMidYMid meet"' in html
    assert "function layoutBacktestPreviewLabels" in html
    assert "minGap = 25" in html

    # JavaScript rendering & reactive bindings
    assert "function updateBacktestParamPreview()" in html
    assert "function setupBacktestInputListeners()" in html
    assert "readFinite" in html
    assert "updateBacktestParamPreview();" in html
    assert "setupBacktestInputListeners();" in html


def test_backtest_param_preview_zero_handling_node():
    """Verify updateBacktestParamPreview correctly preserves valid zero values in Node.js."""
    import shutil
    import subprocess

    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js not installed")

    html = client.get("/").text
    start = html.find("<script>")
    end = html.rfind("</script>")
    assert start != -1 and end != -1
    script = html[start + len("<script>"):end]

    dom_prelude = """
    let networkCalls = 0;
    let trackNetwork = false;
    const elements = {};
    const makeElem = (id) => {
      if (!elements[id]) {
        elements[id] = {
          id: id,
          style: {},
          textContent: '',
          innerHTML: '',
          value: '',
          appendChild: () => {},
          classList: { add: () => {}, remove: () => {}, toggle: () => {} },
          addEventListener: () => {},
          querySelectorAll: () => []
        };
      }
      return elements[id];
    };
    const window = { selectedBacktestFile: '', addEventListener: () => {}, location: { search: '' } };
    globalThis.window = window;
    const document = {
      getElementById: (id) => makeElem(id),
      querySelectorAll: () => []
    };
    globalThis.document = document;
    globalThis.$ = (id) => makeElem(id);
    const fetch = () => {
      if (trackNetwork) networkCalls++;
      return Promise.resolve({ ok: true, json: async () => ({}) });
    };
    globalThis.fetch = fetch;
    """

    test_js = """
    if (typeof updateBacktestParamPreview !== 'function') {
      throw new Error('updateBacktestParamPreview function is not defined');
    }

    // Set zero values for backtest inputs
    $('btOffset').value = '0';
    $('btExit5m').value = '0';
    $('btExitReversal').value = '0';
    $('btEntryDelay').value = '0';
    $('btQuoteLo').value = '0';
    $('btDeadZoneVal').value = '0';

    trackNetwork = true;
    updateBacktestParamPreview();
    trackNetwork = false;

    if (networkCalls > 0) {
      throw new Error(`expected 0 network calls during preview, got: ${networkCalls}`);
    }

    const svgHtml = $('btParamPreviewSvg').innerHTML;
    if (!svgHtml.includes('Long Bid: $0.500') || !svgHtml.includes('Short Comp: $0.500')) {
      throw new Error(`expected Long Bid & Short Comp at $0.500 for zero offset, got: ${svgHtml}`);
    }

    // Cluster every visible level at the same price and verify the layout pass
    // enforces its 25-unit minimum gap after sorting and clamping.
    $('btQuoteLo').value = '0.5';
    $('btQuoteHi').value = '0.5';
    updateBacktestParamPreview();
    const clusteredCenters = Array.from(
      $('btParamPreviewSvg').innerHTML.matchAll(/data-label-center="([0-9.]+)"/g),
      match => Number(match[1]),
    ).sort((a, b) => a - b);
    if (clusteredCenters.length < 4) {
      throw new Error(`expected four clustered preview labels, got: ${clusteredCenters.length}`);
    }
    for (let i = 1; i < clusteredCenters.length; i += 1) {
      if (clusteredCenters[i] - clusteredCenters[i - 1] < 25) {
        throw new Error(`preview labels overlap: ${clusteredCenters.join(', ')}`);
      }
    }

    console.log('BT_PARAM_PREVIEW_ZERO_TESTS_PASSED');
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
    assert "BT_PARAM_PREVIEW_ZERO_TESTS_PASSED" in res.stdout


# ── Jungle King tab (issue #319) ─────────────────────────────────────────────
# The OFAT manifest (research/jungle-king/) as a read-only quick reference.
# Contract: SPEC-319.md — manifest groups in manifest order, registry join
# server-side, baseline prominent, nothing mutable.

def _jk_baselines():
    """Baseline per manifest key, read from the checklist README [x] rows.

    The viewer must present the manifest's own baselines (operator-replicable
    CLI defaults), not engine defaults — the provenance guard in SPEC-319.md.
    Rows don't name their parameter; the `####` heading above does, so parse
    line by line tracking the current heading. The Exit Thresholds section has
    one shared baseline applying to all six per-slug keys.
    """
    readme = Path(__file__).resolve().parent.parent / "research" / "jungle-king" / "README.md"
    text = readme.read_text(encoding="utf-8")
    exit_slugs = ("default_5m", "default_15m", "btc-up-or-down-5m",
                  "sol-up-or-down-5m", "btc-up-or-down-15m", "sol-up-or-down-15m")
    baselines: dict[str, object] = {}
    current = None                      # param name, or "exit:*" for the shared section
    for line in text.splitlines():
        # The heading wraps the key in parens: `#### Label (`key`)`.
        h4 = re.match(r"^#### .+? \(`([a-z_]+)`\)\s*$", line)
        if h4:
            current = h4.group(1)
            continue
        if re.match(r"^### .+Exit Thresholds", line):
            current = "exit:*"
            continue
        if re.match(r"^### ", line):
            current = None
            continue
        m = re.match(r'^- \[x\] (.+?) — \*\*Baseline.*$', line)
        if m and current:
            # Rows read `- [x] <value>[ (prose)] — **Baseline...**`; the value
            # token may carry prose, so classify on the token's own shape.
            token = m.group(1).strip()
            if current == "exit:*":
                value = float(re.match(r"[0-9.eE+-]+", token).group(0))
                for slug in exit_slugs:
                    baselines[f"exit_thresh_by_slug.{slug}"] = value
                continue
            if token.startswith(("True", "False")):
                baselines[current] = token == "True"
                continue
            num = re.match(r"^([0-9][0-9.eE+-]*)", token)
            if num and not token.startswith("["):
                text_value = num.group(1)
                # Integer-valued rows stay int so the type-aware parity check
                # matches the registry's int defaults (e.g. quote_shares 120).
                baselines[current] = float(text_value) if "." in text_value else int(text_value)
                continue
            pair = re.match(r"^\[([0-9.eE+-]+),\s*([0-9.eE+-]+)\]", token)
            if pair:
                baselines[current] = [float(pair.group(1)), float(pair.group(2))]
                continue
            # Bare word — the first word is the value, e.g. `pct (fraction ...)`.
            baselines[current] = token.split()[0]
    return baselines


def _flatten_registry():
    """param_spec() grouped dict → {param_name: spec_entry}.

    Only exit_thresh_by_slug.* flattens to per-slug keys, mirroring the
    manifest's per-slug naming.
    """
    out: dict[str, dict] = {}
    for entries in _spec().values():
        for name, v in entries.items():
            if name == "exit_thresh_by_slug":
                for slug in ("default_5m", "default_15m", "btc-up-or-down-5m",
                             "sol-up-or-down-5m", "btc-up-or-down-15m", "sol-up-or-down-15m"):
                    out[f"exit_thresh_by_slug.{slug}"] = v
                continue
            out[name] = v
    return out


def test_jungle_king_endpoint_serves_full_manifest():
    """Serve all manifest entries in the expected four-group response."""
    body = client.get("/api/jungle-king").json()
    assert set(body.keys()) == {"groups"}
    groups = body["groups"]
    assert [g["key"] for g in groups] == [
        "trading_knobs", "exit_thresholds", "structural_limits", "execution_assumptions"
    ]
    params = [p for g in groups for p in g["params"]]
    manifest = json.loads(
        (Path(__file__).resolve().parent.parent / "research" / "jungle-king" / "param_ranges.json")
        .read_text(encoding="utf-8")
    )
    assert [p["name"] for p in params] == list(manifest.keys())
    for p in params:
        assert p["values"] == manifest[p["name"]]
        # quote_range's baseline is the [lo, hi] pair; the #333 allow-list keys
        # carry bool/string baselines; every other key is a numeric scalar.
        assert (
            isinstance(p["baseline"], (int, float))
            or isinstance(p["baseline"], bool)
            or isinstance(p["baseline"], str)
            or (isinstance(p["baseline"], list) and len(p["baseline"]) == 2)
        )
        assert isinstance(p["baseline_in_values"], bool)
        assert p["param_class"] in ("tuning", "structural", "assumption")
        assert "label" in p and "unit" in p


def test_jungle_king_baseline_matches_manifest_checklist():
    """Keep each displayed baseline aligned with the README checklist."""
    body = client.get("/api/jungle-king").json()
    params = [p for g in body["groups"] for p in g["params"]]
    by_name = {p["name"]: p for p in params}
    readme_baselines = _jk_baselines()
    assert readme_baselines, "README checklist parsing broke"
    for name, value in readme_baselines.items():
        assert name in by_name, f"{name} missing from endpoint"
        # Type-aware: a README `False` row must not match an API `0`, and a
        # `close` row must not match some numeric value.
        assert by_name[name]["baseline"] == value, \
            f"{name} baseline drifted from the manifest checklist"
        assert type(by_name[name]["baseline"]) is type(value), \
            f"{name} baseline type drifted from the README checklist"
    assert by_name["offset"]["baseline_in_values"] is True
    # Every endpoint baseline must have a README `[x]` row, or the checklist
    # has drifted behind the manifest.
    missing_rows = sorted(set(by_name) - set(readme_baselines))
    assert missing_rows == [], f"endpoint baselines without a README [x] row: {missing_rows}"


def test_jungle_king_registry_join_and_exit_inheritance():
    """Use registry metadata and inherit tuning class for per-slug exits."""
    body = client.get("/api/jungle-king").json()
    params = [p for g in body["groups"] for p in g["params"]]
    by_name = {p["name"]: p for p in params}
    registry = _flatten_registry()
    # Shared params carry the registry's identity (one label source — issue #164 rule).
    for name in ("offset", "max_pair_cost", "taker_fee_rate", "dead_zone_val"):
        assert by_name[name]["registry"] is not None
        assert by_name[name]["label"] == registry[name]["label"]
        assert by_name[name]["param_class"] == registry[name]["param_class"]
    # exit_thresh_by_slug.* inherits the parent's class (tuning) and derives its label.
    exit5 = by_name["exit_thresh_by_slug.btc-up-or-down-5m"]
    assert exit5["param_class"] == "tuning"
    assert registry["exit_thresh_by_slug.btc-up-or-down-5m"]["param_class"] == "tuning"
    assert "BTC" in exit5["label"] and "5m" in exit5["label"]
    # Issue #333: the three checklist-only knobs joined the manifest. Each
    # carries its registry class and the engine default as baseline.
    for name, cls, baseline in (
        ("enable_leg_chase", "tuning", False),
        ("naked_leg_at_expiry", "structural", "close"),
        ("dead_zone_unit", "structural", "pct"),
    ):
        assert by_name[name]["param_class"] == cls
        assert by_name[name]["baseline"] == baseline
        assert by_name[name]["baseline_in_values"] is True
        assert by_name[name]["label"] == registry[name]["label"]


def test_jungle_king_group_membership_matches_manifest_sections():
    """Preserve manifest grouping and cover exactly its 22 keys.

    Issue #333: `enable_leg_chase` sits before `exit_reversal` (registry
    order), and `naked_leg_at_expiry` / `dead_zone_unit` join structural
    limits, the unit directly after the value it qualifies.
    """
    body = client.get("/api/jungle-king").json()
    groups = {g["key"]: [p["name"] for p in g["params"]] for g in body["groups"]}
    assert groups["trading_knobs"] == [
        "offset", "queue_gate", "quote_shares", "entry_delay_sec", "entry_delay_pct",
        "enable_leg_chase", "exit_reversal",
    ]
    assert len(groups["exit_thresholds"]) == 6
    assert all(n.startswith("exit_thresh_by_slug.") for n in groups["exit_thresholds"])
    assert groups["structural_limits"] == [
        "max_pair_cost", "quote_range", "naked_leg_at_expiry", "dead_zone_val", "dead_zone_unit",
    ]
    assert groups["execution_assumptions"] == ["taker_fee_rate", "merge_gas_usd", "tick_size", "min_quote_shares"]
    assert sum(len(g["params"]) for g in body["groups"]) == 22


_JK_MANIFEST = json.loads(
    (Path(__file__).resolve().parent.parent / "research" / "jungle-king" / "param_ranges.json")
    .read_text(encoding="utf-8")
)


@pytest.mark.parametrize("contents", [
    "{not-json",
    "{}",
    json.dumps({"offset": [0.02]}),
    json.dumps({**_JK_MANIFEST, "offset": []}),
    json.dumps({**_JK_MANIFEST, "offset": [0.02, "bad"]}),
    json.dumps({**_JK_MANIFEST, "offset": [True]}),
    json.dumps({**_JK_MANIFEST, "offset": [float("nan")]}),
    json.dumps({**_JK_MANIFEST, "quote_range": [[0.1, "bad"]]}),
    json.dumps({**_JK_MANIFEST, "quote_range": [[0.9, 0.1]]}),
    json.dumps({**_JK_MANIFEST, "quote_range": [[-0.1, 0.9]]}),
    # Issue #333: non-numeric candidates are accepted only for the three named
    # allow-list keys, only with the correct type, only inside their domain.
    json.dumps({**_JK_MANIFEST, "enable_leg_chase": [0, 1]}),
    json.dumps({**_JK_MANIFEST, "enable_leg_chase": [0.0, 1.0]}),
    json.dumps({**_JK_MANIFEST, "enable_leg_chase": ["true"]}),
    json.dumps({**_JK_MANIFEST, "enable_leg_chase": [True, "yes"]}),
    json.dumps({**_JK_MANIFEST, "naked_leg_at_expiry": ["settle", "hold"]}),
    json.dumps({**_JK_MANIFEST, "naked_leg_at_expiry": [True, False]}),
    json.dumps({**_JK_MANIFEST, "dead_zone_unit": ["pct", "PCT"]}),
    json.dumps({**_JK_MANIFEST, "dead_zone_unit": [True, False]}),
    # A string or bool never validates for a numeric key.
    json.dumps({**_JK_MANIFEST, "offset": ["0.02"]}),
    json.dumps({**_JK_MANIFEST, "dead_zone_val": [True]}),
])
def test_jungle_king_malformed_manifest_is_a_clean_error(tmp_path, monkeypatch, contents):
    """Reject unreadable, incomplete, or type-confused manifest shapes."""
    from server import osc_dash
    manifest = tmp_path / "param_ranges.json"
    manifest.write_text(contents, encoding="utf-8")
    monkeypatch.setattr(osc_dash, "JUNGLE_KING_MANIFEST", manifest)

    response = client.get("/api/jungle-king")

    assert response.status_code == 500
    assert response.json()["detail"]


def test_jungle_king_missing_manifest_is_not_found(tmp_path, monkeypatch):
    """Return a not-found response when the source manifest is absent."""
    from server import osc_dash
    monkeypatch.setattr(osc_dash, "JUNGLE_KING_MANIFEST", tmp_path / "missing.json")

    response = client.get("/api/jungle-king")

    assert response.status_code == 404
    assert response.json()["detail"]


def test_jungle_king_baseline_outside_candidates_is_kept(tmp_path, monkeypatch):
    """Return the baseline separately when it is outside candidate values."""
    from server import osc_dash
    manifest = {**_JK_MANIFEST, "offset": [0.01, 0.03]}
    manifest_path = tmp_path / "param_ranges.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(osc_dash, "JUNGLE_KING_MANIFEST", manifest_path)

    response = client.get("/api/jungle-king")

    assert response.status_code == 200
    offset = next(
        param
        for group in response.json()["groups"]
        for param in group["params"]
        if param["name"] == "offset"
    )
    assert offset["baseline"] == 0.02
    assert offset["baseline_in_values"] is False


def test_jungle_king_endpoint_is_get_only_and_does_not_modify_manifest():
    """Keep the manifest endpoint read-only and reject write methods."""
    manifest_path = Path(__file__).resolve().parent.parent / "research" / "jungle-king" / "param_ranges.json"
    before = manifest_path.read_bytes()

    response = client.get("/api/jungle-king")
    rejected_write = client.post("/api/jungle-king", json={"offset": [0.03]})

    assert response.status_code == 200
    assert rejected_write.status_code == 405
    assert manifest_path.read_bytes() == before


def test_jungle_king_panel_has_no_mutating_controls_or_requests():
    """Keep the tab free of sweep, write, and execution controls."""
    html = client.get("/").text
    panel = re.search(r'<div id="tab-jungleking".*?(?=<!-- TAB 3:)', html, re.DOTALL)
    loader = re.search(r"function loadJungleKing\(\)\s*\{(?P<body>.*?)\n\}", html, re.DOTALL)

    assert panel is not None
    assert loader is not None
    assert not re.search(r"<(?:button|form|input)\b", panel.group(0), re.IGNORECASE)
    assert "/api/backtest" not in loader.group(0)
    assert "/api/collector/" not in loader.group(0)
    assert "/api/live/" not in loader.group(0)


# ── Jungle King tab skeleton (issue #319, TASK-2) ────────────────────────────

def test_root_serves_jungle_king_tab_anchors():
    """Serve the sidebar button, panel, and loader hook in the dashboard."""
    html = client.get("/").text
    assert "tab-btn-jungleking" in html
    assert "tab-jungleking" in html
    assert "loadJungleKing" in html
    assert "Jungle King" in html


# ── Jungle King presentation (issue #319, TASK-3) ────────────────────────────

def test_jungle_king_render_js_present():
    """Wire rendering and baseline indicators into the tab load path."""
    html = client.get("/").text
    assert "function loadJungleKing" in html
    assert "function renderJungleKing" in html
    assert "jkBaselineChip" in html
    assert "jkBaselineMissing" in html
    assert "fetch('/api/jungle-king')" in html


def test_jungle_king_client_coalesces_pending_loads():
    """Repeated tab opens share one pending fetch."""
    node_bin = shutil.which("node")
    if node_bin is None:
        raise RuntimeError("Node.js is required for the Jungle King client tests")
    html = client.get("/").text
    load_match = re.search(r"function loadJungleKing\(\)\s*\{(?P<body>.*?)\n\}", html, re.DOTALL)
    assert load_match is not None
    payload = json.dumps(client.get("/api/jungle-king").json())
    expected_groups = len(client.get("/api/jungle-king").json()["groups"])
    harness = f"""
    const elements = {{ jkGroups: {{ innerHTML: '' }}, jkNotice: {{style: {{}}}} }};
    const $ = (id) => elements[id] || null;
    const window = {{}};
    function renderJungleKing(data) {{ elements.jkGroups.innerHTML = String(data.groups.length); }}
    let JK_DATA = null;
    let JK_LOAD_PROMISE = null;
    let resolveFetch;
    let fetchCount = 0;
    globalThis.fetch = () => {{ fetchCount += 1; return new Promise(resolve => {{ resolveFetch = resolve; }}); }};
    {load_match.group(0)}
    const first = loadJungleKing();
    const second = loadJungleKing();
    if (first !== second || fetchCount !== 1) throw new Error('pending calls were not coalesced');
    resolveFetch({{ok:true, json:async()=>({payload})}});
    Promise.all([first, second]).then(() => {{
      if (elements.jkGroups.innerHTML !== '{expected_groups}') throw new Error('successful load was not rendered');
      console.log('JK_LOAD_OK');
    }}).catch(err => {{ console.error(err); process.exitCode = 1; }});
    """
    result = subprocess.run([node_bin, "-e", harness], capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert result.returncode == 0, f"Node script failed: {result.stderr}\\n{result.stdout}"
    assert "JK_LOAD_OK" in result.stdout


def test_jungle_king_retries_after_failed_fetch():
    """An error is shown and a later open retries the failed request."""
    node_bin = shutil.which("node")
    if node_bin is None:
        raise RuntimeError("Node.js is required for the Jungle King client tests")
    html = client.get("/").text
    load_match = re.search(r"function loadJungleKing\(\)\s*\{(?P<body>.*?)\n\}", html, re.DOTALL)
    assert load_match is not None
    harness = f"""
    const elements = {{ jkGroups: {{ innerHTML: 'stale' }}, jkNotice: {{style: {{}}, textContent: ''}} }};
    const $ = (id) => elements[id] || null;
    const window = {{}};
    function renderJungleKing(data) {{ elements.jkGroups.innerHTML = String(data.groups.length); }}
    let JK_DATA = null;
    let JK_LOAD_PROMISE = null;
    let fetchCount = 0;
    globalThis.fetch = async () => {{
      fetchCount += 1;
      if (fetchCount === 1) return {{ok:false, status:500, json:async()=>({{detail:'corrupt manifest'}})}};
      return {{ok:true, json:async()=>({{groups:[]}})}};
    }};
    {load_match.group(0)}
    (async()=>{{
      await loadJungleKing();
      if (elements.jkGroups.innerHTML !== '' || !elements.jkNotice.textContent.includes('corrupt manifest')) throw new Error('failure state was not reported');
      await loadJungleKing();
      if (fetchCount !== 2 || JK_DATA === null || elements.jkGroups.innerHTML !== '0') throw new Error('later open did not retry');
      console.log('JK_RETRY_OK');
    }})().catch(err=>{{console.error(err);process.exitCode=1;}});
    """
    result = subprocess.run([node_bin, "-e", harness], capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert result.returncode == 0, f"Node script failed: {result.stderr}\\n{result.stdout}"
    assert "JK_RETRY_OK" in result.stdout


def test_jungle_king_render_node():
    """Render the real API payload through the page's renderer."""
    node_bin = shutil.which("node")
    if node_bin is None:
        raise RuntimeError("Node.js is required for the Jungle King client tests")
    html = client.get("/").text
    helpers = re.search(r"function jkEsc\(s\)\s*\{.*?(?=\nlet isCollectorActive)", html, re.DOTALL)
    assert helpers is not None
    payload = json.dumps(client.get("/api/jungle-king").json())
    harness = f"""
    const elements = {{ jkGroups: {{ innerHTML: '' }}, jkNotice: {{style: {{}}}} }};
    const $ = (id) => elements[id] || null;
    const window = {{}};
    {helpers.group(0)}
    const payload = {payload};
    renderJungleKing({{groups: []}});
    if (!elements.jkGroups.innerHTML.includes('No parameters')) throw new Error('missing empty-state notice');
    renderJungleKing(payload);
    const html = elements.jkGroups.innerHTML;
    const result = {{
      groupCards: (html.match(/class=\"card jk-group/g) || []).length,
      parameterCards: (html.match(/class=\"jk-param\"/g) || []).length,
      baselineChips: (html.match(/jkBaselineChip/g) || []).length,
      baselineLabels: (html.match(/class=\"jk-chip-baseline-tag\"/g) || []).length,
      tuningBadges: (html.match(/TUNING KNOB/g) || []).length,
      structuralBadges: (html.match(/STRUCTURAL LIMIT/g) || []).length,
      assumptionBadges: (html.match(/EXECUTION ASSUMPTION/g) || []).length
    }};
    const outside = JSON.parse(JSON.stringify(payload));
    outside.groups[0].params[0].baseline_in_values = false;
    outside.groups[0].params[0].values = [0.01, 0.03];
    renderJungleKing(outside);
    result.missingBaselineMarkers = (elements.jkGroups.innerHTML.match(/jkBaselineMissing/g) || []).length;
    result.outsideRangeBaselineChips = (elements.jkGroups.innerHTML.match(/jkBaselineChip/g) || []).length;
    result.outsideRangeBaselineLabels = (elements.jkGroups.innerHTML.match(/class=\"jk-chip-baseline-tag\"/g) || []).length;
    renderJungleKing(payload);
    const chipHtml = elements.jkGroups.innerHTML;
    result.nonNumericChips = {{
      falseBaseline: chipHtml.includes('>false<span class="jk-chip-baseline-tag">BASELINE</span>'),
      pctBaseline: chipHtml.includes('>pct<span class="jk-chip-baseline-tag">BASELINE</span>'),
      closeBaseline: chipHtml.includes('>close<span class="jk-chip-baseline-tag">BASELINE</span>'),
      trueChip: chipHtml.includes('>true<'),
      holdChip: chipHtml.includes('>hold<'),
      secChip: chipHtml.includes('>sec<')
    }};
    console.log(JSON.stringify(result));
    """
    result = subprocess.run([node_bin, "-e", harness], capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert result.returncode == 0, f"Node script failed: {result.stderr}\\n{result.stdout}"
    rendered = json.loads(result.stdout.strip())
    assert rendered["groupCards"] == 4
    assert rendered["parameterCards"] == 22
    assert rendered["baselineChips"] == 22
    assert rendered["baselineLabels"] == 22
    assert rendered["tuningBadges"] > 0
    assert rendered["structuralBadges"] > 0
    assert rendered["assumptionBadges"] > 0
    assert rendered["missingBaselineMarkers"] == 1
    assert rendered["outsideRangeBaselineChips"] == 21
    assert rendered["outsideRangeBaselineLabels"] == 21
    # Issue #333: the non-numeric cards render their values verbatim — `false`
    # as JSON spells it, strings as-is — and mark the engine-default chips.
    chips = rendered["nonNumericChips"]
    assert all(chips.values()), f"non-numeric chip rendering broke: {chips}"


# Registry/manifest drift guard (issue #333): every BacktestParams field must
# be visible to the OFAT viewer. A new registry key that skips the manifest
# makes the tab under-report what can be swept — silently, until now.
# Registry parameters deliberately not swept go in this allow-list, one reason
# per entry; execution assumptions DO belong in the manifest (held-at-baseline
# group), so they are covered and never listed here.
_JK_OUT_OF_SCOPE_ALLOW_LIST: dict[str, str] = {}


def test_jungle_king_covers_every_registry_parameter():
    """No BacktestParams field can go missing from the manifest silently."""
    manifest_keys = set(_JK_MANIFEST)
    registry = _flatten_registry()
    for name in registry:
        covered = (
            name in manifest_keys
            or name.startswith("exit_thresh_by_slug.")
            or name in _JK_OUT_OF_SCOPE_ALLOW_LIST
        )
        assert covered, (
            f"registry parameter {name!r} is missing from the Jungle King "
            "manifest and is not declared out of scope"
        )
    for name in _JK_OUT_OF_SCOPE_ALLOW_LIST:
        assert name in registry, f"stale allow-list entry: {name!r} is not a registry key"
        assert name not in manifest_keys, f"stale allow-list entry: {name!r} is already in the manifest"


def test_jungle_king_engine_defaults_sit_inside_their_declared_domains():
    """The non-numeric domain map and the engine cannot drift apart."""
    from server import osc_dash
    registry = _flatten_registry()
    for name, domain in osc_dash._JUNGLE_KING_NON_NUMERIC_DOMAINS.items():
        assert name in registry, f"{name!r} is not a registry parameter"
        default = registry[name]["default"]
        assert isinstance(default, bool) or default in domain, \
            f"engine default {default!r} of {name!r} left its declared domain {sorted(domain)!r}"


def test_backtest_runtime_estimation_badge_present():
    """Issue #330: dashboard includes dynamic runtime estimation badge and helpers."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert 'id="btRuntimeEstBadge"' in html
    assert 'class="bt-runtime-badge"' in html
    assert "calculateBtEstimatedRuntime" in html
    assert "updateBtRuntimeEstimate" in html
    assert 'onchange="updateBtRuntimeEstimate()"' in html


def test_backtest_runtime_estimation_client_calculation():
    """Issue #330: client-side estimation calculation behaves dynamically based on scope."""
    node_bin = shutil.which("node")
    if node_bin is None:
        pytest.skip("Node.js is required for client runtime estimation test")

    html = client.get("/").text
    helpers = re.search(
        r"function fmtElapsed\(ms\)\s*\{.*?(?=\n// One reader for every Backtester control)",
        html,
        re.DOTALL,
    )
    assert helpers is not None

    harness = f"""
    const elements = {{
      btRuntimeEstBadge: {{ textContent: '', title: '', style: {{}} }},
      btFileSelect: {{ value: 'ticks_test.jsonl' }}
    }};
    const $ = (id) => elements[id] || null;
    const window = {{
      tickManifestFiles: [
        {{
          name: 'ticks_test.jsonl',
          windows_count: 500,
          market_breakdown: [
            {{ series: 'btc-up-or-down-5m', duration: 300, windows: 50 }},
            {{ series: 'eth-up-or-down-5m', duration: 300, windows: 50 }},
            {{ series: 'sol-up-or-down-5m', duration: 300, windows: 50 }},
            {{ series: 'xrp-up-or-down-5m', duration: 300, windows: 50 }},
            {{ series: 'bnb-up-or-down-5m', duration: 300, windows: 50 }},
            {{ series: 'btc-up-or-down-15m', duration: 900, windows: 50 }},
            {{ series: 'eth-up-or-down-15m', duration: 900, windows: 50 }},
            {{ series: 'sol-up-or-down-15m', duration: 900, windows: 50 }},
            {{ series: 'xrp-up-or-down-15m', duration: 900, windows: 50 }},
            {{ series: 'bnb-up-or-down-15m', duration: 900, windows: 50 }}
          ]
        }}
      ]
    }};

    {helpers.group(0)}

    // 1. All 5 tokens, both durations -> 500 windows
    selectedBtTokens = new Set(['BTC', 'ETH', 'BNB', 'SOL', 'XRP']);
    selectedBtDuration = 'both';
    const estAll = calculateBtEstimatedRuntime('ticks_test.jsonl');

    // 2. Filter to BTC only, both durations -> 100 windows
    selectedBtTokens = new Set(['BTC']);
    selectedBtDuration = 'both';
    const estBtc = calculateBtEstimatedRuntime('ticks_test.jsonl');

    // 3. Filter to BTC only, 5m only -> 50 windows
    selectedBtTokens = new Set(['BTC']);
    selectedBtDuration = '5m';
    const estBtc5m = calculateBtEstimatedRuntime('ticks_test.jsonl');

    // 4. Update badge UI
    updateBtRuntimeEstimate();
    const badgeText = elements.btRuntimeEstBadge.textContent;

    // 5. Zero windows filter
    selectedBtTokens = new Set(['DOGE']);
    updateBtRuntimeEstimate();
    const zeroBadgeText = elements.btRuntimeEstBadge.textContent;

    console.log(JSON.stringify({{
      estAllWindows: estAll.windows,
      estAllSec: estAll.seconds,
      estBtcWindows: estBtc.windows,
      estBtcSec: estBtc.seconds,
      estBtc5mWindows: estBtc5m.windows,
      estBtc5mSec: estBtc5m.seconds,
      badgeText: badgeText,
      zeroBadgeText: zeroBadgeText
    }}));
    """
    result = subprocess.run([node_bin, "-e", harness], capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert result.returncode == 0, f"Node script failed: {result.stderr}\\n{result.stdout}"
    data = json.loads(result.stdout.strip())
    assert data["estAllWindows"] == 500
    assert data["estBtcWindows"] == 100
    assert data["estBtc5mWindows"] == 50
    assert data["estAllSec"] > data["estBtcSec"] > data["estBtc5mSec"]
    assert "50 win" in data["badgeText"]
    assert "0 windows" in data["zeroBadgeText"]


# --- Issue #331: streaming backtest ------------------------------------------


def _fake_queue_factory():
    """Fresh plain queue per call — tests swap the manager proxy for this."""
    import queue as _queue
    return _queue.Queue()


def _make_backtest_ticks_file(tmp_path, name="fake_stream.jsonl", windows=4):
    """Deterministic multi-window ticks fixture, one file, several windows."""
    fake_file = tmp_path / name
    ticks = []
    for i in range(windows):
        cid = f"0xCID_{i:04d}"
        slug = f"btc-updown-5m-{i:04d}"
        base_ts = 1000.0 + i * 500
        ticks.append(_make_fake_tick(base_ts, cid, slug, "btc-up-or-down-5m", 0.50, tape=[{"asset": f"{cid}_up", "price": 0.48, "size": 100}]))
        ticks.append(_make_fake_tick(base_ts + 1, cid, slug, "btc-up-or-down-5m", 0.48))
        ticks.append(_make_fake_tick(base_ts + 2, cid, slug, "btc-up-or-down-5m", 0.52, tape=[{"asset": f"{cid}_dn", "price": 0.46, "size": 100}]))
        ticks.append(_make_fake_tick(base_ts + 3, cid, slug, "btc-up-or-down-5m", 0.50))
    with open(fake_file, "w", encoding="utf-8") as f:
        for t in ticks:
            f.write(json.dumps(t) + "\n")
    return fake_file


def test_worker_progress_emission_matches_final_curve(tmp_path, monkeypatch):
    """The worker's progress points use the final curve's scaling and the
    returned dict is unchanged when progress is enabled (Issue #331)."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    fake_file = _make_backtest_ticks_file(tmp_path)

    q = _fake_queue_factory()
    from dataclasses import asdict
    from backtest import BacktestParams
    params_dict = asdict(BacktestParams(offset=0.02))

    # Run WITH progress.
    with_progress = osc_dash._run_backtest_simulation_worker(
        str(tmp_path), str(fake_file), params_dict, 5, 0.0, 0, {}, {},
        "", "", progress_queue=q, progress_batch_windows=1,
    )
    # Run WITHOUT progress (current behavior).
    without_progress = osc_dash._run_backtest_simulation_worker(
        str(tmp_path), str(fake_file), params_dict, 5, 0.0, 0, {}, {},
        "", "",
    )

    assert json.dumps(with_progress, sort_keys=True) == json.dumps(without_progress, sort_keys=True)

    # Collect progress messages: batch=1 → one message per window plus final flush.
    messages = []
    while True:
        try:
            messages.append(q.get_nowait())
        except Exception:
            break
    assert messages, "no progress messages emitted"
    points = [p for m in messages for p in m["points"]]
    assert len(points) == len(with_progress["equity_curve"])
    # Same scaling as the final curve: cumulative values match the sorted curve.
    final_curve = with_progress["equity_curve"]
    assert points[-1]["cumulative_pnl_cents"] == final_curve[-1]["cumulative_pnl_cents"]
    last_msg = messages[-1]
    assert last_msg["windows_done"] == len(points)
    assert last_msg["provisional_total_pnl_cents"] == final_curve[-1]["cumulative_pnl_cents"]
    # Each point carries pnl_cents + provisional cumulative.
    for p in points:
        assert "pnl_cents" in p and "cumulative_pnl_cents" in p


def test_worker_progress_queue_failure_does_not_fail_run(tmp_path, monkeypatch):
    """A queue put failure disables emission without changing the result."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    fake_file = _make_backtest_ticks_file(tmp_path)

    class ExplodingQueue:
        def put_nowait(self, msg):
            raise RuntimeError("queue broken")

    from dataclasses import asdict
    from backtest import BacktestParams
    params_dict = asdict(BacktestParams(offset=0.02))

    res = osc_dash._run_backtest_simulation_worker(
        str(tmp_path), str(fake_file), params_dict, 5, 0.0, 0, {}, {},
        "", "", progress_queue=ExplodingQueue(), progress_batch_windows=1,
    )
    baseline = osc_dash._run_backtest_simulation_worker(
        str(tmp_path), str(fake_file), params_dict, 5, 0.0, 0, {}, {},
        "", "",
    )
    assert json.dumps(res, sort_keys=True) == json.dumps(baseline, sort_keys=True)


def test_backtest_guard_releaser_releases_once():
    """The per-run release-once helper clears the guards exactly once."""
    with osc_dash._BACKTEST_LOCK:
        osc_dash._BACKTEST_RUNNING = True
    # Acquire the semaphore for real so the release below returns it to its
    # true initial value instead of inflating the shared counter past 1.
    release = osc_dash._make_backtest_guard_releaser()
    acquired = osc_dash.get_backtest_semaphore()._value
    # Simulate the held guard the way the endpoint holds it: value drained.
    sem = osc_dash.get_backtest_semaphore()
    drained = []
    while sem._value > 0:
        drained.append(True)
        sem._value -= 1
    release()
    try:
        assert not osc_dash._BACKTEST_RUNNING
        release()  # second call must be a no-op
        assert not osc_dash._BACKTEST_RUNNING
    finally:
        # Restore the counter so later tests see the pristine semaphore.
        sem._value = acquired


def test_shutdown_backtest_pool_also_shuts_manager():
    """Issue #331: shutdown_backtest_pool clears the manager singleton too."""
    mgr = osc_dash._get_backtest_manager()
    assert mgr is not None
    osc_dash._new_backtest_progress_queue()
    osc_dash.shutdown_backtest_pool()
    assert osc_dash._BACKTEST_MANAGER is None


def _parse_sse_events(text: str) -> list[dict]:
    """Parse a buffered SSE body into its JSON data envelopes."""
    events = []
    for block in text.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload:
                    events.append(json.loads(payload))
    return events


def _install_stream_test_harness(monkeypatch, tmp_path):
    """Thread-pool + plain-queue harness so no spawn worker runs in tests."""
    import concurrent.futures
    mock_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(osc_dash, "get_backtest_pool", lambda: mock_pool)
    monkeypatch.setattr(osc_dash, "_new_backtest_progress_queue", _fake_queue_factory)
    return mock_pool


def test_backtest_stream_progress_before_final_and_curve_equality(tmp_path, monkeypatch):
    """Issue #331: >=1 progress precedes exactly one final; the final payload
    equals /api/backtest's (with and without limit_windows); the last
    provisional cumulative equals the unlimited final total."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    _make_backtest_ticks_file(tmp_path)
    mock_pool = _install_stream_test_harness(monkeypatch, tmp_path)

    url = "/api/backtest/stream?file=fake_stream.jsonl&offset=0.02&size=5"
    with client.stream("GET", url) as res:
        assert res.status_code == 200
        body = "".join(chunk for chunk in res.iter_text())
    events = _parse_sse_events(body)
    types = [e["type"] for e in events]
    assert types.count("final") == 1
    assert "progress" in types
    assert types.index("progress") < len(types) - 1  # progress precedes final
    final = events[-1]
    assert final["type"] == "final"

    blocking = client.get("/api/backtest?file=fake_stream.jsonl&offset=0.02&size=5").json()
    assert json.dumps(final["result"], sort_keys=True) == json.dumps(blocking, sort_keys=True)

    # Provisional running total reaches the final unlimited total.
    progress_points = [p for e in events if e["type"] == "progress" for p in e["points"]]
    assert progress_points[-1]["cumulative_pnl_cents"] == blocking["equity_curve"][-1]["cumulative_pnl_cents"]

    # limit_windows truncates the final curve identically on both transports.
    with client.stream("GET", url + "&limit_windows=2") as res:
        limited_body = "".join(chunk for chunk in res.iter_text())
    limited_events = _parse_sse_events(limited_body)
    limited_final = next(e for e in limited_events if e["type"] == "final")
    limited_blocking = client.get(
        "/api/backtest?file=fake_stream.jsonl&offset=0.02&size=5&limit_windows=2").json()
    assert json.dumps(limited_final["result"], sort_keys=True) == json.dumps(limited_blocking, sort_keys=True)

    mock_pool.shutdown(wait=True)


def test_backtest_stream_validation_errors_match_blocking(tmp_path, monkeypatch):
    """Issue #331: bad series/file params fail with the blocking endpoint's shapes."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    _install_stream_test_harness(monkeypatch, tmp_path)

    bad_series = client.get("/api/backtest/stream?file=x.jsonl&series=bcc")
    assert bad_series.status_code == 400
    # Same shapes as the blocking endpoint: bad series 400, bad file an `error`
    # payload identical to /api/backtest's (see the traversal-rejection test).
    bad_file = client.get("/api/backtest/stream?file=../secrets.jsonl")
    assert bad_file.json()["error"] == "invalid file param"
    blocking_bad = client.get("/api/backtest?file=../secrets.jsonl")
    assert bad_file.json() == blocking_bad.json()


def test_backtest_stream_429_when_busy(tmp_path, monkeypatch):
    """Issue #331: the stream endpoint honours the same single-run guard."""
    import threading
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    _make_backtest_ticks_file(tmp_path)
    mock_pool = _install_stream_test_harness(monkeypatch, tmp_path)

    started = threading.Event()
    release = threading.Event()

    def blocking_worker(*args, **kwargs):
        started.set()
        release.wait(timeout=5.0)
        return {"params_hash": "t", "params": {}, "params_groups": {}, "overall": {},
                "per_series": {}, "equity_curve": [], "trades_sample": [],
                "pnl_histogram": dict(osc_dash.EMPTY_PNL_HISTOGRAM), "n_snaps": 0, "n_windows": 0}

    monkeypatch.setattr(osc_dash, "_run_backtest_simulation_worker", blocking_worker)
    t = threading.Thread(target=lambda: client.get("/api/backtest?file=fake_stream.jsonl"))
    t.start()
    assert started.wait(timeout=3.0)
    res = client.get("/api/backtest/stream?file=fake_stream.jsonl")
    assert res.status_code == 429
    assert "already in progress" in res.json()["error"].lower()
    release.set()
    t.join(timeout=5.0)
    mock_pool.shutdown(wait=True)


def test_backtest_stream_disconnect_releases_guards_immediately(tmp_path, monkeypatch):
    """Issue #331: a mid-stream ASGI disconnect terminates the pool, releases
    the guards synchronously, and lets a new run start at once. A stale task
    freed later must not clear a newer run's guards."""
    import threading
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    _make_backtest_ticks_file(tmp_path)

    import concurrent.futures
    mock_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(osc_dash, "get_backtest_pool", lambda: mock_pool)
    q = _fake_queue_factory()
    monkeypatch.setattr(osc_dash, "_new_backtest_progress_queue", lambda: q)

    terminate_calls = {"n": 0}
    real_terminate = osc_dash._terminate_backtest_pool

    def counting_terminate(pool=None):  # #371: cleanup now passes the run's own pool
        terminate_calls["n"] += 1
        # The pool is a test double; emulate only the singleton detach.
        if pool is None or osc_dash._BACKTEST_POOL is pool:
            osc_dash._BACKTEST_POOL = None

    monkeypatch.setattr(osc_dash, "_terminate_backtest_pool", counting_terminate)

    first_progress_seen = threading.Event()
    release_worker = threading.Event()

    def blocking_worker(*args, **kwargs):
        progress_queue = args[10] if len(args) > 10 else kwargs.get("progress_queue")
        try:
            progress_queue.put_nowait({
                "windows_done": 1, "provisional_total_pnl_cents": 1.0,
                "points": [{"pnl_cents": 1.0, "cumulative_pnl_cents": 1.0}],
            })
            first_progress_seen.set()
            release_worker.wait(timeout=5.0)
        except Exception:
            pass
        return {"params_hash": "t", "params": {}, "params_groups": {}, "overall": {},
                "per_series": {}, "equity_curve": [], "trades_sample": [],
                "pnl_histogram": dict(osc_dash.EMPTY_PNL_HISTOGRAM), "n_snaps": 0, "n_windows": 0}

    monkeypatch.setattr(osc_dash, "_run_backtest_simulation_worker", blocking_worker)

    async def drive_asgi_with_disconnect():
        import asyncio as _aio
        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1", "method": "GET", "scheme": "http",
            "path": "/api/backtest/stream", "raw_path": b"/api/backtest/stream",
            "query_string": b"file=fake_stream.jsonl&offset=0.02&size=5",
            "root_path": "", "headers": [(b"host", b"testserver")],
            "client": ("testclient", 50000), "server": ("testserver", 80),
        }
        body_chunks = []
        saw_disconnect = {"flag": False}
        request_sent = {"flag": False}

        async def receive():
            if not request_sent["flag"]:
                request_sent["flag"] = True
                return {"type": "http.request", "body": b"", "more_body": False}
            # Block until the test observed the first progress chunk, then hang up.
            while not saw_disconnect["flag"]:
                await _aio.sleep(0.01)
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body":
                body_chunks.append(message.get("body", b""))
                if b"progress" in message.get("body", b""):
                    saw_disconnect["flag"] = True

        await osc_dash.app(scope, receive, send)
        return b"".join(body_chunks)

    import asyncio
    body = asyncio.run(drive_asgi_with_disconnect())
    assert b"progress" in body

    # Guards released synchronously on disconnect; pool termination requested.
    assert not osc_dash._BACKTEST_RUNNING
    sem = osc_dash.get_backtest_semaphore()
    assert not sem.locked()
    assert terminate_calls["n"] >= 1

    # The next request must not be rejected with 429.
    release_worker.set()
    next_run = client.get("/api/backtest?file=fake_stream.jsonl&limit_windows=1")
    assert next_run.status_code == 200
    mock_pool.shutdown(wait=True)


def test_real_process_pool_backtest_passes_deterministically(tmp_path, monkeypatch):
    """Issue #341: a backtest against the REAL spawn ProcessPoolExecutor (no
    ThreadPoolExecutor monkeypatch) passes deterministically on Windows/pytest.
    The pool is reset before and after so the singleton never leaks a broken
    executor into another test."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    _make_backtest_ticks_file(tmp_path)
    osc_dash.shutdown_backtest_pool()  # fresh pool for this test
    try:
        res = client.get("/api/backtest", params={"file": "fake_stream.jsonl"})
        assert res.status_code == 200, res.text[:500]
        body = res.json()
        assert "overall" in body and "n_windows" in body
        # Non-vacuous: the spawn worker really simulated fixture windows.
        assert body["n_windows"] > 0, body.get("overall")
    finally:
        osc_dash.shutdown_backtest_pool()


def test_real_process_pool_sweep_stream_passes_deterministically(tmp_path, monkeypatch):
    """Issue #341: the SSE sweep stream against the REAL spawn pool completes
    with a final event — the stream path drains its queue by polling, which is
    the exact code shape that interacted badly with spawned workers before."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    _make_backtest_ticks_file(tmp_path)
    # The REAL manager queue is required here: a plain queue.Queue is not
    # picklable across the spawn boundary (that pairing only works with the
    # ThreadPoolExecutor harness). This also exercises the manager lifecycle.
    osc_dash.shutdown_backtest_pool()
    try:
        with client.stream("GET", "/api/backtest/sweep/stream?axis=queue&file=fake_stream.jsonl") as res:
            assert res.status_code == 200
            body = "".join(chunk for chunk in res.iter_text())
        events = _parse_sse_events(body)
        types = [e["type"] for e in events]
        assert types.count("final") == 1, types
        assert "error" not in types, body[:500]
    finally:
        osc_dash.shutdown_backtest_pool()


def test_broken_pool_diagnostic_mentions_exitcode_and_original_error():
    """Issue #341: the diagnostic replaces "terminated abruptly" with the
    worker pid, its exit code and the original exception — and never raises,
    even when the pool is already torn down."""
    import concurrent.futures.process as _cfp

    class _FakeProc:
        pid = 4242
        exitcode = -9
        stderr = None

    class _FakePool:
        _processes = {"w0": _FakeProc()}

    original = osc_dash._BACKTEST_POOL
    osc_dash._BACKTEST_POOL = _FakePool()
    try:
        text = osc_dash._diagnose_broken_pool(_cfp.BrokenProcessPool("terminated abruptly"))
        # Explicit pool argument wins over the singleton (review #346): the
        # failed request must report its own pool's pid, not a newer pool's.
        class _OtherProc:
            pid = 9999
            exitcode = 1
            stderr = None

        class _OtherPool:
            _processes = {"w0": _OtherProc()}

        text_explicit = osc_dash._diagnose_broken_pool(
            _cfp.BrokenProcessPool("terminated abruptly"), _OtherPool())
    finally:
        osc_dash._BACKTEST_POOL = original
    assert "pid=4242" in text
    assert "exitcode=-9" in text
    assert "terminated abruptly" in text
    assert "pid=9999" in text_explicit and "pid=4242" not in text_explicit

    # Already-torn-down pool: still a clean string, still no exception.
    text = osc_dash._diagnose_broken_pool(_cfp.BrokenProcessPool("terminated abruptly"), None)
    assert "no live worker process found" in text


# ===========================================================================
# Issue #371: Backtest tab lag / freeze / stuck-run regression coverage.
# ===========================================================================


def test_pair_stats_accumulator_matches_builtin_sum_oracle():
    """Numerical contract (#371): for every prefix, the accumulator's mean
    equals round(sum(prefix)/len(prefix), 4) exactly — the interpreter's
    compensated ``sum()`` on 3.12+, plain addition below. Includes 0.0,
    settle-boundary values (1.00004/1.00006) and rounding-boundary values."""
    import random
    rng = random.Random(371)
    values = [1.00004, 1.00006, 1.0001, 0.0, 1e-8, 1.00005, 1.0, 0.99995]
    values += [rng.uniform(0.95, 1.05) for _ in range(2200)]
    values += [1.00004, 1.00006, 0.0, -0.5, 3.25]

    acc = osc_dash._PairStatsAccumulator()
    prefix: list = []
    checked = 0
    for i, v in enumerate(values):
        acc.add_cost(v)
        prefix.append(v)
        if i < 20 or i % 97 == 0:
            expected = round(sum(prefix) / len(prefix), 4)
            assert round(acc.cost_mean(), 4) == expected, f"cost mean drift at prefix {i + 1}"
            checked += 1
    assert checked >= 20

    # Edge accumulator: same contract on a different denominator.
    acc2 = osc_dash._PairStatsAccumulator()
    edges = [0.5, -0.25, 1e-9, 3.5, 0.0]
    prefix2: list = []
    for v in edges:
        acc2.add_edge(v)
        prefix2.append(v)
        assert round(acc2.edge_mean(), 4) == round(sum(prefix2) / len(prefix2), 4)

    # Above-settle semantics: only round(c, 4) > 1.00 counts; 0.0 counts as a value.
    assert acc.above_settle_count() == sum(1 for c in prefix if round(c, 4) > 1.00)
    # Empty metric -> None.
    empty = osc_dash._PairStatsAccumulator()
    assert empty.cost_mean() is None and empty.edge_mean() is None
    # Bounded retained state regardless of values consumed.
    assert acc.state_size() == 6


def test_worker_progress_pair_stats_match_final_result(tmp_path, monkeypatch):
    """#371: the last progress envelope's pair mean, edge mean and above-settle
    count equal the final result's overall block (constant-size accumulators
    must not change any displayed number)."""
    monkeypatch.setattr(osc_dash, "TICKS_DIR", tmp_path)
    fake_file = _make_backtest_ticks_file(tmp_path)
    q = _fake_queue_factory()
    from dataclasses import asdict
    from backtest import BacktestParams
    params_dict = asdict(BacktestParams(offset=0.02))
    result = osc_dash._run_backtest_simulation_worker(
        str(tmp_path), str(fake_file), params_dict, 5, 0.0, 0, {}, {},
        "", "", progress_queue=q, progress_batch_windows=1,
    )
    messages = []
    while True:
        try:
            messages.append(q.get_nowait())
        except Exception:
            break
    assert messages
    last = messages[-1]
    overall = result["overall"]
    assert last["mean_pair_cost"] == overall["mean_pair_cost"]
    assert last["mean_pair_edge_cents"] == overall["mean_pair_edge_cents"]
    assert last["pairs_above_settle"] == overall["pairs_above_settle"]
    # Envelope key order contract (payload shape unchanged).
    assert list(last.keys())[-4:] == [
        "mean_pair_cost", "mean_pair_edge_cents", "pairs_above_settle", "pnl_sample_cents",
    ]


def test_terminate_backtest_pool_binds_to_passed_pool(monkeypatch):
    """#371: late cleanup of run A must not terminate run B's pool. The global
    singleton is cleared only when it still refers to the passed pool; the
    no-argument call keeps the previous global-only behaviour."""
    class FakePool:
        def __init__(self, name):
            self.name = name
            self._processes = {}
            self.shutdown_calls = []

        def shutdown(self, wait=False, cancel_futures=True):
            self.shutdown_calls.append((wait, cancel_futures))

    pool_a, pool_b = FakePool("A"), FakePool("B")
    monkeypatch.setattr(osc_dash, "_BACKTEST_POOL", pool_b)

    # Run A's late cleanup targets pool A: terminated, but B stays the global.
    osc_dash._terminate_backtest_pool(pool_a)
    assert pool_a.shutdown_calls, "run A's pool was not terminated"
    assert osc_dash._BACKTEST_POOL is pool_b, "run B's global pool was stolen by run A's cleanup"

    # Run B's own cleanup: terminates B and clears the global.
    osc_dash._terminate_backtest_pool(pool_b)
    assert pool_b.shutdown_calls
    assert osc_dash._BACKTEST_POOL is None

    # No-argument call: legacy behaviour (terminate + clear whatever is global).
    pool_c = FakePool("C")
    monkeypatch.setattr(osc_dash, "_BACKTEST_POOL", pool_c)
    osc_dash._terminate_backtest_pool()
    assert pool_c.shutdown_calls and osc_dash._BACKTEST_POOL is None


def test_oscillation_poll_guard_suppresses_and_refreshes():
    """#371: the timer-driven poll is suppressed on the Backtest tab, while the
    document is hidden, and while a poll is in flight; returning from Backtest
    with stale data refreshes immediately. Explicit tick() calls stay unguarded."""
    node_bin = shutil.which("node")
    if node_bin is None:
        pytest.skip("Node.js is not installed")
    html = client.get("/").text
    block = re.search(
        r"let currentActiveTab = 'cockpit';.*?function switchTab\(name\)\{.*?\n\}",
        html, re.DOTALL)
    assert block is not None, "polling guard block not found in served HTML"
    harness = """
    const document = { hidden: false, querySelectorAll: () => [], addEventListener: () => {} };
    const window = {};
    const $ = () => null; // switchTab touches DOM elements; they do not exist here
    let tickCalls = 0;
    function tick() {
      tickCalls += 1;
      // Resolved on a microtask so pollTick's in-flight guard can still catch
      // a second synchronous call before `done` runs.
      return Promise.resolve();
    }
    function updateBacktestParamPreview() {}
    function initBacktestIdle() {}
    function renderCockpitUI() {}
    function fetchCockpitState() {}
    function loadManifest() {}
    function loadGoldenCard() {}
    function runVerifyQueue() {}
    function loadJungleKing() {}
    function renderSummaryCharts() {}
""" + block.group(0) + """
    (async () => {
      // Backtest tab active. Timer-driven poll is skipped and marks stale.
      switchTab('backtest');
      pollTick();
      await new Promise(r => setTimeout(r, 5));
      if (tickCalls !== 0) throw new Error('poll fired while Backtest tab active');
      if (!btOscStale) throw new Error('skipped poll did not mark data stale');

      // Hidden document: skipped too.
      switchTab('marketdata');
      if (tickCalls < 1) throw new Error('returning from Backtest did not refresh stale data');
      const afterReturn = tickCalls;
      document.hidden = true;
      pollTick();
      await new Promise(r => setTimeout(r, 5));
      if (tickCalls !== afterReturn) throw new Error('poll fired while document hidden');
      document.hidden = false;

      // In-flight poll: no overlapping fetch starts.
      pollTick();               // starts one poll
      const afterFirst = tickCalls;
      pollTick();               // must be skipped while the first is in flight
      if (tickCalls !== afterFirst) throw new Error('overlapping poll started while in flight');
      await new Promise(r => setTimeout(r, 5));
      if (tickCalls !== afterReturn + 1) throw new Error('timer poll did not run exactly once');

      // Explicit tick() callers are never guarded.
      switchTab('marketdata');
      await tick();
      if (tickCalls < afterReturn + 2) throw new Error('explicit tick() was guarded');
      const afterExplicit = tickCalls;

      // Staleness refresh on visibility return.
      btOscStale = true;
      btOscRefreshIfStale();
      if (tickCalls !== afterExplicit + 1) throw new Error('stale refresh did not poll exactly once');
      console.log('POLL_GUARD_OK');
    })().catch(err => { console.error(err); process.exitCode = 1; });
    """
    result = subprocess.run([node_bin, "-e", harness], capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert result.returncode == 0, f"Node script failed: {result.stderr}\n{result.stdout}"
    assert "POLL_GUARD_OK" in result.stdout


def test_backtest_timer_uses_poll_wrapper():
    """#371: setInterval drives the guarded wrapper, not bare tick."""
    html = client.get("/").text
    assert "setInterval(pollTick, 3000)" in html
    assert "setInterval(tick, 3000)" not in html
    assert "addEventListener('visibilitychange'" in html


def test_consume_backtest_stream_terminal_eof_watchdog_and_comments():
    """#371: with opt-in options the reader returns after final/error, EOF
    without a terminal event fails visibly, callback errors are surfaced (not
    swallowed as bad payloads), comment heartbeats are ignored, and a silent
    connection trips the inactivity watchdog."""
    node_bin = shutil.which("node")
    if node_bin is None:
        pytest.skip("Node.js is not installed")
    html = client.get("/").text
    fn = re.search(r"async function consumeBacktestStream\(.*?\n\}", html, re.DOTALL)
    assert fn is not None, "consumeBacktestStream not found"
    harness = """
    const window = { _btAbort: null };
""" + fn.group(0) + """
    function makeReader(chunks, opts) {
      opts = opts || {};
      let cancelled = false;
      let i = 0;
      return {
        get cancelled() { return cancelled; },
        read() {
          if (i < chunks.length) {
            const v = chunks[i++];
            return Promise.resolve({ done: false, value: new TextEncoder().encode(v) });
          }
          if (opts.endAfterChunks) return Promise.resolve({ done: true });
          return new Promise(() => {}); // never resolves
        },
        cancel() { cancelled = true; return Promise.resolve(); },
      };
    }
    const SEP = String.fromCharCode(13, 10, 13, 10);
    (async () => {
      // (a) final event ends the read even though the socket would never close.
      {
        const events = [];
        const ctl = { signal: { aborted: false } }; window._btAbort = ctl;
        const reader = makeReader(['data: ' + JSON.stringify({type:'final', result:{}}) + SEP]);
        await consumeBacktestStream({ body: { getReader: () => reader } }, ctl, ev => events.push(ev.type),
          { returnOnTerminal: true });
        if (events.length !== 1 || events[0] !== 'final') throw new Error('final not delivered');
        if (!reader.cancelled) throw new Error('reader not cancelled after final');
      }

      // (b) EOF without a terminal event fails visibly (legacy behaviour: silent success).
      {
        let eofFailed = null;
        const ctl = { signal: { aborted: false } }; window._btAbort = ctl;
        const reader = makeReader([], { endAfterChunks: true });
        await consumeBacktestStream({ body: { getReader: () => reader } }, ctl, () => {},
          { failOnEofWithoutTerminal: true, onEof: () => { eofFailed = 'Stream ended without a result'; } });
        if (eofFailed === null) throw new Error('EOF without terminal did not fail');
      }

      // (c) A render callback throwing while handling final is surfaced, not swallowed.
      {
        let cbErr = null;
        const ctl = { signal: { aborted: false } }; window._btAbort = ctl;
        const reader = makeReader(['data: ' + JSON.stringify({type:'final', result:{}}) + SEP]);
        await consumeBacktestStream({ body: { getReader: () => reader } }, ctl,
          () => { throw new Error('render bug'); },
          { returnOnTerminal: true, onCallbackError: (ev, e) => { cbErr = e.message; } });
        if (cbErr !== 'render bug') throw new Error('callback error suppressed: ' + cbErr);
      }

      // (d) SSE comment heartbeats are ignored; data after them still parsed.
      {
        const events = [];
        const ctl = { signal: { aborted: false } }; window._btAbort = ctl;
        const reader = makeReader([': ping' + SEP, 'data: ' + JSON.stringify({type:'progress'}) + SEP], { endAfterChunks: true });
        await consumeBacktestStream({ body: { getReader: () => reader } }, ctl, ev => events.push(ev.type), {});
        if (events.length !== 1 || events[0] !== 'progress') throw new Error('comment line broke parsing');
      }

      // (e) Inactivity watchdog: a silent reader fails within the timeout.
      {
        const ctl = { signal: { aborted: false }, abort() { this.signal.aborted = true; } }; window._btAbort = ctl;
        let stalled = false;
        const reader = makeReader([]); // never resolves
        await consumeBacktestStream({ body: { getReader: () => reader } }, ctl, () => {},
          { inactivityTimeoutMs: 30, onStall: () => { stalled = true; } });
        if (!stalled) throw new Error('silent connection did not trip the watchdog');
        if (!ctl.signal.aborted) throw new Error('watchdog did not abort the controller');
        if (!reader.cancelled) throw new Error('watchdog did not cancel the reader');
      }
      console.log('STREAM_LIFECYCLE_OK');
    })().catch(err => { console.error(err); process.exitCode = 1; });
    """
    result = subprocess.run([node_bin, "-e", harness], capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, f"Node script failed: {result.stderr}\n{result.stdout}"
    assert "STREAM_LIFECYCLE_OK" in result.stdout


def test_render_coalescing_one_scheduled_render_per_interval():
    """#371: several progress envelopes within one interval produce exactly one
    scheduled render, always with the latest envelope, and a superseded/finished
    run (window._btAbort !== btRenderToken) never renders."""
    node_bin = shutil.which("node")
    if node_bin is None:
        pytest.skip("Node.js is not installed")
    html = client.get("/").text
    block = re.search(
        r"// #371: render coalescing state.*?function btCancelScheduledRender\(\)\{.*?\n\}",
        html, re.DOTALL)
    assert block is not None, "render coalescing block not found"
    appendFn = re.search(r"function btAppendProvisionalPoints\(msg\)\{.*?\n\}", html, re.DOTALL)
    assert appendFn is not None
    harness = """
    const window = { _btAbort: null };
    let pendingTimeout = null;
    let pendingTimeoutFn = null;
    const setTimeout = (fn, ms) => { pendingTimeoutFn = fn; pendingTimeout = ms; return 1; };
    const clearTimeout = (id) => { if (id === 1) { pendingTimeoutFn = null; pendingTimeout = null; } };
    let pendingRaf = null;
    const requestAnimationFrame = (fn) => { pendingRaf = fn; return 2; };
    const cancelAnimationFrame = (id) => { if (id === 2) pendingRaf = null; };
    let renderCount = 0, renderedMsg = null;
    const btProvisionalChart = { data: { datasets: [{ data: [] }], labels: [] } }; // enough shape for the data push
    function btRenderProvisional(msg) { renderCount += 1; renderedMsg = msg; }
""" + block.group(0) + "\n" + appendFn.group(0) + """
    const env1 = { points: [{ cumulative_pnl_cents: 1 }], windows_done: 1 };
    const env2 = { points: [{ cumulative_pnl_cents: 2 }], windows_done: 2 };
    const env3 = { points: [{ cumulative_pnl_cents: 3 }], windows_done: 3 };
    const ctl = {};
    window._btAbort = ctl;
    btRenderToken = ctl;
    btAppendProvisionalPoints(env1);
    btAppendProvisionalPoints(env2);
    btAppendProvisionalPoints(env3);
    if (renderCount !== 0) throw new Error('rendered before the interval gate');
    if (pendingTimeoutFn === null) throw new Error('no render was scheduled');
    if (pendingTimeout > 500) throw new Error('interval gate exceeded ~500ms: ' + pendingTimeout);
    pendingTimeoutFn();          // timer fires
    if (typeof pendingRaf !== 'function') throw new Error('raf not requested');
    pendingRaf();                // frame paints
    if (renderCount !== 1) throw new Error('expected exactly one coalesced render, got ' + renderCount);
    if (renderedMsg !== env3) throw new Error('render did not use the latest envelope');

    // Ownership: after the run finishes (token cleared), a late frame never renders.
    btAppendProvisionalPoints(env1);
    btRenderToken = null;
    pendingTimeoutFn();
    if (typeof pendingRaf !== 'function') throw new Error('late frame was not scheduled');
    pendingRaf();
    if (renderCount !== 1) throw new Error('late frame rendered after run finished');

    // Cancel path drops pending work.
    btRenderToken = ctl;
    btAppendProvisionalPoints(env2);
    btCancelScheduledRender();
    if (pendingTimeoutFn !== null) throw new Error('cancel left a pending timer');
    console.log('RENDER_COALESCE_OK');
    """
    result = subprocess.run([node_bin, "-e", harness], capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert result.returncode == 0, f"Node script failed: {result.stderr}\n{result.stdout}"
    assert "RENDER_COALESCE_OK" in result.stdout


def test_bt_hist_single_pass_min_max():
    """#371: the provisional histogram finds min/max in one pass — no spread
    over up to 2000 values four times a second — and keeps the label text."""
    html = client.get("/").text
    assert "Math.min(...vals)" not in html, "histogram still spreads vals for min/max"
    assert "provisional n=" in html, "provisional label text changed"


def test_mark_backtest_failed_shows_visible_message():
    """#371: failures render the message text in a visible Backtest element,
    not only red '--' and 'Failed'."""
    node_bin = shutil.which("node")
    if node_bin is None:
        pytest.skip("Node.js is not installed")
    html = client.get("/").text
    fn = re.search(r"function markBacktestFailed\(errMsg\)\{.*?\n\}", html, re.DOTALL)
    assert fn is not None
    harness = f"""
    const elements = {{
      btHash: {{ textContent: '' }},
      btLastRunTime: {{ textContent: '' }},
      btElapsedTime: {{ textContent: '', style: {{}} }},
      btElapsedSub: {{ textContent: 'Simulating…' }},
      btCardPairCost: {{ textContent: '$0.000', style: {{}} }},
    }};
    const $ = (id) => elements[id] || null;
    {fn.group(0)}
    markBacktestFailed('Another backtest or sweep is already running on the server.');
    if (!elements.btCardPairCost.textContent.includes('already running')) throw new Error('message text not shown in a visible element');
    if (!elements.btHash.textContent.includes('Backtest error:')) throw new Error('hash line lost');
    if (elements.btElapsedSub.textContent !== 'Failed') throw new Error('failed state lost');
    console.log('MARK_FAILED_OK');
    """
    result = subprocess.run([node_bin, "-e", harness], capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert result.returncode == 0, f"Node script failed: {result.stderr}\n{result.stdout}"
    assert "MARK_FAILED_OK" in result.stdout


def test_backtest_429_visible_message_and_no_final_sleep():
    """#371: the last 429 attempt surfaces an actionable message instead of a
    silent give-up, and no sleep runs after the final attempt."""
    html = client.get("/").text
    assert "Another backtest or sweep is already running on the server. Wait for it to finish, then press Run again." in html
    # No sleep after the last attempt: the delay is gated on attempt < 3.
    assert "if (attempt < 3) await new Promise(r => setTimeout(r, 300));" in html
    # The old unconditional sleep must be gone.
    assert "if (res.status !== 429 || window._btAbort === null) break;\n      await new Promise" not in html
    # The 429 path routes through markBacktestFailed.
    assert "markBacktestFailed(lastErrorText ||" in html


def test_sweep_visual_destroys_detached_chart_instances():
    """#371: repeated renderSweepVisual calls must destroy the previous sweep
    charts even though grid.innerHTML='' detaches their canvases before the
    id-based lookup runs — otherwise Chart.instances grows without bound
    (1540 dead instances measured) until the main thread freezes."""
    node_bin = shutil.which("node")
    if node_bin is None:
        pytest.skip("Node.js is not installed")
    html = client.get("/").text
    render_fn = re.search(
        r"function renderSweepVisual\(.*?\n\}\n\n\n// Statistical Summary Charts",
        html, re.DOTALL)
    destroy_instance_fn = re.search(r"function destroyChartInstance\(canvasId\)\{.*?\n\}", html, re.DOTALL)
    destroy_fn = re.search(r"function destroyChart\(canvas\)\{.*?\n\}", html, re.DOTALL)
    assert render_fn is not None
    assert destroy_instance_fn is not None
    assert destroy_fn is not None
    harness = """
    // ---- minimal DOM: only what renderSweepVisual touches ----
    const collectCanvases = node => {
      const out = [];
      const walk = n => (n.children || []).forEach(c => { if (c.tagName === 'CANVAS' && c.id) out.push(c); walk(c); });
      walk(node);
      return out;
    };
    const makeBox = () => {
      const b = { children: [], style: {} };
      let htmlVal = '';
      Object.defineProperty(b, 'innerHTML', { get: () => htmlVal, set: v => { htmlVal = String(v); b.children.length = 0; } });
      b.appendChild = c => b.children.push(c);
      return b;
    };
    const elements = {
      btSweepMeta: makeBox(),
      btSweepAggCard: { setAttribute: () => {} },
      btSweepGrid: null,
    };
    elements.chartSweepAgg = (() => {
      const cv = { tagName: 'CANVAS', id: 'chartSweepAgg', height: 0, style: {}, children: [] };
      cv.getContext = () => { if (!cv._ctx) cv._ctx = { canvas: cv }; return cv._ctx; };
      return cv;
    })();
    const grid = makeBox();
    grid.querySelectorAll = sel => sel === 'canvas[id]' ? collectCanvases(grid) : [];
    elements.btSweepGrid = grid;
    const document = {
      getElementById: id => elements[id] || null,
      createElement: tag => {
        const el = { tagName: String(tag).toUpperCase(), children: [], style: {}, className: '', tabIndex: 0, textContent: '' };
        el.setAttribute = (k, v) => { el.attributes = el.attributes || {}; el.attributes[k] = v; if (k === 'id') el.id = v; };
        el.addEventListener = () => {};
        el.appendChild = c => el.children.push(c);
        if (el.tagName === 'CANVAS') el.getContext = () => { if (!el._ctx) el._ctx = { canvas: el }; return el._ctx; };
        return el;
      },
      addEventListener: () => {},
      activeElement: null,
    };
    // ---- Chart.js mock: getChart(idString) resolves through the live DOM, so
    // it CANNOT find a chart whose canvas was detached — the real leak path.
    const chartByCanvas = new Map();
    function Chart(ctx, config) {
      const canvas = ctx.canvas;
      this.canvas = canvas;
      this.id = 'c' + (++Chart._next);
      Chart.instances[this.id] = this;
      chartByCanvas.set(canvas, this);
    }
    Chart.instances = {};
    Chart._next = 0;
    Chart.getChart = key => {
      if (typeof key === 'string') {
        const cv = document.getElementById(key);
        return cv ? chartByCanvas.get(cv) : undefined;
      }
      return chartByCanvas.get(key);
    };
    Chart.prototype.destroy = function () {
      delete Chart.instances[this.id];
      chartByCanvas.delete(this.canvas);
    };
    // ---- stubs for page helpers the extracted code calls ----
    const window = {};
    const performance = { now: () => 0 };
    const $ = id => elements[id] || null;
    const getThemeTokens = () => ({ gold: 'g', up: 'u', down: 'd', line: 'l', dim: 'm', faint: 'f', proj: 'p' });
    const fmtElapsed = () => '';
    const setupBtChartDialog = () => {};
    const sweepAxisLabel = () => 'axis';
    const sweepCard = () => '';
    const sweepCardTail = () => '';
    const sweepChartOptions = () => ({});
    const sweepChartColors = () => [];
    const sweepZeroLinePlugin = () => ({});
    {DESTROY_INSTANCE_FN}
    {DESTROY_FN}
    {RENDER_FN}
    const data = {
      axis: 'queue',
      points: [
        { value: 10, overall: { total_pnl_cents: -3150 }, per_series: { BTC5m: -2000, ETH5m: -1150 } },
        { value: 20, overall: { total_pnl_cents: -2100 }, per_series: { BTC5m: -1500, ETH5m: -600 } }
      ],
      series_order: ['BTC5m', 'ETH5m'],
      series_labels: { BTC5m: 'BTC 5m', ETH5m: 'ETH 5m' },
      best_overall: { label: 'queue=20', total_pnl_cents: -2100 },
      best_market: { series: 'BTC5m', point_label: 'queue=20', total_pnl_cents: -1500 },
      n_windows: 1470
    };
    // A real sweep re-renders the grid on every progress event.
    for (let i = 0; i < 12; i++) renderSweepVisual(data, 'golden/ticks.jsonl', false);
    const liveCount = Object.keys(Chart.instances).length;
    const attachedCanvases = collectCanvases(grid).concat([elements.chartSweepAgg]);
    if (liveCount !== attachedCanvases.length) {
      throw new Error('instance leak: ' + liveCount + ' live charts for ' + attachedCanvases.length + ' canvases (expected 3)');
    }
    for (const id of Object.keys(Chart.instances)) {
      if (!attachedCanvases.includes(Chart.instances[id].canvas)) {
        throw new Error('chart ' + id + ' survived on a detached canvas');
      }
    }
    console.log('SWEEP_CHART_TEARDOWN_OK');
    """
    harness = (harness
               .replace("{DESTROY_INSTANCE_FN}", destroy_instance_fn.group(0))
               .replace("{DESTROY_FN}", destroy_fn.group(0))
               .replace("{RENDER_FN}", render_fn.group(0).replace("\n\n\n// Statistical Summary Charts", "")))
    result = subprocess.run([node_bin, "-e", harness], capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert result.returncode == 0, f"Node script failed: {result.stderr}\n{result.stdout}"
    assert "SWEEP_CHART_TEARDOWN_OK" in result.stdout

