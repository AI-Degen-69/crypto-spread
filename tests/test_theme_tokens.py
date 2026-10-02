"""Tests verifying the dashboard design tokens and theme consistency.

Ensures :root is the single source of truth for color definitions in FULL_APP_HTML,
and that Chart.js configs and CSS rules do not carry hardcoded hex literals.
"""

import re
import shutil
import subprocess
import pytest
from server.osc_dash import FULL_APP_HTML

NODE_BIN = shutil.which("node")
requires_node = pytest.mark.skipif(not NODE_BIN, reason="Node.js is not installed")


def test_root_tokens_defined():
    """Verify that :root contains all canonical design tokens."""
    required_tokens = [
        "--bg",
        "--panel",
        "--panel2",
        "--line",
        "--line-hi",
        "--line-dark",
        "--tx",
        "--dim",
        "--faint",
        "--up",
        "--up-hi",
        "--upS",
        "--down",
        "--downS",
        "--gold",
        "--warn",
        "--proj",
        "--cyan",
    ]
    # Locate :root block
    root_match = re.search(r":root\s*\{([^}]+)\}", FULL_APP_HTML)
    assert root_match is not None, "Missing :root definition in FULL_APP_HTML"
    root_content = root_match.group(1)

    for token in required_tokens:
        assert f"{token}:" in root_content, f"Token {token} not defined in :root"


def test_no_hardcoded_hexes_in_style_block():
    """Verify zero hardcoded hex colors exist in <style> outside :root definition."""
    style_match = re.search(r"<style>(.*?)</style>", FULL_APP_HTML, re.DOTALL)
    assert style_match is not None, "Missing <style> block in FULL_APP_HTML"
    style_content = style_match.group(1)

    # Remove :root { ... } declaration
    cleaned_style = re.sub(r":root\s*\{[^}]+\}", "", style_content)

    # Strip CSS comments /* ... */
    cleaned_style = re.sub(r"/\*.*?\*/", "", cleaned_style, flags=re.DOTALL)

    # Find hex color literals: #xxx or #xxxxxx or #xxxxxxxx
    hex_matches = re.findall(r"#[0-9a-fA-F]{3,8}\b", cleaned_style)
    assert not hex_matches, f"Found hardcoded hex literals in CSS rules: {hex_matches}"


def test_theme_helpers_defined():
    """Verify getThemeToken, getThemeTokens, and hexToRgba helpers are present."""
    assert "const getThemeToken = name =>" in FULL_APP_HTML
    assert "const getThemeTokens = () =>" in FULL_APP_HTML
    assert "const hexToRgba = (hex, alpha) =>" in FULL_APP_HTML


def test_no_hardcoded_hexes_in_chartjs_configs():
    """Verify Chart.js instantiations read colors via theme tokens rather than raw hexes."""
    # Find Chart instances
    # 1. equityChartInstance
    equity_chart = re.search(r"equityChartInstance = new Chart\([^)]+\,\s*\{.*?\n\s*\}\);", FULL_APP_HTML, re.DOTALL)
    assert equity_chart is not None, "equityChartInstance not found"
    chart_body = equity_chart.group(0)
    assert "#" not in chart_body, f"Found hardcoded hex in equityChartInstance: {chart_body}"
    assert "theme.up" in chart_body
    assert "theme.down" in chart_body
    assert "theme.dim" in chart_body
    assert "theme.line" in chart_body

    # 2. pnlHistChartInstance
    pnl_chart = re.search(
        r"destroyChartInstance\('chartPnlHist'\);.*?pnlHistChartInstance = new Chart\([^)]+\,\s*\{.*?\n\s*\}\);",
        FULL_APP_HTML,
        re.DOTALL,
    )
    assert pnl_chart is not None, "pnlHistChartInstance block not found"
    pnl_body = pnl_chart.group(0)
    assert "#" not in pnl_body, f"Found hardcoded hex in pnlHistChartInstance: {pnl_body}"
    assert "theme.up" in pnl_body
    assert "theme.down" in pnl_body
    assert "theme.dim" in pnl_body
    assert "theme.line" in pnl_body

    # 3. renderSummaryCharts
    summary_charts = re.search(r"async function renderSummaryCharts\(\)\s*\{.*?^}", FULL_APP_HTML, re.DOTALL | re.MULTILINE)
    assert summary_charts is not None, "renderSummaryCharts not found"
    summary_body = summary_charts.group(0)
    assert "#" not in summary_body, f"Found hardcoded hex in renderSummaryCharts: {summary_body}"
    assert "theme.up" in summary_body
    assert "theme.down" in summary_body
    assert "theme.gold" in summary_body
    assert "theme.proj" in summary_body
    assert "theme.dim" in summary_body


def test_sweep_visual_uses_numeric_axis_and_aligned_market_labels():
    """Verify Sweep Visual renders numeric X/Y points and canonical label metadata."""
    assert 'id="btSweepCard"' in FULL_APP_HTML
    assert 'id="btSweepAxis"' in FULL_APP_HTML
    assert 'id="btnRunSweepVisual"' in FULL_APP_HTML
    assert 'id="chartSweepAgg"' in FULL_APP_HTML
    assert 'id="btSweepGrid"' in FULL_APP_HTML
    assert "type: 'bar'" in FULL_APP_HTML
    assert "parsing: false" in FULL_APP_HTML
    assert "beginAtZero: true" in FULL_APP_HTML
    assert "afterBuildTicks" in FULL_APP_HTML
    assert "xTickLabels" in FULL_APP_HTML
    assert "best_overall" in FULL_APP_HTML
    assert "best_market" in FULL_APP_HTML
    assert "series_labels" in FULL_APP_HTML
    assert "★ BEST MARKET" in FULL_APP_HTML
    assert "Queue depth — shares ahead" in FULL_APP_HTML
    assert "Stop distance — default" in FULL_APP_HTML
    assert "Stop distance — BTC" in FULL_APP_HTML
    assert "Stop distance — SOL" in FULL_APP_HTML
    assert "exit_5m (X = stop distance)" not in FULL_APP_HTML
    assert "Each bar is a separate replay" not in FULL_APP_HTML
    assert "title.textContent = `${marketDisplayName}${isBestMarket ? ' ★ BEST MARKET' : ''}`" in FULL_APP_HTML
    assert "sweepMarketName" in FULL_APP_HTML
    assert "sweepTokenColor" in FULL_APP_HTML
    assert "formatSweepMoneyTick" in FULL_APP_HTML
    assert "window._btRunning = false" in FULL_APP_HTML
    assert "waiting for the selected-file backtest to finish" in FULL_APP_HTML
    assert "exit_default_15m" in FULL_APP_HTML
    assert "window.selectedBacktestFile = el.value" in FULL_APP_HTML
    assert "if (el.tagName === 'SELECT' && (id === 'btFileSelect' || id === 'btMaxStartDelay'))" not in FULL_APP_HTML
    assert "if(!equityChartInstance) runBacktest();" not in FULL_APP_HTML
    assert "Opening the tab is read-only" in FULL_APP_HTML


def test_dropdown_preferred_preselect_wiring():
    """Issue #279: star-marked preferred option + first-load pre-select wiring.

    The parenthesised detail is a window count, not a line count: lines describe
    how the collector wrote the file, windows describe how much research it can
    support. The strongest count the file can back is shown, tagged with the
    readiness tier it earned, so two files are comparable at a glance.
    """
    assert "`${f.is_preferred ? '★ ' : ''}${f.name} (${detail})`" in FULL_APP_HTML
    # No line counts may reappear in the dataset picker.
    assert "linesFormatted" not in FULL_APP_HTML
    assert "lines)`" not in FULL_APP_HTML
    # An unverifiable file says so rather than printing a misleading zero.
    assert "windows unknown — not verified" in FULL_APP_HTML
    assert "research windows" in FULL_APP_HTML
    assert "!window._btFileChosen && d.preferred_file" in FULL_APP_HTML
    assert "window._btFileChosen = false; // Issue #279: flips on any manual dataset pick" in FULL_APP_HTML
    assert "window._btFileChosen = true;" in FULL_APP_HTML
    # Reset to Defaults picks All Files explicitly — also a choice loadManifest keeps.
    assert 'window.selectedBacktestFile = "";\n  window._btFileChosen = true;' in FULL_APP_HTML


def test_preferred_badge_uses_theme_tokens():
    """Issue #279/#294: ★ Preferred (tier 1) and ★ Best available (tier 2) badges via --gold."""
    assert "if (f.is_preferred)" in FULL_APP_HTML
    assert "★ Preferred" in FULL_APP_HTML
    assert "★ Best available" in FULL_APP_HTML
    assert "d.preferred_tier === 1" in FULL_APP_HTML
    assert "color:var(--gold);font-weight:700;font-size:11px;white-space:nowrap;margin-left:6px" in FULL_APP_HTML


def test_component_styles_use_css_variables():
    """Verify specific CSS components use proper semantic CSS variables."""
    # .tbl td uses --line-dark
    assert re.search(r"\.tbl td\{[^}]*border-bottom:[^;]*var\(--line-dark\)", FULL_APP_HTML)
    # .btn-primary uses --up and --bg
    assert re.search(r"\.btn-primary\{[^}]*background:var\(--up\)[^}]*color:var\(--bg\)", FULL_APP_HTML)
    # .btn-primary:hover uses --up-hi
    assert re.search(r"\.btn-primary:hover\{[^}]*background:var\(--up-hi\)", FULL_APP_HTML)
    # .spinner and .thinking-dots use --bg
    assert re.search(r"\.spinner\{[^}]*border-top-color:var\(--bg\)", FULL_APP_HTML)
    assert re.search(r"\.thinking-dots span\{[^}]*background:var\(--bg\)", FULL_APP_HTML)


def test_sweep_visual_options_zero_reference_line():
    """Issue #332: sweepChartOptions contains y-grid callbacks testing tick.value === 0."""
    assert "maintainAspectRatio: !!detail" in FULL_APP_HTML
    assert "ctx.tick && ctx.tick.value === 0" in FULL_APP_HTML
    assert 'height="120"' in FULL_APP_HTML
    assert "cv.height = 95;" in FULL_APP_HTML
    assert "minmax(220px,1fr)" in FULL_APP_HTML


@requires_node
def test_sweep_chart_colors_sign_and_gold_precedence():
    """Issue #332: sweepChartColors colors profit=up, loss=down, zero/dim, and best=gold."""
    start = FULL_APP_HTML.find("function sweepChartColors(")
    assert start != -1, "sweepChartColors function missing from FULL_APP_HTML"
    idx = FULL_APP_HTML.find("{", start)
    depth = 0
    end = idx
    for i in range(idx, len(FULL_APP_HTML)):
        if FULL_APP_HTML[i] == "{":
            depth += 1
        elif FULL_APP_HTML[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    fn_code = FULL_APP_HTML[start:end]

    test_script = f"""
    {fn_code}
    const theme = {{ gold: 'GOLD', up: 'UP', down: 'DOWN', dim: 'DIM', proj: 'PROJ' }};

    // 1. Mixed aggregate: positive, negative, zero, best overall
    const dataMixed = {{
        points: [
            {{ value: 10, overall: {{ total_pnl_cents: 500 }} }},
            {{ value: 20, overall: {{ total_pnl_cents: -300 }} }},
            {{ value: 30, overall: {{ total_pnl_cents: 0 }} }},
            {{ value: 40, overall: {{ total_pnl_cents: 800 }} }}
        ],
        best_overall: {{ value: 40 }}
    }};
    const colorsMixed = sweepChartColors(dataMixed, null, theme);
    if (JSON.stringify(colorsMixed) !== JSON.stringify(['UP', 'DOWN', 'DIM', 'GOLD'])) {{
        throw new Error('Mixed aggregate failed: ' + JSON.stringify(colorsMixed));
    }}

    // 2. All negative aggregate: best overall is least negative, must be GOLD (gold wins over sign)
    const dataAllNeg = {{
        points: [
            {{ value: 10, overall: {{ total_pnl_cents: -500 }} }},
            {{ value: 20, overall: {{ total_pnl_cents: -100 }} }},
            {{ value: 30, overall: {{ total_pnl_cents: -400 }} }}
        ],
        best_overall: {{ value: 20 }}
    }};
    const colorsAllNeg = sweepChartColors(dataAllNeg, null, theme);
    if (JSON.stringify(colorsAllNeg) !== JSON.stringify(['DOWN', 'GOLD', 'DOWN'])) {{
        throw new Error('All negative aggregate failed: ' + JSON.stringify(colorsAllNeg));
    }}

    // 3. Per-market matching seriesKey
    const dataMarket = {{
        points: [
            {{ value: 10, per_series: {{ btc_5m: 200 }} }},
            {{ value: 20, per_series: {{ btc_5m: -100 }} }}
        ],
        best_market: {{ series: 'btc_5m', value: 10 }}
    }};
    const colorsMarket = sweepChartColors(dataMarket, 'btc_5m', theme);
    if (JSON.stringify(colorsMarket) !== JSON.stringify(['GOLD', 'DOWN'])) {{
        throw new Error('Per-market matching failed: ' + JSON.stringify(colorsMarket));
    }}

    // 4. Per-market non-matching best_market series
    const colorsOtherMarket = sweepChartColors(dataMarket, 'eth_5m', theme);
    if (JSON.stringify(colorsOtherMarket) !== JSON.stringify(['DIM', 'DIM'])) {{
        throw new Error('Per-market non-matching failed: ' + JSON.stringify(colorsOtherMarket));
    }}

    console.log('SWEEP_CHART_COLORS_PASSED');
    """

    res = subprocess.run([NODE_BIN], input=test_script, capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert res.returncode == 0, f"Node test failed: {res.stderr}\n{res.stdout}"
    assert "SWEEP_CHART_COLORS_PASSED" in res.stdout


@requires_node
def test_sweep_market_name_formatting():
    """Verify sweepMarketName formats slugs to `<TOKEN> <XXm>` with leading zero for 5m."""
    start = FULL_APP_HTML.find("function sweepMarketName(")
    assert start != -1, "sweepMarketName function missing from FULL_APP_HTML"
    idx = FULL_APP_HTML.find("{", start)
    depth = 0
    end = idx
    for i in range(idx, len(FULL_APP_HTML)):
        if FULL_APP_HTML[i] == "{":
            depth += 1
        elif FULL_APP_HTML[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    fn_code = FULL_APP_HTML[start:end]

    test_script = f"""
    {fn_code}
    const cases = [
        ['btc-up-or-down-5m', 'BTC 05m'],
        ['eth-up-or-down-5m', 'ETH 05m'],
        ['bnb-up-or-down-5m', 'BNB 05m'],
        ['sol-up-or-down-5m', 'SOL 05m'],
        ['xrp-up-or-down-5m', 'XRP 05m'],
        ['btc-up-or-down-15m', 'BTC 15m'],
        ['eth-up-or-down-15m', 'ETH 15m'],
        ['bnb-up-or-down-15m', 'BNB 15m'],
        ['sol-up-or-down-15m', 'SOL 15m'],
        ['xrp-up-or-down-15m', 'XRP 15m'],
        ['custom-slug-abc', 'custom-slug-abc'],
        ['', ''],
    ];

    for (const [input, expected] of cases) {{
        const actual = sweepMarketName(input);
        if (actual !== expected) {{
            throw new Error(`sweepMarketName('${{input}}') returned '${{actual}}', expected '${{expected}}'`);
        }}
    }}
    console.log('SWEEP_MARKET_NAME_PASSED');
    """

    res = subprocess.run([NODE_BIN], input=test_script, capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert res.returncode == 0, f"Node test failed: {res.stderr}\n{res.stdout}"
    assert "SWEEP_MARKET_NAME_PASSED" in res.stdout


@requires_node
def test_sweep_token_color_palette():
    """Verify sweepTokenColor resolves brand colors from ALL_COCKPIT_SERIES without hardcoded hex."""
    start_series = FULL_APP_HTML.find("const ALL_COCKPIT_SERIES = [")
    assert start_series != -1, "ALL_COCKPIT_SERIES missing from FULL_APP_HTML"
    end_series = FULL_APP_HTML.find("];", start_series) + 2
    series_code = FULL_APP_HTML[start_series:end_series]

    start = FULL_APP_HTML.find("function sweepTokenColor(")
    assert start != -1, "sweepTokenColor function missing from FULL_APP_HTML"
    idx = FULL_APP_HTML.find("{", start)
    depth = 0
    end = idx
    for i in range(idx, len(FULL_APP_HTML)):
        if FULL_APP_HTML[i] == "{":
            depth += 1
        elif FULL_APP_HTML[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    fn_code = FULL_APP_HTML[start:end]

    # Helper body itself must not contain literal '#'
    assert "#" not in fn_code, f"sweepTokenColor helper body contains hardcoded hex: {fn_code}"

    test_script = f"""
    {series_code}
    {fn_code}

    const btcColor = ALL_COCKPIT_SERIES.find(s => s.slug === 'btc-up-or-down-5m').color;
    const ethColor = ALL_COCKPIT_SERIES.find(s => s.slug === 'eth-up-or-down-15m').color;
    const solColor = ALL_COCKPIT_SERIES.find(s => s.slug === 'sol-up-or-down-5m').color;
    const bnbColor = ALL_COCKPIT_SERIES.find(s => s.slug === 'bnb-up-or-down-5m').color;
    const xrpColor = ALL_COCKPIT_SERIES.find(s => s.slug === 'xrp-up-or-down-15m').color;

    if (!btcColor || sweepTokenColor('btc-up-or-down-5m') !== btcColor) throw new Error('BTC color mismatch');
    if (!ethColor || sweepTokenColor('eth-up-or-down-15m') !== ethColor) throw new Error('ETH color mismatch');
    if (!solColor || sweepTokenColor('sol-up-or-down-5m') !== solColor) throw new Error('SOL color mismatch');
    if (!bnbColor || sweepTokenColor('bnb-up-or-down-5m') !== bnbColor) throw new Error('BNB color mismatch');
    if (!xrpColor || sweepTokenColor('xrp-up-or-down-15m') !== xrpColor) throw new Error('XRP color mismatch');
    if (sweepTokenColor('unknown-market') !== '') throw new Error('Unknown market must return empty string');

    console.log('SWEEP_TOKEN_COLOR_PASSED');
    """

    res = subprocess.run([NODE_BIN], input=test_script, capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert res.returncode == 0, f"Node test failed: {res.stderr}\n{res.stdout}"
    assert "SWEEP_TOKEN_COLOR_PASSED" in res.stdout


@requires_node
def test_format_sweep_money_tick():
    """Verify formatSweepMoneyTick trims .00 on integer amounts and preserves decimal cents."""
    start = FULL_APP_HTML.find("function formatSweepMoneyTick(")
    assert start != -1, "formatSweepMoneyTick function missing from FULL_APP_HTML"
    idx = FULL_APP_HTML.find("{", start)
    depth = 0
    end = idx
    for i in range(idx, len(FULL_APP_HTML)):
        if FULL_APP_HTML[i] == "{":
            depth += 1
        elif FULL_APP_HTML[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    fn_code = FULL_APP_HTML[start:end]

    test_script = f"""
    {fn_code}
    const cases = [
        [-200, '$-200'],
        [0, '$0'],
        [-0, '$0'],
        [-0.00001, '$0'],
        [12.5, '$12.50'],
        [12.34, '$12.34'],
        [12, '$12'],
        [-50.5, '$-50.50'],
    ];

    for (const [input, expected] of cases) {{
        const actual = formatSweepMoneyTick(input);
        if (actual !== expected) {{
            throw new Error(`formatSweepMoneyTick(${{input}}) returned '${{actual}}', expected '${{expected}}'`);
        }}
    }}
    console.log('FORMAT_SWEEP_MONEY_TICK_PASSED');
    """

    res = subprocess.run([NODE_BIN], input=test_script, capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert res.returncode == 0, f"Node test failed: {res.stderr}\n{res.stdout}"
    assert "FORMAT_SWEEP_MONEY_TICK_PASSED" in res.stdout


