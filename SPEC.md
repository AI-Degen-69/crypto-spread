# Spec: Collapsible Hover-Expandable Sidebar Navigation (Issue #65)

## 1. Objective
Redesign the primary dashboard navigation in `server/osc_dash.py` by replacing the horizontal tabs in the top header with a modern, collapsible vertical sidebar menu:
1. **Collapsed State (`48px` / `w-12`)**: A slim vertical strip anchored to the left of the viewport (`height: 100vh`, `position: fixed`, `z-index: 100`) showing centered, crisp SVG icons with tooltips (`title` attribute).
2. **Hover / Expanded State (`220px`)**: Smoothly expands on hover (`transition: width 0.25s cubic-bezier(0.16, 1, 0.3, 1)`) or when toggled/pinned, unveiling left-aligned tab labels with zero text wrap (`white-space: nowrap`).
3. **Pinning / Toggle Support**: A top toggle button (`#sidebarToggleBtn`) allows the operator to pin the sidebar open (persisting state via `localStorage.getItem('cui_sidebar_pinned')`).
4. **Header Streamlining**: Remove `.nav-tabs` from `#app-hdr`, leaving a clean, balanced header focused on branding/title on the left and system telemetry badges (`#collectorBadge`, `#tapeBadge`) and polling controls on the right.
5. **Main Content Offset**: The main application content (`.wrap` / `#app-main`) adjusts its left margin (`margin-left: 48px`, or `220px` when pinned) to prevent any content occlusion.
6. **100% Backward Compatibility**: Preserve all existing tab button IDs (`tab-btn-cockpit`, `tab-btn-live`, `tab-btn-backtest`, `tab-btn-summary`, `tab-btn-ticks`) and onclick handlers (`switchTab(...)`).

---

## 2. Tech Stack & Dependencies
- **Backend Framework**: FastAPI + Uvicorn (Python 3.10+)
- **Frontend Architecture**: Embedded Vanilla ES6 JavaScript + Modern CSS3 custom properties in `server/osc_dash.py` (zero external CDN or build dependencies)
- **Testing Framework**: `pytest`, `pytest-asyncio`, FastAPI `TestClient` in `tests/test_osc_dash_integration.py`

---

## 3. Commands
- Run all tests:
  ```powershell
  python -m pytest -q
  ```
- Run Dashboard integration tests:
  ```powershell
  python -m pytest tests/test_osc_dash_integration.py -q
  ```
- Start Dashboard server:
  ```powershell
  python -m uvicorn server.osc_dash:app --host 127.0.0.1 --port 8802
  ```

---

## 4. Project Structure & Touchpoints
```
server/
  osc_dash.py                  -> Add sidebar CSS, inject <aside id="app-sidebar">, clean #app-hdr, update layout offset, add toggle JS
tests/
  test_osc_dash_integration.py -> Update nav tests, assert #app-sidebar, assert SVG icons, assert tab switching
SPEC.md                        -> This specification document
```

---

## 5. Implementation Details & Code Style

### 5.1 Sidebar Layout & Structure (`server/osc_dash.py`)
Add `<aside class="cui-sidebar" id="app-sidebar">` directly inside `<body>` before `#app-hdr`:

```html
<aside class="cui-sidebar" id="app-sidebar" aria-label="Main Navigation">
  <!-- Top: Pin/Toggle Button & Logo Symbol -->
  <div class="sidebar-header">
    <button class="sidebar-toggle-btn" id="sidebarToggleBtn" onclick="toggleSidebarPin()" title="Pin/Unpin Sidebar" aria-label="Toggle Sidebar Navigation">
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
        <line x1="9" y1="3" x2="9" y2="21"></line>
      </svg>
    </button>
    <div class="sidebar-brand-text">CRYPTO SPREAD</div>
  </div>

  <!-- Middle: Primary Nav Tabs -->
  <nav class="sidebar-nav">
    <button class="sidebar-tab-btn active" id="tab-btn-cockpit" onclick="switchTab('cockpit')" title="Live Trading Cockpit">
      <span class="nav-icon">
        <!-- Lightning Bolt SVG -->
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
        </svg>
      </span>
      <span class="nav-label">Live Trading Cockpit</span>
    </button>

    <button class="sidebar-tab-btn" id="tab-btn-live" onclick="switchTab('live')" title="Live Books & Queue">
      <span class="nav-icon">
        <!-- Radar / Signal SVG -->
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M4.93 4.93a10 10 0 0 1 14.14 0"></path>
          <path d="M7.76 7.76a6 6 0 0 1 8.48 0"></path>
          <circle cx="12" cy="12" r="2"></circle>
          <path d="M12 14v7"></path>
        </svg>
      </span>
      <span class="nav-label">Live Books & Queue</span>
    </button>

    <button class="sidebar-tab-btn" id="tab-btn-backtest" onclick="switchTab('backtest')" title="Backtest Sweeper">
      <span class="nav-icon">
        <!-- Trending Chart SVG -->
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="22 7 13.5 15.5 8.5 10.5 2 17"></polyline>
          <polyline points="16 7 22 7 22 13"></polyline>
        </svg>
      </span>
      <span class="nav-label">Backtest Sweeper</span>
    </button>

    <button class="sidebar-tab-btn" id="tab-btn-summary" onclick="switchTab('summary')" title="Stats Summary">
      <span class="nav-icon">
        <!-- Bar Chart SVG -->
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <line x1="18" y1="20" x2="18" y2="10"></line>
          <line x1="12" y1="20" x2="12" y2="4"></line>
          <line x1="6" y1="20" x2="6" y2="14"></line>
        </svg>
      </span>
      <span class="nav-label">Stats Summary</span>
    </button>

    <button class="sidebar-tab-btn" id="tab-btn-ticks" onclick="switchTab('ticks')" title="Tick Files">
      <span class="nav-icon">
        <!-- Hard Drive / Database SVG -->
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <ellipse cx="12" cy="5" rx="9" ry="3"></ellipse>
          <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"></path>
          <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"></path>
        </svg>
      </span>
      <span class="nav-label">Tick Files</span>
    </button>
  </nav>

  <div class="sidebar-divider"></div>

  <!-- Utility Links -->
  <div class="sidebar-nav sidebar-utility">
    <a href="https://polymarket.com" target="_blank" rel="noopener noreferrer" class="sidebar-link-btn" title="Polymarket CLOB">
      <span class="nav-icon">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="10"></circle>
          <line x1="2" y1="12" x2="22" y2="12"></line>
          <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path>
        </svg>
      </span>
      <span class="nav-label">Polymarket Venue</span>
    </a>
    <a href="https://github.com/AI-Degen-69/crypto-spread" target="_blank" rel="noopener noreferrer" class="sidebar-link-btn" title="GitHub Repository">
      <span class="nav-icon">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22"></path>
        </svg>
      </span>
      <span class="nav-label">GitHub Repo</span>
    </a>
  </div>

  <!-- Bottom: Connection / Bot Status Pill -->
  <div class="sidebar-footer">
    <div class="sidebar-status-pill" id="sidebarBotStatusPill" title="Trading Bot Engine Status">
      <span class="status-indicator-dot" id="sidebarStatusDot"></span>
      <span class="status-indicator-text" id="sidebarStatusText">ENGINE IDLE</span>
    </div>
  </div>
</aside>
```

### 5.2 CSS Architecture
```css
:root {
  --sidebar-w-collapsed: 48px;
  --sidebar-w-expanded: 220px;
}

.cui-sidebar {
  position: fixed;
  top: 0;
  left: 0;
  bottom: 0;
  width: var(--sidebar-w-collapsed);
  background: var(--bg2);
  border-right: 1px solid var(--line);
  z-index: 1000;
  display: flex;
  flex-direction: column;
  transition: width 0.25s cubic-bezier(0.16, 1, 0.3, 1);
  overflow: hidden;
  box-shadow: 2px 0 8px rgba(0, 0, 0, 0.2);
}

.cui-sidebar:hover,
.cui-sidebar.pinned {
  width: var(--sidebar-w-expanded);
}

body {
  padding-left: var(--sidebar-w-collapsed);
  transition: padding-left 0.25s cubic-bezier(0.16, 1, 0.3, 1);
}

body.sidebar-pinned {
  padding-left: var(--sidebar-w-expanded);
}

.sidebar-header {
  height: 48px;
  display: flex;
  align-items: center;
  padding: 0 12px;
  gap: 12px;
  border-bottom: 1px solid var(--line);
  flex-shrink: 0;
}

.sidebar-toggle-btn {
  background: transparent;
  border: none;
  color: var(--dim);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 6px;
  border-radius: 6px;
  transition: color 0.15s, background 0.15s;
}

.sidebar-toggle-btn:hover {
  color: var(--tx);
  background: var(--panel2);
}

.cui-sidebar.pinned .sidebar-toggle-btn {
  color: var(--up);
  background: rgba(51, 201, 181, 0.15);
}

.sidebar-brand-text {
  font: 800 12px var(--disp);
  letter-spacing: 0.08em;
  color: var(--tx);
  white-space: nowrap;
  opacity: 0;
  transition: opacity 0.2s ease;
}

.cui-sidebar:hover .sidebar-brand-text,
.cui-sidebar.pinned .sidebar-brand-text {
  opacity: 1;
}

.sidebar-nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 6px;
  flex: 1;
}

.sidebar-tab-btn,
.sidebar-link-btn {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 10px;
  border-radius: 6px;
  background: transparent;
  border: 1px solid transparent;
  color: var(--dim);
  font: 600 12px var(--disp);
  cursor: pointer;
  text-decoration: none;
  white-space: nowrap;
  transition: all 0.15s ease;
  width: 100%;
  text-align: left;
}

.sidebar-tab-btn:hover,
.sidebar-link-btn:hover {
  color: var(--tx);
  background: var(--panel2);
}

.sidebar-tab-btn.active {
  color: var(--up);
  background: rgba(51, 201, 181, 0.12);
  border-color: rgba(51, 201, 181, 0.25);
  font-weight: 700;
}

.nav-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  flex-shrink: 0;
}

.nav-label {
  opacity: 0;
  transition: opacity 0.2s ease;
  white-space: nowrap;
}

.cui-sidebar:hover .nav-label,
.cui-sidebar.pinned .nav-label {
  opacity: 1;
}

.sidebar-divider {
  height: 1px;
  background: var(--line);
  margin: 6px 10px;
}

.sidebar-footer {
  padding: 10px 6px;
  border-top: 1px solid var(--line);
  flex-shrink: 0;
}

.sidebar-status-pill {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 6px;
  background: var(--panel);
  border: 1px solid var(--line);
  white-space: nowrap;
}

.status-indicator-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--dim);
  flex-shrink: 0;
}

.status-indicator-dot.running {
  background: var(--up);
  box-shadow: 0 0 6px var(--up);
}

.status-indicator-text {
  font: 700 10px var(--mono);
  color: var(--dim);
  opacity: 0;
  transition: opacity 0.2s ease;
}

.cui-sidebar:hover .status-indicator-text,
.cui-sidebar.pinned .status-indicator-text {
  opacity: 1;
}
```

### 5.3 JavaScript Interaction & State Persistence
In `server/osc_dash.py`:
```javascript
function toggleSidebarPin() {
  const sb = $('app-sidebar');
  if (!sb) return;
  const isPinned = sb.classList.toggle('pinned');
  document.body.classList.toggle('sidebar-pinned', isPinned);
  try {
    localStorage.setItem('cui_sidebar_pinned', isPinned ? 'true' : 'false');
  } catch (e) {}
}

function initSidebarState() {
  try {
    if (localStorage.getItem('cui_sidebar_pinned') === 'true') {
      const sb = $('app-sidebar');
      if (sb) sb.classList.add('pinned');
      document.body.classList.add('sidebar-pinned');
    }
  } catch (e) {}
}

// In switchTab(tabName):
function switchTab(name) {
  document.querySelectorAll('.tab-content').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.sidebar-tab-btn').forEach(b => b.classList.remove('active'));
  const pane = $('tab-' + name);
  const btn = $('tab-btn-' + name);
  if (pane) pane.classList.add('active');
  if (btn) btn.classList.add('active');
  if (name === 'cockpit') pollCockpit();
  if (name === 'live') pollLive();
  if (name === 'ticks') loadManifest();
}
```

---

## 6. Testing Strategy

### 6.1 Integration Contract Tests (`tests/test_osc_dash_integration.py`)
1. **DOM Navigation Elements Verification**:
   - `assert "app-sidebar" in html`
   - `assert "sidebarToggleBtn" in html`
   - `assert "tab-btn-cockpit" in html`
   - `assert "tab-btn-live" in html`
   - `assert "tab-btn-backtest" in html`
   - `assert "tab-btn-summary" in html`
   - `assert "tab-btn-ticks" in html`
   - `assert "toggleSidebarPin" in html`
   - `assert "sidebarBotStatusPill" in html`
2. **SVG Icon Presence**:
   - Verify that all tab navigation buttons contain an SVG element `<svg` and `<span class="nav-label">`.
3. **Streamlined Header Check**:
   - Verify `#app-hdr` still contains `collectorBadge`, `tapeBadge`, `btnToggleCollector`, and does *not* contain duplicate horizontal tab buttons.
4. **Active Tab Switching Logic**:
   - Verify `switchTab` updates `.sidebar-tab-btn.active`.

### 6.2 Regression Testing
- Execute full test suite: `python -m pytest -q` (all 197+ tests must pass).

---

## 7. Boundaries

- **Always do:**
  - Preserve all existing tab button element IDs (`tab-btn-cockpit`, `tab-btn-live`, `tab-btn-backtest`, `tab-btn-summary`, `tab-btn-ticks`).
  - Preserve existing JavaScript function signatures (`switchTab(name)`, `toggleCollector()`, `pollOnce()`, etc.).
  - Preserve responsive layout so tables, grids, and cards in tabs scale cleanly.
  - Run `python -m pytest -q` before opening any PR.
- **Ask first:**
  - Introducing new external fonts or external CDN script/style dependencies.
  - Removing or reordering primary application views.
- **Never do:**
  - Break existing SPA DOM selectors required by client-side tests.
  - Introduce horizontal layout shifts or content occlusion on load.
  - Hardcode fixed pixel widths for main content containers that break responsive resizing.

---

## 8. Success Criteria
- [ ] `#app-sidebar` is rendered anchored to the left of the viewport.
- [ ] Sidebar starts collapsed (`~48px`) displaying centered SVG icons.
- [ ] Hovering over the sidebar smoothly transitions its width to `~220px` without text clipping.
- [ ] `#sidebarToggleBtn` pins/unpins the sidebar and persists setting across page reloads via `localStorage`.
- [ ] Main page content offsets smoothly with zero content clipping or overlap.
- [ ] Horizontal tab container is removed from `#app-hdr`; title, badges, and polling buttons remain intact.
- [ ] Clicking any sidebar tab switches the active tab pane seamlessly via `switchTab()`.
- [ ] All existing automated tests in `tests/test_osc_dash_integration.py` pass.
- [ ] New unit and integration tests covering the sidebar DOM, toggle, and SVG icons pass.
- [ ] Full regression suite passes cleanly (`python -m pytest -q`).

---

## 9. Assumptions Surface
1. **Scope**: Scoped specifically to Issue #65 (Sidebar redesign). Issues #54 and #64 remain queued in the recommended roadmap order.
2. **Styling**: Zero external UI frameworks; purely native CSS transitions and embedded SVGs consistent with the existing dark theme in `server/osc_dash.py`.
3. **Local Storage**: `localStorage` key `'cui_sidebar_pinned'` is safely wrapped in `try/catch` to support private/sandboxed iframe modes.
