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


def test_js_helpers_presence_and_execution():
    """Verify formatSignedMoneyPct, groupOrdersByPair, groupPositionsByPair exist in the served script."""
    import json
    import subprocess

    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    # Extract JS script
    script_start = html.find("<script>")
    script_end = html.rfind("</script>")
    assert script_start != -1 and script_end != -1
    js_code = html[script_start + len("<script>"):script_end]

    # Node runner script testing pure JS helper functions
    test_harness = f"""
    // Mock browser globals & prevent event loop hanging
    const setInterval = () => 0;
    const clearInterval = () => {{}};
    const setTimeout = () => 0;
    const clearTimeout = () => {{}};
    const fetch = () => Promise.resolve({{ ok: true, json: async () => ({{}}) }});
    const EventSource = class {{ constructor() {{}} addEventListener() {{}} close() {{}} }};

    const makeElem = () => ({{
      classList: {{
        add: () => {{}},
        remove: () => {{}},
        toggle: () => {{}}
      }},
      addEventListener: () => {{}},
      querySelectorAll: () => [],
      value: ''
    }});
    const window = {{ selectedBacktestFile: '', addEventListener: () => {{}}, location: {{ search: '' }} }};
    globalThis.window = window;
    const document = {{
      getElementById: makeElem,
      querySelectorAll: () => []
    }};
    const localStorage = {{
      _data: {{}},
      getItem(k) {{ return this._data[k] || null; }},
      setItem(k, v) {{ this._data[k] = String(v); }}
    }};

    {js_code}

    // 1. Test formatSignedMoneyPct
    if (typeof formatSignedMoneyPct !== 'function') throw new Error('formatSignedMoneyPct missing');
    const posFmt = formatSignedMoneyPct(0.20, 4.2);
    if (posFmt !== '+$0.20 (+4.2%)') throw new Error('Positive fmt mismatch: ' + posFmt);

    const negFmt = formatSignedMoneyPct(-0.25, -25.0);
    if (negFmt !== '-$0.25 (-25.0%)') throw new Error('Negative fmt mismatch: ' + negFmt);
    if (negFmt.includes('$-')) throw new Error('Corrupt money sign $-: ' + negFmt);

    const zeroFmt = formatSignedMoneyPct(0, 0);
    if (zeroFmt !== '$0.00 (0.0%)') throw new Error('Zero fmt mismatch: ' + zeroFmt);

    const nullFmt = formatSignedMoneyPct(null, null);
    if (nullFmt !== '--') throw new Error('Null fmt mismatch: ' + nullFmt);

    // 2. Test groupOrdersByPair
    if (typeof groupOrdersByPair !== 'function') throw new Error('groupOrdersByPair missing');
    const sampleOrders = [
      {{ order_id: '1', market: 'BTC 5m', side: 'BUY (DOWN)', price: 0.48, size: 5, status: 'OPEN', time: '14:00:01' }},
      {{ order_id: '2', market: 'BTC 5m', side: 'BUY (UP)', price: 0.48, size: 5, status: 'OPEN', time: '14:00:00' }},
      {{ order_id: '3', market: 'SOL 5m', side: 'BUY (UP)', price: 0.47, size: 5, status: 'OPEN', time: '14:00:02' }}
    ];
    const groupedOrders = groupOrdersByPair(sampleOrders);
    if (!groupedOrders['BTC 5m'] || !groupedOrders['SOL 5m']) throw new Error('Grouping key missing');
    if (groupedOrders['BTC 5m'].status !== 'Paired') throw new Error('BTC 5m should be Paired');
    if (groupedOrders['BTC 5m'].pair_cost !== '$0.96') throw new Error('Pair cost should be $0.96, got: ' + groupedOrders['BTC 5m'].pair_cost);
    if (groupedOrders['BTC 5m'].legs[0].side !== 'Up') throw new Error('UP leg must come before DOWN leg');
    if (groupedOrders['SOL 5m'].status !== 'Unpaired') throw new Error('SOL 5m should be Unpaired');

    // 3. Test groupPositionsByPair
    if (typeof groupPositionsByPair !== 'function') throw new Error('groupPositionsByPair missing');
    const samplePos = [
      {{ asset: 'BTC 5m', title: 'BTC 5m', outcome: 'UP', size: 5, avgPrice: 0.48, time: '14:00:00' }},
      {{ asset: 'BTC 5m', title: 'BTC 5m', outcome: 'DOWN', size: 5, avgPrice: 0.48, time: '14:00:05' }}
    ];
    const groupedPos = groupPositionsByPair(samplePos, {{}});
    if (!groupedPos['BTC 5m']) throw new Error('BTC 5m position group missing');
    if (groupedPos['BTC 5m'].status !== 'Paired') throw new Error('Position pair should be Paired');
    // 4. Test tab switching and localStorage persistence
    switchOtTab('positions');
    if (localStorage.getItem('crypto-spread-ot-view') !== 'positions') throw new Error('localStorage failed to save positions');
    switchOtTab('trades');
    if (localStorage.getItem('crypto-spread-ot-view') !== 'trades') throw new Error('localStorage failed to save trades');

    console.log('ALL_JS_TESTS_PASSED');
    process.exit(0);
    """

    res = subprocess.run(["node"], input=test_harness, capture_output=True, text=True, encoding="utf-8", timeout=5)
    assert res.returncode == 0, f"Node test harness failed: {res.stderr}\n{res.stdout}"
    assert "ALL_JS_TESTS_PASSED" in res.stdout


def test_empty_states_sentence_case():
    """Verify all 3 tabs render proper sentence-case empty state descriptions."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    assert "No orders are resting on the book." in html
    assert "No open positions held in account." in html
    assert "No closed trades recorded in this session." in html


def test_cockpit_dom_rendering_with_state():
    """Verify renderCockpitUI updates tab counts, orders, positions, and trade history in DOM."""
    import subprocess

    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    script_start = html.find("<script>")
    script_end = html.rfind("</script>")
    js_code = html[script_start + len("<script>"):script_end]

    test_harness = f"""
    const setInterval = () => 0;
    const clearInterval = () => {{}};
    const setTimeout = () => 0;
    const clearTimeout = () => {{}};
    const fetch = () => Promise.resolve({{ ok: true, json: async () => ({{}}) }});
    const EventSource = class {{ constructor() {{}} addEventListener() {{}} close() {{}} }};

    const elements = {{}};
    function getOrCreate(id) {{
      if (!elements[id]) {{
        elements[id] = {{
          id,
          textContent: '',
          innerHTML: '',
          className: '',
          classList: {{
            classes: new Set(),
            add(c) {{ this.classes.add(c); }},
            remove(c) {{ this.classes.delete(c); }},
            toggle(c, val) {{ if (val) this.classes.add(c); else this.classes.delete(c); }}
          }},
          querySelectorAll: () => [],
          addEventListener: () => {{}},
          style: {{}}
        }};
      }}
      return elements[id];
    }}

    const window = {{ selectedBacktestFile: '', addEventListener: () => {{}}, location: {{ search: '' }} }};
    globalThis.window = window;
    const document = {{
      getElementById: id => getOrCreate(id),
      querySelectorAll: () => []
    }};
    const localStorage = {{
      _data: {{}},
      getItem(k) {{ return this._data[k] || null; }},
      setItem(k, v) {{ this._data[k] = String(v); }}
    }};

    {js_code}

    // Call updateCockpitUI with mock live state
    const mockState = {{
      is_running: true,
      open_orders: [
        {{ order_id: 'ord-1', market: 'BTC 5m', side: 'BUY (UP)', price: 0.48, size: 5, status: 'OPEN', time: '14:05:00' }},
        {{ order_id: 'ord-2', market: 'BTC 5m', side: 'BUY (DOWN)', price: 0.48, size: 5, status: 'OPEN', time: '14:05:01' }}
      ],
      open_positions: [
        {{ asset: 'ETH 5m', title: 'ETH 5m', outcome: 'UP', size: 5, avgPrice: 0.48, curPrice: 0.50, time: '14:01:00' }}
      ],
      trades: [
        {{ timestamp: '14:00:00', label: 'BTC 5m', action: 'PAIR_MERGE', shares: 5, entry_price_up: 0.48, entry_price_down: 0.48, exit_price: 1.00, pnl_usd: 0.20, pnl_pct: 4.2 }}
      ]
    }};

    renderCockpitUI(mockState);

    // Verify Tab count badges updated
    if (elements['otOrdersCount'].textContent !== '2') throw new Error('otOrdersCount should be 2, got: ' + elements['otOrdersCount'].textContent);
    if (elements['otPositionsCount'].textContent !== '1') throw new Error('otPositionsCount should be 1, got: ' + elements['otPositionsCount'].textContent);
    if (elements['otTradesCount'].textContent !== '1') throw new Error('otTradesCount should be 1, got: ' + elements['otTradesCount'].textContent);

    // Verify Orders Body HTML contains pair status and 9 columns
    const ordHtml = elements['cockpitOrdersBody'].innerHTML;
    if (!ordHtml.includes('PAIRED')) throw new Error('Orders body missing PAIRED tag: ' + ordHtml);
    if (!ordHtml.includes('$0.96')) throw new Error('Orders body missing pair cost $0.96: ' + ordHtml);
    if (!ordHtml.includes('14:05:00')) throw new Error('Orders body missing time: ' + ordHtml);

    // Verify Positions Body HTML contains Base Cost and Market Value
    const posHtml = elements['cockpitPositionsBody'].innerHTML;
    if (!posHtml.includes('ETH 5m')) throw new Error('Positions body missing ETH 5m: ' + posHtml);
    if (!posHtml.includes('$0.480')) throw new Error('Positions body missing Base Cost $0.480: ' + posHtml);

    // Verify Trades Body HTML contains Merged cause and signed gain
    const tradesHtml = elements['cockpitTradesBody'].innerHTML;
    if (!tradesHtml.includes('Merged')) throw new Error('Trades body missing Merged cause: ' + tradesHtml);
    if (!tradesHtml.includes('+$0.20 (+4.2%)')) throw new Error('Trades body missing +$0.20 (+4.2%): ' + tradesHtml);

    console.log('ALL_DOM_RENDER_TESTS_PASSED');
    process.exit(0);
    """

    res = subprocess.run(["node"], input=test_harness, capture_output=True, text=True, encoding="utf-8", timeout=5)
    assert res.returncode == 0, f"Node DOM render test failed: {res.stderr}\n{res.stdout}"
    assert "ALL_DOM_RENDER_TESTS_PASSED" in res.stdout

