"""Issue #155 — Recent Windows entry-relative MAX UP/DOWN, RESULT pill, SERIES link.

The table renders client-side in JS (`renderOscillation` in `server/osc_dash.py`),
so these tests assert on the render-source fragments inside `GET /` HTML,
following the `test_orders_trades_table.py` pattern.
"""
from fastapi.testclient import TestClient
from server.osc_dash import app

client = TestClient(app)


def _block():
    response = client.get("/")
    assert response.status_code == 200
    start = response.text.find("Recent Windows")
    assert start != -1, "Recent Windows render block missing from dashboard HTML"
    end = response.text.find("</tbody></table>", start)
    assert end != -1, "Recent Windows table close missing from dashboard HTML"
    return response.text[start:end]


def test_header_has_result_and_no_window_or_link_columns():
    """C1: 7 columns — Result added, Window and Link headers gone."""
    block = _block()
    assert "<th>Result</th>" in block
    assert "<th>Window</th>" not in block
    assert "<th>Link</th>" not in block


def test_series_cell_is_polymarket_hyperlink_with_time_range():
    """C4: SERIES cell links to the window URL and shows a start->end time range."""
    block = _block()
    assert "startsWith('https://')" in block
    assert "target=\"_blank\" rel=\"noopener\"" in block
    assert "w.start_ts" in block
    assert "w.end_ts" in block
    assert "Open \u2197" not in block


def test_max_up_down_are_entry_relative():
    """C2: excursion measured from OPEN (mx-sm / sm-mn), not from the 0.50 base."""
    block = _block()
    assert "(mx-sm)" in block
    assert "(sm-mn)" in block
    assert "w.max_up||0" not in block
    assert "w.max_down||0" not in block


def test_exit_highlight_threshold_at_five_cents():
    """C2: highlight emphasis applies at the $0.05 exit level."""
    block = _block()
    assert "upDelta>=0.05" in block
    assert "downDelta>=0.05" in block


def test_result_pill_up_down_null():
    """C3: green UP / red DOWN pill via pill() helper, '-' on null close."""
    block = _block()
    assert "pill('pill-osc','UP')" in block
    assert "pill('pill-mono','DOWN')" in block
    assert "cm==null" in block
