# Implementation Plan: Collapsible Hover-Expandable Sidebar Navigation (Issue #65)

## Overview
Redesign the primary dashboard navigation in `server/osc_dash.py` by replacing the horizontal tabs in the top header with a modern, collapsible vertical sidebar menu (`48px` collapsed width with icon-only view; `220px` expanded on hover or when pinned). Streamline the top header `#app-hdr` to focus exclusively on title/branding, system telemetry badges, and polling controls, while preserving 100% backward compatibility for all element IDs and existing functions.

## Architecture Decisions
1. **Zero External Dependencies**: Use native CSS3 variables and transitions with inline SVG icons embedded directly in `server/osc_dash.py`. No external icon fonts or CDN libraries.
2. **Fixed Sidebar with Viewport Offset**: Anchor `<aside class="cui-sidebar" id="app-sidebar">` to the left (`position: fixed`, `height: 100vh`, `z-index: 1000`). Apply `padding-left: 48px` (or `220px` when pinned) to `body` to prevent content occlusion.
3. **Smooth Hover & Pinning Transitions**: Use `transition: width 0.25s cubic-bezier(0.16, 1, 0.3, 1)`. The `#sidebarToggleBtn` allows operators to lock the sidebar in an expanded state, persisted via `localStorage.getItem('cui_sidebar_pinned')`.
4. **Strict Element ID Compatibility**: Retain `#tab-btn-cockpit`, `#tab-btn-live`, `#tab-btn-backtest`, `#tab-btn-summary`, `#tab-btn-ticks` so existing tests and DOM queries continue to function seamlessly.
5. **Dynamic Bot Engine Telemetry in Sidebar Footer**: Wire `#sidebarBotStatusPill` and `#sidebarStatusDot` into `renderCockpitUI(st)` so operators see live bot status even when navigating other tabs.

## Task List

### Phase 1: CSS Styling & Layout Foundations
- [ ] Task 1: Add sidebar CSS variables, transitions, and responsive layout rules in `server/osc_dash.py`
  - Acceptance: CSS defines `--sidebar-w-collapsed: 48px` and `--sidebar-w-expanded: 220px`. Styles defined for `.cui-sidebar`, `.sidebar-header`, `.sidebar-toggle-btn`, `.sidebar-brand-text`, `.sidebar-nav`, `.sidebar-tab-btn`, `.sidebar-link-btn`, `.nav-icon`, `.nav-label`, `.sidebar-divider`, `.sidebar-footer`, `.sidebar-status-pill`, and `.status-indicator-dot`. Body viewport padding transitions smoothly when pinned.
  - Verify: `python -m pytest tests/test_osc_dash_integration.py -q`
  - Files: `server/osc_dash.py`

### Checkpoint: Layout Foundations
- [ ] CSS rules load without syntax errors and existing tests pass.

### Phase 2: DOM Restructuring & Navigation Logic
- [ ] Task 2: Inject `<aside id="app-sidebar">` with SVG icons, streamline `#app-hdr`, and update `switchTab()`
  - Acceptance: `<aside id="app-sidebar">` inserted before `#app-hdr` containing `#sidebarToggleBtn`, all 5 tab buttons with inline SVG icons (`#tab-btn-cockpit`, `#tab-btn-live`, `#tab-btn-backtest`, `#tab-btn-summary`, `#tab-btn-ticks`), utility links (Polymarket & GitHub), and `#sidebarBotStatusPill`. Obsolete horizontal tabs removed from `#app-hdr`. `switchTab()` activates `.sidebar-tab-btn.active`.
  - Verify: `python -m pytest tests/test_osc_dash_integration.py -q`
  - Files: `server/osc_dash.py`

- [ ] Task 3: Implement sidebar toggle pinning, localStorage persistence, and live engine status sync
  - Acceptance: `toggleSidebarPin()` toggles `.pinned` on `#app-sidebar` and `.sidebar-pinned` on `document.body` with `localStorage` persistence. `initSidebarState()` restores pinned state on load. `renderCockpitUI(st)` updates `#sidebarStatusDot` and `#sidebarStatusText`.
  - Verify: DOM inspection and integration tests.
  - Files: `server/osc_dash.py`

### Checkpoint: Functional Sidebar & Interaction
- [ ] Sidebar expands on hover, pins when toggled, and switches tabs cleanly.

### Phase 3: Integration Tests & Full Suite Verification
- [ ] Task 4: Add sidebar DOM contract tests in `tests/test_osc_dash_integration.py` and run full regression suite
  - Acceptance: Assert presence of `#app-sidebar`, `#sidebarToggleBtn`, all 5 tab buttons with SVG icons, absence of duplicate header tabs, and toggle functions. Full test suite passes with zero regressions (`python -m pytest -q`).
  - Verify: `python -m pytest -q`
  - Files: `tests/test_osc_dash_integration.py`

### Checkpoint: Complete
- [ ] All acceptance criteria met.
- [ ] 197+ tests passing cleanly.

## Risks and Mitigations
| Risk | Impact | Mitigation |
|------|--------|------------|
| Layout shift or content overlap when sidebar expands | Low | Sidebar expands over content with shadow, while pinning smoothly shifts body `padding-left`. |
| Existing integration tests breaking due to removed `.nav-tabs` container | Medium | Retain all tab button IDs and ensure `tests/test_osc_dash_integration.py` targets button IDs directly. |
| Private browsing mode blocking `localStorage` access | Low | Wrap all `localStorage` reads/writes in `try/catch` blocks. |

## Open Questions
- None. Requirements and design specifications are fully locked in `SPEC.md`.
