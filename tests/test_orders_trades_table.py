"""Unit and integration tests for the unified Orders & Trades table component (Issue #59)."""
import pytest
from fastapi.testclient import TestClient
from server.osc_dash import app

client = TestClient(app)


def test_unified_orders_trades_card_and_tabs_exist():
    """Verify unified card #orders-trades-card, tab bar, tab buttons, and count pills exist."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    # Unified card container
    assert 'id="orders-trades-card"' in html

    # Tab navigation and buttons
    assert 'class="ot-tabs"' in html or 'ot-tabs' in html
    assert 'id="otTabBtnOrders"' in html
    assert 'id="otTabBtnPositions"' in html
    assert 'id="otTabBtnTrades"' in html

    # Live count badges on tab buttons
    assert 'id="otOrdersCount"' in html
    assert 'id="otPositionsCount"' in html
    assert 'id="otTradesCount"' in html

    # Backward compatible & active table IDs
    assert 'id="cockpitOrdersTable"' in html
    assert 'id="cockpitPositionsTable"' in html
    assert 'id="cockpitTradesTable"' in html


def test_open_orders_columns_and_exclusions():
    """Verify Tab 1 (Open Orders) contains 9 specified columns and excludes Remaining, Queue Ahead, Age, Leg."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    # Find open orders table header block
    assert 'id="cockpitOrdersTable"' in html
    orders_table_start = html.find('id="cockpitOrdersTable"')
    orders_table_end = html.find('</table>', orders_table_start)
    orders_table_html = html[orders_table_start:orders_table_end]

    # Required 9 column headers
    assert "Time" in orders_table_html
    assert "Market" in orders_table_html
    assert "Side" in orders_table_html
    assert "Price" in orders_table_html
    assert "Size" in orders_table_html
    assert "Filled" in orders_table_html
    assert "Total Cost" in orders_table_html
    assert "Status" in orders_table_html
    assert "Action" in orders_table_html

    # Deliberately excluded clutter
    assert "Remaining" not in orders_table_html
    assert "Queue Ahead" not in orders_table_html
    assert "Age" not in orders_table_html
    assert "<th>Leg</th>" not in orders_table_html


def test_positions_columns_and_exclusions():
    """Verify Tab 2 (Positions) contains 8 specified columns and excludes Cost, Leg, Avg Price."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    assert 'id="cockpitPositionsTable"' in html
    pos_table_start = html.find('id="cockpitPositionsTable"')
    pos_table_end = html.find('</table>', pos_table_start)
    pos_table_html = html[pos_table_start:pos_table_end]

    # Required 8 column headers
    assert "Time" in pos_table_html
    assert "Market" in pos_table_html
    assert "Side" in pos_table_html
    assert "Size" in pos_table_html
    assert "Base Cost" in pos_table_html
    assert "Market Value" in pos_table_html
    assert "Unrealized $ (%)" in pos_table_html
    assert "Realized $ (%)" in pos_table_html

    # Deliberately excluded
    assert "<th>Cost</th>" not in pos_table_html
    assert "<th>Leg</th>" not in pos_table_html
    assert "Avg Price" not in pos_table_html
    assert "Avg Buy Price" not in pos_table_html


def test_closed_trades_columns():
    """Verify Tab 3 (Closed Trades) contains 8 specified columns: Time, Market, Cause, Shares, Base Cost, Exit Price, Gain / Loss $ (%), Details."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    assert 'id="cockpitTradesTable"' in html
    trades_table_start = html.find('id="cockpitTradesTable"')
    trades_table_end = html.find('</table>', trades_table_start)
    trades_table_html = html[trades_table_start:trades_table_end]

    # Required 8 column headers
    assert "Time" in trades_table_html
    assert "Market" in trades_table_html
    assert "Cause" in trades_table_html
    assert "Shares" in trades_table_html
    assert "Base Cost" in trades_table_html
    assert "Exit Price" in trades_table_html
    assert "Gain / Loss $ (%)" in trades_table_html
    assert "Details" in trades_table_html
