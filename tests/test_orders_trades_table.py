import shutil
import pytest
from fastapi.testclient import TestClient
from server.osc_dash import app

client = TestClient(app)
NODE_BIN = shutil.which("node")
requires_node = pytest.mark.skipif(not NODE_BIN, reason="Node.js is not installed")


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


@requires_node
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

    // Sub-cent formatting: -0.002 and 0.002 should not format as -$0.00 or +$0.00
    const tinyNeg = formatSignedMoneyPct(-0.002, 0);
    if (tinyNeg !== '$0.00 (0.0%)') throw new Error('Sub-cent negative mismatch: ' + tinyNeg);

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
      {{ asset: 'BTC 5m', title: 'BTC 5m', outcome: 'UP', size: 5, avgPrice: 0.48, cashPnl: 0.20, time: '14:00:00' }},
      {{ asset: 'BTC 5m', title: 'BTC 5m', outcome: 'DOWN', size: 5, avgPrice: 0.48, cashPnl: 0.10, time: '14:00:05' }}
    ];
    const groupedPos = groupPositionsByPair(samplePos, {{}});
    if (!groupedPos['BTC 5m']) throw new Error('BTC 5m position group missing');
    if (groupedPos['BTC 5m'].status !== 'Paired') throw new Error('Position pair should be Paired');
    if (Math.abs(groupedPos['BTC 5m'].realized_usd - 0.30) > 0.001) throw new Error('Realized USD should be 0.30, got: ' + groupedPos['BTC 5m'].realized_usd);

    // Test partial position without curPrice falling back to baseCost instead of false loss
    const partialPos = [
      {{ asset: 'ETH 5m', title: 'ETH 5m', outcome: 'UP', size: 5, avgPrice: 0.48, curPrice: null }},
      {{ asset: 'ETH 5m', title: 'ETH 5m', outcome: 'DOWN', size: 2, avgPrice: 0.48, curPrice: null }}
    ];
    const groupedPartial = groupPositionsByPair(partialPos, {{}});
    if (groupedPartial['ETH 5m'].status !== 'Partial') throw new Error('Should be Partial');
    if (groupedPartial['ETH 5m'].market_val == null || groupedPartial['ETH 5m'].market_val <= 2.00) {{
      throw new Error('Partial market_val should fall back to baseCost, got: ' + groupedPartial['ETH 5m'].market_val);
    }}

    // 4. Test tab switching and localStorage persistence
    switchOtTab('positions');
    if (localStorage.getItem('crypto-spread-ot-view') !== 'positions') throw new Error('localStorage failed to save positions');
    switchOtTab('trades');
    if (localStorage.getItem('crypto-spread-ot-view') !== 'trades') throw new Error('localStorage failed to save trades');

    console.log('ALL_JS_TESTS_PASSED');
    process.exit(0);
    """

    res = subprocess.run([NODE_BIN], input=test_harness, capture_output=True, text=True, encoding="utf-8", timeout=5)
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


@requires_node
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
        {{ asset: 'ETH 5m', title: 'ETH 5m', outcome: 'UP', size: 5, avgPrice: 0.48, curPrice: 0.50, time: '14:01:00' }},
        {{ asset: 'ETH 5m', title: 'ETH 5m', outcome: 'DOWN', size: 5, avgPrice: 0.48, curPrice: 0.50, time: '14:01:01' }}
      ],
      trades: [
        {{ timestamp: '14:00:00', label: 'BTC 5m', action: 'PAIR_MERGE', shares: 5, entry_price_up: 0.48, entry_price_down: 0.48, exit_price: 1.00, pnl_usd: 0.20, pnl_pct: 4.2 }}
      ]
    }};

    renderCockpitUI(mockState);

    // Verify Tab count badges updated
    if (elements['otOrdersCount'].textContent !== '2') throw new Error('otOrdersCount should be 2, got: ' + elements['otOrdersCount'].textContent);
    if (elements['otPositionsCount'].textContent !== '2') throw new Error('otPositionsCount should be 2, got: ' + elements['otPositionsCount'].textContent);
    if (elements['otTradesCount'].textContent !== '1') throw new Error('otTradesCount should be 1, got: ' + elements['otTradesCount'].textContent);

    // Verify Orders Body HTML contains pair status, merged time cell with status border, and no divider on Market cell
    const ordHtml = elements['cockpitOrdersBody'].innerHTML;
    if (!ordHtml.includes('PAIRED')) throw new Error('Orders body missing PAIRED tag: ' + ordHtml);
    if (!ordHtml.includes('$0.96')) throw new Error('Orders body missing pair cost $0.96: ' + ordHtml);
    if (!ordHtml.includes('14:05:00')) throw new Error('Orders body missing time: ' + ordHtml);
    if (!ordHtml.includes('<td rowspan="2" class="mono ot-pair-lead"')) throw new Error('Orders body missing merged Time cell with rowspan="2": ' + ordHtml);
    if (!ordHtml.includes('border-left:2px solid var(--up)')) throw new Error('Orders Time cell missing green status border: ' + ordHtml);
    if (ordHtml.includes('14:05:01')) throw new Error('Secondary order leg should not render duplicate time cell: ' + ordHtml);
    if (ordHtml.includes('class="ot-pair-lead" style="vertical-align:top;border-left:2px solid')) {{
      throw new Error('Orders Market cell should not have border-left divider: ' + ordHtml);
    }}

    // Verify Positions Body HTML contains merged time cell with status border, Base Cost, and Market Value
    const posHtml = elements['cockpitPositionsBody'].innerHTML;
    if (!posHtml.includes('ETH 5m')) throw new Error('Positions body missing ETH 5m: ' + posHtml);
    if (!posHtml.includes('$0.480')) throw new Error('Positions body missing Base Cost $0.480: ' + posHtml);
    if (!posHtml.includes('<td rowspan="2" class="mono ot-pair-lead"')) throw new Error('Positions body missing merged Time cell with rowspan="2": ' + posHtml);
    if (!posHtml.includes('border-left:2px solid var(--up)')) throw new Error('Positions Time cell missing green status border: ' + posHtml);
    if (posHtml.includes('14:01:01')) throw new Error('Secondary position leg should not render duplicate time cell: ' + posHtml);
    if (posHtml.includes('class="ot-pair-lead" style="vertical-align:top;border-left:2px solid')) {{
      throw new Error('Positions Market cell should not have border-left divider: ' + posHtml);
    }}

    // Verify Trades Body HTML contains Merged cause and signed gain
    const tradesHtml = elements['cockpitTradesBody'].innerHTML;
    if (!tradesHtml.includes('Merged')) throw new Error('Trades body missing Merged cause: ' + tradesHtml);
    if (!tradesHtml.includes('+$0.20 (+4.2%)')) throw new Error('Trades body missing +$0.20 (+4.2%): ' + tradesHtml);

    console.log('ALL_DOM_RENDER_TESTS_PASSED');
    process.exit(0);
    """

    res = subprocess.run([NODE_BIN], input=test_harness, capture_output=True, text=True, encoding="utf-8", timeout=5)
    assert res.returncode == 0, f"Node DOM render test failed: {res.stderr}\n{res.stdout}"
    assert "ALL_DOM_RENDER_TESTS_PASSED" in res.stdout


def test_engine_order_resolution_and_cleanup():
    """Verify get_open_orders_list resolves CLOB token IDs to markets and sides, and cancel_live_order clears state."""
    import sys
    from unittest.mock import MagicMock, patch
    from strategy.live_trader import LiveTraderEngine, MarketLiveState

    engine = LiveTraderEngine()
    m = MarketLiveState(
        slug="btc-5m",
        label="BTC 5m",
        color="#f7931a",
        up_token="token_up_123",
        down_token="token_down_456",
        order_id_up="ord_up_999",
        order_status_up="RESTING",
        order_time_up="14:10:00",
    )
    engine.markets["btc-5m"] = m

    mock_client = MagicMock()
    mock_client.get_orders.return_value = [
        {
            "id": "clob_1",
            "asset_id": "token_up_123",
            "side": "BUY",
            "price": 0.48,
            "original_size": 5.0,
            "status": "OPEN",
            "created_at": "2026-09-04T14:15:30Z",
        }
    ]
    engine.get_clob_client = MagicMock(return_value=mock_client)

    dummy_clob_types = MagicMock()
    with patch.dict(sys.modules, {"py_clob_client_v2.clob_types": dummy_clob_types, "py_clob_client.clob_types": dummy_clob_types}):
        orders = engine.get_open_orders_list()
    assert len(orders) == 2  # clob_1 and ord_up_999
    clob_order = next(o for o in orders if o["order_id"] == "clob_1")
    assert clob_order["market"] == "BTC 5m"
    assert "UP" in clob_order["side"]
    assert len(clob_order["time"]) == 8 and ":" in clob_order["time"]

    engine_order = next(o for o in orders if o["order_id"] == "ord_up_999")
    assert engine_order["time"] == "14:10:00"

    # Test single-order cancellation cleans up state in self.markets
    mock_client.cancel.return_value = True
    ok = engine.cancel_live_order("ord_up_999")
    assert ok is True
    assert m.order_id_up is None
    assert m.order_status_up == "CANCELLED"
    assert m.order_time_up == "-"


@requires_node
def test_group_helpers_preserve_slugs():
    """Verify groupOrdersByPair and groupPositionsByPair preserve market_slug and series_slug."""
    import subprocess

    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    script_start = html.find("<script>")
    script_end = html.rfind("</script>")
    js_code = html[script_start + len("<script>"):script_end]

    test_harness = f"""
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
    globalThis.window = {{ addEventListener: () => {{}}, location: {{ search: '' }} }};
    const document = {{ getElementById: id => getOrCreate(id), querySelectorAll: () => [] }};
    const localStorage = {{ getItem: () => null, setItem: () => {{}} }};

    {js_code}

    const testOrders = [
      {{ order_id: '1', market: 'BTC 5m', market_slug: 'btc-updown-5m-1', series_slug: 'btc-5m', side: 'BUY (UP)', price: 0.48, size: 5 }},
      {{ order_id: '2', market: 'BTC 5m', market_slug: 'btc-updown-5m-1', series_slug: 'btc-5m', side: 'BUY (DOWN)', price: 0.48, size: 5 }}
    ];
    const grpOrders = groupOrdersByPair(testOrders);
    const btcOrderGrp = grpOrders['BTC 5m'];
    if (!btcOrderGrp) throw new Error('BTC 5m group missing');
    if (btcOrderGrp.market_slug !== 'btc-updown-5m-1') throw new Error('market_slug missing on order group: ' + btcOrderGrp.market_slug);
    if (btcOrderGrp.series_slug !== 'btc-5m') throw new Error('series_slug missing on order group: ' + btcOrderGrp.series_slug);

    const testPositions = [
      {{ title: 'ETH 5m', market_slug: 'eth-updown-5m-2', series_slug: 'eth-5m', outcome: 'UP', size: 5, avgPrice: 0.48, curPrice: 0.50 }}
    ];
    const grpPositions = groupPositionsByPair(testPositions, {{}});
    const ethPosGrp = grpPositions['ETH 5m'];
    if (!ethPosGrp) throw new Error('ETH 5m position group missing');
    if (ethPosGrp.market_slug !== 'eth-updown-5m-2') throw new Error('market_slug missing on pos group: ' + ethPosGrp.market_slug);
    if (ethPosGrp.series_slug !== 'eth-5m') throw new Error('series_slug missing on pos group: ' + ethPosGrp.series_slug);

    console.log('SLUG_PRESERVATION_TESTS_PASSED');
    process.exit(0);
    """

    res = subprocess.run([NODE_BIN], input=test_harness, capture_output=True, text=True, encoding="utf-8", timeout=5)
    assert res.returncode == 0, f"Node slug preservation test failed: {res.stderr}\n{res.stdout}"
    assert "SLUG_PRESERVATION_TESTS_PASSED" in res.stdout


@requires_node
def test_polymarket_market_hyperlinks_dom():
    """Verify Live Market Matrix, Open Orders, Positions, and Closed Trades render direct Polymarket links."""
    import subprocess

    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    script_start = html.find("<script>")
    script_end = html.rfind("</script>")
    js_code = html[script_start + len("<script>"):script_end]

    test_harness = f"""
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
    globalThis.window = {{ addEventListener: () => {{}}, location: {{ search: '' }} }};
    const document = {{ getElementById: id => getOrCreate(id), querySelectorAll: () => [] }};
    const localStorage = {{ getItem: () => null, setItem: () => {{}} }};

    {js_code}

    const mockState = {{
      is_running: true,
      markets: {{
        'btc-up-or-down-5m': {{
          mid: 0.50,
          spread: 0.02,
          resting_up: 0.48,
          resting_down: 0.48,
          market_slug: 'btc-updown-5m-window-101'
        }}
      }},
      open_orders: [
        {{ order_id: 'ord-1', market: 'BTC 5m', market_slug: 'btc-updown-5m-window-101', series_slug: 'btc-5m', side: 'BUY (UP)', price: 0.48, size: 5, status: 'OPEN', time: '14:05:00' }}
      ],
      open_positions: [
        {{ asset: 'ETH 5m', title: 'ETH 5m', market_slug: 'eth-updown-5m-window-202', series_slug: 'eth-5m', outcome: 'UP', size: 5, avgPrice: 0.48, curPrice: 0.50, time: '14:01:00' }}
      ],
      trades: [
        {{ timestamp: '14:00:00', label: 'SOL 5m', market_slug: 'sol-updown-5m-window-303', slug: 'sol-5m', action: 'PAIR_MERGE', shares: 5, entry_price_up: 0.48, entry_price_down: 0.48, exit_price: 1.00, pnl_usd: 0.20, pnl_pct: 4.2 }}
      ]
    }};

    renderCockpitUI(mockState);

    // 1. Check Matrix Card Header link
    const matrixHtml = elements['cockpitMarketGrid'].innerHTML;
    if (!matrixHtml.includes('https://polymarket.com/market/btc-updown-5m-window-101')) {{
      throw new Error('Matrix grid missing direct Polymarket market link: ' + matrixHtml);
    }}
    if (!matrixHtml.includes('target="_blank"') || !matrixHtml.includes('rel="noopener"')) {{
      throw new Error('Matrix grid link missing target="_blank" or rel="noopener"');
    }}

    // 2. Check Open Orders table link
    const ordHtml = elements['cockpitOrdersBody'].innerHTML;
    if (!ordHtml.includes('https://polymarket.com/market/btc-updown-5m-window-101')) {{
      throw new Error('Orders table missing direct Polymarket market link: ' + ordHtml);
    }}
    if (!ordHtml.includes('target="_blank"') || !ordHtml.includes('rel="noopener"')) {{
      throw new Error('Orders table link missing target="_blank" or rel="noopener"');
    }}

    // 3. Check Positions table link
    const posHtml = elements['cockpitPositionsBody'].innerHTML;
    if (!posHtml.includes('https://polymarket.com/market/eth-updown-5m-window-202')) {{
      throw new Error('Positions table missing direct Polymarket market link: ' + posHtml);
    }}
    if (!posHtml.includes('target="_blank"') || !posHtml.includes('rel="noopener"')) {{
      throw new Error('Positions table link missing target="_blank" or rel="noopener"');
    }}

    // 4. Check Closed Trades table link
    const tradesHtml = elements['cockpitTradesBody'].innerHTML;
    if (!tradesHtml.includes('https://polymarket.com/market/sol-updown-5m-window-303')) {{
      throw new Error('Trades table missing direct Polymarket market link: ' + tradesHtml);
    }}
    if (!tradesHtml.includes('target="_blank"') || !tradesHtml.includes('rel="noopener"')) {{
      throw new Error('Trades table link missing target="_blank" or rel="noopener"');
    }}

    console.log('ALL_HYPERLINK_DOM_TESTS_PASSED');
    process.exit(0);
    """

    res = subprocess.run([NODE_BIN], input=test_harness, capture_output=True, text=True, encoding="utf-8", timeout=5)
    assert res.returncode == 0, f"Node hyperlink DOM test failed: {res.stderr}\n{res.stdout}"
    assert "ALL_HYPERLINK_DOM_TESTS_PASSED" in res.stdout


@requires_node
def test_polymarket_hyperlinks_fallback():
    """Verify fallback to series slug when market_slug is empty or absent."""
    import subprocess

    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    script_start = html.find("<script>")
    script_end = html.rfind("</script>")
    js_code = html[script_start + len("<script>"):script_end]

    test_harness = f"""
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
    globalThis.window = {{ addEventListener: () => {{}}, location: {{ search: '' }} }};
    const document = {{ getElementById: id => getOrCreate(id), querySelectorAll: () => [] }};
    const localStorage = {{ getItem: () => null, setItem: () => {{}} }};

    {js_code}

    const mockState = {{
      is_running: true,
      markets: {{
        'btc-up-or-down-5m': {{
          mid: 0.50,
          spread: 0.02,
          resting_up: 0.48,
          resting_down: 0.48,
          market_slug: '' // pending discovery
        }}
      }},
      open_orders: [
        {{ order_id: 'ord-1', market: 'BTC 5m', series_slug: 'btc-5m', side: 'BUY (UP)', price: 0.48, size: 5, status: 'OPEN', time: '14:05:00' }}
      ],
      open_positions: [
        {{ asset: 'ETH 5m', title: 'ETH 5m', series_slug: 'eth-5m', outcome: 'UP', size: 5, avgPrice: 0.48, curPrice: 0.50, time: '14:01:00' }}
      ],
      trades: [
        {{ timestamp: '14:00:00', label: 'SOL 5m', slug: 'sol-5m', action: 'PAIR_MERGE', shares: 5, entry_price_up: 0.48, entry_price_down: 0.48, exit_price: 1.00, pnl_usd: 0.20, pnl_pct: 4.2 }}
      ]
    }};

    renderCockpitUI(mockState);

    // Verify matrix grid fell back to series slug
    const matrixHtml = elements['cockpitMarketGrid'].innerHTML;
    if (!matrixHtml.includes('https://polymarket.com/market/btc-up-or-down-5m') && !matrixHtml.includes('https://polymarket.com/market/btc-5m')) {{
      throw new Error('Matrix grid fallback missing: ' + matrixHtml);
    }}

    // Verify orders table fell back to series slug
    const ordHtml = elements['cockpitOrdersBody'].innerHTML;
    if (!ordHtml.includes('https://polymarket.com/market/btc-5m')) {{
      throw new Error('Orders table fallback missing: ' + ordHtml);
    }}

    // Verify positions table fell back to series slug
    const posHtml = elements['cockpitPositionsBody'].innerHTML;
    if (!posHtml.includes('https://polymarket.com/market/eth-5m')) {{
      throw new Error('Positions table fallback missing: ' + posHtml);
    }}

    // Verify trades table fell back to series slug
    const tradesHtml = elements['cockpitTradesBody'].innerHTML;
    if (!tradesHtml.includes('https://polymarket.com/market/sol-5m')) {{
      throw new Error('Trades table fallback missing: ' + tradesHtml);
    }}

    console.log('ALL_FALLBACK_TESTS_PASSED');
    process.exit(0);
    """

    res = subprocess.run([NODE_BIN], input=test_harness, capture_output=True, text=True, encoding="utf-8", timeout=5)
    assert res.returncode == 0, f"Node fallback test failed: {res.stderr}\n{res.stdout}"
    assert "ALL_FALLBACK_TESTS_PASSED" in res.stdout


@requires_node
def test_stopped_market_matrix_and_bids_cancelled_dom():
    """Verify stopped-out and timed-out markets render FLAT (STOPPED OUT) and CANCELLED bids in Matrix."""
    import subprocess

    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    script_start = html.find("<script>")
    script_end = html.rfind("</script>")
    js_code = html[script_start + len("<script>"):script_end]

    test_harness = f"""
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
    globalThis.window = {{ addEventListener: () => {{}}, location: {{ search: '' }} }};
    const document = {{ getElementById: id => getOrCreate(id), querySelectorAll: () => [] }};
    const localStorage = {{ getItem: () => null, setItem: () => {{}} }};

    {js_code}

    const mockState = {{
      is_running: true,
      markets: {{
        'btc-up-or-down-5m': {{
          mid: 0.50,
          spread: 0.02,
          resting_up: 0.48,
          resting_down: 0.48,
          status: 'STOP_EXIT',
          exit_taken: true,
          filled_up: true,
          filled_down: false,
          fill_price_up: 0.48
        }},
        'eth-up-or-down-5m': {{
          mid: 0.50,
          spread: 0.02,
          resting_up: 0.48,
          resting_down: 0.48,
          status: 'TIMEOUT_NO_FILL',
          entry_cancelled_timeout: true
        }},
        'sol-up-or-down-5m': {{
          mid: 0.50,
          spread: 0.02,
          resting_up: 0.48,
          resting_down: 0.48,
          status: 'DRIFT_SKIPPED'
        }}
      }},
      open_orders: [],
      open_positions: [],
      trades: []
    }};

    renderCockpitUI(mockState);

    const matrixHtml = elements['cockpitMarketGrid'].innerHTML;

    // BTC 5m stopped out: should render FLAT (STOPPED OUT) and CANCELLED (STOPPED OUT)
    if (!matrixHtml.includes('FLAT (STOPPED OUT)')) {{
      throw new Error('Matrix card missing FLAT (STOPPED OUT): ' + matrixHtml);
    }}
    if (matrixHtml.includes('LONG UP') || matrixHtml.includes('LONG DOWN')) {{
      throw new Error('Matrix card should not show stale LONG UP/DOWN when stopped: ' + matrixHtml);
    }}
    if (!matrixHtml.includes('CANCELLED (STOPPED OUT)')) {{
      throw new Error('Matrix card missing CANCELLED (STOPPED OUT) bids text: ' + matrixHtml);
    }}

    // ETH 5m timeout: should render CANCELLED (10% TIMEOUT)
    if (!matrixHtml.includes('CANCELLED (10% TIMEOUT)')) {{
      throw new Error('Matrix card missing CANCELLED (10% TIMEOUT) bids text: ' + matrixHtml);
    }}

    // SOL 5m drift: should render CANCELLED (ADVERSE DRIFT)
    if (!matrixHtml.includes('CANCELLED (ADVERSE DRIFT)')) {{
      throw new Error('Matrix card missing CANCELLED (ADVERSE DRIFT) bids text: ' + matrixHtml);
    }}

    console.log('STOPPED_MATRIX_TESTS_PASSED');
    process.exit(0);
    """

    res = subprocess.run([NODE_BIN], input=test_harness, capture_output=True, text=True, encoding="utf-8", timeout=5)
    assert res.returncode == 0, f"Node stopped matrix test failed: {res.stderr}\n{res.stdout}"
    assert "STOPPED_MATRIX_TESTS_PASSED" in res.stdout


@requires_node
def test_cancelled_orders_table_rendering_dom():
    """Verify cancelled orders render with CANCELED badge, disabled action button, and proper pair status."""
    import subprocess

    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    script_start = html.find("<script>")
    script_end = html.rfind("</script>")
    js_code = html[script_start + len("<script>"):script_end]

    test_harness = f"""
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
    globalThis.window = {{ addEventListener: () => {{}}, location: {{ search: '' }} }};
    const document = {{ getElementById: id => getOrCreate(id), querySelectorAll: () => [] }};
    const localStorage = {{ getItem: () => null, setItem: () => {{}} }};

    {js_code}

    const mockState = {{
      is_running: true,
      markets: {{}},
      open_orders: [
        // BTC 5m: one open leg, one cancelled leg -> Partial group
        {{ order_id: 'ord-btc-up', market: 'BTC 5m', side: 'BUY (UP)', price: 0.48, size: 5, status: 'OPEN', time: '14:00:00' }},
        {{ order_id: 'ord-btc-down', market: 'BTC 5m', side: 'BUY (DOWN)', price: 0.48, size: 5, status: 'CANCELED', time: '14:00:01' }},
        // ETH 5m: both legs cancelled -> Cancelled group
        {{ order_id: 'ord-eth-up', market: 'ETH 5m', side: 'BUY (UP)', price: 0.48, size: 5, status: 'CANCELLED', time: '14:00:02' }},
        {{ order_id: 'ord-eth-down', market: 'ETH 5m', side: 'BUY (DOWN)', price: 0.48, size: 5, status: 'CANCELED', time: '14:00:03' }}
      ],
      open_positions: [],
      trades: []
    }};

    renderCockpitUI(mockState);

    const ordHtml = elements['cockpitOrdersBody'].innerHTML;

    // BTC 5m should be marked PARTIAL
    if (!ordHtml.includes('PARTIAL')) {{
      throw new Error('Orders table missing PARTIAL tag for mixed group: ' + ordHtml);
    }}
    // ETH 5m should be marked CANCELLED
    if (!ordHtml.includes('CANCELLED')) {{
      throw new Error('Orders table missing CANCELLED tag for all-cancelled group: ' + ordHtml);
    }}

    // ord-btc-up (OPEN) should have a cancel button
    if (!ordHtml.includes('data-order-id="ord-btc-up"')) {{
      throw new Error('ord-btc-up should have an active Cancel button: ' + ordHtml);
    }}

    // ord-btc-down (CANCELED) and ord-eth-* should NOT have cancel buttons
    if (ordHtml.includes('data-order-id="ord-btc-down"')) {{
      throw new Error('Cancelled order ord-btc-down should not have a Cancel button');
    }}
    if (ordHtml.includes('data-order-id="ord-eth-up"')) {{
      throw new Error('Cancelled order ord-eth-up should not have a Cancel button');
    }}
    if (ordHtml.includes('data-order-id="ord-eth-down"')) {{
      throw new Error('Cancelled order ord-eth-down should not have a Cancel button');
    }}

    // Cancelled orders should render with ot-tag-cancelled
    if (!ordHtml.includes('ot-tag-cancelled')) {{
      throw new Error('Orders table missing ot-tag-cancelled class for cancelled orders: ' + ordHtml);
    }}

    // Verify leading Time cell has gold border for Partial (BTC 5m) and dim border for Cancelled (ETH 5m)
    if (!ordHtml.includes('border-left:2px solid var(--gold)')) {{
      throw new Error('Partial order group missing gold border on Time cell: ' + ordHtml);
    }}
    if (!ordHtml.includes('border-left:2px solid var(--dim)')) {{
      throw new Error('Cancelled order group missing dim border on Time cell: ' + ordHtml);
    }}
    if (ordHtml.includes('class="ot-pair-lead" style="vertical-align:top;border-left:2px solid')) {{
      throw new Error('Orders Market cell should not have border-left divider: ' + ordHtml);
    }}

    console.log('CANCELLED_ORDERS_TABLE_DOM_TESTS_PASSED');
    process.exit(0);
    """

    res = subprocess.run([NODE_BIN], input=test_harness, capture_output=True, text=True, encoding="utf-8", timeout=5)
    assert res.returncode == 0, f"Node cancelled orders table test failed: {res.stderr}\n{res.stdout}"
    assert "CANCELLED_ORDERS_TABLE_DOM_TESTS_PASSED" in res.stdout


def test_toast_container_and_css():
    """Verify #toastContainer exists in dashboard DOM and toast CSS styles are defined (Issue #81)."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    # 1. Container element in DOM
    assert 'id="toastContainer"' in html
    assert 'class="toast-container"' in html or "toast-container" in html

    # 2. CSS rules
    assert ".toast-container" in html
    assert ".toast{" in html or ".toast {" in html or ".toast " in html
    assert ".toast-merged" in html
    assert ".toast-stoploss" in html
    assert ".toast-filled" in html
    assert ".toast-close" in html
    assert "z-index:9999" in html or "z-index: 9999" in html

