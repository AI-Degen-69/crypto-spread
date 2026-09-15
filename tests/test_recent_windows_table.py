"""Issue #155 — Recent Windows entry-relative MAX UP/DOWN, RESULT pill, SERIES link.

The table renders client-side in JS (`tick()` in `server/osc_dash.py`).
Fragment tests assert on the render source inside `GET /` HTML; the DOM test
executes the real renderer in Node with mocked `/api/oscillation` data,
following the `test_orders_trades_table.py` harness pattern.
"""
import shutil

import pytest
from fastapi.testclient import TestClient
from server.osc_dash import app

client = TestClient(app)
NODE_BIN = shutil.which("node")
requires_node = pytest.mark.skipif(not NODE_BIN, reason="Node.js is not installed")


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


@requires_node
def test_tick_dom_renders_entry_relative_table():
    """Execute the real tick() renderer with mocked windows; assert the DOM."""
    import json
    import subprocess

    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    script_start = html.find("<script>")
    script_end = html.rfind("</script>")
    assert script_start != -1 and script_end != -1
    js_code = html[script_start + len("<script>"):script_end]

    mock_windows = [
        {
            "series": "btc-up-or-down-5m", "label": "btc 5m", "class": "monotonic",
            "start_mid": 0.54, "max_mid": 0.55, "min_mid": 0.50, "close_mid": 0.56,
            "start_ts": 1789509900, "end_ts": 1789510200,
            "url": "https://polymarket.com/market/btc-updown-5m-1789509900",
        },
        {
            "series": "eth-up-or-down-5m", "label": "eth 5m", "class": "monotonic",
            "start_mid": 0.50, "max_mid": 0.52, "min_mid": 0.44, "close_mid": 0.30,
            "start_ts": 0.0, "end_ts": 0.0,
            "url": "https://polymarket.com/market/eth-updown-5m-1789509900",
        },
        {
            "series": "sol-up-or-down-5m", "label": "sol 5m", "class": "flat",
            "start_mid": None, "max_mid": None, "min_mid": None, "close_mid": None,
            "start_ts": None, "end_ts": None, "url": "javascript:alert(1)",
        },
    ]
    mock_payload = json.dumps({"summary": {}, "windows": mock_windows})

    test_harness = f"""
    const setInterval = () => 0;
    const clearInterval = () => {{}};
    const setTimeout = () => 0;
    const clearTimeout = () => {{}};
    const EventSource = class {{ constructor() {{}} addEventListener() {{}} close() {{}} }};
    const MOCK_OSC = {mock_payload};
    const fetch = (url) => Promise.resolve({{
      ok: true,
      json: async () => String(url).includes('/api/oscillation') ? MOCK_OSC : ({{}}),
    }});

    const elements = {{}};
    function makeElem(id) {{
      return {{
        id, textContent: '', innerHTML: '', className: '', value: '',
        style: {{}}, dataset: {{}},
        classList: {{
          classes: new Set(),
          add(c) {{ this.classes.add(c); }},
          remove(c) {{ this.classes.delete(c); }},
          toggle(c, val) {{ if (val) this.classes.add(c); else this.classes.delete(c); }}
        }},
        querySelectorAll: () => [],
        addEventListener: () => {{}},
      }};
    }}
    function getOrCreate(id) {{
      if (!elements[id]) elements[id] = makeElem(id);
      return elements[id];
    }}

    const window = {{ selectedBacktestFile: '', addEventListener: () => {{}}, location: {{ search: '' }} }};
    globalThis.window = window;
    const document = {{
      getElementById: id => getOrCreate(id),
      querySelectorAll: () => [],
      createElement: () => makeElem('dyn'),
      addEventListener: () => {{}},
    }};
    const localStorage = {{
      _data: {{}},
      getItem(k) {{ return this._data[k] || null; }},
      setItem(k, v) {{ this._data[k] = String(v); }}
    }};

    {js_code}

    (async () => {{
      if (typeof tick !== 'function') throw new Error('tick() renderer missing');
      await tick();
      const tbl = elements['windowsTableWrap'].innerHTML;
      if (!tbl.includes('<th>Result</th>')) throw new Error('missing Result header: ' + tbl.slice(0, 300));
      if (tbl.includes('<th>Window</th>')) throw new Error('stale Window header rendered');
      if (tbl.includes('<th>Link</th>')) throw new Error('stale Link header rendered');
      // Entry-relative: 0.54 open / 0.55 high is +$0.01, never +$0.05, never ++
      if (!tbl.includes('+$0.01')) throw new Error('missing entry-relative +$0.01: ' + tbl.slice(0, 2000));
      if (tbl.includes('++$')) throw new Error('double-sign regression in deltas');
      if (tbl.includes('---')) throw new Error('epoch/empty range must collapse to single -');
      // RESULT pills: UP (0.56), DOWN (0.30), '-' (null close)
      if (!tbl.includes('UP') || !tbl.includes('DOWN')) throw new Error('missing UP/DOWN pills');
      // SERIES hyperlink + https allowlist blocks javascript: URLs
      if (!tbl.includes('https://polymarket.com/market/btc-updown-5m-1789509900'))
        throw new Error('missing Polymarket SERIES link');
      if (tbl.includes('javascript:')) throw new Error('javascript: URL leaked into href');
      console.log('ALL_RECENT_WINDOWS_DOM_TESTS_PASSED');
      process.exit(0);
    }})().catch(e => {{ console.error('DOM harness: ' + (e && e.stack || e)); process.exit(1); }});
    """

    res = subprocess.run([NODE_BIN], input=test_harness, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert res.returncode == 0, f"Node DOM render test failed: {res.stderr}\n{res.stdout}"
    assert "ALL_RECENT_WINDOWS_DOM_TESTS_PASSED" in res.stdout
