"""Tests verifying the dashboard design tokens and theme consistency.

Ensures :root is the single source of truth for color definitions in FULL_APP_HTML,
and that Chart.js configs and CSS rules do not carry hardcoded hex literals.
"""

import re
from server.osc_dash import FULL_APP_HTML


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
