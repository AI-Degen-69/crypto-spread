# Plan — Issue #133: Increase Orders & Trades table height and view capacity in Live Cockpit

- **Issue:** https://github.com/AI-Degen-69/crypto-spread/issues/133
- **Branch:** `feat/cockpit-table-height-133` (off `master`)
- **Size tier:** **Small** — 1 application file (`server/osc_dash.py`) + 1 test file (`tests/test_orders_trades_table.py`). UI/CSS enhancement, no backend API change, zero external dependencies.
- **Task type:** **Design/UI** (layout, CSS styling, table sizing, responsive UX) + **Code** (HTML structure, JS view toggle, regression tests).
- **Stack detected:** Python 3.12 / FastAPI, embedded SPA (`FULL_APP_HTML`); `pytest` + Node DOM test harness.
- **Verification mode:** Automated tests (`python -m pytest tests/test_orders_trades_table.py -q`).
- **interview-me:** Skipped. The issue contains explicit container IDs, height specifications (`min(560px, 65vh)`), sticky header requirements, and acceptance criteria.

## Skills prescribed per task

| Domain | Skill |
| --- | --- |
| Planning | `spec-driven-development`, `constraint-driven-development`, `planning-and-task-breakdown` |
| UI & Layout | `frontend-ui-engineering`, `modern-web-guidance` |
| Build & Quality | `test-driven-development`, `code-review-and-quality` |

---

## Tasks

### T1 — [x] `[Design/UI]` Table container height expansion & responsive CSS
- **Domain:** `[Design/UI]`
- **Target Files:** `server/osc_dash.py`
- **Skill:** `frontend-ui-engineering`
- **Description:**
  - Replace inline `max-height:280px` / `max-height:300px` on `#otPaneOrders`, `#otPanePositions`, and `#otPaneTrades` with a unified responsive class `.ot-pane-scroll` configured for `max-height: min(560px, 65vh); overflow-y: auto;`.
  - Ensure `.ot-pane-scroll` expands comfortably on desktop viewports while gracefully adapting to smaller laptop screens without overflowing the page.
- **Verification:** Unit test asserting the expanded height styling on `#otPaneOrders`, `#otPanePositions`, and `#otPaneTrades`.

### T2 — [x] `[Design/UI]` Sticky headers & compact table row density
- **Domain:** `[Design/UI]`
- **Target Files:** `server/osc_dash.py`
- **Skill:** `frontend-ui-engineering`
- **Description:**
  - Refine `.ot-pane .tbl thead th` sticky styling (`position: sticky; top: 0; z-index: 10; background: var(--panel); box-shadow: 0 1px 0 var(--line);`) to guarantee no text bleed-through when scrolling down dozens of rows.
  - Optimize vertical padding on `.ot-pane .tbl td` (6px 8px, font-size 12px) and `.ot-pane .tbl th` (7px 8px) so at least 15+ order rows fit in view simultaneously without vertical clutter.
- **Verification:** Assert CSS rules for sticky header and row density are present in rendered markup.

### T3 — [x] `[Design/UI]` Outside-the-box improvement: View Height Mode Toggle (`⛶ Expand` / `🗗 Standard`)
- **Domain:** `[Design/UI]`
- **Target Files:** `server/osc_dash.py`
- **Skill:** `frontend-ui-engineering`
- **Description:**
  - Add a height toggle button (`#otHeightToggleBtn`) next to the `🔄 Refresh` button in `#orders-trades-card` header.
  - Implement `toggleOtHeight()` in JS to allow switching between Standard expanded mode (`min(560px, 65vh)`) and Full Tall mode (`85vh`).
  - Persist the operator's preference in `localStorage.getItem('crypto-spread-ot-height')` and restore it on initial load.
- **Verification:** Node-based test in `tests/test_orders_trades_table.py` checking toggle execution and `localStorage` persistence.

### T4 — [x] `[Code/Tests]` Regression & integration test suite update
- **Domain:** `[Code/Tests]`
- **Target Files:** `tests/test_orders_trades_table.py`
- **Skill:** `test-driven-development`
- **Description:**
  - Add tests validating that `#otPaneOrders`, `#otPanePositions`, `#otPaneTrades` have >= 500px / 65vh height.
  - Test sticky header CSS properties and compact padding.
  - Test `toggleOtHeight()` and `localStorage` integration in the Node test harness.
  - Verify all 30 existing tests plus new tests pass with 0 failures.
- **Verification:** `python -m pytest tests/test_orders_trades_table.py -q` passes 100%.
