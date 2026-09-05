# Tasks: Issue #65 Collapsible Hover-Expandable Sidebar Navigation

- [x] Task 1: Add sidebar CSS variables, transitions, and responsive layout rules in `server/osc_dash.py`
  - Acceptance: CSS defines `--sidebar-w-collapsed: 48px` and `--sidebar-w-expanded: 220px`. Styles defined for `.cui-sidebar`, `.sidebar-header`, `.sidebar-toggle-btn`, `.sidebar-brand-text`, `.sidebar-nav`, `.sidebar-tab-btn`, `.sidebar-link-btn`, `.nav-icon`, `.nav-label`, `.sidebar-divider`, `.sidebar-footer`, `.sidebar-status-pill`, and `.status-indicator-dot`. Body viewport padding transitions smoothly when pinned.
  - Verify: `python -m pytest tests/test_osc_dash_integration.py -q`
  - Files: `server/osc_dash.py`

- [x] Task 2: Inject `<aside id="app-sidebar">` with SVG icons, streamline `#app-hdr`, and update `switchTab()`
  - Acceptance: `<aside id="app-sidebar">` inserted before `#app-hdr` containing `#sidebarToggleBtn`, all 5 tab buttons with inline SVG icons (`#tab-btn-cockpit`, `#tab-btn-live`, `#tab-btn-backtest`, `#tab-btn-summary`, `#tab-btn-ticks`), utility links (Polymarket & GitHub), and `#sidebarBotStatusPill`. Obsolete horizontal tabs removed from `#app-hdr`. `switchTab()` activates `.sidebar-tab-btn.active`.
  - Verify: `python -m pytest tests/test_osc_dash_integration.py -q`
  - Files: `server/osc_dash.py`

- [x] Task 3: Implement sidebar toggle pinning, localStorage persistence, and live engine status sync
  - Acceptance: `toggleSidebarPin()` toggles `.pinned` on `#app-sidebar` and `.sidebar-pinned` on `document.body` with `localStorage` persistence. `initSidebarState()` restores pinned state on load. `renderCockpitUI(st)` updates `#sidebarStatusDot` and `#sidebarStatusText`.
  - Verify: DOM inspection and integration tests.
  - Files: `server/osc_dash.py`

- [ ] Task 4: Add sidebar DOM contract tests in `tests/test_osc_dash_integration.py` and run full regression suite
  - Acceptance: Assert presence of `#app-sidebar`, `#sidebarToggleBtn`, all 5 tab buttons with SVG icons, absence of duplicate header tabs, and toggle functions. Full test suite passes with zero regressions (`python -m pytest -q`).
  - Verify: `python -m pytest -q`
  - Files: `tests/test_osc_dash_integration.py`
